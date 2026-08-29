import json
import os
import uuid
from typing import Any
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool
from slugify import slugify

from ...config import get_settings
from ...database import get_db
from ...deps import require_scope, resolve_optional_user
from ...models import Portal, UploadJob, User, VectorLayer
from ...schemas import DefaultStyle, JobStatus, LayerRename, PortalRefOut, SharingUpdate, VectorLayerOut
from ...services import martin as martin_svc
from ...tasks.vector_ingest import ingest_vector
from . import exports
from ..common import (apply_sharing, demo_upload_cap, busy_job_progress, by_ref, creator_names, portals_using,
                      prune_layer_from_portals, record_audit, visible_to)

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


# ── Public-read authorization for the file-backed (GeoParquet) display endpoints ──────────────
# These endpoints serve WITHOUT auth so published (unauthenticated) portals can render the data.
# But only layers the admin actually exposed should be reachable by id: `is_public` (the explicit
# share/catalog opt-in) OR membership in a PUBLISHED portal. A layer that is neither is private and
# must 404 — even to a caller who enumerates ids. The published-portal id set is cached (these are
# hot paths — pmtiles/parquet fire many range requests per pan) and invalidated by
# `invalidate_public_layers()` whenever publish/share/delete state changes.
_PUBLISHED_VECTOR_IDS: set[int] | None = None


def invalidate_public_layers() -> None:
    """Drop the cached public-layer set + the pmtiles/parquet key caches (which fold in the same
    readability check). Call after any publish/unpublish/share/delete that changes exposure."""
    global _PUBLISHED_VECTOR_IDS
    _PUBLISHED_VECTOR_IDS = None
    _PMTILES_KEY_CACHE.clear()
    # The dashboard's answer cache too. A deleted layer is the case that matters: its id can be
    # reused, and answering a widget with the previous layer's totals is a wrong number with no
    # symptom. (Ordinary re-ingest is bounded by that cache's own TTL instead — the ingest runs in
    # celery, a different process, so it cannot reach this one's memory.)
    from ...services import aggregate as _agg
    _agg.invalidate()
    # The raster side keeps the same kind of cache for its description endpoints, and the events
    # that invalidate one invalidate the other — every caller already reaches for this function, so
    # forwarding here is what stops the two from drifting. Imported lazily: raster.py imports from
    # this module, so a top-level import would be circular.
    try:
        from .raster import invalidate_public_rasters
        invalidate_public_rasters()
    except ImportError:                 # pragma: no cover - only during a partial import
        pass
    _PARQUET_PREFIX_CACHE.clear()


def _dashboard_ids(raw, kind: str) -> set[int]:
    """Layer ids a published portal's DASHBOARD binds, by kind. Shared with `raster.py` (which
    imports it) so the two published-id caches read the config the same way — a second parser would
    eventually disagree about which layers a dashboard exposes."""
    try:
        from ...services.dashboard import dashboard_layer_refs
        cfg = json.loads(raw) if isinstance(raw, str) else raw
        vectors, rasters = dashboard_layer_refs(cfg if isinstance(cfg, dict) else None)
    except (ValueError, TypeError, AttributeError):
        return set()
    return rasters if kind == "raster" else vectors


async def _published_vector_ids(db: AsyncSession) -> set[int]:
    global _PUBLISHED_VECTOR_IDS
    if _PUBLISHED_VECTOR_IDS is None:
        rows = (await db.execute(
            select(Portal.layer_configs, Portal.dashboard)
            .where(Portal.published == True))).all()  # noqa: E712
        ids: set[int] = set()
        for cfg, dash in rows:
            try:
                configs = json.loads(cfg) if isinstance(cfg, str) else (cfg or [])
                for lc in configs:
                    if lc.get("layer_id") is not None and lc.get("layer_type", "vector") in (None, "vector"):
                        ids.add(int(lc["layer_id"]))
            except (ValueError, TypeError, AttributeError):
                pass
            # V-16: a dashboard widget can summarise a layer the MAP never draws (an indicator over
            # a table of readings, say). Its aggregate endpoint has to answer for that layer or the
            # widget is a permanent error on a portal that is deliberately publishing it.
            ids |= _dashboard_ids(dash, "vector")
        _PUBLISHED_VECTOR_IDS = ids
    return _PUBLISHED_VECTOR_IDS


async def _publicly_readable(layer, db: AsyncSession) -> bool:
    """True if a file-backed layer may be served to an unauthenticated caller: shared (`is_public`)
    or shown by a published portal. Otherwise it is private and callers get a 404."""
    if layer is None:
        return False
    if getattr(layer, "is_public", False):
        return True
    return layer.id in await _published_vector_ids(db)


@router.get("", response_model=list[VectorLayerOut])
async def list_layers(
    user: User = Depends(require_scope("data:read")),
    db: AsyncSession = Depends(get_db),
):
    # Shared workspace: every member sees all layers (role gates WRITES, not reads).
    result = await db.execute(
        select(VectorLayer).where(visible_to(user, VectorLayer)).order_by(VectorLayer.created_at.desc()))
    layers = result.scalars().all()
    names = await creator_names(db, layers)
    jobs = await busy_job_progress(db, layers, "vector")
    out = []
    for l in layers:
        o = VectorLayerOut.from_orm_json(l)
        o.created_by = names.get(l.user_id)
        if l.id in jobs:
            o.progress, o.current_step = jobs[l.id]
        out.append(o)
    return out


@router.get("/{layer_id}/usage", response_model=list[PortalRefOut])
async def layer_usage(layer_id: int, user: User = Depends(require_scope("data:read")),
                      db: AsyncSession = Depends(get_db)):
    """Portals that include this layer — shown in the delete-confirmation dialog so the user knows
    what a deletion will affect (the layer is pruned from these portals + re-published on delete)."""
    return [PortalRefOut.model_validate(p) for p in await portals_using(db, "vector", layer_id)]


