import json
import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from slugify import slugify

from ..config import get_settings
from ..database import get_db
from ..deps import get_current_user
from ..models import Portal, RasterLayer, User, VectorLayer
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

    vector_layers = []
    if vector_ids:
        r = await db.execute(select(VectorLayer).where(VectorLayer.id.in_(vector_ids), VectorLayer.status == "ready"))
        vector_layers = r.scalars().all()

    raster_layers = []
    if raster_ids:
        r = await db.execute(select(RasterLayer).where(RasterLayer.id.in_(raster_ids), RasterLayer.status == "ready"))
        raster_layers = r.scalars().all()

    user_data = generate_style(layer_configs, vector_layers, raster_layers)
    build_portal_bundle(
        portal.slug, portal.title, user_data, portal.template_id, layer_configs,
        access_type=portal.access_type,
        password_sha256=portal.access_password_sha256,
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


@router.get("/{slug}/export")
async def export_portal_layer(
    slug: str,
    layer_id: int,
    bbox: str,
    format: str = "geojson",
    db: AsyncSession = Depends(get_db),
):
    """
    Public: download a portal's vector layer clipped to a bbox, as GeoJSON or CSV.
    Only layers that belong to the portal can be exported; capped at 50k features.
    (Portal vector tiles are already public, so this does not expose new data.)
    """
    portal = (await db.execute(select(Portal).where(Portal.slug == slug))).scalar_one_or_none()
    if not portal:
        raise HTTPException(404, "Portal not found.")
    configs = json.loads(portal.layer_configs or "[]")
    if not any(c.get("layer_id") == layer_id and c.get("layer_type") == "vector" for c in configs):
        raise HTTPException(404, "Layer is not part of this portal.")
    layer = (await db.execute(
        select(VectorLayer).where(VectorLayer.id == layer_id, VectorLayer.status == "ready")
    )).scalar_one_or_none()
    if not layer:
        raise HTTPException(404, "Layer not available.")
    if format not in ("geojson", "csv"):
        raise HTTPException(400, "format must be 'geojson' or 'csv'.")
    try:
        minx, miny, maxx, maxy = (float(v) for v in bbox.split(","))
    except Exception:
        raise HTTPException(400, "bbox must be 'minx,miny,maxx,maxy'.")

    import asyncpg
    settings = get_settings()
    schema, table = layer.schema_name, layer.table_name
    env = "ST_MakeEnvelope($1,$2,$3,$4,4326)"
    cap = 50000
    conn = await asyncpg.connect(settings.postgis_sync_dsn, timeout=20)
    try:
        if format == "geojson":
            sql = (
                "SELECT jsonb_build_object('type','FeatureCollection','features',"
                "COALESCE(jsonb_agg(f.feat), '[]'::jsonb))::text FROM ("
                "  SELECT jsonb_build_object('type','Feature',"
                "    'geometry', ST_AsGeoJSON(geom)::jsonb,"
                "    'properties', to_jsonb(t) - 'geom') AS feat"
                f'  FROM "{schema}"."{table}" t'
                f"  WHERE geom && {env} AND ST_Intersects(geom, {env})"
                f"  LIMIT {cap}"
                ") f"
            )
            data = await conn.fetchval(sql, minx, miny, maxx, maxy)
            return Response(
                content=data or '{"type":"FeatureCollection","features":[]}',
                media_type="application/geo+json",
                headers={"Content-Disposition": f'attachment; filename="{table}.geojson"'},
            )

        # CSV — attributes + a geometry_wkt column
        sql = (
            "SELECT (to_jsonb(t) - 'geom')::text AS props, ST_AsText(geom) AS wkt "
            f'FROM "{schema}"."{table}" t '
            f"WHERE geom && {env} AND ST_Intersects(geom, {env}) LIMIT {cap}"
        )
        rows = await conn.fetch(sql, minx, miny, maxx, maxy)
        import csv
        import io
        cols: list[str] = []
        recs: list[dict] = []
        for r in rows:
            props = json.loads(r["props"])
            props["geometry_wkt"] = r["wkt"]
            recs.append(props)
            for k in props:
                if k not in cols:
                    cols.append(k)
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=cols)
        writer.writeheader()
        for rec in recs:
            writer.writerow(rec)
        return Response(
            content=buf.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{table}.csv"'},
        )
    finally:
        await conn.close()


