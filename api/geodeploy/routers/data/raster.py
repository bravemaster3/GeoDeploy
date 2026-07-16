import os
import uuid
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import get_settings
from ...database import get_db
from ...deps import get_current_user, require_editor
from ...models import RasterLayer, UploadJob, User
from ...schemas import JobStatus, RasterDefaultStyle, RasterLayerOut, SharingUpdate
from ...services.titiler import get_tile_url as raster_tile_url, COLORMAPS
from ...tasks.raster_ingest import ingest_raster
from ..common import creator_names, visible_to

router = APIRouter(prefix="/data/raster", tags=["raster"])

ALLOWED_EXTENSIONS = {".tif", ".tiff"}
MAX_FILE_SIZE = 10 * 1024 * 1024 * 1024  # 10 GB


@router.get("/colormaps")
async def list_colormaps():
    return COLORMAPS


@router.get("", response_model=list[RasterLayerOut])
async def list_layers(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    import json
    result = await db.execute(
        select(RasterLayer).where(visible_to(user)).order_by(RasterLayer.created_at.desc())
    )
    layers = result.scalars().all()
    names = await creator_names(db, layers)
    out = []
    for l in layers:
        obj = RasterLayerOut.from_orm_json(l)
        obj.created_by = names.get(l.user_id)
        if l.status == "ready":
            ds = json.loads(l.default_style) if l.default_style else {}
            obj.tile_url = raster_tile_url(
                l.s3_key,
                colormap=ds.get("colormap"),
                rescale=ds.get("rescale"),
                algorithm=ds.get("algorithm"),
                zfactor=ds.get("zfactor"),
                bidx=ds.get("bidx"),
            )
        out.append(obj)
    return out


@router.get("/{layer_id}/stats")
async def raster_stats(layer_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Suggested stretch (min,max) from TiTiler band statistics (2nd–98th percentile)."""
    result = await db.execute(select(RasterLayer).where(RasterLayer.id == layer_id))
    layer = result.scalar_one_or_none()
    if not layer:
        raise HTTPException(404, "Layer not found.")
    if layer.status != "ready":
        raise HTTPException(409, "Layer is not ready yet.")

    import httpx
    settings = get_settings()
    cog_url = f"s3://{settings.storage_bucket}/{layer.s3_key}"
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(f"{settings.titiler_url}/cog/statistics", params={"url": cog_url})
            r.raise_for_status()
            stats = r.json()
    except Exception as exc:
        raise HTTPException(502, f"Could not read raster statistics: {exc}") from exc

    mins, maxs = [], []
    for s in stats.values():
        if not isinstance(s, dict):
            continue
        lo = s.get("percentile_2", s.get("min"))
        hi = s.get("percentile_98", s.get("max"))
        if lo is not None:
            mins.append(lo)
        if hi is not None:
            maxs.append(hi)
    if not mins or not maxs:
        raise HTTPException(422, "No usable statistics returned.")
    return {"rescale": f"{round(min(mins), 4)},{round(max(maxs), 4)}"}


@router.post("/upload", response_model=JobStatus, status_code=202)
async def upload_raster(
    file: UploadFile = File(...),
    user: User = Depends(require_editor),
    db: AsyncSession = Depends(get_db),
):
    settings = get_settings()
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(400, f"Unsupported type: {ext}. Upload GeoTIFF (.tif/.tiff).")

    os.makedirs(f"{settings.data_dir}/temp", exist_ok=True)
    tmp_path = f"{settings.data_dir}/temp/{uuid.uuid4()}{ext}"

    size = 0
    with open(tmp_path, "wb") as f:
        while chunk := await file.read(4 * 1024 * 1024):
            size += len(chunk)
            if size > MAX_FILE_SIZE:
                os.unlink(tmp_path)
                raise HTTPException(413, "File exceeds 10 GB limit.")
            f.write(chunk)

    base_name = os.path.splitext(file.filename or "raster")[0]
    s3_key = f"rasters/{user.id}/{uuid.uuid4().hex}/{base_name}.tif"

    layer = RasterLayer(
        user_id=user.id,
        name=base_name,
        s3_key=s3_key,
        file_size=size,
        status="processing",
    )
    db.add(layer)
    await db.flush()

    job_id = str(uuid.uuid4())
    job = UploadJob(id=job_id, layer_id=layer.id, layer_type="raster")
    db.add(job)
    await db.commit()
    await db.refresh(layer)

    ingest_raster.delay(job_id, layer.id, tmp_path, s3_key)

    return JobStatus(
        id=job_id, layer_id=layer.id, layer_type="raster",
        status="queued", progress=0, current_step="Queued", error_message=None,
    )


@router.get("/jobs/{job_id}", response_model=JobStatus)
async def job_status(job_id: str, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    result = await db.execute(select(UploadJob).where(UploadJob.id == job_id))
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(404, "Job not found.")
    return job


@router.put("/{layer_id}/sharing", response_model=RasterLayerOut)
async def save_sharing(
    layer_id: int,
    body: SharingUpdate,
    user: User = Depends(require_editor),
    db: AsyncSession = Depends(get_db),
):
    """Data-sharing settings: opt the layer into the public STAC catalog (`/api/stac`) and the
    public raw-COG route, plus its catalog metadata (abstract/keywords/license/attribution)."""
    result = await db.execute(select(RasterLayer).where(RasterLayer.id == layer_id))
    layer = result.scalar_one_or_none()
    if not layer:
        raise HTTPException(404, "Layer not found.")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(layer, field, value)
    await db.commit()
    await db.refresh(layer)
    return RasterLayerOut.from_orm_json(layer)


@router.get("/{layer_id}/cog")
async def raster_cog(layer_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    """PUBLIC range proxy for the layer's Cloud-Optimized GeoTIFF — ONLY when the admin shared
    the layer (`is_public`). This is what makes `/vsicurl/https://host/api/data/raster/{id}/cog`
    work in QGIS/GDAL (full pixel access, the modern WCS — notes §0h) and gives a direct
    download URL. Same pmtiles/parquet proxy pattern: Range → 206, creds stay server-side."""
    result = await db.execute(select(RasterLayer).where(RasterLayer.id == layer_id))
    layer = result.scalar_one_or_none()
    if not layer or layer.status != "ready" or not layer.is_public or not layer.s3_key:
        raise HTTPException(404, "No shared raster for this layer.")

    from starlette.concurrency import run_in_threadpool
    from fastapi.responses import StreamingResponse
    from ...services.minio import get_s3_client
    settings = get_settings()
    s3 = get_s3_client()
    params = {"Bucket": settings.storage_bucket, "Key": layer.s3_key}
    rng = request.headers.get("range")
    if rng:
        params["Range"] = rng
    try:
        obj = await run_in_threadpool(lambda: s3.get_object(**params))
    except Exception:
        raise HTTPException(404, "Object not found.")
    headers = {"Accept-Ranges": "bytes", "Cache-Control": "public, max-age=3600",
               "Content-Disposition": f'inline; filename="{layer.name}.tif"'}
    status = 200
    if obj.get("ContentRange"):
        headers["Content-Range"] = obj["ContentRange"]
        status = 206
    if obj.get("ContentLength") is not None:
        headers["Content-Length"] = str(obj["ContentLength"])
    return StreamingResponse(obj["Body"].iter_chunks(256 * 1024), status_code=status,
                             media_type="image/tiff", headers=headers)


@router.put("/{layer_id}/default-style", response_model=RasterLayerOut)
async def save_default_style(
    layer_id: int,
    body: RasterDefaultStyle,
    user: User = Depends(require_editor),
    db: AsyncSession = Depends(get_db),
):
    import json
    result = await db.execute(select(RasterLayer).where(RasterLayer.id == layer_id))
    layer = result.scalar_one_or_none()
    if not layer:
        raise HTTPException(404, "Layer not found.")
    layer.default_style = json.dumps(body.model_dump())
    await db.commit()
    await db.refresh(layer)
    return RasterLayerOut.from_orm_json(layer)


@router.delete("/{layer_id}", status_code=204)
async def delete_layer(layer_id: int, user: User = Depends(require_editor), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(RasterLayer).where(RasterLayer.id == layer_id))
    layer = result.scalar_one_or_none()
    if not layer:
        raise HTTPException(404, "Layer not found.")

    settings = get_settings()
    # DETACH vs DELETE: only objects under GeoDeploy's OWN `rasters/` upload area are removed.
    # A COG attached via import-existing points at a pre-existing bucket key — "import" means
    # LISTING, not copying (user decision 2026-07-10): deleting the layer unlists it, the file
    # stays, and it reappears in Import existing.
    if (layer.s3_key or "").startswith("rasters/"):
        try:
            from ...services.minio import get_s3_client
            s3 = get_s3_client()
            s3.delete_object(Bucket=settings.storage_bucket, Key=layer.s3_key)
        except Exception:
            pass

    await db.delete(layer)
    await db.commit()
