import os
import uuid
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import get_settings
from ...database import get_db
from ...deps import require_scope
from ...models import RasterLayer, UploadJob, User
from ...schemas import JobStatus, LayerRename, PortalRefOut, RasterDefaultStyle, RasterLayerOut, SharingUpdate
from ...services import share_links
from ...services.titiler import get_tile_url as raster_tile_url, COLORMAPS
from ...tasks.raster_ingest import ingest_raster
from ..common import (apply_sharing, busy_job_progress, by_ref, creator_names, portals_using,
                      prune_layer_from_portals, record_audit, visible_to)

router = APIRouter(prefix="/data/raster", tags=["raster"])

ALLOWED_EXTENSIONS = {".tif", ".tiff"}
MAX_FILE_SIZE = 10 * 1024 * 1024 * 1024  # 10 GB


@router.get("/colormaps")
async def list_colormaps():
    return COLORMAPS


@router.get("", response_model=list[RasterLayerOut])
async def list_layers(user: User = Depends(require_scope("data:read")), db: AsyncSession = Depends(get_db)):
    import json
    result = await db.execute(
        select(RasterLayer).where(visible_to(user, RasterLayer)).order_by(RasterLayer.created_at.desc())
    )
    layers = result.scalars().all()
    names = await creator_names(db, layers)
    jobs = await busy_job_progress(db, layers, "raster")
    out = []
    for l in layers:
        obj = RasterLayerOut.from_orm_json(l)
        obj.created_by = names.get(l.user_id)
        if l.id in jobs:
            obj.progress, obj.current_step = jobs[l.id]
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


@router.get("/{layer_id}/usage", response_model=list[PortalRefOut])
async def layer_usage(layer_id: int, user: User = Depends(require_scope("data:read")),
                      db: AsyncSession = Depends(get_db)):
    """Portals that include this raster — shown in the delete-confirmation dialog."""
    return [PortalRefOut.model_validate(p) for p in await portals_using(db, "raster", layer_id)]


@router.get("/{layer_id}/stats")
async def raster_stats(layer_id: int, user: User = Depends(require_scope("data:read")), db: AsyncSession = Depends(get_db)):
    """Suggested stretch (min,max) from TiTiler band statistics (2nd–98th percentile)."""
    result = await db.execute(
        select(RasterLayer).where(RasterLayer.id == layer_id, visible_to(user, RasterLayer)))
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
    user: User = Depends(require_scope("data:write")),
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
    await record_audit(db, user, "raster.upload", "raster", layer.id,
                       {"name": base_name, "file": file.filename})

    ingest_raster.delay(job_id, layer.id, tmp_path, s3_key)

    return JobStatus(
        id=job_id, layer_id=layer.id, layer_type="raster",
        status="queued", progress=0, current_step="Queued", error_message=None,
    )


# ── Large rasters: direct-to-storage in presigned parts ──────────────────────────────────────
# A GeoTIFF over ~100 MB cannot be POSTed through the API when a CDN fronts the instance
# (Cloudflare's free tier cuts request bodies at 100 MB) — the request never even arrives. So the
# browser uploads to object storage in parts and we ingest from there, mirroring the large-VECTOR
# flow. PART_SIZE and the multipart bodies are imported from the vector router deliberately: one
# definition, so the two paths cannot drift on the part size that keeps requests under the limit.
from .vector import PART_SIZE, MultipartComplete, MultipartInitiate   # noqa: E402


class LargeRasterComplete(BaseModel):
    s3_key: str
    name: str | None = None
    file_size: int | None = None


def _raster_key(user_id: int, filename: str) -> str:
    base = os.path.splitext(os.path.basename(filename or "raster"))[0] or "raster"
    ext = os.path.splitext(filename or "")[1].lower() or ".tif"
    return f"rasters/{user_id}/{uuid.uuid4().hex}/{base}{ext}"


