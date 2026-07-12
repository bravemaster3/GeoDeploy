"""Tile a GeoParquet layer to PMTiles (Celery, background).

This is the DISPLAY path for GeoParquet layers: tippecanoe builds a single .pmtiles archive once,
which the browser then streams via HTTP range requests (no per-pan server work). DuckDB
(analysis/download) keeps reading the original .parquet — the .pmtiles is display-only.

Two feeds into tippecanoe, primary + fallback:
  1. PRIMARY (default): a native concurrent stream — DuckDB (baked `spatial`) converts geometry to
     GeoJSON streamed to tippecanoe's stdin so the feed OVERLAPS the tiling pass. No serialized
     intermediate, no per-feature shapely. An optional DISPLAY-ONLY simplification (OFF by default —
     it cut corners on big polygons and dropped small ones; re-enable with PMTILES_SIMPLIFY=1) can
     remove sub-tile-pixel vertices to speed tiling; even then it touches ONLY the tiles — the source
     .parquet (downloads/clip/identify) always stays full-resolution.
  2. FALLBACK: stream GeoParquet → GeoJSONSeq via shapely (no simplify), used only if `spatial` is
     unavailable.

Both feeds cap DuckDB memory and spill to disk, so tiling a huge layer runs in bounded RAM on a
small VPS. All scratch (DuckDB spills, tippecanoe's temp, the output .pmtiles) lives under a per-run
dir removed in `finally`, so a killed run cannot leak GBs into data/temp. An earlier FlatGeobuf feed
was dropped: writing the whole .fgb before tiling serialized the feed (lost the overlap) and was a
net loss on heavy geometry.
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
# Max zoom baked into the tiles — the single biggest lever on tiling time + output size (`-zg`, which
# guesses, exploded to z14+ ≈ 2h on 9.5M polygons). MapLibre overzooms past the cap, so the map still
# shows detail beyond it. Chosen ADAPTIVELY by feature count (see `_resolve_maxzoom`) so a one-line
# install needs no tuning: heavy layers get a lower zoom (far fewer tiles = fast), light layers a
# higher one. Setting `PMTILES_MAXZOOM` in .env overrides the adaptive choice for the whole deployment.
PMTILES_MAXZOOM_OVERRIDE = os.getenv("PMTILES_MAXZOOM")
# Extra geometry simplification below the max zoom (tippecanoe's tile-space factor; higher = more
# aggressive). OFF by default ("0"): the aggressive factor cut corners on big polygons (visibly
# ugly). Leaving it unset lets tippecanoe keep faithful geometry. Set a value to re-enable.
PMTILES_SIMPLIFICATION = os.getenv("PMTILES_SIMPLIFICATION", "0")
# How tippecanoe sheds features from over-budget tiles: "drop" (--drop-densest-as-needed, the
# default — cheap, just discards the densest) or "coalesce" (--coalesce-densest-as-needed — MERGES
# them, preserving polygon area coverage at low zoom but far more expensive per tile). Drop is the
# default because coalesce's per-tile geometry union was a large slice of the slow tiling pass.
PMTILES_DENSEST = os.getenv("PMTILES_DENSEST", "drop").strip().lower()
# DuckDB memory cap for the tiling feed (default 80% of RAM would OOM a small VPS beside tippecanoe).
PMTILES_TILE_MEMORY_LIMIT = os.getenv("PMTILES_TILE_MEMORY_LIMIT", "1GB")
PMTILES_TILE_THREADS = int(os.getenv("PMTILES_TILE_THREADS", "2"))
# Feed mode: "native" (DuckDB→GeoJSON streamed concurrently to tippecanoe, with display-only
# simplification) or "geojsonseq" to force the shapely stream (no simplify). Env-tunable for debugging.
PMTILES_INPUT = os.getenv("PMTILES_INPUT", "native").strip().lower()
# Display-only geometry simplification for the tiles feed — NOT the stored data. OFF by default:
# the tolerance (~one tile-unit at the max zoom, ≈9.5 m at z10) cut corners on large parcels and made
# sub-tolerance polygons (small buildings) collapse and disappear when zoomed in. Set PMTILES_SIMPLIFY=1
# to re-enable (speeds tiling ~50-75% on dense polygons at the cost of that visual fidelity);
# PMTILES_SIMPLIFY_FACTOR scales the tolerance. Never touches downloads/clip/identify (those read the
# original .parquet at full resolution).
PMTILES_SIMPLIFY = os.getenv("PMTILES_SIMPLIFY", "0").strip().lower() not in ("0", "false", "off", "")
PMTILES_SIMPLIFY_FACTOR = float(os.getenv("PMTILES_SIMPLIFY_FACTOR", "1.0"))
# Guarantee every feature survives to the deepest zoom, so NOTHING disappears when zoomed in — even in
# dense areas. tippecanoe keeps adding zoom levels ONLY in the tiles that are still dropping features
# until they all fit (--extend-zooms-if-still-dropping), and never merges small polygons away
# (--no-tiny-polygon-reduction). ON by default (visual completeness > tiling speed/size). The map's
# pmtiles source reads its max zoom from the archive header, so these extra levels are fetched
# automatically on overzoom. Trade-off: denser layers tile slower and produce a bigger archive (disk /
# bandwidth only — not RAM, it streams by range request). Set PMTILES_KEEP_ALL_FEATURES=0 to let dense
# tiles thin at the max zoom for smaller/faster archives.
PMTILES_KEEP_ALL_FEATURES = os.getenv("PMTILES_KEEP_ALL_FEATURES", "1").strip().lower() not in ("0", "false", "off", "")
_TILE_EXTENT = 4096  # tippecanoe tile-units per edge; sizes the tolerance to ~1 unit at max zoom


def _simplify_tol(maxzoom: int) -> float | None:
    """A degree tolerance ~one tile-unit at `maxzoom` (sub-pixel at the tiled zoom), or None when
    simplification is disabled. Feed-only — never applied to the stored GeoParquet."""
    if not PMTILES_SIMPLIFY:
        return None
    return (360.0 / (2 ** maxzoom * _TILE_EXTENT)) * PMTILES_SIMPLIFY_FACTOR


def _resolve_maxzoom(feature_count: int) -> int:
    """Pick tippecanoe's max zoom from the layer's feature count so a one-line install tiles heavy
    layers fast without any tuning. An explicit `PMTILES_MAXZOOM` env var overrides this. The tiers
    trade a little top-zoom detail (MapLibre overzooms, so the map still looks right) for a large cut
    in tiling time and archive size on dense data (each zoom is ~4x fewer bottom-level tiles)."""
    if PMTILES_MAXZOOM_OVERRIDE:
        return int(PMTILES_MAXZOOM_OVERRIDE)
    n = feature_count or 0
    if n >= 10_000_000:
        return 10
    if n >= 2_000_000:
        return 11
    if n >= 500_000:
        return 12
    return 13


def _layer_feature_count(db_path: str, layer_id) -> int:
    """Read a layer's stored feature_count (0 if unknown) to drive {@link _resolve_maxzoom}."""
    import sqlite3
    try:
        con = sqlite3.connect(db_path)
        try:
            row = con.execute(
                "SELECT feature_count FROM vector_layers WHERE id=?", (layer_id,)).fetchone()
        finally:
            con.close()
        return int(row[0]) if row and row[0] is not None else 0
    except Exception:
        return 0