@router.post("/upload", response_model=JobStatus, status_code=202)
async def upload_vector(
    file: UploadFile = File(...),
    user: User = Depends(require_scope("data:write")),
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
    await record_audit(db, user, "vector.upload", "vector", layer.id,
                       {"name": base_name, "file": file.filename})

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
    user: User = Depends(require_scope("data:write")),
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
    user: User = Depends(require_scope("data:write")),
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
    user: User = Depends(require_scope("data:write")),
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


# ── Multipart (chunked) upload ────────────────────────────────────────────────────────────────
# A single multi-GB PUT gets reset behind a CDN (Cloudflare buffers/caps the body). So big files
# upload in parts, each a presigned PUT well under the ~100 MB limit. The browser: initiate → PUT
# every part → complete (assemble), then the usual /geoparquet|large/complete registers the key.
PART_SIZE = 48 * 1024 * 1024  # 48 MB per part — safely under Cloudflare's request-body limit


class MultipartInitiate(BaseModel):
    filename: str
    file_size: int
    kind: str  # "geoparquet" | "large"


class MultipartPart(BaseModel):
    part_number: int
    etag: str


class MultipartComplete(BaseModel):
    s3_key: str
    upload_id: str
    parts: list[MultipartPart] = []


@router.post("/upload/multipart/initiate")
async def multipart_initiate(body: MultipartInitiate, user: User = Depends(require_scope("data:write"))):
    """Begin a chunked upload: validate, mint a key under the user's prefix, open the S3 multipart
    upload, and return a presigned PUT URL for every part. The key convention matches the single-PUT
    flows so /geoparquet/complete and /large/complete accept it unchanged."""
    demo_upload_cap(body.file_size)
    import math
    from ...services import minio as minio_svc
    ext = os.path.splitext(body.filename or "")[1].lower()
    if body.kind == "geoparquet":
        if ext not in GEOPARQUET_EXTENSIONS:
            raise HTTPException(400, "Upload a .parquet / .geoparquet file.")
        if body.file_size > MAX_GEOPARQUET_SIZE:
            raise HTTPException(413, "File exceeds 10 GB limit.")
    else:
        if ext not in LARGE_VECTOR_EXTENSIONS:
            raise HTTPException(400, f"Unsupported file type: {ext}.")
        if body.file_size > MAX_LARGE_UPLOAD:
            raise HTTPException(413, f"File exceeds the {MAX_LARGE_UPLOAD // (1024**3)} GB limit.")
    if body.file_size <= 0:
        raise HTTPException(400, "Empty file.")

    base = slugify(os.path.splitext(os.path.basename(body.filename or "layer"))[0], separator="_") or "layer"
    s3_key = f"vectors/{user.id}/{uuid.uuid4().hex}/{base}{ext}"
    upload_id = await run_in_threadpool(minio_svc.create_multipart, s3_key)
    num_parts = max(1, math.ceil(body.file_size / PART_SIZE))
    parts = await run_in_threadpool(minio_svc.presign_parts, s3_key, upload_id, num_parts)
    return {"s3_key": s3_key, "upload_id": upload_id, "part_size": PART_SIZE, "parts": parts}


@router.post("/upload/multipart/complete")
async def multipart_complete(body: MultipartComplete, user: User = Depends(require_scope("data:write"))):
    """Assemble the uploaded parts into the final object. The key persists; registration is a
    separate call to /geoparquet/complete or /large/complete (same as the single-PUT flows)."""
    from ...services import minio as minio_svc
    if not (body.s3_key or "").startswith(f"vectors/{user.id}/"):
        raise HTTPException(400, "Invalid storage key.")
    if not body.parts:
        raise HTTPException(400, "No parts to complete.")
    await run_in_threadpool(minio_svc.complete_multipart, body.s3_key, body.upload_id,
                            [p.model_dump() for p in body.parts])
    return {"s3_key": body.s3_key}


@router.post("/upload/multipart/abort")
async def multipart_abort(body: MultipartComplete, user: User = Depends(require_scope("data:write"))):
    """Discard a cancelled/failed chunked upload (frees the staged parts)."""
    from ...services import minio as minio_svc
    if not (body.s3_key or "").startswith(f"vectors/{user.id}/"):
        raise HTTPException(400, "Invalid storage key.")
    await run_in_threadpool(minio_svc.abort_multipart, body.s3_key, body.upload_id)
    return {"ok": True}


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
    user: User = Depends(require_scope("data:write")),
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
    user: User = Depends(require_scope("data:write")),
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
async def job_status(job_id: str, db: AsyncSession = Depends(get_db), user: User = Depends(require_scope("data:read"))):
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
    user: User = Depends(require_scope("data:read")),
    db: AsyncSession = Depends(get_db),
):
    """Authed viewport query for the editor preview's deck.gl overlay."""
    result = await db.execute(
        select(VectorLayer).where(VectorLayer.id == layer_id, visible_to(user, VectorLayer)))
    return await _viewport_geojson(result.scalar_one_or_none(), bbox, limit)