@router.post("/upload/multipart/initiate")
async def raster_multipart_initiate(body: MultipartInitiate,
                                    user: User = Depends(require_scope("data:write"))):
    """Open a chunked upload for a raster and presign every part."""
    import math

    from ...services import minio as minio_svc
    ext = os.path.splitext(body.filename or "")[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(400, f"Unsupported type: {ext}. Upload GeoTIFF (.tif/.tiff).")
    if body.file_size <= 0:
        raise HTTPException(400, "Empty file.")
    if body.file_size > MAX_FILE_SIZE:
        raise HTTPException(413, "File exceeds 10 GB limit.")

    s3_key = _raster_key(user.id, body.filename)
    upload_id = await run_in_threadpool(minio_svc.create_multipart, s3_key)
    num_parts = max(1, math.ceil(body.file_size / PART_SIZE))
    parts = await run_in_threadpool(minio_svc.presign_parts, s3_key, upload_id, num_parts)
    return {"s3_key": s3_key, "upload_id": upload_id, "part_size": PART_SIZE, "parts": parts}


@router.post("/upload/multipart/complete")
async def raster_multipart_complete(body: MultipartComplete,
                                    user: User = Depends(require_scope("data:write"))):
    """Assemble the parts. Registration is the separate /large/complete call, matching the vector
    flow. The key must be inside the caller's own prefix — never trust a client-supplied key."""
    from ...services import minio as minio_svc
    if not (body.s3_key or "").startswith(f"rasters/{user.id}/"):
        raise HTTPException(400, "Invalid storage key.")
    await run_in_threadpool(
        minio_svc.complete_multipart, body.s3_key, body.upload_id,
        [{"PartNumber": p.part_number, "ETag": p.etag} for p in body.parts])
    return {"s3_key": body.s3_key}


@router.post("/upload/multipart/abort", status_code=204)
async def raster_multipart_abort(body: MultipartComplete,
                                 user: User = Depends(require_scope("data:write"))):
    from ...services import minio as minio_svc
    if not (body.s3_key or "").startswith(f"rasters/{user.id}/"):
        raise HTTPException(400, "Invalid storage key.")
    await run_in_threadpool(minio_svc.abort_multipart, body.s3_key, body.upload_id)


@router.post("/large/complete", response_model=JobStatus, status_code=202)
async def large_raster_complete(body: LargeRasterComplete,
                                user: User = Depends(require_scope("data:write")),
                                db: AsyncSession = Depends(get_db)):
    """Register a raster already uploaded to storage and queue its COG conversion."""
    if not (body.s3_key or "").startswith(f"rasters/{user.id}/"):
        raise HTTPException(400, "Invalid storage key.")
    base_name = (body.name or "").strip() or os.path.splitext(os.path.basename(body.s3_key))[0]
    # The COG is written beside the upload, so the raw file can be dropped afterwards.
    dest_key = f"{os.path.dirname(body.s3_key)}/{base_name}.tif"

    layer = RasterLayer(user_id=user.id, name=base_name, s3_key=dest_key,
                        file_size=body.file_size, status="processing")
    db.add(layer)
    await db.flush()
    job_id = str(uuid.uuid4())
    db.add(UploadJob(id=job_id, layer_id=layer.id, layer_type="raster"))
    await db.commit()
    await db.refresh(layer)
    await record_audit(db, user, "raster.upload", "raster", layer.id,
                       {"name": base_name, "direct_upload": True})

    from ...tasks.raster_ingest import ingest_raster_from_storage
    ingest_raster_from_storage.delay(job_id, layer.id, body.s3_key, dest_key)
    return JobStatus(id=job_id, layer_id=layer.id, layer_type="raster", status="queued",
                     progress=0, current_step="Queued", error_message=None)


@router.get("/jobs/{job_id}", response_model=JobStatus)
async def job_status(job_id: str, db: AsyncSession = Depends(get_db), user: User = Depends(require_scope("data:read"))):
    result = await db.execute(select(UploadJob).where(UploadJob.id == job_id))
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(404, "Job not found.")
    return job


@router.put("/{layer_id}/sharing", response_model=RasterLayerOut)
async def save_sharing(
    layer_id: int,
    body: SharingUpdate,
    user: User = Depends(require_scope("data:write")),
    db: AsyncSession = Depends(get_db),
):
    """Data-sharing settings: set the workspace `visibility` (private | organization | public) plus
    catalog metadata. `public` opts the layer into the STAC catalog (`/api/stac`) + the public
    raw-COG route and syncs the derived `is_public`. Any editor+ may re-share a resource they can
    SEE (a private layer they don't own 404s via the filter below)."""
    result = await db.execute(
        select(RasterLayer).where(RasterLayer.id == layer_id, visible_to(user, RasterLayer)))
    layer = result.scalar_one_or_none()
    if not layer:
        raise HTTPException(404, "Layer not found.")
    apply_sharing(layer, body)
    await db.commit()
    await db.refresh(layer)
    await record_audit(db, user, "raster.share", "raster", layer.id,
                       {"name": layer.name, "visibility": layer.visibility})
    return RasterLayerOut.from_orm_json(layer)


@router.put("/{layer_id}/rename", response_model=RasterLayerOut)
async def rename_layer(
    layer_id: int,
    body: LayerRename,
    user: User = Depends(require_scope("data:write")),
    db: AsyncSession = Depends(get_db),
):
    """Rename a raster layer's display name. Cosmetic; already-published portals keep the baked name
    until re-published."""
    result = await db.execute(
        select(RasterLayer).where(RasterLayer.id == layer_id, visible_to(user, RasterLayer)))
    layer = result.scalar_one_or_none()
    if not layer:
        raise HTTPException(404, "Layer not found.")
    old_name = layer.name
    layer.name = body.name.strip()
    await db.commit()
    await db.refresh(layer)
    await record_audit(db, user, "raster.rename", "raster", layer.id,
                       {"from": old_name, "to": layer.name})
    return RasterLayerOut.from_orm_json(layer)


@router.get("/{layer_ref}/cog")
async def raster_cog(layer_ref: str, request: Request, db: AsyncSession = Depends(get_db)):
    """PUBLIC range proxy for the layer's Cloud-Optimized GeoTIFF — ONLY when the admin shared
    the layer (`is_public`). This is what makes `/vsicurl/https://host/api/data/raster/{id}/cog`
    work in QGIS/GDAL (full pixel access, the modern WCS — notes §0h) and gives a direct
    download URL. Same pmtiles/parquet proxy pattern: Range → 206, creds stay server-side."""
    result = await db.execute(select(RasterLayer).where(by_ref(RasterLayer, layer_ref)))
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


@router.get("/{layer_ref}/tilejson")
async def raster_tilejson(layer_ref: str, request: Request, db: AsyncSession = Depends(get_db)):
    """PUBLIC TileJSON (3.0.0) for a shared raster — the ONE URL other tools add directly.

    Why not TiTiler's own `/cog/…/tilejson.json`? Its self-referencing tile URL is built from the
    container's internal origin: `http://titiler:8000/cog/tiles/…` — wrong host, wrong scheme, and
    missing nginx's `/raster` prefix. So we emit our own, with the same styling TiTiler would get
    (band/colormap/stretch from `default_style`) baked into the tile template.

    Crucially it carries `bounds`, which a bare XYZ template cannot — that is what makes "zoom to
    layer" work in GeoLibre/QGIS. Bounds come from the stored EPSG:4326 bbox (see
    `cog_converter.inspect` — it reprojects); when a legacy row has none we ask TiTiler once."""
    import json
    from fastapi.responses import JSONResponse

    result = await db.execute(select(RasterLayer).where(by_ref(RasterLayer, layer_ref)))
    layer = result.scalar_one_or_none()
    if not layer or layer.status != "ready" or not layer.is_public or not layer.s3_key:
        raise HTTPException(404, "No shared raster for this layer.")

    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = request.headers.get("host") or request.url.netloc
    base = f"{proto}://{host}"
    ds = json.loads(layer.default_style) if layer.default_style else {}
    tj = {
        "tilejson": "3.0.0",
        "name": layer.name,
        "scheme": "xyz",
        "tiles": [base + raster_tile_url(
            layer.s3_key, colormap=ds.get("colormap"), rescale=ds.get("rescale"),
            algorithm=ds.get("algorithm"), zfactor=ds.get("zfactor"), bidx=ds.get("bidx"))],
        "minzoom": 0,
        "maxzoom": 22,
    }
    if layer.abstract:
        tj["description"] = layer.abstract
    if layer.attribution:
        tj["attribution"] = layer.attribution

    bbox = None
    try:
        bbox = json.loads(layer.bbox) if layer.bbox else None
    except ValueError:
        bbox = None
    if not (isinstance(bbox, list) and len(bbox) == 4):
        bbox = await _titiler_bounds(layer.s3_key)
    if bbox:
        tj["bounds"] = bbox
        tj["center"] = [(bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2, 0]
    return JSONResponse(tj, headers={"Access-Control-Allow-Origin": "*",
                                     "Cache-Control": "public, max-age=300"})


async def _titiler_bounds(s3_key: str) -> list[float] | None:
    """Best-effort WGS84 bounds from TiTiler `/cog/info` — only for rows whose bbox was never
    stored (pre-`inspect` imports). Never raises: no bounds is better than a failed TileJSON."""
    import httpx
    settings = get_settings()
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(f"{settings.titiler_url}/cog/info",
                                 params={"url": f"s3://{settings.storage_bucket}/{s3_key}"})
            r.raise_for_status()
            b = r.json().get("bounds")
        return [float(v) for v in b][:4] if b and len(b) >= 4 else None
    except Exception:
        return None


@router.get("/{layer_id}/links")
async def raster_share_links(layer_id: int, request: Request,
                             user: User = Depends(require_scope("data:read")),
                             db: AsyncSession = Depends(get_db)):
    """The "Share links" panel feed: every tool-ready URL for this raster. Authed (it is layer
    metadata), but the URLs themselves only RESOLVE while the layer is shared `public` — hence
    the `public` flag, which the UI turns into a "make it public first" notice."""
    import json
    result = await db.execute(
        select(RasterLayer).where(RasterLayer.id == layer_id, visible_to(user, RasterLayer)))
    layer = result.scalar_one_or_none()
    if not layer:
        raise HTTPException(404, "Layer not found.")
    if layer.status != "ready":
        raise HTTPException(409, "Layer is not ready yet.")
    ds = json.loads(layer.default_style) if layer.default_style else {}
    base = share_links.request_base(request)
    return {"public": bool(layer.is_public), "name": layer.name,
            "catalog": f"{base}/api/stac",
            "links": share_links.raster_links(layer, base, ds)}


@router.put("/{layer_id}/default-style", response_model=RasterLayerOut)
async def save_default_style(
    layer_id: int,
    body: RasterDefaultStyle,
    user: User = Depends(require_scope("data:write")),
    db: AsyncSession = Depends(get_db),
):
    import json
    result = await db.execute(
        select(RasterLayer).where(RasterLayer.id == layer_id, visible_to(user, RasterLayer)))
    layer = result.scalar_one_or_none()
    if not layer:
        raise HTTPException(404, "Layer not found.")
    layer.default_style = json.dumps(body.model_dump())
    await db.commit()
    await db.refresh(layer)
    return RasterLayerOut.from_orm_json(layer)


@router.delete("/{layer_id}", status_code=204)
async def delete_layer(layer_id: int, user: User = Depends(require_scope("data:write")), db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(RasterLayer).where(RasterLayer.id == layer_id, visible_to(user, RasterLayer)))
    layer = result.scalar_one_or_none()
    if not layer:
        raise HTTPException(404, "Layer not found.")
    layer_name = layer.name  # capture before deletion for the audit entry

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
    pruned = await prune_layer_from_portals(db, "raster", layer_id)  # drop the ghost from portals + re-publish
    await record_audit(db, user, "raster.delete", "raster", layer_id,
                       {"name": layer_name, "portals_updated": [p.title for p in pruned]})
