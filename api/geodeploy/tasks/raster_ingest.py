"""
Raster ingest pipeline: uploaded GeoTIFF → COG conversion → MinIO → TiTiler ready.
"""
import json
import os
import tempfile
from datetime import datetime, timezone

import boto3
from botocore.client import Config

from .. import state_db
from ..celery_app import celery_app
from ..config import get_settings
from ..services.cog_converter import convert_to_cog, inspect as inspect_raster, is_cog


def _get_storage_creds() -> dict:
    """The storage credentials EVERY background task uses. ONE source, shared with the API.

    This used to read `setup_config` FIRST and fall back to the environment. That ordering broke
    every large upload on any instance that had been restored, in a way designed to look like a
    different bug:

      * the browser uploads to a presigned URL minted by the API, which signs with the LIVE
        credentials (`services/minio.storage_settings`) — so the upload succeeds, 100%, no error;
      * the background convert then downloads the object with the creds from `setup_config` — which
        `pg_restore` replaced with the SNAPSHOT'S, i.e. another instance's MinIO keys, or the same
        keys encrypted under another instance's GEODEPLOY_SECRET_KEY;
      * the layer goes straight to `error`. "It uploads, then says error."

    Every task here reached storage this way (`convert_upload`, `csv_import`, `geoparquet_import`,
    `geoparquet_prep`, `export`, `pmtiles_tile`, raster ingest), so a restore silently disabled the
    whole ingest pipeline while the API kept working perfectly.

    The DB-first read had a real reason in 2026-06 (notes §0f): the worker's environment is fixed
    when its container is CREATED, and the setup wizard runs after that, so `.env` alone left the
    worker with install-time credentials. That problem now has a better answer —
    `state_db.runtime_storage()`, a small file in the shared data dir that the API republishes from
    what it just proved works, on every start. `storage_settings()` reads it. So the database copy is
    no longer the freshest source; it is only the one a restore can poison.

    `setup_config` stays as a LAST resort, after the runtime file and the environment, for an
    instance that somehow has neither — never ahead of them.
    """
    from ..services.minio import storage_settings

    endpoint, bucket, access_key, secret_key, region = storage_settings()
    if access_key:
        _log_creds_once("runtime/env", endpoint, bucket, access_key)
        return {"endpoint": endpoint, "bucket": bucket, "access_key": access_key,
                "secret_key": secret_key, "region": region or "us-east-1"}

    with state_db.connect() as conn:
        row = conn.execute(
            "SELECT storage_endpoint, storage_bucket, storage_access_key, storage_secret_key, storage_region "
            "FROM setup_config WHERE id=1"
        ).fetchone()
    if row and row[2]:
        # Raw shim read — SQLAlchemy's EncryptedText does not apply, so decrypt explicitly.
        from ..crypto import decrypt_secret
        _log_creds_once("setup_config (LAST RESORT)", row[0], row[1], row[2])
        return {"endpoint": row[0], "bucket": row[1], "access_key": row[2],
                "secret_key": decrypt_secret(row[3]), "region": row[4] or "us-east-1"}
    settings = get_settings()
    _log_creds_once("settings", settings.storage_endpoint, settings.storage_bucket,
                    settings.storage_access_key)
    return {"endpoint": settings.storage_endpoint, "bucket": settings.storage_bucket,
            "access_key": settings.storage_access_key, "secret_key": settings.storage_secret_key,
            "region": settings.storage_region or "us-east-1"}


_creds_logged = None


def _log_creds_once(source: str, endpoint, bucket, access_key) -> None:
    """Say WHERE the storage credentials came from, once per worker process.

    A rejected key surfaces as a bare `403 Forbidden` from botocore with no hint of which key was
    used or where it was read — so diagnosing it means reading code and guessing. One line in the
    worker log next to the traceback turns that into a glance. Logged once (and again if the answer
    changes) so it cannot become noise in a long-running worker.

    The access key is an identifier, not a secret, but it is still truncated: enough to compare
    against `.env` at a glance, not enough to be worth harvesting from a log someone pastes into an
    issue. The secret is never logged at all.
    """
    global _creds_logged
    import logging
    fingerprint = (source, endpoint, bucket, access_key)
    if fingerprint == _creds_logged:
        return
    _creds_logged = fingerprint
    masked = (access_key[:4] + "…" + access_key[-2:]) if access_key and len(access_key) > 8 else "(none)"
    logging.getLogger(__name__).info(
        "storage credentials from %s — endpoint=%s bucket=%s access_key=%s",
        source, endpoint or "(none)", bucket or "(none)", masked)


def _update_job(job_id: str, **kwargs) -> None:
    with state_db.connect() as conn:
        sets = ", ".join(f"{k} = ?" for k in kwargs)
        values = list(kwargs.values()) + [job_id]
        conn.execute(f"UPDATE upload_jobs SET {sets} WHERE id = ?", values)


def _update_layer(layer_id: int, **kwargs) -> None:
    with state_db.connect() as conn:
        sets = ", ".join(f"{k} = ?" for k in kwargs)
        values = list(kwargs.values()) + [layer_id]
        conn.execute(f"UPDATE raster_layers SET {sets} WHERE id = ?", values)


