"""Tile a GeoParquet layer to PMTiles (Celery, background).

This is the DISPLAY path for GeoParquet layers: tippecanoe builds a single .pmtiles archive once,
which the browser then streams via HTTP range requests (no per-pan server work). We stream the
GeoParquet → GeoJSONSeq into tippecanoe's stdin so we never spool a multi-GB intermediate file.
DuckDB (analysis/download) keeps reading the original .parquet — the .pmtiles is display-only.
"""
import logging
import os
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
# tiles and very long tiling). `-zg` exploded; z14 was still ~2h on a 9.5M-polygon file (the tiling
# pass, not the stream, dominates). z12 is ~16x fewer bottom-level tiles and is visually identical
# at portal zooms — MapLibre overzooms past the cap, so features still show past z12, just without
# extra detail. The single biggest lever on tiling time + output size. Env-tunable to retune per run.
PMTILES_MAXZOOM = int(os.getenv("PMTILES_MAXZOOM", "12"))
# Extra geometry simplification below the max zoom (tippecanoe's tile-space factor; default 1, higher
# = more aggressive). Cuts per-tile vertex work on dense data. Set to 0/"" to drop the flag.
PMTILES_SIMPLIFICATION = os.getenv("PMTILES_SIMPLIFICATION", "10")


@celery_app.task(bind=True, name="geodeploy.tasks.pmtiles_tile.tile_geoparquet")
def tile_geoparquet(self, layer_id, s3_key, pmtiles_key):
    from ..services import duckdb_engine
    settings = get_settings()
    db_path = f"{settings.data_dir}/sqlite/geodeploy.db"
    creds = _get_storage_creds(db_path)
    tmpdir = f"{settings.data_dir}/temp"
    os.makedirs(tmpdir, exist_ok=True)
    out_path = os.path.join(tmpdir, f"{uuid4().hex}.pmtiles")

    _update_layer(db_path, layer_id, tile_status="tiling")
    started = time.monotonic()
    logger.info("tile_geoparquet: layer %s — tiling %s → z%s (PMTiles)", layer_id, s3_key, PMTILES_MAXZOOM)
    try:
        # Capped max zoom (PMTILES_MAXZOOM) keeps tiling fast. --coalesce-densest-as-needed MERGES
        # the densest features when a tile is over budget (vs --drop, which discards them) — preserves
        # area coverage for polygons at low zoom, at the cost of merging their attributes in those
        # over-budget tiles. --simplification cuts vertex work. Read GeoJSONSeq from stdin
        # (/dev/stdin) so there's no giant intermediate file on disk.
        cmd = ["tippecanoe", "-o", out_path, "-l", PMTILES_LAYER, "-z", str(PMTILES_MAXZOOM),
               "--coalesce-densest-as-needed", "--force", "-t", tmpdir]
        if PMTILES_SIMPLIFICATION and str(PMTILES_SIMPLIFICATION) not in ("0", ""):
            cmd += ["--simplification", str(PMTILES_SIMPLIFICATION)]
        cmd.append("/dev/stdin")
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)

        # tippecanoe writes its progress bar to stderr using carriage returns (\r), which never
        # newline-flush to `docker logs`. Read it byte-wise, split on \r AND \n, and re-log each
        # update so the tiling phase is observable. (The shapely stream logs its own feature count.)
        def pump_stderr():
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

        feed_res = {}

        def feed():
            try:
                feed_res["count"] = duckdb_engine.stream_geojsonseq(s3_key, proc.stdin, creds)
            except Exception as e:  # noqa: BLE001
                feed_res["e"] = e
            finally:
                try:
                    proc.stdin.close()
                except Exception:
                    pass

        t = threading.Thread(target=feed, daemon=True)
        e_thread = threading.Thread(target=pump_stderr, daemon=True)
        t.start()
        e_thread.start()
        ret = proc.wait()
        t.join()
        e_thread.join()

        if feed_res.get("e"):
            raise feed_res["e"]
        if ret != 0:
            raise RuntimeError(f"tippecanoe exited with code {ret}")
        logger.info("tile_geoparquet: layer %s — tippecanoe done, %s features in %.0fs; uploading .pmtiles",
                    layer_id, f"{feed_res.get('count', 0):,}", time.monotonic() - started)

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
        if os.path.exists(out_path):
            try:
                os.unlink(out_path)
            except OSError:
                pass