@router.get("/{layer_ref}/features.arrow")
async def vector_features_arrow(
    layer_ref: str,
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
    result = await db.execute(select(VectorLayer).where(by_ref(VectorLayer, layer_ref)))
    layer = result.scalar_one_or_none()
    if not await _publicly_readable(layer, db):
        raise HTTPException(404, "Layer not found.")
    if layer.storage_backend != "geoparquet" or not layer.s3_key:
        raise HTTPException(400, "This layer is not a GeoParquet (file-backed) layer.")
    body = await run_in_threadpool(
        duckdb_engine.query_features_arrow, layer.s3_key, _parse_bbox(bbox),
        max(1, min(limit, 200000)))
    if body is None:
        return Response(status_code=204)
    return Response(content=body, media_type="application/vnd.apache.arrow.stream")


@router.get("/{layer_ref}/features.geojson")
async def vector_features_public(
    layer_ref: str,
    bbox: str | None = None,
    limit: int = 50000,
    db: AsyncSession = Depends(get_db),
):
    """PUBLIC viewport query for the deck.gl overlay in published (unauthenticated) portals. Public
    by layer id, mirroring the `/pmtiles` range proxy's posture: published portals are public, the
    caller can only address a DB row by id (not arbitrary keys), and bucket creds stay server-side.
    (Single-admin self-hosted assumption — for multi-tenant cloud this needs portal scoping + auth,
    same open question as the rest of the public-portal surface; see notes §0h-addendum.)"""
    result = await db.execute(select(VectorLayer).where(by_ref(VectorLayer, layer_ref)))
    layer = result.scalar_one_or_none()
    if not await _publicly_readable(layer, db):
        raise HTTPException(404, "Layer not found.")
    return await _viewport_geojson(layer, bbox, limit)


async def _legend_readable(layer, request: Request, db: AsyncSession) -> bool:
    """Public, or visible to whoever is asking.

    `_publicly_readable` alone was wrong here in a way live testing found immediately: the OWNER of
    an organization layer, holding a valid token, got a 404 for the legend of their own data. Every
    other per-layer artifact is a rendering the public can already see, so public-only is right for
    them; a legend is metadata a signed-in user reads in the dashboard, and the plugin asks for it
    with exactly the credential the dashboard uses.
    """
    if layer is None:
        return False
    if await _publicly_readable(layer, db):
        return True
    user = await resolve_optional_user(request, db)
    if user is None:
        return False
    # Re-query THROUGH the visibility filter rather than re-deciding what it means here — A-02 lives
    # in `visible_to`, and a second copy would drift from it.
    allowed = await db.execute(
        select(VectorLayer.id).where(VectorLayer.id == layer.id, visible_to(user, VectorLayer)))
    return allowed.scalar_one_or_none() is not None


@router.get("/{layer_ref}/legend")
async def vector_legend(layer_ref: str, request: Request, db: AsyncSession = Depends(get_db)):
    """PUBLIC legend for a vector layer's default style — the swatches and labels, computed once.

    `services.symbology.legend_entries` already decides what a legend shows, and the portal and the
    About page read it. Nothing exposed it, so any OTHER renderer — the QGIS plugin above all — had
    to re-derive class labels from `default_style` and would eventually disagree with the map about
    where a break falls or how a number is rounded. Same argument as `/field-stats`: the client asks
    rather than recomputes.

    Single-symbol layers get a one-entry legend here (using the layer's own name), where
    `legend_entries` returns `[]` — a caller drawing a legend still needs a swatch, and inventing
    one per renderer is how they drift.
    """
    from ...services import symbology

    result = await db.execute(select(VectorLayer).where(by_ref(VectorLayer, layer_ref)))
    layer = result.scalar_one_or_none()
    if not await _legend_readable(layer, request, db):
        raise HTTPException(404, "Layer not found.")

    style = {}
    if layer.default_style:
        try:
            style = json.loads(layer.default_style).get("style") or {}
        except ValueError:
            style = {}
    entries = symbology.legend_entries(style)
    mode = style.get("color_mode") or "single"
    if not entries:
        entries = [{"color": style.get("color") or symbology.DEFAULT_COLOR, "label": layer.name}]
    return {
        "layer": layer.name,
        "ref": layer.uid or str(layer.id),
        "kind": "vector",
        "geometry_type": layer.geometry_type,
        "color_mode": mode,
        "field": style.get("color_field") if mode != "single" else None,
        "entries": entries,
        # Size-from-field is a second visual dimension a legend may want to show separately.
        "size": ({"field": style.get("size_field"), "stops": style.get("size_stops")}
                 if style.get("size_field") and style.get("size_stops") else None),
    }


@router.get("/{layer_ref}/tilejson")
async def vector_tilejson(layer_ref: str, request: Request, db: AsyncSession = Depends(get_db)):
    """PUBLIC TileJSON (3.0.0) for a PostGIS vector layer — the ONE URL other tools add directly as a
    vector-tile source (QGIS 'Vector Tiles' → from TileJSON, MapLibre/GeoLibre vector source, deck.gl).
    It carries the absolute {z}/{x}/{y} tile URL (https-aware), bounds, and the `vector_layers` entry
    with the source-layer name + fields — so the consumer doesn't have to know any of that. CORS-open
    (public metadata), like /tiles/."""
    from ...json_safe import SafeJSONResponse

    result = await db.execute(select(VectorLayer).where(by_ref(VectorLayer, layer_ref)))
    layer = result.scalar_one_or_none()
    if not await _publicly_readable(layer, db):
        raise HTTPException(404, "Layer not found.")

    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = request.headers.get("host") or request.url.netloc
    base = f"{proto}://{host}"
    fields = {}
    if layer.columns:
        try:
            for c in json.loads(layer.columns):
                fields[c["name"]] = c.get("type", "String")
        except (ValueError, KeyError, TypeError):
            pass

    if getattr(layer, "storage_backend", "postgis") != "postgis":
        # A TILED GeoParquet layer has a TileJSON too, pointing at the per-tile reader above. Until
        # this existed the only vector-tile answer for these layers was "open the whole archive",
        # which is the slow path this endpoint exists to replace — and the zoom range and bounds
        # below are read from the archive itself rather than guessed, so a client knows exactly
        # which tiles are worth asking for.
        from ...services import pmtiles_reader

        key = await _pmtiles_key(layer_ref, db)
        if not key:
            raise HTTPException(404, "No vector-tile TileJSON for this layer — it is file-backed "
                                     "and not tiled yet. Tile it in My Data, or use the GeoParquet "
                                     "asset.")
        try:
            fetch = _pmtiles_fetch(key)
            header, _ = await run_in_threadpool(pmtiles_reader.open_archive, key, fetch)
            meta = await run_in_threadpool(pmtiles_reader.metadata, key, fetch)
        except Exception as exc:      # noqa: BLE001 - an unreadable archive is a missing TileJSON
            raise HTTPException(404, f"Tiles unreadable: {exc}")
        # tippecanoe names the layer inside the tiles; a consumer that guesses it draws nothing.
        vector_layers = meta.get("vector_layers") or []
        src = (vector_layers[0].get("id") if vector_layers else None) or "geodeploy"
        from ...services.share_links import public_ref
        api = f"{base}/api/data/vector/{public_ref(layer)}"
        tj = {
            "tilejson": "3.0.0",
            "name": layer.name,
            "scheme": "xyz",
            "tiles": [f"{api}/tiles/{{z}}/{{x}}/{{y}}"],
            "minzoom": header.min_zoom,
            "maxzoom": header.max_zoom,
            "bounds": header.bounds,
            "center": [(header.min_lon + header.max_lon) / 2,
                       (header.min_lat + header.max_lat) / 2, 0],
            "vector_layers": vector_layers or [{"id": src, "fields": fields}],
        }
        return SafeJSONResponse(tj, headers={"Access-Control-Allow-Origin": "*",
                                             "Cache-Control": "public, max-age=300"})

    src = f"{layer.schema_name}.{layer.table_name}"
    tj = {
        "tilejson": "3.0.0",
        "name": layer.name,
        "scheme": "xyz",
        "tiles": [f"{base}/tiles/{src}/{{z}}/{{x}}/{{y}}"],
        "minzoom": 0,
        # NOT 22. Martin builds these tiles live from PostGIS, so it will answer at any zoom — but a
        # client that believes tiles exist at z22 requests a fresh one at every zoom step past the
        # point where they stop adding detail, and gets an empty tile back. Declaring the depth
        # where the data is genuinely resolved lets QGIS and MapLibre OVER-ZOOM the last real tile
        # instead: same picture, no request. Vector tiles scale without pixelating, which is what
        # makes this safe.
        "maxzoom": 18,
        "vector_layers": [{"id": src, "fields": fields}],
    }
    bbox = json.loads(layer.bbox) if layer.bbox else None
    if bbox:
        tj["bounds"] = bbox
        tj["center"] = [(bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2, 0]
    return SafeJSONResponse(tj, headers={"Access-Control-Allow-Origin": "*",
                                     "Cache-Control": "public, max-age=300"})


@router.get("/{layer_id}/links")
async def vector_share_links(layer_id: int, request: Request,
                             user: User = Depends(require_scope("data:read")),
                             db: AsyncSession = Depends(get_db)):
    """The "Share links" panel feed: every tool-ready URL for this layer (TileJSON / PMTiles /
    GeoJSON / GeoArrow / manifest / STAC), each labelled with the tools it is meant for. Authed —
    it is layer metadata — but the URLs only RESOLVE for a publicly-readable layer (`public` flag
    below), so the UI can prompt for a Public share first."""
    result = await db.execute(
        select(VectorLayer).where(VectorLayer.id == layer_id, visible_to(user, VectorLayer)))
    layer = result.scalar_one_or_none()
    if not layer:
        raise HTTPException(404, "Layer not found.")
    if layer.status != "ready":
        raise HTTPException(409, "Layer is not ready yet.")
    from ...services import share_links
    base = share_links.request_base(request)
    return {"public": bool(layer.is_public), "name": layer.name,
            "catalog": f"{base}/api/stac",
            "links": share_links.vector_links(layer, base)}


@router.get("/{layer_ref}/identify")
async def vector_identify(
    layer_ref: str,
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
    result = await db.execute(select(VectorLayer).where(by_ref(VectorLayer, layer_ref)))
    layer = result.scalar_one_or_none()
    if not await _publicly_readable(layer, db):
        raise HTTPException(404, "Layer not found.")
    if layer.storage_backend != "geoparquet" or not layer.s3_key:
        raise HTTPException(400, "This layer is not a GeoParquet (file-backed) layer.")
    tol = min(max(float(tol), 1e-7), 1.0)
    feats = await run_in_threadpool(
        duckdb_engine.query_features_at_point, layer.s3_key, lng, lat, tol,
        max(1, min(int(limit), 25)))
    return {"features": feats}


# ── V-16 Dashboard: server-side summarisation ────────────────────────────────────────────────
# Indicators, gauges, charts, tables, selectors and the map's feature-click read through these
# four endpoints. They are
# PUBLIC-by-id with exactly the posture `features.geojson` above already takes — a published
# dashboard is browsed anonymously, and a widget that cannot answer without a login would make
# every dashboard a members-only page. `_publicly_readable` is the gate: shared, or drawn by a
# published portal, or 404.
#
# The summarisation itself lives in `services/aggregate.py`, which chooses the engine from the
# layer's own backend (PostGIS in PostGIS, GeoParquet in DuckDB) — see that module on why that is
# the fastest route rather than the conventional one.

class AttrFilter(BaseModel):
    """One attribute predicate published by a source widget. `op`: in | eq | between | gte | lte |
    daterange | notnull. Several combine with AND — that IS the cross-filter semantics (a selector
    and a map selection both active narrow the result; they do not replace one another)."""
    field: str
    op: str = "in"
    values: list[Any] | None = None
    value: Any | None = None
    min: Any | None = None
    max: Any | None = None
    date: bool = False


class AggregateRequest(BaseModel):
    op: str = "count"                     # count | sum | avg | min | max
    field: str | None = None              # required unless op == count
    groupBy: str | None = None            # a category field, or a date field with timeBucket
    timeBucket: str | None = None         # hour | day | week | month | quarter | year
    limit: int | None = None
    sort: str | None = None               # value_desc | value_asc | key_asc
    # SEVERAL measures against one grouping — "average height AND average age per district". Each
    # entry is {op, field, label}. Absent, the single op/field above is used, which is what every
    # chart authored before this sends.
    series: list[dict[str, Any]] | None = None
    # A declared relation letting a filter from ANOTHER layer reach this one. {layerId, leftField,
    # rightField, filters}. The server resolves `layerId` itself — the client never names a table.
    join: dict[str, Any] | None = None
    filters: list[AttrFilter] | None = None
    # The GEOMETRY filter — a clicked feature, a drawn polygon or a drawn bbox, already normalised
    # to one GeoJSON geometry in EPSG:4326 by the dashboard's filter bus. Carried separately from
    # `filters` because it is a different kind of thing: it has no field and no value.
    geometry: dict[str, Any] | None = None


class ScatterRequest(BaseModel):
    """Y against X, per feature. Sampled server-side — see `services/aggregate.parquet_scatter`."""
    xField: str | None = None
    yField: str | None = None
    limit: int | None = None
    filters: list[AttrFilter] | None = None
    geometry: dict[str, Any] | None = None


class ProfileRequest(BaseModel):
    """What the column-profile widget asks: describe these columns, over the current selection."""
    fields: list[str] = []
    topN: int | None = None               # values a categorical column lists (default 5)
    filters: list[AttrFilter] | None = None
    geometry: dict[str, Any] | None = None


class TableRequest(BaseModel):
    fields: list[str] | None = None
    filters: list[AttrFilter] | None = None
    geometry: dict[str, Any] | None = None
    sort: str | None = None
    dir: str | None = None
    limit: int | None = None
    offset: int | None = None
    # TEXT SEARCH rides on this request rather than having an endpoint of its own: a search result IS
    # a table row — same columns, same per-row bbox for click-to-zoom, same paging. A second endpoint
    # would be a second place to build a row and a second place to get the public-readability check
    # right. `searchFields` are validated against the layer's catalog in the service.
    search: str | None = None
    searchFields: list[str] | None = None
    searchMode: str | None = None          # contains (default) | prefix
    join: dict[str, Any] | None = None
    # Skips the COUNT(*) over the predicate, which is a second full pass and which also defeats the
    # LIMIT short-circuit. A search box wants the first few matches, not a census; a table's pager
    # needs the number, so it leaves this alone.
    withTotal: bool | None = None


async def _public_vector(layer_ref: str, db: AsyncSession) -> VectorLayer:
    result = await db.execute(select(VectorLayer).where(by_ref(VectorLayer, layer_ref)))
    layer = result.scalar_one_or_none()
    if not await _publicly_readable(layer, db):
        raise HTTPException(404, "Layer not found.")
    if layer.status != "ready":
        raise HTTPException(409, "Layer is not ready yet.")
    return layer


async def _resolve_join(spec: dict, layer: VectorLayer, db: AsyncSession) -> str | None:
    """Turn `spec['join'].layerId` into the LAYER, in place. Returns a note when the join cannot be
    honoured, in which case the join is removed rather than half-applied.

    The client sends an id, never a table name: the join reads another layer, so it has to pass the
    same public-readability check as reading that layer directly — otherwise a relation would be a
    way to query a layer you cannot open.

    A cross-engine pair is refused HERE rather than deep in the query builder, because refusing
    early is what lets the answer carry a reason. A number that quietly ignored half its filter is
    the worst outcome available.
    """
    j = spec.get("join")
    if not isinstance(j, dict):
        return None
    spec.pop("join", None)
    try:
        other_id = int(j.get("layerId"))
    except (TypeError, ValueError):
        return None
    if other_id == layer.id:
        return None                      # a layer joined to itself is a filter, not a relation
    result = await db.execute(select(VectorLayer).where(VectorLayer.id == other_id))
    other = result.scalar_one_or_none()
    if not other or not await _publicly_readable(other, db):
        return "the related layer is not available"
    if other.status != "ready":
        return "the related layer is still processing"
    left = (other.storage_backend or "postgis") == "geoparquet"
    right = (layer.storage_backend or "postgis") == "geoparquet"
    if left != right:
        return ("these two layers are stored differently (one file-backed, one in the database), "
                "so a filter cannot travel between them yet")
    spec["join"] = {"layer": other, "leftField": j.get("leftField"),
                    "rightField": j.get("rightField"), "filters": j.get("filters")}
    return None


def _spec(model: BaseModel) -> dict:
    """A request model → the plain dict `services/aggregate` takes. `exclude_none` is deliberate:
    the service distinguishes "absent" from "explicitly null" for `groupBy` and `sort`."""
    return model.model_dump(exclude_none=True)


@router.post("/{layer_ref}/scatter")
async def vector_scatter(layer_ref: str, req: ScatterRequest,
                         db: AsyncSession = Depends(get_db)):
    """PUBLIC X/Y sample for the dashboard's scatter widget.

    Returns `{points:[[x,y],…], x, y, total, sampled}`. The points are a RANDOM sample, not the first
    N: a prepped GeoParquet layer is stored in spatial partitions, so the first N rows are one corner
    of the map and a plot drawn from them would state a relationship that only holds there.
    """
    from ...services import aggregate as agg
    layer = await _public_vector(layer_ref, db)
    spec = _spec(req)
    try:
        if layer.storage_backend == "geoparquet":
            return await run_in_threadpool(agg.parquet_scatter, layer, spec)
        return await agg.postgis_scatter(db, layer, spec)
    except agg.AggregateError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, f"Could not plot this layer: {exc}") from exc


@router.post("/{layer_ref}/profile")
async def vector_profile(layer_ref: str, req: ProfileRequest,
                         db: AsyncSession = Depends(get_db)):
    """PUBLIC column profile for the dashboard's profile widget.

    Returns `{total, fields:[{field, kind, count, nulls, distinct, …}]}` — a numeric column carries
    min/max/avg/median, a categorical one carries `top:[{value, count}]`. Narrowed by the same
    filters and geometry as every other widget, so the panel describes what is currently selected
    rather than the whole layer.
    """
    from ...services import aggregate as agg
    layer = await _public_vector(layer_ref, db)
    spec = _spec(req)
    try:
        if layer.storage_backend == "geoparquet":
            return await run_in_threadpool(agg.parquet_profile, layer, spec)
        return await agg.postgis_profile(db, layer, spec)
    except agg.AggregateError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, f"Could not describe this layer: {exc}") from exc


@router.post("/{layer_ref}/aggregate")
async def vector_aggregate(layer_ref: str, req: AggregateRequest,
                           db: AsyncSession = Depends(get_db)):
    """PUBLIC aggregation for the dashboard's indicator / gauge / chart widgets.

    Returns either `{op, value, count}` (no groupBy) or `{groups:[{key, value, count}], truncated}`.
    One number per widget per filter change, instead of a feature set the browser would have to
    reduce — see `services/aggregate.py`.
    """
    from ...services import aggregate as agg
    layer = await _public_vector(layer_ref, db)
    spec = _spec(req)
    join_note = await _resolve_join(spec, layer, db)
    try:
        if layer.storage_backend == "geoparquet":
            out = await run_in_threadpool(agg.parquet_aggregate, layer, spec)
        else:
            out = await agg.postgis_aggregate(db, layer, spec)
        # A refused join travels WITH the answer. The number is real — it simply was not narrowed by
        # the other layer's filter, and the widget says so rather than looking like it agrees with
        # the chart that published it.
        if join_note and isinstance(out, dict):
            out = dict(out, joinNote=join_note)
        return out
    except agg.AggregateError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, f"Could not summarise this layer: {exc}") from exc


