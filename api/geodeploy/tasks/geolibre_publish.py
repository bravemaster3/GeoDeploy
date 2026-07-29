"""Celery orchestrator for GeoLibre → GeoDeploy publish.

The `POST /interop/geolibre/publish` endpoint (async) has already: parsed the `.geolibre.json`,
created a `VectorLayer` + `UploadJob` per vector layer (status "processing") with its GeoJSON written
to a temp file, created `ExternalSource` rows for tiles, and created the `Portal` shell with the
translated `layer_configs`. This task finishes the job in the worker:

  1. Run each vector ingest **synchronously in-process** via `ingest_vector.apply(...)` (reusing the
     exact, tested ingest — native-CRS, Z-preserving PostGIS COPY, Martin reload). A layer that fails
     stays `status="error"` and is simply excluded when the bundle is built (it only loads "ready"
     layers), so one bad layer never sinks the whole publish.
  2. Build + publish the portal bundle by reusing the router's async `_rebuild_bundle` under a fresh
     session, then flip `published=True`.

INTEGRATION NOTE: this path needs the running stack (PostGIS/Celery/Martin) to validate end-to-end;
it is wired against confirmed entry points but not yet exercised against a live worker.
"""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
import socket
import sqlite3
import urllib.parse
import urllib.request
import uuid

from ..celery_app import celery_app
from ..config import get_settings
from .raster_ingest import ingest_raster
from .vector_ingest import ingest_vector

logger = logging.getLogger(__name__)

# Cap on a downloaded COG so a runaway/huge URL can't fill the disk.
_MAX_COG_BYTES = 4 * 1024 * 1024 * 1024


@celery_app.task(bind=True, name="geodeploy.tasks.geolibre_publish.publish_geolibre_project")
def publish_geolibre_project(self, portal_id: int, ingest_jobs: list[list],
                             raster_jobs: list[list] | None = None):
    """`ingest_jobs`: [job_id, layer_id, tmp_path, layer_name, schema_name, table_name] per vector.
    `raster_jobs`: [job_id, layer_id, cog_url, s3_key] per COG raster."""
    for job in ingest_jobs:
        try:
            # Synchronous, in-process. A failed ingest marks its own layer/job "error"; we continue so
            # the portal still publishes with whatever ingested cleanly.
            ingest_vector.apply(args=tuple(job))
        except Exception:  # defensive — .apply() usually captures rather than raises
            logger.exception("geolibre publish: vector ingest failed for job %s", job)

    for job_id, layer_id, url, s3_key in (raster_jobs or []):
        tmp_path = None
        try:
            tmp_path = _download_https_cog(url)
            ingest_raster.apply(args=(job_id, layer_id, tmp_path, s3_key))
        except Exception as exc:
            logger.exception("geolibre publish: raster import failed for %s", url)
            _mark_raster_error(layer_id, job_id, str(exc))
            if tmp_path and os.path.exists(tmp_path):
                os.unlink(tmp_path)

    try:
        asyncio.run(_finalize_portal(portal_id))
    except Exception:
        logger.exception("geolibre publish: finalizing portal %s failed", portal_id)
        raise


def _assert_public_https(url: str) -> None:
    """Reject anything but https to a PUBLIC host — a basic SSRF guard so an imported project can't
    point the worker at internal services (metadata endpoints, the DB, localhost, private ranges).
    Resolves the host and rejects if ANY resolved address is private/loopback/link-local/reserved.
    (Residual TOCTOU: urllib re-resolves on connect; acceptable for an authenticated portal:write
    caller. A fully airtight fix pins the connection to the vetted IP.)"""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https":
        raise ValueError("Only https COG URLs are imported.")
    host = parsed.hostname
    if not host:
        raise ValueError("COG URL has no host.")
    try:
        infos = socket.getaddrinfo(host, parsed.port or 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise ValueError(f"Could not resolve COG host {host!r}.") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
                or ip.is_multicast or ip.is_unspecified):
            raise ValueError(f"COG host {host!r} resolves to a non-public address ({ip}); blocked.")


def _download_https_cog(url: str) -> str:
    """Stream an https COG to a temp file (size-capped). Returns the local path (ingest_raster deletes
    it after converting to COG + uploading to MinIO)."""
    _assert_public_https(url)
    settings = get_settings()
    os.makedirs(f"{settings.data_dir}/temp", exist_ok=True)
    dest = f"{settings.data_dir}/temp/{uuid.uuid4()}.tif"
    req = urllib.request.Request(url, headers={"User-Agent": "GeoDeploy"})
    total = 0
    with urllib.request.urlopen(req, timeout=60) as resp, open(dest, "wb") as fh:  # noqa: S310 (https-gated)
        while chunk := resp.read(4 * 1024 * 1024):
            total += len(chunk)
            if total > _MAX_COG_BYTES:
                raise ValueError("COG exceeds the import size limit.")
            fh.write(chunk)
    return dest


def _mark_raster_error(layer_id: int, job_id: str, message: str) -> None:
    """Flag the layer + job as failed (download error) so a failed COG doesn't linger 'processing';
    the failed layer is then excluded from the built bundle (only 'ready' layers load)."""
    db_path = f"{get_settings().data_dir}/sqlite/geodeploy.db"
    try:
        conn = sqlite3.connect(db_path, timeout=30)
        conn.execute("UPDATE raster_layers SET status = 'error', error_message = ? WHERE id = ?",
                     (message, layer_id))
        conn.execute("UPDATE upload_jobs SET status = 'error', error_message = ? WHERE id = ?",
                     (message, job_id))
        conn.commit()
        conn.close()
    except Exception:
        logger.exception("geolibre publish: could not mark raster %s error", layer_id)


async def _finalize_portal(portal_id: int) -> None:
    """Build the static bundle from the portal's persisted config (reusing the router's rebuild) and
    mark it published — the same effect as POST /portals/{id}/publish, driven from the worker."""
    from datetime import datetime, timezone

    from sqlalchemy import select

    from ..database import AsyncSessionLocal
    from ..models import Portal
    from ..routers.portals import _rebuild_bundle

    async with AsyncSessionLocal() as db:
        portal = (await db.execute(select(Portal).where(Portal.id == portal_id))).scalar_one_or_none()
        if portal is None:
            logger.warning("geolibre publish: portal %s vanished before finalize", portal_id)
            return
        await _rebuild_bundle(portal, db)
        portal.published = True
        portal.published_at = datetime.now(timezone.utc)
        await db.commit()
