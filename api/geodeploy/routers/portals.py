import json
import os
import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from slugify import slugify

from ..config import get_settings
from ..database import get_db
from ..deps import get_current_user
from ..models import ExternalSource, Portal, RasterLayer, User, VectorLayer
from ..schemas import PortalCreate, PortalOut, PortalUpdate
from ..services.portal_generator import build_portal_bundle, generate_style

router = APIRouter(prefix="/portals", tags=["portals"])


@router.get("", response_model=list[PortalOut])
async def list_portals(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Portal).where(Portal.user_id == user.id).order_by(Portal.created_at.desc()))
    return [PortalOut.from_orm_json(p) for p in result.scalars().all()]


@router.post("", response_model=PortalOut, status_code=201)
async def create_portal(req: PortalCreate, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    base_slug = slugify(req.title, separator="-")
    slug = base_slug
    suffix = 0
    while (await db.execute(select(Portal).where(Portal.slug == slug))).scalar_one_or_none():
        suffix += 1
        slug = f"{base_slug}-{suffix}"

    portal = Portal(
        user_id=user.id,
        title=req.title,
        slug=slug,
        description=req.description,
        template_id=req.template_id,
        layer_configs=json.dumps([lc.model_dump() for lc in req.layer_configs]),
        access_type=req.access_type,
    )
    if req.access_password:
        from hashlib import sha256
        from passlib.context import CryptContext
        portal.access_password_hash = CryptContext(schemes=["bcrypt"], deprecated="auto").hash(req.access_password)
        portal.access_password_sha256 = sha256(req.access_password.encode()).hexdigest()

    db.add(portal)
    await db.commit()
    await db.refresh(portal)
    return PortalOut.from_orm_json(portal)


@router.get("/{portal_id}", response_model=PortalOut)
async def get_portal(portal_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    portal = await _get_owned(portal_id, user.id, db)
    return PortalOut.from_orm_json(portal)


@router.put("/{portal_id}", response_model=PortalOut)
async def update_portal(portal_id: int, req: PortalUpdate, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    portal = await _get_owned(portal_id, user.id, db)
    if req.title is not None:
        portal.title = req.title
    if req.description is not None:
        portal.description = req.description
    if req.template_id is not None:
        portal.template_id = req.template_id
    if req.layer_configs is not None:
        portal.layer_configs = json.dumps([lc.model_dump() for lc in req.layer_configs])
    if req.initial_view is not None:
        portal.initial_view = json.dumps(req.initial_view)
    if req.access_type is not None:
        portal.access_type = req.access_type
    if req.access_password is not None:
        from hashlib import sha256
        from passlib.context import CryptContext
        portal.access_password_hash = CryptContext(schemes=["bcrypt"], deprecated="auto").hash(req.access_password)
        portal.access_password_sha256 = sha256(req.access_password.encode()).hexdigest()

    await db.commit()
    await db.refresh(portal)
    return PortalOut.from_orm_json(portal)


@router.post("/{portal_id}/publish", response_model=PortalOut)
async def publish_portal(portal_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    portal = await _get_owned(portal_id, user.id, db)
    layer_configs = json.loads(portal.layer_configs or "[]")

    vector_ids = [cfg["layer_id"] for cfg in layer_configs if cfg.get("layer_type") == "vector"]
    raster_ids = [cfg["layer_id"] for cfg in layer_configs if cfg.get("layer_type") == "raster"]
    external_ids = [cfg["layer_id"] for cfg in layer_configs if cfg.get("layer_type") == "external"]

    vector_layers = []
    if vector_ids:
        r = await db.execute(select(VectorLayer).where(VectorLayer.id.in_(vector_ids), VectorLayer.status == "ready"))
        vector_layers = r.scalars().all()

    raster_layers = []
    if raster_ids:
        r = await db.execute(select(RasterLayer).where(RasterLayer.id.in_(raster_ids), RasterLayer.status == "ready"))
        raster_layers = r.scalars().all()

    external_sources = []
    if external_ids:
        r = await db.execute(select(ExternalSource).where(ExternalSource.id.in_(external_ids)))
        external_sources = r.scalars().all()

    user_data = generate_style(layer_configs, vector_layers, raster_layers, external_sources)
    initial_view = json.loads(portal.initial_view) if portal.initial_view else None
    build_portal_bundle(
        portal.slug, portal.title, user_data, portal.template_id, layer_configs,
        access_type=portal.access_type,
        password_sha256=portal.access_password_sha256,
        initial_view=initial_view,
    )

    portal.published = True
    portal.published_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(portal)
    return PortalOut.from_orm_json(portal)


@router.post("/{portal_id}/unpublish", response_model=PortalOut)
async def unpublish_portal(portal_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    import shutil, os
    settings = get_settings()
    portal = await _get_owned(portal_id, user.id, db)
    portal_dir = f"{settings.data_dir}/portals/{portal.slug}"
    if os.path.exists(portal_dir):
        shutil.rmtree(portal_dir)
    portal.published = False
    portal.published_at = None
    await db.commit()
    await db.refresh(portal)
    return PortalOut.from_orm_json(portal)


@router.delete("/{portal_id}", status_code=204)
async def delete_portal(portal_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    import shutil, os
    settings = get_settings()
    portal = await _get_owned(portal_id, user.id, db)
    portal_dir = f"{settings.data_dir}/portals/{portal.slug}"
    if os.path.exists(portal_dir):
        shutil.rmtree(portal_dir)
    await db.delete(portal)
    await db.commit()


# ── Area-select export (offloaded to Celery) ─────────────────────────────────

class ExportItem(BaseModel):
    layer_id: int
    layer_type: str          # vector | raster
    format: str = "geojson"  # geojson | gpkg | csv | tif


class ExportBundleRequest(BaseModel):
    bbox: str                # "minx,miny,maxx,maxy" in EPSG:4326
    items: list[ExportItem]


def _sweep_old_exports(settings, max_age: int = 3600) -> None:
    import time
    d = f"{settings.data_dir}/temp/exports"
    if not os.path.isdir(d):
        return
    now = time.time()
    for f in os.listdir(d):
        fp = os.path.join(d, f)
        try:
            if now - os.path.getmtime(fp) > max_age:
                os.unlink(fp)
        except Exception:
            pass


@router.post("/{slug}/export-bundle", status_code=202)
async def start_export_bundle(slug: str, req: ExportBundleRequest, db: AsyncSession = Depends(get_db)):
    """Validate + enqueue a clip job; returns a job_id to poll. Heavy work runs in Celery."""
    from ..tasks.export import export_bundle as export_task
    portal = (await db.execute(select(Portal).where(Portal.slug == slug))).scalar_one_or_none()
    if not portal:
        raise HTTPException(404, "Portal not found.")
    configs = json.loads(portal.layer_configs or "[]")
    try:
        parts = [float(v) for v in req.bbox.split(",")]
        assert len(parts) == 4
    except Exception:
        raise HTTPException(400, "bbox must be 'minx,miny,maxx,maxy'.")

    resolved: list[dict] = []
    for it in req.items:
        if not any(c.get("layer_id") == it.layer_id and c.get("layer_type") == it.layer_type for c in configs):
            continue
        if it.layer_type == "vector":
            layer = (await db.execute(select(VectorLayer).where(
                VectorLayer.id == it.layer_id, VectorLayer.status == "ready"))).scalar_one_or_none()
            if layer:
                resolved.append({"type": "vector", "schema": layer.schema_name,
                                 "table": layer.table_name, "name": layer.name, "format": it.format})
        else:
            layer = (await db.execute(select(RasterLayer).where(
                RasterLayer.id == it.layer_id, RasterLayer.status == "ready"))).scalar_one_or_none()
            if layer:
                resolved.append({"type": "raster", "s3_key": layer.s3_key, "name": layer.name})
    if not resolved:
        raise HTTPException(400, "No exportable layers in the request.")

    _sweep_old_exports(get_settings())
    task = export_task.delay(req.bbox, resolved)
    return {"job_id": task.id}


@router.get("/{slug}/export-status/{job_id}")
async def export_status(slug: str, job_id: str):
    from ..celery_app import celery_app
    settings = get_settings()
    if os.path.exists(f"{settings.data_dir}/temp/exports/{job_id}.zip"):
        return {"status": "ready"}
    state = celery_app.AsyncResult(job_id).state
    if state == "FAILURE":
        return {"status": "error"}
    if state == "STARTED":
        return {"status": "processing"}
    return {"status": "queued"}


@router.get("/{slug}/export-download/{job_id}")
async def export_download(slug: str, job_id: str):
    import re
    from fastapi.responses import FileResponse
    if not re.fullmatch(r"[A-Za-z0-9_-]+", job_id):
        raise HTTPException(400, "Invalid job id.")
    settings = get_settings()
    path = f"{settings.data_dir}/temp/exports/{job_id}.zip"
    if not os.path.exists(path):
        raise HTTPException(404, "Export not ready or expired.")
    return FileResponse(path, media_type="application/zip", filename="selection.zip")


async def _get_owned(portal_id: int, user_id: int, db: AsyncSession) -> Portal:
    result = await db.execute(select(Portal).where(Portal.id == portal_id, Portal.user_id == user_id))
    portal = result.scalar_one_or_none()
    if not portal:
        raise HTTPException(404, "Portal not found.")
    return portal