@router.post("/{layer_ref}/table")
async def vector_table(layer_ref: str, req: TableRequest, db: AsyncSession = Depends(get_db)):
    """PUBLIC attribute rows for the dashboard's list/table and details widgets.

    Each row carries a lon/lat `bbox` computed server-side, so clicking a row zooms and highlights
    the map without a second request — click-to-zoom is the widget's whole purpose and a round trip
    per click reads as a broken control.
    """
    from ...services import aggregate as agg
    layer = await _public_vector(layer_ref, db)
    spec = _spec(req)
    join_note = await _resolve_join(spec, layer, db)
    try:
        if layer.storage_backend == "geoparquet":
            out = await run_in_threadpool(agg.parquet_table, layer, spec)
        else:
            out = await agg.postgis_table(db, layer, spec)
        if join_note and isinstance(out, dict):
            out = dict(out, joinNote=join_note)
        return out
    except agg.AggregateError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, f"Could not read this layer: {exc}") from exc


class PickRequest(BaseModel):
    """A click on the dashboard's map widget."""
    lng: float
    lat: float
    # Hit tolerance in degrees. A polygon layer needs none; a point or line layer needs one, because
    # an exact intersection with a click almost never happens. The client scales it by zoom.
    tol: float = 0.0


@router.post("/{layer_ref}/pick")
async def vector_pick(layer_ref: str, req: PickRequest, db: AsyncSession = Depends(get_db)):
    """PUBLIC: the EXACT geometry (EPSG:4326) and attributes of the feature under a clicked point.

    Why this exists rather than reading the click off the map: `queryRenderedFeatures` returns
    geometry clipped to the vector TILE the click landed in. Feeding that to zonal statistics would
    compute a parcel's elevation over whichever fragment of it happened to be in that tile and
    report the number as the parcel's — a wrong answer with no visible symptom. So the geometry that
    drives a spatial filter comes from the layer, not from the renderer.

    Both backends: PostGIS resolves it with an indexed ST_Intersects, GeoParquet with a
    covering-bbox prune plus an exact shapely test (there is no spatial extension on the DuckDB read
    path — see `services/aggregate.py`).
    """
    from ...services import aggregate as agg
    layer = await _public_vector(layer_ref, db)
    tol = min(max(float(req.tol or 0.0), 0.0), 1.0)
    try:
        if layer.storage_backend == "geoparquet":
            hit = await run_in_threadpool(agg.parquet_pick, layer, req.lng, req.lat, tol)
        else:
            hit = await agg.postgis_pick(db, layer, req.lng, req.lat, tol)
    except agg.AggregateError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, f"Could not read this layer: {exc}") from exc
    if not hit:
        # 204, not 404: "nothing is under your click" is a normal outcome of clicking a map, and a
        # 404 in the console on every empty click reads as a broken portal.
        return Response(status_code=204)
    return hit


