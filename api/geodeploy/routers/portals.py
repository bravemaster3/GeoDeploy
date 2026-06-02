import json
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


# ── Area-select export ───────────────────────────────────────────────────────

class ExportItem(BaseModel):
    layer_id: int
    layer_type: str        # vector | raster
    format: str            # geojson | gpkg | csv | tif


class ExportBundleRequest(BaseModel):
    bbox: str              # "minx,miny,maxx,maxy" in EPSG:4326
    items: list[ExportItem]


_EXPORT_CAP = 50000
_ENV = "ST_MakeEnvelope($1,$2,$3,$4,4326)"


def _safe_name(name: str) -> str:
    return slugify(name or "layer", separator="_") or "layer"


async def _vec_geojson(conn, schema: str, table: str, b) -> str:
    sql = (
        "SELECT jsonb_build_object('type','FeatureCollection','features',"
        "COALESCE(jsonb_agg(f.feat), '[]'::jsonb))::text FROM ("
        "  SELECT jsonb_build_object('type','Feature',"
        "    'geometry', ST_AsGeoJSON(geom)::jsonb,"
        "    'properties', to_jsonb(t) - 'geom') AS feat"
        f'  FROM "{schema}"."{table}" t'
        f"  WHERE geom && {_ENV} AND ST_Intersects(geom, {_ENV})"
        f"  LIMIT {_EXPORT_CAP}"
        ") f"
    )
    data = await conn.fetchval(sql, *b)
    return data or '{"type":"FeatureCollection","features":[]}'


async def _vec_csv(conn, schema: str, table: str, b) -> str:
    import csv
    import io
    sql = (
        "SELECT (to_jsonb(t) - 'geom')::text AS props, ST_AsText(geom) AS wkt "
        f'FROM "{schema}"."{table}" t '
        f"WHERE geom && {_ENV} AND ST_Intersects(geom, {_ENV}) LIMIT {_EXPORT_CAP}"
    )
    rows = await conn.fetch(sql, *b)
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
    w = csv.DictWriter(buf, fieldnames=cols)
    w.writeheader()
    for rec in recs:
        w.writerow(rec)
    return buf.getvalue()


