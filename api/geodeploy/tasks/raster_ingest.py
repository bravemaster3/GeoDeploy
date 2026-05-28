"""
Raster ingest pipeline: uploaded GeoTIFF → COG conversion → MinIO → TiTiler ready.
"""
import json
import os
import tempfile
from datetime import datetime, timezone

import boto3
from botocore.client import Config

from ..celery_app import celery_app
from ..config import get_settings
from ..services.cog_converter import convert_to_cog, inspect as inspect_raster, is_cog


def _get_storage_creds(db_path: str) -> dict:
    import sqlite3
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT storage_endpoint, storage_bucket, storage_access_key, storage_secret_key, storage_region "
            "FROM setup_config WHERE id=1"
        ).fetchone()
    if row and row[2]:
        return {"endpoint": row[0], "bucket": row[1], "access_key": row[2],
                "secret_key": row[3], "region": row[4] or "us-east-1"}
    settings = get_settings()
    return {"endpoint": settings.storage_endpoint, "bucket": settings.storage_bucket,
            "access_key": settings.storage_access_key, "secret_key": settings.storage_secret_key,
            "region": settings.storage_region or "us-east-1"}


def _update_job(db_path: str, job_id: str, **kwargs) -> None:
    import sqlite3
    with sqlite3.connect(db_path) as conn:
        sets = ", ".join(f"{k} = ?" for k in kwargs)
        values = list(kwargs.values()) + [job_id]
        conn.execute(f"UPDATE upload_jobs SET {sets} WHERE id = ?", values)


def _update_layer(db_path: str, layer_id: int, **kwargs) -> None:
    import sqlite3
    with sqlite3.connect(db_path) as conn:
        sets = ", ".join(f"{k} = ?" for k in kwargs)
        values = list(kwargs.values()) + [layer_id]
        conn.execute(f"UPDATE raster_layers SET {sets} WHERE id = ?", values)


@celery_app.task(bind=True, name="geodeploy.tasks.raster_ingest.ingest_raster")
def ingest_raster(self, job_id: str, layer_id: int, file_path: str, s3_key: str):
    settings = get_settings()
    db_path = f"{settings.data_dir}/sqlite/geodeploy.db"

    def step(msg: str, progress: int) -> None:
        _update_job(db_path, job_id, status="processing", current_step=msg, progress=progress,
                    started_at=datetime.now(timezone.utc).isoformat())

    cog_path = None
    try:
        step("Inspecting raster", 5)
        meta = inspect_raster(file_path)

        if meta["crs"] is None:
            raise ValueError("Raster has no CRS. Please set the CRS before uploading.")

        step("Converting to Cloud-Optimised GeoTIFF", 20)
        with tempfile.NamedTemporaryFile(suffix=".tif", delete=False, dir=os.path.dirname(file_path)) as tmp:
            cog_path = tmp.name

        if is_cog(file_path):
            cog_path = file_path
        else:
            convert_to_cog(file_path, cog_path)

        file_size = os.path.getsize(cog_path)

        step("Uploading to storage", 60)
        creds = _get_storage_creds(db_path)
        s3 = boto3.client(
            "s3",
            endpoint_url=creds["endpoint"],
            aws_access_key_id=creds["access_key"],
            aws_secret_access_key=creds["secret_key"],
            region_name=creds["region"],
            config=Config(signature_version="s3v4"),
        )
        s3.upload_file(
            cog_path,
            creds["bucket"],
            s3_key,
            ExtraArgs={"ContentType": "image/tiff"},
        )

        step("Saving metadata", 90)
        _update_layer(db_path, layer_id,
                      status="ready",
                      crs=meta["crs"],
                      bbox=json.dumps(meta["bbox"]),
                      band_count=meta["band_count"],
                      nodata_value=meta["nodata_value"],
                      file_size=file_size,
                      updated_at=datetime.now(timezone.utc).isoformat())

        _update_job(db_path, job_id, status="ready", progress=100,
                    completed_at=datetime.now(timezone.utc).isoformat())

    except Exception as exc:
        _update_job(db_path, job_id, status="error", error_message=str(exc),
                    completed_at=datetime.now(timezone.utc).isoformat())
        _update_layer(db_path, layer_id, status="error", error_message=str(exc))
        raise
    finally:
        if file_path and os.path.exists(file_path):
            os.unlink(file_path)
        if cog_path and cog_path != file_path and os.path.exists(cog_path):
            os.unlink(cog_path)