@router.get("/{layer_ref}/distinct")
async def vector_distinct(layer_ref: str, field: str, limit: int = 200,
                          db: AsyncSession = Depends(get_db)):
    """PUBLIC option list for a dashboard selector: the distinct values of a text field (by
    frequency), or the min/max of a numeric or date field.

    Separate from `/field-stats`, which answers the SYMBOLOGY question (a classification with
    breaks and colours) for a signed-in editor. This one answers "what can a visitor pick", is
    anonymous, and returns no classification — the two would otherwise drift into one endpoint
    serving two audiences with two trust levels.
    """
    from ...services import aggregate as agg
    layer = await _public_vector(layer_ref, db)
    limit = max(5, min(int(limit), agg.DISTINCT_LIMIT))
    try:
        if layer.storage_backend == "geoparquet":
            return await run_in_threadpool(agg.parquet_distinct, layer, field, limit)
        return await agg.postgis_distinct(db, layer, field, limit)
    except agg.AggregateError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, f"Could not read this field: {exc}") from exc


@router.post("/{layer_id}/tile", response_model=VectorLayerOut)
async def tile_layer(
    layer_id: int,
    cluster: bool | None = None,
    user: User = Depends(require_scope("data:write")),
    db: AsyncSession = Depends(get_db),
):
    """(Re)generate the PMTiles archive for a GeoParquet layer — used to tile a file uploaded
    before tiling existed, or to retry after an error.

    `cluster` sets low-zoom POINT clustering for this and future tilings. It belongs on this action
    rather than on a settings form because it is baked into the archive by tippecanoe: changing it
    without re-tiling would change nothing, and the two are therefore one decision. Omitted leaves
    the layer's current setting alone, so a plain retry does not silently change how it draws."""
    from ...tasks.pmtiles_tile import tile_geoparquet
    result = await db.execute(
        select(VectorLayer).where(VectorLayer.id == layer_id, visible_to(user, VectorLayer)))
    layer = result.scalar_one_or_none()
    if not layer:
        raise HTTPException(404, "Layer not found.")
    if layer.storage_backend != "geoparquet" or not layer.s3_key:
        raise HTTPException(400, "This layer is not a GeoParquet (file-backed) layer.")

    pmtiles_key = (layer.s3_key.rsplit(".", 1)[0] if "." in layer.s3_key else layer.s3_key) + ".pmtiles"
    if cluster is not None:
        # Persisted BEFORE the task is queued: the worker reads the flag from the database (the task
        # is queued from several places and passing it per call would let them disagree), so a commit
        # that landed after the queue would tile with the previous setting.
        layer.cluster_points = bool(cluster)
    layer.tile_status = "tiling"
    await db.commit()
    await db.refresh(layer)
    tile_geoparquet.delay(layer.id, layer.s3_key, pmtiles_key)
    return VectorLayerOut.from_orm_json(layer)


