"""Tile a GeoParquet layer to PMTiles (Celery, background).

This is the DISPLAY path for GeoParquet layers: tippecanoe builds a single .pmtiles archive once,
which the browser then streams via HTTP range requests (no per-pan server work). DuckDB
(analysis/download) keeps reading the original .parquet — the .pmtiles is display-only.

Two feeds into tippecanoe, primary + fallback:
  1. FAST (default): DuckDB converts GeoParquet → FlatGeobuf natively (baked `spatial` extension),
     tippecanoe reads the binary .fgb file. No per-feature shapely/pyproj funnel (the old bottleneck).
  2. FALLBACK: stream GeoParquet → GeoJSONSeq into tippecanoe's stdin via shapely. Used if the FGB
     export fails (e.g. `spatial` unavailable, or an unwritable attribute type).

Both feeds cap DuckDB memory and spill to disk, so tiling a huge layer runs in bounded RAM on a
small VPS. All scratch (the .fgb, DuckDB spills, tippecanoe's temp) lives under a per-run dir that
is removed in `finally`, so a killed run cannot leak GBs into data/temp.
"""
import logging
import os
import shutil
import subprocess
import threading
import time
from datetime import datetime, timezone
from uuid import uuid4

from ..celery_app import celery_app
from ..config import get_settings
from .raster_ingest import _get_storage_creds
from .vector_ingest import _update_layer

logger = logging.getLogger(__name__)

# tippecanoe layer name → the MVT "source-layer" the MapLibre style references.
PMTILES_LAYER = "geodeploy"
# Cap the max zoom (instead of `-zg`, which picks a high zoom for dense data → an explosion of
# tiles and very long tiling). `-zg` exploded; z14 was still ~2h on a 9.5M-polygon file. z12 is
# ~16x fewer bottom-level tiles and is visually identical at portal zooms — MapLibre overzooms past
# the cap. The single biggest lever on tiling time + output size. Env-tunable to retune per run.
PMTILES_MAXZOOM = int(os.getenv("PMTILES_MAXZOOM", "12"))
# Extra geometry simplification below the max zoom (tippecanoe's tile-space factor; higher = more
# aggressive). Cuts per-tile vertex work on dense data. Set to 0/"" to drop the flag.
PMTILES_SIMPLIFICATION = os.getenv("PMTILES_SIMPLIFICATION", "10")
# How tippecanoe sheds features from over-budget tiles: "drop" (--drop-densest-as-needed, the
# default — cheap, just discards the densest) or "coalesce" (--coalesce-densest-as-needed — MERGES
# them, preserving polygon area coverage at low zoom but far more expensive per tile). Drop is the
# default because coalesce's per-tile geometry union was a large slice of the slow tiling pass.
PMTILES_DENSEST = os.getenv("PMTILES_DENSEST", "drop").strip().lower()
# DuckDB memory cap for the tiling feed (default 80% of RAM would OOM a small VPS beside tippecanoe).
PMTILES_TILE_MEMORY_LIMIT = os.getenv("PMTILES_TILE_MEMORY_LIMIT", "1GB")
PMTILES_TILE_THREADS = int(os.getenv("PMTILES_TILE_THREADS", "2"))
# Feed mode: "fgb" (native FlatGeobuf, fast) with automatic fallback to the GeoJSONSeq stream, or
# "geojsonseq" to force the shapely stream (skip FGB entirely). Env-tunable for debugging.
PMTILES_INPUT = os.getenv("PMTILES_INPUT", "fgb").strip().lower()


def _densest_flag() -> str:
    return "--coalesce-densest-as-needed" if PMTILES_DENSEST == "coalesce" \
        else "--drop-densest-as-needed"


def _tippecanoe_base(out_path: str, scratch: str) -> list:
    """The tippecanoe argv common to both feeds (output, layer, zoom, simplification, densest,
    a dedicated on-disk scratch dir)."""
    cmd = ["tippecanoe", "-o", out_path, "-l", PMTILES_LAYER, "-z", str(PMTILES_MAXZOOM),
           _densest_flag(), "--force", "-t", scratch]
    if PMTILES_SIMPLIFICATION and str(PMTILES_SIMPLIFICATION) not in ("0", ""):
        cmd += ["--simplification", str(PMTILES_SIMPLIFICATION)]
    return cmd


def _pump_stderr(proc):
    """tippecanoe writes its progress bar to stderr with carriage returns (\\r), which never
    newline-flush to `docker logs`. Read byte-wise, split on \\r AND \\n, re-log each update so the
    tiling phase is observable."""
    buf = b""
    while True:
        chunk = proc.stderr.read(256)
        if not chunk:
            break
        buf += chunk
        while True:
            idx = min((i for i in (buf.find(b"\r"), buf.find(b"\n")) if i != -1), default=-1)
            if idx == -1:
                break
            line, buf = buf[:idx], buf[idx + 1:]
            line = line.strip()
            if line:
                logger.info("tippecanoe: %s", line.decode(errors="replace"))
    if buf.strip():
        logger.info("tippecanoe: %s", buf.strip().decode(errors="replace"))