def _default_rescale(s3_key: str, settings) -> str | None:
    """Best-effort default display stretch (2–98 percentile) from TiTiler stats. Non-8-bit rasters
    (float/int) render BLACK on tile servers that assume 0–255, so we bake a stretch into the layer's
    default_style — which the portal, the About page, AND the STAC 'tiles' URL all read. Returns
    "min,max" or None. The COG is already in storage by the time this runs."""
    import httpx
    cog_url = f"s3://{settings.storage_bucket}/{s3_key}"
    try:
        r = httpx.get(f"{settings.titiler_url}/cog/statistics", params={"url": cog_url}, timeout=30)
        r.raise_for_status()
        stats = r.json()
    except Exception:
        return None
    mins, maxs = [], []
    for s in (stats.values() if isinstance(stats, dict) else []):
        if not isinstance(s, dict):
            continue
        lo = s.get("percentile_2", s.get("min"))
        hi = s.get("percentile_98", s.get("max"))
        if lo is not None:
            mins.append(lo)
        if hi is not None:
            maxs.append(hi)
    if mins and maxs and max(maxs) > min(mins):
        return f"{round(min(mins), 4)},{round(max(maxs), 4)}"
    return None


@celery_app.task(bind=True, name="geodeploy.tasks.raster_ingest.ingest_raster")
def ingest_raster(self, job_id: str, layer_id: int, file_path: str, s3_key: str):
    settings = get_settings()

    def step(msg: str, progress: int) -> None:
        _update_job(job_id, status="processing", current_step=msg, progress=progress,
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
        creds = _get_storage_creds()
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
        # Bake a default display stretch for non-8-bit rasters so they don't render black by default
        # (portal / About page / STAC tiles URL all read default_style.rescale). 8-bit maps directly.
        extra = {}
        if (meta.get("dtype") or "").lower() not in ("uint8", "int8", "", "none"):
            rescale = _default_rescale(s3_key, settings)
            if rescale:
                extra["default_style"] = json.dumps({"rescale": rescale})
        _update_layer(layer_id,
                      status="ready",
                      crs=meta["crs"],
                      bbox=json.dumps(meta["bbox"]),
                      band_count=meta["band_count"],
                      nodata_value=meta["nodata_value"],
                      file_size=file_size,
                      updated_at=datetime.now(timezone.utc).isoformat(),
                      **extra)

        _update_job(job_id, status="ready", progress=100,
                    completed_at=datetime.now(timezone.utc).isoformat())

    except Exception as exc:
        _update_job(job_id, status="error", error_message=str(exc),
                    completed_at=datetime.now(timezone.utc).isoformat())
        _update_layer(layer_id, status="error", error_message=str(exc))
        raise
    finally:
        if file_path and os.path.exists(file_path):
            os.unlink(file_path)
        if cog_path and cog_path != file_path and os.path.exists(cog_path):
            os.unlink(cog_path)


@celery_app.task(bind=True, name="geodeploy.tasks.raster_ingest.ingest_raster_from_storage")
def ingest_raster_from_storage(self, job_id: str, layer_id: int, source_key: str, dest_key: str):
    """Ingest a raster the BROWSER uploaded straight to object storage.

    Why this exists: a GeoTIFF over ~100 MB cannot be POSTed through the API at all when a CDN sits
    in front of the instance (Cloudflare's free tier cuts request bodies at 100 MB), so large
    rasters upload direct-to-storage in presigned parts, exactly like large vectors. By that point
    the bytes are in the bucket and the normal pipeline just needs a local file.

    So: download once, then hand off to `ingest_raster` UNCHANGED — it inspects, converts to COG,
    uploads to `dest_key` and writes the metadata. Reusing it rather than duplicating that logic is
    the point; COG conversion and the default-stretch heuristic have too much history to fork.
    `.apply()` runs it in-process (not a second queued task) so progress lands on the same job row.
    """
    import os
    import tempfile

    settings = get_settings()
    creds = _get_storage_creds()
    tmp_path = None
    try:
        _update_job(job_id, status="processing", current_step="Fetching uploaded file", progress=3,
                    started_at=datetime.now(timezone.utc).isoformat())
        os.makedirs(f"{settings.data_dir}/temp", exist_ok=True)
        ext = os.path.splitext(source_key)[1] or ".tif"
        fd, tmp_path = tempfile.mkstemp(suffix=ext, dir=f"{settings.data_dir}/temp")
        os.close(fd)

        s3 = boto3.client(
            "s3", endpoint_url=creds["endpoint"], aws_access_key_id=creds["access_key"],
            aws_secret_access_key=creds["secret_key"], region_name=creds["region"],
            config=Config(signature_version="s3v4"))
        s3.download_file(creds["bucket"], source_key, tmp_path)

        # ingest_raster deletes tmp_path itself when it finishes.
        ingest_raster.apply(args=(job_id, layer_id, tmp_path, dest_key))
        tmp_path = None

        # The raw upload is superseded by the COG at dest_key. Only remove it if they differ —
        # a raster already COG-shaped can legitimately be converted in place to the same key.
        if source_key != dest_key:
            try:
                s3.delete_object(Bucket=creds["bucket"], Key=source_key)
            except Exception:
                pass      # a leftover raw object costs space, not correctness
    except Exception as exc:
        _update_job(job_id, status="error", error_message=str(exc),
                    completed_at=datetime.now(timezone.utc).isoformat())
        _update_layer(layer_id, status="error", error_message=str(exc))
        raise
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)