@router.post("/{layer_id}/prepare", response_model=VectorLayerOut)
async def prepare_layer(
    layer_id: int,
    user: User = Depends(require_scope("data:write")),
    db: AsyncSession = Depends(get_db),
):
    """Spatially prepare a GeoParquet layer: rewrite it Z-order-sorted with a GeoParquet 1.1 bbox
    covering column so DuckDB prunes row-groups on a bbox filter (fast analysis + viewport display).
    Idempotent — overwrites the object in place. The file stays GeoParquet (no PostGIS, no PMTiles)."""
    from ...tasks.geoparquet_prep import prepare_geoparquet
    result = await db.execute(
        select(VectorLayer).where(VectorLayer.id == layer_id, visible_to(user, VectorLayer)))
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


def _qi(ident: str) -> str:
    """Quote a SQL identifier — neither driver can parameterise one, and a column name here comes
    from a request. Doubling embedded quotes is the whole defence and it is sufficient: the result
    is a single quoted identifier that cannot terminate early."""
    return '"' + str(ident).replace('"', '""') + '"'


@router.get("/{layer_ref}/field-stats")
async def field_stats(
    layer_ref: str,
    field: str,
    classes: int = 5,
    method: str = "quantile",
    ramp: str = "viridis",
    reverse: bool = False,
    user: User = Depends(require_scope("data:read")),
    db: AsyncSession = Depends(get_db),
):
    """Everything the symbology editor needs to classify ONE attribute of a layer.

    Returns the distribution AND a ready-made `suggestion` (classes for a numeric field, categories
    for a text one), so choosing a field produces a styled map in one round trip rather than three.
    The suggestion is computed by `services/symbology`, the same module the renderers use, so what
    the editor previews is what the published portal draws.

    Both storage backends, because a layer's backend is an implementation detail the person styling
    it should never have to think about: PostGIS layers are queried with SQL, GeoParquet layers
    through DuckDB (`duckdb_engine.field_stats`).

    NUMERIC columns return sampled raw values rather than pre-computed breaks — the classifier must
    be one implementation, or the editor and the portal would disagree about which class a feature
    is in. TEXT columns return distinct values by frequency, capped, so a 400-value column produces
    a legend rather than a wall.
    """
    from ...services import symbology

    result = await db.execute(
        select(VectorLayer).where(by_ref(VectorLayer, layer_ref), visible_to(user, VectorLayer)))
    layer = result.scalar_one_or_none()
    if not layer:
        raise HTTPException(404, "Layer not found.")
    if not (field or "").strip():
        raise HTTPException(400, "A field name is required.")

    # The field must be one the layer actually has. This is an allow-list check, not only a
    # nicety: `field` reaches a SQL identifier below, and the catalog is the authority on what
    # columns exist. (`_qi` quotes it as well — both, deliberately.)
    known = {c.get("name") for c in json.loads(layer.columns or "[]") if isinstance(c, dict)}
    if known and field not in known:
        raise HTTPException(400, f"No such field on this layer: {field}")

    classes = max(2, min(int(classes), 12))
    if method not in ("quantile", "equal", "jenks"):
        raise HTTPException(400, "method must be quantile, equal or jenks.")

    if layer.storage_backend == "geoparquet":
        if not layer.s3_key:
            raise HTTPException(400, "This layer has no data file yet.")
        from ...services import duckdb_engine
        try:
            stats = await run_in_threadpool(duckdb_engine.field_stats, layer.s3_key, field)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except Exception as exc:
            raise HTTPException(502, f"Could not read the field: {exc}") from exc
    else:
        stats = await _postgis_field_stats(db, layer, field)

    if stats.get("kind") == "numeric":
        stats["suggestion"] = {
            "color_mode": "graduated",
            "color_ramp_reverse": reverse,   # echoed so a client stores the direction it asked for
            "classes": symbology.build_classes(stats.get("values") or [], method, classes, ramp,
                                               reverse),
        }
        # The raw sample is for the classifier, not for the browser: tens of thousands of numbers
        # would be the bulk of this response and the UI never reads them.
        stats["values"] = None
    else:
        stats["suggestion"] = {
            "color_mode": "categorized",
            "color_ramp_reverse": reverse,
            "categories": symbology.build_categories(
                [c["value"] for c in stats.get("categories") or []], reverse=reverse),
        }
    return stats