def _densest_flag() -> str:
    return "--coalesce-densest-as-needed" if PMTILES_DENSEST == "coalesce" \
        else "--drop-densest-as-needed"


def _tippecanoe_base(out_path: str, scratch: str, maxzoom: int) -> list:
    """The tippecanoe argv common to both feeds (output, layer, zoom, simplification, densest,
    a dedicated on-disk scratch dir)."""
    cmd = ["tippecanoe", "-o", out_path, "-l", PMTILES_LAYER, "-z", str(maxzoom),
           _densest_flag(), "--force", "-t", scratch]
    if PMTILES_KEEP_ALL_FEATURES:
        # Add deeper zooms only where tiles still drop, and keep small polygons — so every feature is
        # present at the deepest zoom and visible when zoomed in, even in dense areas.
        cmd += ["--extend-zooms-if-still-dropping", "--no-tiny-polygon-reduction"]
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

    feature_count = _layer_feature_count(db_path, layer_id)
    maxzoom = _resolve_maxzoom(feature_count)
    simplify_tol = _simplify_tol(maxzoom)
    _update_layer(db_path, layer_id, tile_status="tiling")
    started = time.monotonic()
    logger.info("tile_geoparquet: layer %s — tiling %s → z%s (PMTiles, input=%s, mem=%s, features=%s, simplify_tol=%s)",
                layer_id, s3_key, maxzoom, PMTILES_INPUT, PMTILES_TILE_MEMORY_LIMIT, f"{feature_count:,}", simplify_tol)
    try:
        used = None
        if PMTILES_INPUT != "geojsonseq":
            # PRIMARY: native concurrent stream — DuckDB converts (and display-only simplifies)
            # geometry to GeoJSON, streamed to tippecanoe's stdin so the feed OVERLAPS the tiling
            # pass (no serialized intermediate file, no per-feature shapely).
            try:
                cmd = _tippecanoe_base(out_path, tile_scratch, maxzoom) + ["/dev/stdin"]

                def _feed_native(stdin):
                    duckdb_engine.stream_tiling_geojsonseq(
                        s3_key, stdin, creds, simplify_tol=simplify_tol,
                        memory_limit=PMTILES_TILE_MEMORY_LIMIT, threads=PMTILES_TILE_THREADS)
                _run_tippecanoe(cmd, feed=_feed_native)
                used = "native"
            except Exception as exc:  # noqa: BLE001
                logger.warning("tile_geoparquet: layer %s — native stream failed (%s); falling back "
                               "to shapely GeoJSONSeq (no simplify)", layer_id, str(exc)[:200])
                # Reset scratch/output so the fallback starts clean.
                shutil.rmtree(tile_scratch, ignore_errors=True)
                os.makedirs(tile_scratch, exist_ok=True)
                if os.path.exists(out_path):
                    os.unlink(out_path)

        if used is None:
            # FALLBACK: shapely stream → tippecanoe stdin (no simplify; used only if `spatial` is
            # unavailable). Also concurrent + memory-bounded.
            cmd = _tippecanoe_base(out_path, tile_scratch, maxzoom) + ["/dev/stdin"]

            def _feed_shapely(stdin):
                duckdb_engine.stream_geojsonseq(
                    s3_key, stdin, creds, memory_limit=PMTILES_TILE_MEMORY_LIMIT,
                    threads=PMTILES_TILE_THREADS)
            _run_tippecanoe(cmd, feed=_feed_shapely)
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
