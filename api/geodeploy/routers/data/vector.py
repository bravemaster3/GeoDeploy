import os
import uuid
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool
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
GEOPARQUET_EXTENSIONS = {".parquet", ".geoparquet"}
MAX_FILE_SIZE = 2 * 1024 * 1024 * 1024  # 2 GB
MAX_GEOPARQUET_SIZE = 10 * 1024 * 1024 * 1024  # 10 GB (uploaded direct-to-storage, not via the API)


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


@router.post("/upload-csv", response_model=JobStatus, status_code=202)
async def upload_csv(
    file: UploadFile = File(...),
    x_column: str = Form(...),
    y_column: str = Form(...),
    srid: int = Form(4326),
    name: str | None = Form(None),
    delimiter: str = Form("comma"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Upload a CSV and build a PostGIS point layer from its X/Y columns (queued, Celery)."""
    from ...tasks import csv_import
    settings = get_settings()
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext != ".csv":
        raise HTTPException(400, "Upload a .csv file.")

    os.makedirs(f"{settings.data_dir}/temp", exist_ok=True)
    tmp_path = f"{settings.data_dir}/temp/{uuid.uuid4().hex}.csv"
    size = 0
    with open(tmp_path, "wb") as f:
        while chunk := await file.read(4 * 1024 * 1024):
            size += len(chunk)
            if size > MAX_FILE_SIZE:
                os.unlink(tmp_path)
                raise HTTPException(413, "File exceeds 2 GB limit.")
            f.write(chunk)

    base_name = os.path.splitext(file.filename or "layer")[0]
    layer_name = (name or "").strip() or base_name
    schema_name = f"geodeploy_u{user.id}"
    table_name = f"csv_{csv_import.safe_name(layer_name, 'layer')}_{uuid.uuid4().hex[:6]}"

    layer = VectorLayer(
        user_id=user.id, name=layer_name, table_name=table_name, schema_name=schema_name,
        file_size=size, geometry_type="point", geometry_column="geom", id_column="id",
        storage_backend="postgis", status="processing",
    )
    db.add(layer)
    await db.flush()
    job_id = str(uuid.uuid4())
    db.add(UploadJob(id=job_id, layer_id=layer.id, layer_type="vector"))
    await db.commit()
    await db.refresh(layer)

    # is_s3=False → the task reads (and then deletes) this local temp CSV.
    csv_import.import_csv.delay(job_id, layer.id, tmp_path, schema_name, table_name,
                               x_column, y_column, srid, False, delimiter)
    return JobStatus(id=job_id, layer_id=layer.id, layer_type="vector",
                     status="queued", progress=0, current_step="Queued", error_message=None)


class GeoParquetPresign(BaseModel):
    filename: str
    name: str | None = None
    file_size: int | None = None


class GeoParquetComplete(BaseModel):
    s3_key: str
    name: str | None = None
    file_size: int | None = None


@router.post("/geoparquet/presign")
async def geoparquet_presign(
    body: GeoParquetPresign,
    user: User = Depends(get_current_user),
):
    """Step 1 of the GeoParquet upload: hand the browser a presigned PUT URL so it uploads the
    file DIRECTLY to object storage (no multi-GB passthrough of the API process/disk). The key
    is derived server-side under the user's `vectors/` prefix so the client can't choose it."""
    from ...services.minio import browser_upload_url
    ext = os.path.splitext(body.filename or "")[1].lower()
    if ext not in GEOPARQUET_EXTENSIONS:
        raise HTTPException(400, "Upload a .parquet / .geoparquet file.")
    if body.file_size and body.file_size > MAX_GEOPARQUET_SIZE:
        raise HTTPException(413, "File exceeds 10 GB limit.")

    base_name = os.path.splitext(os.path.basename(body.filename or "layer"))[0]
    safe_file = slugify(base_name, separator="_") or "layer"
    # Parallel to the raster convention (rasters/{uid}/{uuid}/x.tif); vectors live under vectors/.
    s3_key = f"vectors/{user.id}/{uuid.uuid4().hex}/{safe_file}.parquet"
    return {"upload_url": browser_upload_url(s3_key), "s3_key": s3_key}


@router.post("/geoparquet/complete", response_model=JobStatus, status_code=202)
async def geoparquet_complete(
    body: GeoParquetComplete,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Step 2: the browser has PUT the file to `s3_key`; register the layer and queue inspection
    (DuckDB reads it in place — never loaded into PostGIS)."""
    from ...tasks.geoparquet_import import import_geoparquet
    if body.file_size and body.file_size > MAX_GEOPARQUET_SIZE:
        raise HTTPException(413, "File exceeds 10 GB limit.")
    # The key must be inside this user's own prefix (the presign step issued it there).
    if not (body.s3_key or "").startswith(f"vectors/{user.id}/"):
        raise HTTPException(400, "Invalid storage key.")

    base_name = os.path.splitext(os.path.basename(body.s3_key))[0]
    layer_name = (body.name or "").strip() or base_name
    table_name = f"gpq_{slugify(layer_name, separator='_') or 'layer'}_{uuid.uuid4().hex[:6]}"
    schema_name = f"geodeploy_u{user.id}"

    layer = VectorLayer(
        user_id=user.id, name=layer_name, table_name=table_name, schema_name=schema_name,
        file_size=body.file_size, storage_backend="geoparquet", s3_key=body.s3_key,
        status="processing",
    )
    db.add(layer)
    await db.flush()
    job_id = str(uuid.uuid4())
    db.add(UploadJob(id=job_id, layer_id=layer.id, layer_type="vector"))
    await db.commit()
    await db.refresh(layer)

    import_geoparquet.delay(job_id, layer.id, body.s3_key)
    return JobStatus(id=job_id, layer_id=layer.id, layer_type="vector",
                     status="queued", progress=0, current_step="Queued", error_message=None)


@router.get("/jobs/{job_id}", response_model=JobStatus)
async def job_status(job_id: str, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    result = await db.execute(select(UploadJob).where(UploadJob.id == job_id))
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(404, "Job not found.")
    return job


def _parse_bbox(bbox: str | None) -> list[float] | None:
    if not bbox:
        return None
    try:
        parts = [float(x) for x in bbox.split(",")]
        return parts if len(parts) == 4 else None
    except ValueError:
        return None


async def _viewport_geojson(layer: VectorLayer | None, bbox: str | None, limit: int) -> dict:
    """Shared GeoParquet viewport query → GeoJSON (EPSG:4326). DuckDB filters by the bbox using the
    covering column (row-group pruning) and caps results; the deck.gl overlay renders the subset.
    The DuckDB work runs in a threadpool so the single uvicorn worker isn't blocked."""
    from starlette.concurrency import run_in_threadpool
    from ...services import duckdb_engine

    if not layer:
        raise HTTPException(404, "Layer not found.")
    if layer.storage_backend != "geoparquet" or not layer.s3_key:
        raise HTTPException(400, "This layer is not a GeoParquet (file-backed) layer.")
    limit = max(1, min(limit, 200000))
    return await run_in_threadpool(duckdb_engine.query_features_geojson, layer.s3_key, _parse_bbox(bbox), limit)


@router.get("/{layer_id}/features")
async def vector_features(
    layer_id: int,
    bbox: str | None = None,
    limit: int = 50000,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Authed viewport query for the editor preview's deck.gl overlay."""
    result = await db.execute(select(VectorLayer).where(VectorLayer.id == layer_id, VectorLayer.user_id == user.id))
    return await _viewport_geojson(result.scalar_one_or_none(), bbox, limit)


@router.get("/{layer_id}/features.geojson")
async def vector_features_public(
    layer_id: int,
    bbox: str | None = None,
    limit: int = 50000,
    db: AsyncSession = Depends(get_db),
):
    """PUBLIC viewport query for the deck.gl overlay in published (unauthenticated) portals. Public
    by layer id, mirroring the `/pmtiles` range proxy's posture: published portals are public, the
    caller can only address a DB row by id (not arbitrary keys), and bucket creds stay server-side.
    (Single-admin self-hosted assumption — for multi-tenant cloud this needs portal scoping + auth,
    same open question as the rest of the public-portal surface; see notes §0h-addendum.)"""
    result = await db.execute(select(VectorLayer).where(VectorLayer.id == layer_id))
    return await _viewport_geojson(result.scalar_one_or_none(), bbox, limit)


@router.post("/{layer_id}/tile", response_model=VectorLayerOut)
async def tile_layer(
    layer_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """(Re)generate the PMTiles archive for a GeoParquet layer — used to tile a file uploaded
    before tiling existed, or to retry after an error."""
    from ...tasks.pmtiles_tile import tile_geoparquet
    result = await db.execute(select(VectorLayer).where(VectorLayer.id == layer_id, VectorLayer.user_id == user.id))
    layer = result.scalar_one_or_none()
    if not layer:
        raise HTTPException(404, "Layer not found.")
    if layer.storage_backend != "geoparquet" or not layer.s3_key:
        raise HTTPException(400, "This layer is not a GeoParquet (file-backed) layer.")

    pmtiles_key = (layer.s3_key.rsplit(".", 1)[0] if "." in layer.s3_key else layer.s3_key) + ".pmtiles"
    layer.tile_status = "tiling"
    await db.commit()
    await db.refresh(layer)
    tile_geoparquet.delay(layer.id, layer.s3_key, pmtiles_key)
    return VectorLayerOut.from_orm_json(layer)


@router.post("/{layer_id}/prepare", response_model=VectorLayerOut)
async def prepare_layer(
    layer_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Spatially prepare a GeoParquet layer: rewrite it Z-order-sorted with a GeoParquet 1.1 bbox
    covering column so DuckDB prunes row-groups on a bbox filter (fast analysis + viewport display).
    Idempotent — overwrites the object in place. The file stays GeoParquet (no PostGIS, no PMTiles)."""
    from ...tasks.geoparquet_prep import prepare_geoparquet
    result = await db.execute(select(VectorLayer).where(VectorLayer.id == layer_id, VectorLayer.user_id == user.id))
    layer = result.scalar_one_or_none()
    if not layer:
        raise HTTPException(404, "Layer not found.")
    if layer.storage_backend != "geoparquet" or not layer.s3_key:
        raise HTTPException(400, "This layer is not a GeoParquet (file-backed) layer.")

    layer.status = "processing"
    await db.commit()
    await db.refresh(layer)
    prepare_geoparquet.delay(layer.id, layer.s3_key)
    return VectorLayerOut.from_orm_json(layer)


@router.get("/{layer_id}/pmtiles")
async def vector_pmtiles(layer_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    """PUBLIC range proxy for a GeoParquet layer's PMTiles archive — MapLibre's pmtiles protocol
    streams the tiles via HTTP Range requests. Public like Martin vector tiles (`/tiles/`), since
    published portals are unauthenticated; same-origin so no CORS, and the bucket creds stay
    server-side. The DB row is the only thing the caller can address (by id), not arbitrary keys."""
    result = await db.execute(select(VectorLayer).where(VectorLayer.id == layer_id))
    layer = result.scalar_one_or_none()
    if not layer or layer.storage_backend != "geoparquet" or not layer.pmtiles_key:
        raise HTTPException(404, "No tiles for this layer.")

    settings = get_settings()
    from ...services.minio import get_s3_client
    s3 = get_s3_client()
    params = {"Bucket": settings.storage_bucket, "Key": layer.pmtiles_key}
    rng = request.headers.get("range")
    if rng:
        params["Range"] = rng
    try:
        obj = await run_in_threadpool(lambda: s3.get_object(**params))
    except Exception:
        raise HTTPException(404, "Tiles not found.")

    headers = {"Accept-Ranges": "bytes", "Cache-Control": "public, max-age=3600"}
    status = 200
    if obj.get("ContentRange"):
        headers["Content-Range"] = obj["ContentRange"]
        status = 206
    if obj.get("ContentLength") is not None:
        headers["Content-Length"] = str(obj["ContentLength"])

    body = obj["Body"]
    return StreamingResponse(body.iter_chunks(256 * 1024), status_code=status,
                             media_type="application/octet-stream", headers=headers)


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
    if layer.storage_backend == "geoparquet":
        # GeoParquet layers live as files on object storage, no PostGIS table. After spatial prep,
        # s3_key is a PREFIX (a partitioned dataset of __cell=N/*.parquet files); before prep it's a
        # single .parquet. Also remove any .pmtiles fallback archive.
        from ...services.minio import get_s3_client
        s3 = get_s3_client()
        b = settings.storage_bucket
        for key in (layer.s3_key, layer.pmtiles_key):
            if not key:
                continue
            try:
                if key.rstrip("/").endswith((".parquet", ".pmtiles")):
                    s3.delete_object(Bucket=b, Key=key)
                else:  # partitioned prefix → delete every object under it
                    prefix = key.rstrip("/") + "/"
                    batch = []
                    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=b, Prefix=prefix):
                        for obj in page.get("Contents", []):
                            batch.append({"Key": obj["Key"]})
                            if len(batch) >= 1000:
                                s3.delete_objects(Bucket=b, Delete={"Objects": batch}); batch = []
                    if batch:
                        s3.delete_objects(Bucket=b, Delete={"Objects": batch})
            except Exception:
                pass
    elif layer.status == "ready":
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
