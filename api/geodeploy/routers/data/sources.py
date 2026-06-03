"""External data source connections — WMS/XYZ (raster) and WFS (vector).

These are displayed in portals WITHOUT ingesting: raster tiles are fetched directly by
the browser; WFS features go through the public same-origin GeoJSON proxy below (avoids
CORS). The provider's licence applies — `attribution` is surfaced on the map.
"""
import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...database import get_db
from ...deps import get_current_user
from ...models import ExternalSource, User
from ...schemas import ExternalSourceCreate, ExternalSourceOut
from ...services import external_sources as ext

router = APIRouter(prefix="/data/sources", tags=["sources"])


def _to_out(src: ExternalSource) -> ExternalSourceOut:
    out = ExternalSourceOut.from_orm_json(src)
    out.tile_url = ext.tile_url(src)
    out.data_url = ext.features_url(src)
    return out


@router.get("", response_model=list[ExternalSourceOut])
async def list_sources(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(ExternalSource).where(ExternalSource.user_id == user.id).order_by(ExternalSource.created_at.desc())
    )
    return [_to_out(s) for s in result.scalars().all()]


@router.post("", response_model=ExternalSourceOut, status_code=201)
async def create_source(
    req: ExternalSourceCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    url = (req.url or "").strip()
    if not url.lower().startswith(("http://", "https://")):
        raise HTTPException(400, "URL must start with http:// or https://")
    kind = ext.kind_for(req.source_type)

    if req.source_type in ("wms", "wfs") and not (req.layer_name or "").strip():
        raise HTTPException(400, f"{req.source_type.upper()} requires a layer name.")

    geometry_type = None
    bbox_json = None
    version = req.version

    if req.source_type == "wfs":
        # Probe the WFS to validate it and learn geometry type + extent.
        try:
            info = await ext.probe_wfs(url, req.layer_name.strip(), req.version)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(400, f"Could not connect to WFS: {exc}") from exc
        geometry_type = info["geometry_type"]
        version = info["version"]
        bbox_json = json.dumps(info["bbox"]) if info.get("bbox") else None

    src = ExternalSource(
        user_id=user.id,
        name=req.name.strip() or req.layer_name or req.source_type.upper(),
        source_type=req.source_type,
        kind=kind,
        url=url,
        layer_name=(req.layer_name or "").strip() or None,
        version=version,
        image_format=req.image_format,
        attribution=(req.attribution or "").strip() or None,
        geometry_type=geometry_type,
        bbox=bbox_json,
    )
    db.add(src)
    await db.commit()
    await db.refresh(src)
    return _to_out(src)


@router.delete("/{source_id}", status_code=204)
async def delete_source(source_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    src = (await db.execute(
        select(ExternalSource).where(ExternalSource.id == source_id, ExternalSource.user_id == user.id)
    )).scalar_one_or_none()
    if not src:
        raise HTTPException(404, "Source not found.")
    await db.delete(src)
    await db.commit()


@router.get("/{source_id}/features.geojson")
async def source_features(source_id: int, db: AsyncSession = Depends(get_db)):
    """PUBLIC GeoJSON proxy for a WFS source (published portals are unauthenticated).

    Only proxies a stored, admin-created source URL (no arbitrary URL from the caller),
    so it is not an open SSRF — the caller only supplies the source id.
    """
    src = (await db.execute(select(ExternalSource).where(ExternalSource.id == source_id))).scalar_one_or_none()
    if not src or src.kind != "vector":
        raise HTTPException(404, "Vector source not found.")
    try:
        gj = await ext.fetch_wfs_geojson(src)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"Upstream WFS error: {exc}") from exc
    return JSONResponse(gj)
