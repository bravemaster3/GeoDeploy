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
import logging

from ..celery_app import celery_app
from .vector_ingest import ingest_vector

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, name="geodeploy.tasks.geolibre_publish.publish_geolibre_project")
def publish_geolibre_project(self, portal_id: int, ingest_jobs: list[list]):
    """`ingest_jobs`: list of [job_id, layer_id, tmp_path, layer_name, schema_name, table_name]."""
    for job in ingest_jobs:
        try:
            # Synchronous, in-process. A failed ingest marks its own layer/job "error"; we continue so
            # the portal still publishes with whatever ingested cleanly.
            ingest_vector.apply(args=tuple(job))
        except Exception:  # defensive — .apply() usually captures rather than raises
            logger.exception("geolibre publish: vector ingest failed for job %s", job)

    try:
        asyncio.run(_finalize_portal(portal_id))
    except Exception:
        logger.exception("geolibre publish: finalizing portal %s failed", portal_id)
        raise


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