@router.get("/{slug}/export-raster")
async def export_portal_raster(slug: str, layer_id: int, bbox: str, db: AsyncSession = Depends(get_db)):
    """Public: download a portal raster clipped to a bbox as a GeoTIFF (native resolution)."""
    portal = (await db.execute(select(Portal).where(Portal.slug == slug))).scalar_one_or_none()
    if not portal:
        raise HTTPException(404, "Portal not found.")
    configs = json.loads(portal.layer_configs or "[]")
    if not any(c.get("layer_id") == layer_id and c.get("layer_type") == "raster" for c in configs):
        raise HTTPException(404, "Layer is not part of this portal.")
    layer = (await db.execute(
        select(RasterLayer).where(RasterLayer.id == layer_id, RasterLayer.status == "ready")
    )).scalar_one_or_none()
    if not layer:
        raise HTTPException(404, "Layer not available.")
    try:
        minx, miny, maxx, maxy = (float(v) for v in bbox.split(","))
    except Exception:
        raise HTTPException(400, "bbox must be 'minx,miny,maxx,maxy'.")

    settings = get_settings()

    def _clip() -> bytes:
        import io
        import rasterio
        from rasterio.windows import Window, from_bounds
        from rasterio.warp import transform_bounds
        env = {
            "AWS_ACCESS_KEY_ID": settings.storage_access_key,
            "AWS_SECRET_ACCESS_KEY": settings.storage_secret_key,
            "AWS_S3_ENDPOINT": settings.storage_endpoint.replace("https://", "").replace("http://", ""),
            "AWS_HTTPS": "NO",
            "AWS_VIRTUAL_HOSTING": "FALSE",
            "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
        }
        with rasterio.Env(**env):
            with rasterio.open(f"s3://{settings.storage_bucket}/{layer.s3_key}") as src:
                west, south, east, north = transform_bounds("EPSG:4326", src.crs, minx, miny, maxx, maxy, densify_pts=21)
                win = from_bounds(west, south, east, north, src.transform)
                win = win.round_offsets().round_lengths()
                win = win.intersection(Window(0, 0, src.width, src.height))
                if win.width < 1 or win.height < 1:
                    raise ValueError("no-overlap")
                data = src.read(window=win)
                profile = src.profile.copy()
                profile.update(
                    driver="GTiff", height=int(win.height), width=int(win.width),
                    transform=src.window_transform(win), compress="lzw",
                )
                buf = io.BytesIO()
                with rasterio.open(buf, "w", **profile) as dst:
                    dst.write(data)
                return buf.getvalue()

    import asyncio
    try:
        content = await asyncio.get_event_loop().run_in_executor(None, _clip)
    except ValueError:
        raise HTTPException(404, "The selected area does not overlap this raster.")
    except Exception as exc:
        raise HTTPException(500, f"Could not clip raster: {exc}") from exc

    from slugify import slugify as _slug
    name = _slug(layer.name, separator="_") or "raster"
    return Response(
        content=content,
        media_type="image/tiff",
        headers={"Content-Disposition": f'attachment; filename="{name}_clip.tif"'},
    )


async def _get_owned(portal_id: int, user_id: int, db: AsyncSession) -> Portal:
    result = await db.execute(select(Portal).where(Portal.id == portal_id, Portal.user_id == user_id))
    portal = result.scalar_one_or_none()
    if not portal:
        raise HTTPException(404, "Portal not found.")
    return portal