async def _gj_to_gpkg(geojson_text: str, layer_name: str) -> bytes:
    """GeoJSON FeatureCollection -> GeoPackage bytes via ogr2ogr (core GeoJSON reader)."""
    import asyncio
    import os
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        src_path = os.path.join(td, "in.geojson")
        out_path = os.path.join(td, "out.gpkg")
        with open(src_path, "w", encoding="utf-8") as f:
            f.write(geojson_text)
        proc = await asyncio.create_subprocess_exec(
            "ogr2ogr", "-f", "GPKG", "-nln", layer_name, out_path, src_path,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        _, err = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError((err or b"").decode("utf-8", "ignore")[:300])
        with open(out_path, "rb") as f:
            return f.read()


def _clip_raster(s3_key: str, b, settings) -> bytes:
    import io
    import rasterio
    from rasterio.windows import Window, from_bounds
    from rasterio.warp import transform_bounds
    minx, miny, maxx, maxy = b
    env = {
        "AWS_ACCESS_KEY_ID": settings.storage_access_key,
        "AWS_SECRET_ACCESS_KEY": settings.storage_secret_key,
        "AWS_S3_ENDPOINT": settings.storage_endpoint.replace("https://", "").replace("http://", ""),
        "AWS_HTTPS": "NO", "AWS_VIRTUAL_HOSTING": "FALSE",
        "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
    }
    with rasterio.Env(**env):
        with rasterio.open(f"s3://{settings.storage_bucket}/{s3_key}") as ds:
            west, south, east, north = transform_bounds("EPSG:4326", ds.crs, minx, miny, maxx, maxy, densify_pts=21)
            win = from_bounds(west, south, east, north, ds.transform).round_offsets().round_lengths()
            win = win.intersection(Window(0, 0, ds.width, ds.height))
            if win.width < 1 or win.height < 1:
                raise ValueError("no-overlap")
            data = ds.read(window=win)
            profile = ds.profile.copy()
            profile.update(driver="GTiff", height=int(win.height), width=int(win.width),
                           transform=ds.window_transform(win), compress="lzw")
            buf = io.BytesIO()
            with rasterio.open(buf, "w", **profile) as out:
                out.write(data)
            return buf.getvalue()


@router.post("/{slug}/export-bundle")
async def export_bundle(slug: str, req: ExportBundleRequest, db: AsyncSession = Depends(get_db)):
    """
    Public: download several portal layers clipped to a bbox as a single ZIP.
    Only layers that belong to the portal are included; 50k-feature cap per vector layer.
    """
    portal = (await db.execute(select(Portal).where(Portal.slug == slug))).scalar_one_or_none()
    if not portal:
        raise HTTPException(404, "Portal not found.")
    configs = json.loads(portal.layer_configs or "[]")
    try:
        b = tuple(float(v) for v in req.bbox.split(","))
        assert len(b) == 4
    except Exception:
        raise HTTPException(400, "bbox must be 'minx,miny,maxx,maxy'.")

    import asyncio
    import io
    import zipfile
    import asyncpg
    settings = get_settings()
    used: set[str] = set()

    def _fn(base: str, ext: str) -> str:
        fn = f"{base}.{ext}"
        i = 1
        while fn in used:
            fn = f"{base}_{i}.{ext}"
            i += 1
        used.add(fn)
        return fn

    zbuf = io.BytesIO()
    conn = await asyncpg.connect(settings.postgis_sync_dsn, timeout=30)
    try:
        with zipfile.ZipFile(zbuf, "w", zipfile.ZIP_DEFLATED) as z:
            for it in req.items:
                if not any(c.get("layer_id") == it.layer_id and c.get("layer_type") == it.layer_type for c in configs):
                    continue
                if it.layer_type == "vector":
                    layer = (await db.execute(select(VectorLayer).where(
                        VectorLayer.id == it.layer_id, VectorLayer.status == "ready"))).scalar_one_or_none()
                    if not layer:
                        continue
                    base = _safe_name(layer.name)
                    if it.format == "csv":
                        z.writestr(_fn(base, "csv"), await _vec_csv(conn, layer.schema_name, layer.table_name, b))
                    elif it.format == "gpkg":
                        gj = await _vec_geojson(conn, layer.schema_name, layer.table_name, b)
                        try:
                            z.writestr(_fn(base, "gpkg"), await _gj_to_gpkg(gj, base))
                        except Exception:
                            z.writestr(_fn(base, "geojson"), gj)  # fall back if ogr2ogr is unavailable
                    else:  # geojson (default)
                        z.writestr(_fn(base, "geojson"), await _vec_geojson(conn, layer.schema_name, layer.table_name, b))
                else:  # raster
                    layer = (await db.execute(select(RasterLayer).where(
                        RasterLayer.id == it.layer_id, RasterLayer.status == "ready"))).scalar_one_or_none()
                    if not layer:
                        continue
                    try:
                        data = await asyncio.get_event_loop().run_in_executor(
                            None, _clip_raster, layer.s3_key, b, settings)
                    except ValueError:
                        continue  # no overlap — skip silently
                    z.writestr(_fn(_safe_name(layer.name) + "_clip", "tif"), data)
    finally:
        await conn.close()

    return Response(
        content=zbuf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="selection.zip"'},
    )


async def _get_owned(portal_id: int, user_id: int, db: AsyncSession) -> Portal:
    result = await db.execute(select(Portal).where(Portal.id == portal_id, Portal.user_id == user_id))
    portal = result.scalar_one_or_none()
    if not portal:
        raise HTTPException(404, "Portal not found.")
    return portal