async def _postgis_field_stats(db: AsyncSession, layer, field: str, sample: int = 100_000,
                               distinct_limit: int = 60) -> dict:
    """The PostGIS half of `field_stats`. Same contract as `duckdb_engine.field_stats`.

    Sampled with a LIMIT for the reason given there — class breaks are a display decision, and one
    an editor is waiting on. The numeric test asks `information_schema` rather than trying a cast:
    a failed cast on one bad row would lose the whole column.
    """
    from sqlalchemy import text

    schema, table = layer.schema_name, layer.table_name
    if not schema or not table:
        raise HTTPException(400, "This layer has no table.")

    dtype_row = (await db.execute(text(
        "SELECT data_type FROM information_schema.columns "
        "WHERE table_schema = :s AND table_name = :t AND column_name = :c"
    ), {"s": schema, "t": table, "c": field})).first()
    if not dtype_row:
        raise HTTPException(400, f"No such field on this layer: {field}")
    dtype = (dtype_row[0] or "").lower()
    numeric = any(k in dtype for k in
                  ("int", "numeric", "decimal", "double", "real", "float", "serial"))

    q = f"{_qi(schema)}.{_qi(table)}"
    col = _qi(field)
    if numeric:
        rows = (await db.execute(text(
            f"SELECT {col}::double precision FROM {q} WHERE {col} IS NOT NULL LIMIT {sample}"
        ))).fetchall()
        values = [float(r[0]) for r in rows if r[0] is not None]
        if not values:
            return {"kind": "numeric", "count": 0, "min": None, "max": None, "values": []}
        return {"kind": "numeric", "count": len(values), "sampled": len(values) >= sample,
                "min": min(values), "max": max(values), "values": values}

    rows = (await db.execute(text(
        f"SELECT {col}::text AS v, COUNT(*) AS n FROM {q} WHERE {col} IS NOT NULL "
        f"GROUP BY 1 ORDER BY n DESC LIMIT {distinct_limit + 1}"
    ))).fetchall()
    cats = [{"value": r[0], "count": int(r[1])} for r in rows[:distinct_limit]]
    return {"kind": "categorical", "count": len(cats),
            "truncated": len(rows) > distinct_limit, "categories": cats}


