import json
import os
import uuid
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool
from slugify import slugify

from ...config import get_settings
from ...database import get_db
from ...deps import get_current_user
from ...models import UploadJob, User, VectorLayer
from ...schemas import DefaultStyle, JobStatus, SharingUpdate, VectorLayerOut
from ...services import martin as martin_svc
from ...tasks.vector_ingest import ingest_vector

router = APIRouter(prefix="/data/vector", tags=["vector"])

ALLOWED_EXTENSIONS = {".zip", ".geojson", ".json", ".gpkg"}
GEOPARQUET_EXTENSIONS = {".parquet", ".geoparquet"}
# Vector formats that can be uploaded DIRECT-to-storage (presigned) and converted to GeoParquet in
# the background — used when a file is too big to POST through the API (see MAX_FILE_SIZE).
LARGE_VECTOR_EXTENSIONS = ALLOWED_EXTENSIONS | {".csv"}
MAX_FILE_SIZE = 2 * 1024 * 1024 * 1024  # 2 GB — the API-passthrough cap (multipart through uvicorn)
MAX_GEOPARQUET_SIZE = 10 * 1024 * 1024 * 1024  # 10 GB (uploaded direct-to-storage, not via the API)
# Direct-to-storage cap for the convert-to-GeoParquet path. Env-tunable; per-plan quotas will
# eventually govern this for GeoDeploy Cloud.
MAX_LARGE_UPLOAD = int(os.getenv("MAX_LARGE_UPLOAD_BYTES", str(10 * 1024 * 1024 * 1024)))


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
    x_column: str | None = Form(None),
    y_column: str | None = Form(None),
    wkt_column: str | None = Form(None),
    srid: int = Form(4326),
    name: str | None = Form(None),
    delimiter: str = Form("comma"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Upload a CSV and build a PostGIS layer from it (queued, Celery): points from X/Y columns,
    or any geometry (e.g. polygons) from a WKT column."""
    from ...tasks import csv_import
    settings = get_settings()
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext != ".csv":
        raise HTTPException(400, "Upload a .csv file.")
    if not wkt_column and not (x_column and y_column):
        raise HTTPException(400, "Pick X/Y columns or a WKT geometry column.")

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
        # WKT can hold any geometry type — the task fills in the real one after the load.
        file_size=size, geometry_type=None if wkt_column else "point",
        geometry_column="geom", id_column="id",
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
                               x_column, y_column, srid, False, delimiter, wkt_column)
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


class LargeVectorPresign(BaseModel):
    filename: str
    name: str | None = None
    file_size: int | None = None


class LargeVectorComplete(BaseModel):
    s3_key: str
    name: str | None = None
    file_size: int | None = None
    # CSV geometry options (ignored for other formats)
    x_column: str | None = None
    y_column: str | None = None
    wkt_column: str | None = None
    srid: int = 4326
    delimiter: str = "comma"


@router.post("/large/presign")
async def large_vector_presign(
    body: LargeVectorPresign,
    user: User = Depends(get_current_user),
):
    """Step 1 of the LARGE-vector upload (CSV / GeoJSON / GeoPackage / shapefile-zip too big to POST
    through the API): hand the browser a presigned PUT URL so it uploads the file DIRECTLY to
    storage, exactly like the GeoParquet flow. The key preserves the original extension so the
    background converter knows the format."""
    from ...services.minio import browser_upload_url
    ext = os.path.splitext(body.filename or "")[1].lower()
    if ext not in LARGE_VECTOR_EXTENSIONS:
        raise HTTPException(400, f"Unsupported file type for large upload: {ext}. "
                                 f"Accepted: {', '.join(sorted(LARGE_VECTOR_EXTENSIONS))}")
    if body.file_size and body.file_size > MAX_LARGE_UPLOAD:
        raise HTTPException(413, f"File exceeds the {MAX_LARGE_UPLOAD // (1024**3)} GB limit.")
    base_name = os.path.splitext(os.path.basename(body.filename or "layer"))[0]
    safe_file = slugify(base_name, separator="_") or "layer"
    s3_key = f"vectors/{user.id}/{uuid.uuid4().hex}/{safe_file}{ext}"
    return {"upload_url": browser_upload_url(s3_key), "s3_key": s3_key}


@router.post("/large/complete", response_model=JobStatus, status_code=202)
async def large_vector_complete(
    body: LargeVectorComplete,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Step 2: the browser has PUT the large file to `s3_key`; register a processing layer and queue
    the conversion to GeoParquet (which chains the spatial prep and marks the layer ready)."""
    from ...tasks.convert_upload import convert_to_geoparquet
    if body.file_size and body.file_size > MAX_LARGE_UPLOAD:
        raise HTTPException(413, f"File exceeds the {MAX_LARGE_UPLOAD // (1024**3)} GB limit.")
    if not (body.s3_key or "").startswith(f"vectors/{user.id}/"):
        raise HTTPException(400, "Invalid storage key.")
    ext = os.path.splitext(body.s3_key)[1].lower()
    if ext not in LARGE_VECTOR_EXTENSIONS:
        raise HTTPException(400, "Unsupported file type.")
    if ext == ".csv" and not body.wkt_column and not (body.x_column and body.y_column):
        raise HTTPException(400, "Pick X/Y columns or a WKT geometry column for the CSV.")

    base_name = os.path.splitext(os.path.basename(body.s3_key))[0]
    layer_name = (body.name or "").strip() or base_name
    table_name = f"gpq_{slugify(layer_name, separator='_') or 'layer'}_{uuid.uuid4().hex[:6]}"
    schema_name = f"geodeploy_u{user.id}"

    csv_opts = None
    if ext == ".csv":
        csv_opts = {"x_column": body.x_column, "y_column": body.y_column,
                    "wkt_column": body.wkt_column, "srid": body.srid, "delimiter": body.delimiter}

    layer = VectorLayer(
        user_id=user.id, name=layer_name, table_name=table_name, schema_name=schema_name,
        file_size=body.file_size, storage_backend="geoparquet", s3_key=body.s3_key,
        status="processing",
        # Persist convert options so "restart processing" can re-run the conversion stage (while the
        # layer's s3_key still points at the raw upload) without the user re-picking columns.
        convert_opts=json.dumps(csv_opts) if csv_opts else None,
    )
    db.add(layer)
    await db.flush()
    job_id = str(uuid.uuid4())
    db.add(UploadJob(id=job_id, layer_id=layer.id, layer_type="vector"))
    await db.commit()
    await db.refresh(layer)

    convert_to_geoparquet.delay(job_id, layer.id, body.s3_key, csv_opts)
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


async def _viewport_geojson(layer: VectorLayer | None, bbox: str | None, limit: int) -> Response:
    """Shared GeoParquet viewport query → GeoJSON (EPSG:4326). DuckDB filters by the bbox using the
    covering column (row-group pruning) and caps results; the deck.gl overlay renders the subset.
    BOTH the DuckDB work AND the JSON serialization run in the threadpool: returning the raw dict
    let FastAPI's jsonable_encoder walk every coordinate of a ~25 MB FeatureCollection ON the event
    loop — minutes of pure-Python encoding at full extent that starved every other request into
    nginx 504s (seen live 2026-07-09). json.dumps is C-speed and off-loop; the pre-serialized body
    is returned as-is."""
    import json
    from starlette.concurrency import run_in_threadpool
    from ...services import duckdb_engine

    if not layer:
        raise HTTPException(404, "Layer not found.")
    if layer.storage_backend != "geoparquet" or not layer.s3_key:
        raise HTTPException(400, "This layer is not a GeoParquet (file-backed) layer.")
    limit = max(1, min(limit, 200000))
    parsed = _parse_bbox(bbox)

    def _query_and_dump() -> str:
        fc = duckdb_engine.query_features_geojson(layer.s3_key, parsed, limit)
        return json.dumps(fc, separators=(",", ":"))

    body = await run_in_threadpool(_query_and_dump)
    return Response(content=body, media_type="application/geo+json")


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


@router.get("/{layer_id}/features.arrow")
async def vector_features_arrow(
    layer_id: int,
    bbox: str | None = None,
    limit: int = 50000,
    db: AsyncSession = Depends(get_db),
):
    """PUBLIC viewport query as a GeoArrow-encoded Arrow IPC stream (geometry only) — the binary
    transport for the portal deck.gl overlay (@geoarrow/deck.gl-layers consumes the buffer
    zero-copy; no GeoJSON is produced server-side or reconstructed client-side). Same public-by-id
    posture as features.geojson. 204 = empty viewport; errors → the client falls back to the
    GeoJSON transport."""
    from starlette.concurrency import run_in_threadpool
    from ...services import duckdb_engine
    result = await db.execute(select(VectorLayer).where(VectorLayer.id == layer_id))
    layer = result.scalar_one_or_none()
    if not layer:
        raise HTTPException(404, "Layer not found.")
    if layer.storage_backend != "geoparquet" or not layer.s3_key:
        raise HTTPException(400, "This layer is not a GeoParquet (file-backed) layer.")
    body = await run_in_threadpool(
        duckdb_engine.query_features_arrow, layer.s3_key, _parse_bbox(bbox),
        max(1, min(limit, 200000)))
    if body is None:
        return Response(status_code=204)
    return Response(content=body, media_type="application/vnd.apache.arrow.stream")


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


@router.get("/{layer_id}/identify")
async def vector_identify(
    layer_id: int,
    lng: float,
    lat: float,
    tol: float = 1e-4,
    limit: int = 10,
    db: AsyncSession = Depends(get_db),
):
    """PUBLIC identify-on-click for GeoParquet (deck.gl-rendered) layers: the attributes of the
    features under a clicked point. Needed because the deck.gl transports ship geometry only
    (GeoArrow) — attributes are fetched per click, not per pan. Public-by-id like
    features.geojson (published portals are unauthenticated); the tiny bbox + covering pruning
    keeps it cheap. `tol` = half-width of the click box in degrees (client scales it by zoom)."""
    from ...services import duckdb_engine
    result = await db.execute(select(VectorLayer).where(VectorLayer.id == layer_id))
    layer = result.scalar_one_or_none()
    if not layer:
        raise HTTPException(404, "Layer not found.")
    if layer.storage_backend != "geoparquet" or not layer.s3_key:
        raise HTTPException(400, "This layer is not a GeoParquet (file-backed) layer.")
    tol = min(max(float(tol), 1e-7), 1.0)
    feats = await run_in_threadpool(
        duckdb_engine.query_features_at_point, layer.s3_key, lng, lat, tol,
        max(1, min(int(limit), 25)))
    return {"features": feats}


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


@router.post("/{layer_id}/reprocess", response_model=JobStatus, status_code=202)
async def reprocess_layer(
    layer_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Restart the background processing of a file-backed (GeoParquet) layer whose job stalled or
    failed — used from the UI when a large upload's conversion or spatial prep died (e.g. the worker
    was restarted mid-task). Re-runs the RIGHT stage based on where the data currently sits, so the
    user never has to re-upload a multi-GB file:
      • s3_key still points at the RAW upload (.csv/.gpkg/.geojson/.zip) → the convert-to-GeoParquet
        stage never finished → re-queue `convert_to_geoparquet` with the saved `convert_opts`.
      • s3_key is already a `.parquet` or a prepared `parts-…/` prefix → re-queue the spatial prep.
    A fresh UploadJob is created so the UI's progress resets and can be polled to completion."""
    from ...tasks.convert_upload import convert_to_geoparquet
    from ...tasks.geoparquet_prep import prepare_geoparquet
    result = await db.execute(select(VectorLayer).where(VectorLayer.id == layer_id, VectorLayer.user_id == user.id))
    layer = result.scalar_one_or_none()
    if not layer:
        raise HTTPException(404, "Layer not found.")
    if layer.storage_backend != "geoparquet" or not layer.s3_key:
        raise HTTPException(400, "Only file-backed (GeoParquet) layers can be reprocessed.")

    ext = os.path.splitext(layer.s3_key)[1].lower()
    needs_convert = ext in LARGE_VECTOR_EXTENSIONS  # raw upload — conversion never completed
    if needs_convert and ext == ".csv" and not layer.convert_opts:
        raise HTTPException(400, "Cannot restart: the CSV geometry options for this upload were not "
                                 "saved (uploaded before restart support). Re-upload the file.")

    layer.status = "processing"
    layer.error_message = None
    job_id = str(uuid.uuid4())
    db.add(UploadJob(id=job_id, layer_id=layer.id, layer_type="vector"))
    await db.commit()
    await db.refresh(layer)

    if needs_convert:
        csv_opts = json.loads(layer.convert_opts) if layer.convert_opts else None
        convert_to_geoparquet.delay(job_id, layer.id, layer.s3_key, csv_opts)
    else:
        prepare_geoparquet.delay(layer.id, layer.s3_key, job_id)
    return JobStatus(id=job_id, layer_id=layer.id, layer_type="vector",
                     status="queued", progress=0, current_step="Queued", error_message=None)


# layer_id → pmtiles object key. Cached because MapLibre's pmtiles protocol issues MANY small Range
# requests per map pan, and an ORM SELECT per request added a DB round-trip to every tile fetch
# (the same problem the parquet range proxy caches for). The key is deterministic and stable across
# re-tiles (overwritten in place); a re-PREP mints a new key, so a stale entry self-heals on the S3
# miss below (fetch fails → drop entry → re-read from the DB next request).
_PMTILES_KEY_CACHE: dict[int, str] = {}


async def _pmtiles_key(layer_id: int, db: AsyncSession) -> str | None:
    cached = _PMTILES_KEY_CACHE.get(layer_id)
    if cached is not None:
        return cached
    result = await db.execute(select(VectorLayer).where(VectorLayer.id == layer_id))
    layer = result.scalar_one_or_none()
    if not layer or layer.storage_backend != "geoparquet" or not layer.pmtiles_key:
        return None
    _PMTILES_KEY_CACHE[layer_id] = layer.pmtiles_key
    return layer.pmtiles_key


@router.get("/{layer_id}/pmtiles")
async def vector_pmtiles(layer_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    """PUBLIC range proxy for a GeoParquet layer's PMTiles archive — MapLibre's pmtiles protocol
    streams the tiles via HTTP Range requests. Public like Martin vector tiles (`/tiles/`), since
    published portals are unauthenticated; same-origin so no CORS, and the bucket creds stay
    server-side. The DB row is the only thing the caller can address (by id), not arbitrary keys."""
    key = await _pmtiles_key(layer_id, db)
    if not key:
        raise HTTPException(404, "No tiles for this layer.")

    settings = get_settings()
    from ...services.minio import get_s3_client
    s3 = get_s3_client()
    params = {"Bucket": settings.storage_bucket, "Key": key}
    rng = request.headers.get("range")
    if rng:
        params["Range"] = rng
    try:
        obj = await run_in_threadpool(lambda: s3.get_object(**params))
    except Exception:
        _PMTILES_KEY_CACHE.pop(layer_id, None)  # stale key (e.g. a re-prep) — re-read next request
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


# layer_id → s3_key prefix for the parquet range proxy. Cached because duckdb-wasm issues MANY
# small range requests per map pan and an ORM SELECT per request starved the api. A re-prep
# repoints s3_key to a NEW parts-<hex> prefix — handled by the miss-refresh in the route (a
# stale prefix's objects are deleted, so the fetch fails, the entry is dropped and re-read).
_PARQUET_PREFIX_CACHE: dict[int, str] = {}


async def _parquet_prefix(layer_id: int, db: AsyncSession) -> str | None:
    cached = _PARQUET_PREFIX_CACHE.get(layer_id)
    if cached is not None:
        return cached
    result = await db.execute(select(VectorLayer).where(VectorLayer.id == layer_id))
    layer = result.scalar_one_or_none()
    if (not layer or layer.storage_backend != "geoparquet" or not layer.s3_key
            or layer.s3_key.rstrip("/").endswith(".parquet")):  # unprepped single file: no manifest
        return None
    prefix = layer.s3_key.rstrip("/")
    _PARQUET_PREFIX_CACHE[layer_id] = prefix
    return prefix


@router.get("/{layer_id}/parquet/{path:path}")
async def vector_parquet_object(layer_id: int, path: str, request: Request,
                                db: AsyncSession = Depends(get_db)):
    """PUBLIC range proxy for a prepared GeoParquet layer's objects — `manifest.json` plus the
    `__cell=N/*.parquet` partition files — so the portal.js duckdb-wasm client can read parquet
    row groups via HTTP Range requests. Public + same-origin like `/pmtiles` (published portals
    are unauthenticated; bucket creds stay server-side). The layer id addresses ONLY keys under
    that layer's own prefix: S3 keys are literal strings, so the prefix join below cannot escape
    it ('..' is rejected anyway as defense-in-depth). HOT PATH: this route is hit dozens of times
    per map pan — keep it off the DB (prefix cache) and reuse the cached boto3 client."""
    if not path or ".." in path.split("/"):
        raise HTTPException(404, "Not found.")
    prefix = await _parquet_prefix(layer_id, db)
    if not prefix:
        raise HTTPException(404, "No parquet dataset for this layer.")

    settings = get_settings()
    from ...services.minio import get_s3_client
    s3 = get_s3_client()
    rng = request.headers.get("range")

    def _get(pfx: str):
        params = {"Bucket": settings.storage_bucket, "Key": f"{pfx}/{path}"}
        if rng:
            params["Range"] = rng
        return s3.get_object(**params)

    try:
        obj = await run_in_threadpool(_get, prefix)
    except Exception:
        # The cached prefix may be stale (re-prep repointed the layer): refresh once and retry.
        _PARQUET_PREFIX_CACHE.pop(layer_id, None)
        fresh = await _parquet_prefix(layer_id, db)
        if not fresh or fresh == prefix:
            raise HTTPException(404, "Object not found.")
        try:
            obj = await run_in_threadpool(_get, fresh)
        except Exception:
            raise HTTPException(404, "Object not found.")

    # Partition files under a parts-<hex> prefix are immutable (a re-prep mints a NEW prefix), so
    # the browser may cache them hard — that's what makes repeat pans/visits cheap. The manifest
    # can be regenerated in place (backfill), so it gets a shorter TTL.
    cache = ("public, max-age=86400, immutable" if path.endswith(".parquet")
             else "public, max-age=3600")
    headers = {"Accept-Ranges": "bytes", "Cache-Control": cache}
    status = 200
    if obj.get("ContentRange"):
        headers["Content-Range"] = obj["ContentRange"]
        status = 206
    if obj.get("ContentLength") is not None:
        headers["Content-Length"] = str(obj["ContentLength"])
    media = "application/json" if path.endswith(".json") else "application/octet-stream"
    return StreamingResponse(obj["Body"].iter_chunks(256 * 1024), status_code=status,
                             media_type=media, headers=headers)


@router.put("/{layer_id}/sharing", response_model=VectorLayerOut)
async def save_sharing(
    layer_id: int,
    body: SharingUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Data-sharing settings: opt the layer into the public STAC catalog (`/api/stac`) plus its
    catalog metadata. Display endpoints portals rely on stay public-by-id regardless; this flag
    governs discovery (and, for rasters, the raw-COG route)."""
    result = await db.execute(select(VectorLayer).where(VectorLayer.id == layer_id, VectorLayer.user_id == user.id))
    layer = result.scalar_one_or_none()
    if not layer:
        raise HTTPException(404, "Layer not found.")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(layer, field, value)
    await db.commit()
    await db.refresh(layer)
    return VectorLayerOut.from_orm_json(layer)


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
        # DETACH vs DELETE: only objects under GeoDeploy's OWN `vectors/` area are deleted —
        # that's where uploads land. A layer attached via import-existing points at someone
        # else's key; deleting the catalog entry must NOT destroy their file (attach-don't-copy).
        from ...services.minio import get_s3_client
        s3 = get_s3_client()
        b = settings.storage_bucket
        for key in (layer.s3_key, layer.pmtiles_key):
            if not key or not key.startswith("vectors/"):
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
    elif layer.status == "ready" and (layer.schema_name or "").startswith("geodeploy_u"):
        # DROP only tables GeoDeploy itself created (they live in its per-user schema). A table
        # in any OTHER schema was ATTACHED via import-existing — "import" means LISTING, not
        # copying (user decision 2026-07-10), so deleting the layer just unlists it and the
        # source table stays untouched (and reappears in Import existing).
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
