import os
import uuid
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import get_settings
from ...database import get_db
from ...deps import get_current_user
from ...models import RasterLayer, UploadJob, User
from ...schemas import JobStatus, RasterLayerOut
from ...tasks.raster_ingest import ingest_raster

router = APIRouter(prefix="/data/raster", tags=["raster"])

ALLOWED_EXTENSIONS = {".tif", ".tiff"}
MAX_FILE_SIZE = 10 * 1024 * 1024 * 1024  # 10 GB


@router.get("", response_model=list[RasterLayerOut])
async def list_layers(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(RasterLayer).where(RasterLayer.user_id == user.id).order_by(RasterLayer.created_at.desc())
    )
    layers = result.scalars().all()
    return [RasterLayerOut.from_orm_json(l) for l in layers]


@router.post("/upload", response_model=JobStatus, status_code=202)
async def upload_raster(
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
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


@router.delete("/{layer_id}", status_code=204)
async def delete_layer(layer_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(RasterLayer).where(RasterLayer.id == layer_id, RasterLayer.user_id == user.id))
    layer = result.scalar_one_or_none()
    if not layer:
        raise HTTPException(404, "Layer not found.")

    settings = get_settings()
    try:
        import boto3
        from botocore.client import Config
        s3 = boto3.client(
            "s3",
            endpoint_url=settings.storage_endpoint,
            aws_access_key_id=settings.storage_access_key,
            aws_secret_access_key=settings.storage_secret_key,
            config=Config(signature_version="s3v4"),
        )
        s3.delete_object(Bucket=settings.storage_bucket, Key=layer.s3_key)
    except Exception:
        pass

    await db.delete(layer)
    await db.commit()