@router.post("/{layer_id}/reprocess", response_model=JobStatus, status_code=202)
async def reprocess_layer(
    layer_id: int,
    user: User = Depends(require_scope("data:write")),
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
    result = await db.execute(
        select(VectorLayer).where(VectorLayer.id == layer_id, visible_to(user, VectorLayer)))
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
# Keyed by the REF THE CALLER USED (uid or legacy id) rather than the row id, so the cache still
# answers without a DB round-trip whichever form the URL carries.
_PMTILES_KEY_CACHE: dict[str, str] = {}


async def _pmtiles_key(layer_ref: str, db: AsyncSession) -> str | None:
    cached = _PMTILES_KEY_CACHE.get(layer_ref)
    if cached is not None:
        return cached
    result = await db.execute(select(VectorLayer).where(by_ref(VectorLayer, layer_ref)))
    layer = result.scalar_one_or_none()
    if not layer or layer.storage_backend != "geoparquet" or not layer.pmtiles_key:
        return None
    if not await _publicly_readable(layer, db):  # private layer, not in any published portal
        return None
    _PMTILES_KEY_CACHE[layer_ref] = layer.pmtiles_key
    return layer.pmtiles_key


# ── Whole-layer / clipped download ────────────────────────────────────────────────────────────
# PUBLIC on the same terms as the other per-layer artifacts (`_publicly_readable`): a layer that is
# shared, or shown by a published portal. See routers/data/exports.py for why this exists at all —
# a PostGIS vector layer was the one thing an outside client could not simply download.

@router.post("/{layer_ref}/export", status_code=202)
async def start_vector_export(layer_ref: str, req: exports.LayerExportRequest,
                              db: AsyncSession = Depends(get_db)):
    """Queue a download of this layer. No bbox = the whole layer; a bbox clips it."""
    result = await db.execute(select(VectorLayer).where(by_ref(VectorLayer, layer_ref)))
    layer = result.scalar_one_or_none()
    if not layer or layer.status != "ready" or not await _publicly_readable(layer, db):
        raise HTTPException(404, "Layer not found.")
    bbox = exports.validate_bbox(req.bbox)
    return exports.start(exports.vector_item(layer, req.format), bbox, req.target_crs)


@router.get("/{layer_ref}/export-status/{job_id}")
async def vector_export_status(layer_ref: str, job_id: str):
    return exports.status(job_id)


@router.get("/{layer_ref}/export-download/{job_id}")
async def vector_export_download(layer_ref: str, job_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(VectorLayer).where(by_ref(VectorLayer, layer_ref)))
    layer = result.scalar_one_or_none()
    name = slugify(layer.name, separator="_") if layer else "export"
    return exports.download(job_id, "{0}.zip".format(name or "export"))


@router.get("/{layer_ref}/pmtiles")
async def vector_pmtiles(layer_ref: str, request: Request, db: AsyncSession = Depends(get_db)):
    """PUBLIC range proxy for a GeoParquet layer's PMTiles archive — MapLibre's pmtiles protocol
    streams the tiles via HTTP Range requests. Public like Martin vector tiles (`/tiles/`), since
    published portals are unauthenticated; same-origin so no CORS, and the bucket creds stay
    server-side. The DB row is the only thing the caller can address (by uid/id), not arbitrary
    keys."""
    key = await _pmtiles_key(layer_ref, db)
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
        _PMTILES_KEY_CACHE.pop(layer_ref, None)  # stale key (e.g. a re-prep) — re-read next request
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


def _pmtiles_fetch(key: str):
    """A `fetch(offset, length) -> bytes` over the archive in object storage. SYNCHRONOUS —
    `pmtiles_reader` is plain code, so the caller runs the whole lookup in a threadpool rather than
    bouncing between loops for each of its one or two range reads."""
    settings = get_settings()
    from ...services.minio import get_s3_client
    s3 = get_s3_client()

    def fetch(offset: int, length: int) -> bytes:
        obj = s3.get_object(Bucket=settings.storage_bucket, Key=key,
                            Range=f"bytes={offset}-{offset + length - 1}")
        return obj["Body"].read()
    return fetch


@router.get("/{layer_ref}/tiles/{z}/{x}/{y}")
async def vector_pmtiles_tile(layer_ref: str, z: int, x: int, y: int,
                              db: AsyncSession = Depends(get_db)):
    """PUBLIC XYZ vector tiles for a tiled GeoParquet layer, read a tile at a time out of its
    PMTiles archive.

    THE POINT: a `{z}/{x}/{y}` URL is viewport-driven, and the archive is not. Handed the whole
    archive, GDAL's PMTiles driver walks every tile at the deepest zoom just to answer "how many
    features?" — on this project's own instance a FIVE-FEATURE layer tiles to 2.17 million entries,
    which is why QGIS hung on small layers. Here the client asks for the four tiles under its
    viewport and the server does one range read each (the header and root directory are cached).

    Same public terms as `/pmtiles` and Martin's `/tiles/` — a published portal is unauthenticated,
    so its display sources must be too. 204 for a tile the archive does not contain: sparse is
    normal, and a 404 invites clients to retry.
    """
    from ...services import pmtiles_reader

    key = await _pmtiles_key(layer_ref, db)
    if not key:
        raise HTTPException(404, "No tiles for this layer.")
    try:
        got = await run_in_threadpool(
            pmtiles_reader.get_tile, key, _pmtiles_fetch(key), z, x, y)
    except pmtiles_reader.PMTilesError as exc:
        raise HTTPException(404, f"Tiles unreadable: {exc}")
    except Exception:
        # A re-tile repoints the layer at a new key; the cached one 404s at storage. Drop both
        # caches so the next request re-reads, exactly as `/pmtiles` does.
        _PMTILES_KEY_CACHE.pop(layer_ref, None)
        pmtiles_reader.forget(key)
        raise HTTPException(404, "Tiles not found.")

    headers = {"Access-Control-Allow-Origin": "*",
               "Cache-Control": "public, max-age=3600"}
    if got is None:
        return Response(status_code=204, headers=headers)
    body, header = got
    return Response(body, media_type=header.media_type, headers=headers)


# layer_id → s3_key prefix for the parquet range proxy. Cached because duckdb-wasm issues MANY
# small range requests per map pan and an ORM SELECT per request starved the api. A re-prep
# repoints s3_key to a NEW parts-<hex> prefix — handled by the miss-refresh in the route (a
# stale prefix's objects are deleted, so the fetch fails, the entry is dropped and re-read).
_PARQUET_PREFIX_CACHE: dict[str, str] = {}   # keyed by the caller's ref (uid or legacy id)


async def _parquet_prefix(layer_ref: str, db: AsyncSession) -> str | None:
    cached = _PARQUET_PREFIX_CACHE.get(layer_ref)
    if cached is not None:
        return cached
    result = await db.execute(select(VectorLayer).where(by_ref(VectorLayer, layer_ref)))
    layer = result.scalar_one_or_none()
    if (not layer or layer.storage_backend != "geoparquet" or not layer.s3_key
            or layer.s3_key.rstrip("/").endswith(".parquet")):  # unprepped single file: no manifest
        return None
    if not await _publicly_readable(layer, db):  # private layer, not in any published portal
        return None
    prefix = layer.s3_key.rstrip("/")
    _PARQUET_PREFIX_CACHE[layer_ref] = prefix
    return prefix


@router.get("/{layer_ref}/parquet/{path:path}")
async def vector_parquet_object(layer_ref: str, path: str, request: Request,
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
    prefix = await _parquet_prefix(layer_ref, db)
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
        _PARQUET_PREFIX_CACHE.pop(layer_ref, None)
        fresh = await _parquet_prefix(layer_ref, db)
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
    user: User = Depends(require_scope("data:write")),
    db: AsyncSession = Depends(get_db),
):
    """Data-sharing settings: set the workspace `visibility` (private | organization | public) plus
    catalog metadata. `public` opts the layer into the STAC catalog (`/api/stac`) + raw-asset access
    and syncs the derived `is_public`. Any editor+ may re-share a resource they can SEE (a private
    layer they don't own is a 404 via the filter below — hidden, not just uneditable).
    A layer's file-backed display endpoints are readable by an unauthenticated caller when public OR
    it is shown by a published portal (see `_publicly_readable`); this can change the exposure, so we
    drop the exposure cache."""
    result = await db.execute(
        select(VectorLayer).where(VectorLayer.id == layer_id, visible_to(user, VectorLayer)))
    layer = result.scalar_one_or_none()
    if not layer:
        raise HTTPException(404, "Layer not found.")
    apply_sharing(layer, body)
    await db.commit()
    await db.refresh(layer)
    invalidate_public_layers()
    await record_audit(db, user, "vector.share", "vector", layer.id,
                       {"name": layer.name, "visibility": layer.visibility})
    return VectorLayerOut.from_orm_json(layer)


@router.put("/{layer_id}/rename", response_model=VectorLayerOut)
async def rename_layer(
    layer_id: int,
    body: LayerRename,
    user: User = Depends(require_scope("data:write")),
    db: AsyncSession = Depends(get_db),
):
    """Rename a vector layer's display name. Cosmetic; already-published portals keep the baked name
    until re-published."""
    result = await db.execute(
        select(VectorLayer).where(VectorLayer.id == layer_id, visible_to(user, VectorLayer)))
    layer = result.scalar_one_or_none()
    if not layer:
        raise HTTPException(404, "Layer not found.")
    old_name = layer.name
    layer.name = body.name.strip()
    await db.commit()
    await db.refresh(layer)
    invalidate_public_layers()
    await record_audit(db, user, "vector.rename", "vector", layer.id,
                       {"from": old_name, "to": layer.name})
    return VectorLayerOut.from_orm_json(layer)


@router.put("/{layer_id}/default-style", response_model=VectorLayerOut)
async def save_default_style(
    layer_id: int,
    body: DefaultStyle,
    user: User = Depends(require_scope("data:write")),
    db: AsyncSession = Depends(get_db),
):
    import json
    result = await db.execute(
        select(VectorLayer).where(VectorLayer.id == layer_id, visible_to(user, VectorLayer)))
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
    user: User = Depends(require_scope("data:write")),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(VectorLayer).where(VectorLayer.id == layer_id, visible_to(user, VectorLayer)))
    layer = result.scalar_one_or_none()
    if not layer:
        raise HTTPException(404, "Layer not found.")
    layer_name = layer.name  # capture before deletion for the audit entry

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
    invalidate_public_layers()  # drop cached exposure/key entries for the removed layer
    pruned = await prune_layer_from_portals(db, "vector", layer_id)  # drop the ghost from portals + re-publish
    await record_audit(db, user, "vector.delete", "vector", layer_id,
                       {"name": layer_name, "portals_updated": [p.title for p in pruned]})

    # Regenerate Martin config without the deleted layer. ALL members' ready postgis layers
    # are included — the config is instance-wide (shared workspace), not per-creator; filtering
    # by the deleting user here would silently drop everyone else's tiles.
    remaining = await db.execute(
        select(VectorLayer).where(
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
