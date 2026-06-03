import os
import uuid
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from slugify import slugify

from ...config import get_settings
from ...database import get_db
from ...deps import get_current_user
from ...models import UploadJob, User, VectorLayer
from ...schemas import DefaultStyle, JobStatus, VectorLayerOut
from ...services import martin as martin_svc
from ...tasks.vector_ingest import ingest_vector

router = APIRouter(prefix="/data/vector", tags=["vector"])

ALLOWED_EXTENSIONS = {".zip", ".geojson", ".json", ".gpkg"}
MAX_FILE_SIZE = 2 * 1024 * 1024 * 1024  # 2 GB


@router.get("", response_model=list[VectorLayerOut])
async def list_layers(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(VectorLayer).where(VectorLayer.user_id == user.id).order_by(VectorLayer.created_at.desc()))
    layers = result.scalars().all()
    return [VectorLayerOut.from_orm_json(l) for l in layers]


@router.post("/upload", response_model=JobStatus, status_code=202)
async def upload_vector(
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    settings = get_settings()
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(400, f"Unsupported file type: {ext}. Accepted: {', '.join(ALLOWED_EXTENSIONS)}")

    os.makedirs(f"{settings.data_dir}/temp", exist_ok=True)
    tmp_path = f"{settings.data_dir}/temp/{uuid.uuid4()}{ext}"

    size = 0
    with open(tmp_path, "wb") as f:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_FILE_SIZE:
                os.unlink(tmp_path)
                raise HTTPException(413, "File exceeds 2 GB limit.")
            f.write(chunk)

    base_name = os.path.splitext(file.filename or "layer")[0]
    layer_name = slugify(base_name, separator="_")
    schema_name = f"geodeploy_u{user.id}"
    table_name = f"{layer_name}_{uuid.uuid4().hex[:6]}"

    layer = VectorLayer(
        user_id=user.id,
        name=base_name,
        table_name=table_name,
        schema_name=schema_name,
        file_size=size,
        status="processing",
    )
    db.add(layer)
    await db.flush()

    job_id = str(uuid.uuid4())
    job = UploadJob(id=job_id, layer_id=layer.id, layer_type="vector")
    db.add(job)
    await db.commit()
    await db.refresh(layer)

    ingest_vector.delay(job_id, layer.id, tmp_path, layer_name, schema_name, table_name)

    return JobStatus(
        id=job_id,
        layer_id=layer.id,
        layer_type="vector",
        status="queued",
        progress=0,
        current_step="Queued",
        error_message=None,
    )


@router.get("/jobs/{job_id}", response_model=JobStatus)
async def job_status(job_id: str, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    result = await db.execute(select(UploadJob).where(UploadJob.id == job_id))
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(404, "Job not found.")
    return job


@router.put("/{layer_id}/default-style", response_model=VectorLayerOut)
async def save_default_style(
    layer_id: int,
    body: DefaultStyle,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    import json
    result = await db.execute(select(VectorLayer).where(VectorLayer.id == layer_id, VectorLayer.user_id == user.id))
    layer = result.scalar_one_or_none()
    if not layer:
        raise HTTPException(404, "Layer not found.")
    layer.default_style = json.dumps(body.model_dump())
    await db.commit()
    await db.refresh(layer)
    return VectorLayerOut.from_orm_json(layer)


@router.delete("/{layer_id}", status_code=204)
async def delete_layer(
    layer_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(VectorLayer).where(VectorLayer.id == layer_id, VectorLayer.user_id == user.id))
    layer = result.scalar_one_or_none()
    if not layer:
        raise HTTPException(404, "Layer not found.")

    settings = get_settings()
    if layer.status == "ready":
        import asyncpg
        try:
            # asyncpg wants the plain postgresql:// DSN (not the +asyncpg SQLAlchemy form);
            # postgis_sync_dsn also carries sslmode for external/managed DBs.
            conn = await asyncpg.connect(settings.postgis_sync_dsn)
            await conn.execute(f'DROP TABLE IF EXISTS "{layer.schema_name}"."{layer.table_name}"')
            await conn.close()
        except Exception:
            pass

    await db.delete(layer)
    await db.commit()

    # Regenerate Martin config without the deleted layer
    remaining = await db.execute(
        select(VectorLayer).where(
            VectorLayer.user_id == user.id,
            VectorLayer.status == "ready",
            VectorLayer.storage_backend == "postgis",
        )
    )
    all_layers = [{"schema_name": l.schema_name, "table_name": l.table_name,
                   "geometry_column": l.geometry_column, "id_column": l.id_column, "crs": l.crs}
                  for l in remaining.scalars().all()]
    try:
        await martin_svc.regenerate_config(all_layers)
    except Exception:
        pass  # Non-fatal — tiles still work until next successful regeneration