def _run_tippecanoe(cmd: list, feed=None) -> None:
    """Run tippecanoe, optionally feeding its stdin from `feed(stdin)` on a thread. Raises on a
    non-zero exit or a feed error."""
    stdin = subprocess.PIPE if feed else None
    proc = subprocess.Popen(cmd, stdin=stdin, stderr=subprocess.PIPE)
    feed_res = {}

    def _feed():
        try:
            feed(proc.stdin)
        except Exception as e:  # noqa: BLE001
            feed_res["e"] = e
        finally:
            try:
                proc.stdin.close()
            except Exception:
                pass

    threads = [threading.Thread(target=_pump_stderr, args=(proc,), daemon=True)]
    if feed:
        threads.insert(0, threading.Thread(target=_feed, daemon=True))
    for t in threads:
        t.start()
    ret = proc.wait()
    for t in threads:
        t.join()
    if feed_res.get("e"):
        raise feed_res["e"]
    if ret != 0:
        raise RuntimeError(f"tippecanoe exited with code {ret}")


@celery_app.task(bind=True, name="geodeploy.tasks.pmtiles_tile.tile_geoparquet")
def tile_geoparquet(self, layer_id, s3_key, pmtiles_key):
    from ..services import duckdb_engine
    settings = get_settings()
    db_path = f"{settings.data_dir}/sqlite/geodeploy.db"
    creds = _get_storage_creds(db_path)
    tmpdir = f"{settings.data_dir}/temp"
    # Per-run scratch dir: the .fgb, DuckDB spills, tippecanoe temp, and the output .pmtiles all live
    # here and are removed in `finally`, so a killed run cannot leak GBs (the old code left a 5GB
    # tippecanoe scratch behind on every OOM). Also isolates concurrent runs.
    run_dir = os.path.join(tmpdir, f"tile-{uuid4().hex}")
    tile_scratch = os.path.join(run_dir, "tc")
    os.makedirs(tile_scratch, exist_ok=True)
    out_path = os.path.join(run_dir, "out.pmtiles")

    _update_layer(db_path, layer_id, tile_status="tiling")
    started = time.monotonic()
    logger.info("tile_geoparquet: layer %s — tiling %s → z%s (PMTiles, input=%s, mem=%s)",
                layer_id, s3_key, PMTILES_MAXZOOM, PMTILES_INPUT, PMTILES_TILE_MEMORY_LIMIT)
    try:
        used = None
        if PMTILES_INPUT != "geojsonseq":
            # FAST path: DuckDB → FlatGeobuf, tippecanoe reads the binary file (parallel).
            fgb_path = os.path.join(run_dir, "input.fgb")
            try:
                duckdb_engine.export_geoparquet_to_fgb(
                    s3_key, fgb_path, creds, memory_limit=PMTILES_TILE_MEMORY_LIMIT,
                    threads=PMTILES_TILE_THREADS)
                # No `-P`: tippecanoe's parallel read is line-delimited-GeoJSON only, not FlatGeobuf
                # (passing it with a .fgb can error → silent fallback). The FGB win is binary parse +
                # no shapely, not parallel input.
                cmd = _tippecanoe_base(out_path, tile_scratch) + [fgb_path]
                _run_tippecanoe(cmd)
                used = "fgb"
            except Exception as exc:  # noqa: BLE001
                logger.warning("tile_geoparquet: layer %s — FlatGeobuf path failed (%s); "
                               "falling back to GeoJSONSeq stream", layer_id, str(exc)[:200])
                # Reset scratch/output so the fallback starts clean.
                shutil.rmtree(tile_scratch, ignore_errors=True)
                os.makedirs(tile_scratch, exist_ok=True)
                if os.path.exists(out_path):
                    os.unlink(out_path)

        if used is None:
            # FALLBACK path: shapely stream → tippecanoe stdin (memory-bounded).
            cmd = _tippecanoe_base(out_path, tile_scratch) + ["/dev/stdin"]

            def _feed(stdin):
                duckdb_engine.stream_geojsonseq(
                    s3_key, stdin, creds, memory_limit=PMTILES_TILE_MEMORY_LIMIT,
                    threads=PMTILES_TILE_THREADS)
            _run_tippecanoe(cmd, feed=_feed)
            used = "geojsonseq"

        logger.info("tile_geoparquet: layer %s — tippecanoe done via %s in %.0fs; uploading .pmtiles",
                    layer_id, used, time.monotonic() - started)

        import boto3
        from botocore.client import Config
        s3 = boto3.client(
            "s3", endpoint_url=creds["endpoint"],
            aws_access_key_id=creds["access_key"], aws_secret_access_key=creds["secret_key"],
            region_name=creds["region"], config=Config(signature_version="s3v4"),
        )
        s3.upload_file(out_path, creds["bucket"], pmtiles_key,
                       ExtraArgs={"ContentType": "application/octet-stream"})

        _update_layer(db_path, layer_id, pmtiles_key=pmtiles_key, tile_status="ready",
                      updated_at=datetime.now(timezone.utc).isoformat())
        logger.info("tile_geoparquet: layer %s — READY in %.0fs total", layer_id, time.monotonic() - started)
    except Exception as exc:
        _update_layer(db_path, layer_id, tile_status="error", error_message=str(exc))
        raise
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)
