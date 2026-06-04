"""Tile a GeoParquet layer to PMTiles (Celery, background).

This is the DISPLAY path for GeoParquet layers: tippecanoe builds a single .pmtiles archive once,
which the browser then streams via HTTP range requests (no per-pan server work). We stream the
GeoParquet → GeoJSONSeq into tippecanoe's stdin so we never spool a multi-GB intermediate file.
DuckDB (analysis/download) keeps reading the original .parquet — the .pmtiles is display-only.
"""
import os
import subprocess
import threading
from datetime import datetime, timezone
from uuid import uuid4

from ..celery_app import celery_app
from ..config import get_settings
from .raster_ingest import _get_storage_creds
from .vector_ingest import _update_layer

# tippecanoe layer name → the MVT "source-layer" the MapLibre style references.
PMTILES_LAYER = "geodeploy"


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
    try:
        # -zg: guess max zoom; drop/extend keep dense areas legible without blowing up tile size.
        # Read GeoJSONSeq from stdin (/dev/stdin) so there's no giant intermediate file on disk.
        cmd = ["tippecanoe", "-o", out_path, "-l", PMTILES_LAYER, "-zg",
               "--drop-densest-as-needed", "--extend-zooms-if-still-dropping",
               "--force", "-t", tmpdir, "/dev/stdin"]
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)

        feed_err = {}

        def feed():
            try:
                duckdb_engine.stream_geojsonseq(s3_key, proc.stdin, creds)
            except Exception as e:  # noqa: BLE001
                feed_err["e"] = e
            finally:
                try:
                    proc.stdin.close()
                except Exception:
                    pass

        t = threading.Thread(target=feed, daemon=True)
        t.start()
        ret = proc.wait()
        t.join()

        if feed_err.get("e"):
            raise feed_err["e"]
        if ret != 0:
            raise RuntimeError(f"tippecanoe exited with code {ret}")

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
    except Exception as exc:
        _update_layer(db_path, layer_id, tile_status="error", error_message=str(exc))
        raise
    finally:
        if os.path.exists(out_path):
            try:
                os.unlink(out_path)
            except OSError:
                pass
