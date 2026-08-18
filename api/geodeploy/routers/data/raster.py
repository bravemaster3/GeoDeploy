import os
import uuid
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import get_settings
from ...database import get_db
from ...deps import require_scope, resolve_optional_user
from ...models import RasterLayer, UploadJob, User
from ...schemas import JobStatus, LayerRename, PortalRefOut, RasterDefaultStyle, RasterLayerOut, SharingUpdate
from ...services import share_links
from ...services.titiler import tile_url_from_style as raster_tile_url, COLORMAPS
from ...tasks.raster_ingest import ingest_raster
from . import exports
from ..common import (apply_sharing, demo_upload_cap, busy_job_progress, by_ref, creator_names, portals_using,
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
            obj.tile_url = raster_tile_url(l.s3_key, ds, band_count=l.band_count)
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


@router.get("/{layer_id}/unique-values")
async def raster_unique_values(layer_id: int, band: int = 1,
                               user: User = Depends(require_scope("data:read")),
                               db: AsyncSession = Depends(get_db)):
    """The distinct pixel values of a CLASSIFIED raster — land cover, soil types, a mask.

    The counterpart of `/stats`, which suggests a stretch for CONTINUOUS data. A classification is
    the other kind of raster entirely: its numbers are labels, a gradient between class 3 and class
    4 means nothing, and what a legend needs is the list of values actually present.

    TiTiler answers this with `categorical=true`, which turns the histogram into one bin per unique
    value. Bounded twice on the way out, because the question is only meaningful for a raster whose
    values ARE classes: `max_size` caps the pixels sampled, and a raster with more distinct values
    than GeoDeploy can colour is reported as continuous rather than truncated into a classification
    that would mis-colour most of the map. This DEM answers 12,145 — which is the correct answer to
    "are you categorical", and it is no.
    """
    from ...services.titiler import MAX_COLOR_CLASSES

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
    params = {"url": cog_url, "categorical": "true", "bidx": band,
              # A SAMPLE, not the whole raster. A full read of a large COG to answer "which classes
              # are in here" would hold the request open for minutes; a classification's values all
              # appear in any decent sample, and the count only has to be right enough to decide
              # whether this is categorical at all.
              "max_size": 1024}
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.get(f"{settings.titiler_url}/cog/statistics", params=params)
            r.raise_for_status()
            stats = r.json()
    except Exception as exc:
        raise HTTPException(502, f"Could not read raster values: {exc}") from exc

    band_stats = next((s for s in stats.values() if isinstance(s, dict)), None)
    histogram = (band_stats or {}).get("histogram") or []
    if len(histogram) < 2:
        raise HTTPException(422, "No usable statistics returned.")
    counts, values = histogram[0], histogram[1]

    entries = []
    for value, count in zip(values, counts):
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        # INTEGERS ONLY. TiTiler maps a colour to a pixel VALUE, so 3.7 has nothing to key on — and
        # a raster of fractional values is continuous whatever its histogram looks like.
        if number != int(number):
            return {"categorical": False, "values": [], "count": len(values),
                    "reason": "This raster holds fractional values, so it is continuous — use a "
                              "colour ramp and a stretch."}
        entries.append({"value": int(number), "count": int(count or 0)})

    entries = [e for e in entries if e["count"] > 0]
    entries.sort(key=lambda e: e["value"])
    if len(entries) > MAX_COLOR_CLASSES:
        return {"categorical": False, "values": [], "count": len(entries),
                "reason": f"{len(entries)} distinct values — more than the {MAX_COLOR_CLASSES} a "
                          f"classification can carry, so this reads as continuous data."}
    return {"categorical": True, "values": entries, "count": len(entries)}


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
    demo_upload_cap(body.file_size)
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
    # `model_dump()`, exactly as the vector path does — NOT a hand-built {"PartNumber", "ETag"} dict.
    # complete_multipart() takes the schema's own field names ({part_number, etag}) and does the
    # boto3 casing itself, so converting here made it look up a key that no longer existed and every
    # large raster upload died with KeyError: 'part_number' at the final assemble step.
    await run_in_threadpool(minio_svc.complete_multipart, body.s3_key, body.upload_id,
                            [p.model_dump() for p in body.parts])
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


# ── Clipped download ──────────────────────────────────────────────────────────────────────────
# A bbox is REQUIRED here: the whole raster is already a single file, streamed by range request
# from /cog, so a "whole raster export" would spend worker minutes re-encoding a worse copy.

# ── Public-read authorization for the raster DESCRIPTION endpoints ────────────────────────────
# `is_public` alone is too strict for these, and the inconsistency was visible from outside: a
# raster that is public only THROUGH a published portal serves its tiles (200) while its own
# TileJSON and WMTS answered 404 — so a client could draw the raster but not discover where it is.
# QGIS hit exactly that: "Could not read the raster's bounds: HTTP Error 404". The vector side has
# always used the wider rule (`vector._publicly_readable`); this is the raster twin of it.
#
# Deliberately NOT applied to `/cog` or `/export`: those hand over the pixels, and "shown in a
# portal" is a licence to look at a picture, not to download the source data.
_PUBLISHED_RASTER_IDS: set[int] | None = None


def invalidate_public_rasters() -> None:
    global _PUBLISHED_RASTER_IDS
    _PUBLISHED_RASTER_IDS = None


async def _published_raster_ids(db: AsyncSession) -> set[int]:
    global _PUBLISHED_RASTER_IDS
    if _PUBLISHED_RASTER_IDS is None:
        import json as _json
        from ...models import Portal
        rows = (await db.execute(
            select(Portal.layer_configs).where(Portal.published == True))).scalars().all()  # noqa: E712
        ids: set[int] = set()
        for cfg in rows:
            try:
                configs = _json.loads(cfg) if isinstance(cfg, str) else (cfg or [])
                for lc in configs:
                    if lc.get("layer_id") is not None and lc.get("layer_type") == "raster":
                        ids.add(int(lc["layer_id"]))
            except (ValueError, TypeError, AttributeError):
                continue
        _PUBLISHED_RASTER_IDS = ids
    return _PUBLISHED_RASTER_IDS


async def _describable(layer, db: AsyncSession) -> bool:
    """True if this raster's DESCRIPTION may be served anonymously: shared, or drawn by a
    published portal. Mirrors `vector._publicly_readable`."""
    if not layer or layer.status != "ready" or not layer.s3_key:
        return False
    if getattr(layer, "is_public", False):
        return True
    return layer.id in await _published_raster_ids(db)


@router.post("/{layer_ref}/export", status_code=202)
async def start_raster_export(layer_ref: str, req: exports.LayerExportRequest,
                              db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(RasterLayer).where(by_ref(RasterLayer, layer_ref)))
    layer = result.scalar_one_or_none()
    if not layer or layer.status != "ready" or not layer.is_public or not layer.s3_key:
        raise HTTPException(404, "No shared raster for this layer.")
    bbox = exports.validate_bbox(req.bbox)
    return exports.start(exports.raster_item(layer, req.format, bbox), bbox,
                         req.target_crs)


@router.get("/{layer_ref}/export-status/{job_id}")
async def raster_export_status(layer_ref: str, job_id: str):
    return exports.status(job_id)


@router.get("/{layer_ref}/export-download/{job_id}")
async def raster_export_download(layer_ref: str, job_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(RasterLayer).where(by_ref(RasterLayer, layer_ref)))
    layer = result.scalar_one_or_none()
    from slugify import slugify
    name = slugify(layer.name, separator="_") if layer else "export"
    return exports.download(job_id, "{0}.zip".format(name or "export"))


@router.get("/{layer_ref}/cog")
async def raster_cog(layer_ref: str, request: Request, db: AsyncSession = Depends(get_db)):
    """Range proxy for the layer's Cloud-Optimized GeoTIFF — the modern WCS (notes §0h).

    This is what makes `/vsicurl/https://host/api/data/raster/{id}/cog` work in QGIS/GDAL with full
    pixel access, and it is a direct download URL. Same pmtiles/parquet proxy pattern: Range → 206,
    creds stay server-side.

    PUBLIC when the layer is shared — and readable by a SIGNED-IN user who may see the layer, which
    it was not before. That omission had a consequence far from here: server-rendered tiles are
    colour rather than values, so the only way to restyle a raster in QGIS is to open this GeoTIFF,
    and for any raster that was not shared publicly the owner could not open it AT ALL. "I am still
    unable to change a raster's symbology from QGIS" was, for a private raster, exactly this 404.
    `visible_to` is the same rule the rest of the authenticated surface uses, so this grants nothing
    new — it stops withholding the pixels from people already entitled to them.
    """
    result = await db.execute(select(RasterLayer).where(by_ref(RasterLayer, layer_ref)))
    layer = result.scalar_one_or_none()
    if not layer or layer.status != "ready" or not layer.s3_key:
        raise HTTPException(404, "No shared raster for this layer.")
    if not layer.is_public:
        # Resolved in the BODY, like the `/legend` route below — `resolve_optional_user` is a plain
        # async helper taking `(request, db)`, not a FastAPI dependency. Wiring it through `Depends`
        # makes FastAPI inspect its signature, find `db: AsyncSession` with no `Depends` default, and
        # refuse to build the app at all.
        user = await resolve_optional_user(request, db)
        allowed = user is not None and (await db.execute(
            select(RasterLayer.id).where(RasterLayer.id == layer.id,
                                         visible_to(user, RasterLayer)))).scalar_one_or_none()
        if not allowed:
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


@router.get("/{layer_ref}/legend")
async def raster_legend(layer_ref: str, request: Request, db: AsyncSession = Depends(get_db)):
    """PUBLIC legend for a shared raster: the colormap and the value range it is stretched over.

    A raster legend is a continuous ramp, not a list of swatches, so this reports the ingredients a
    renderer needs to draw one — `colormap`, `rescale` and the band — rather than pretending to be
    the vector legend. The point is the same as the vector route: the numbers on a QGIS legend
    should be the numbers the portal used, and the only way to guarantee that is to read them from
    the layer rather than re-derive them from the file.
    """
    import json

    result = await db.execute(select(RasterLayer).where(by_ref(RasterLayer, layer_ref)))
    layer = result.scalar_one_or_none()
    if not layer or layer.status != "ready":
        raise HTTPException(404, "No shared raster for this layer.")
    if not layer.is_public:
        # Same correction as the vector route: a signed-in caller reads the legend of a raster they
        # can see. `is_public` alone 404s the owner of their own organization layer.
        user = await resolve_optional_user(request, db)
        allowed = user is not None and (await db.execute(
            select(RasterLayer.id).where(RasterLayer.id == layer.id,
                                         visible_to(user, RasterLayer)))).scalar_one_or_none()
        if not allowed:
            raise HTTPException(404, "No shared raster for this layer.")

    # A RASTER's default style is stored FLAT — {opacity, colormap, rescale, …} — where a vector's
    # nests the visual part under "style". This route was written against the vector shape, so
    # `.get("style")` was always missing and every field came back null: verified on a live
    # instance, where a raster with `rescale: "0.0,2.0"` reported `rescale: null`. The legend has
    # therefore never told a renderer anything it did not already have to guess.
    #
    # Both shapes are read, flat first, so nothing depends on which version wrote the row.
    style = {}
    if layer.default_style:
        try:
            stored = json.loads(layer.default_style) or {}
        except ValueError:
            stored = {}
        style = stored if "style" not in stored else (stored.get("style") or {})
    rescale = style.get("rescale")
    if isinstance(rescale, str):
        # Stored as TiTiler wants it ("min,max"); a client wants numbers.
        try:
            rescale = [float(v) for v in rescale.split(",")]
        except ValueError:
            rescale = None
    return {
        "layer": layer.name,
        "ref": layer.uid or str(layer.id),
        "kind": "raster",
        # A CLASSIFIED raster is not a ramp. Saying it is would make every renderer draw a
        # gradient over land-cover codes — a legend that disagrees with the map it describes.
        "ramp": not bool(style.get("color_classes")),
        "entries": [{"value": c.get("value"), "color": c.get("color"),
                     "label": str(c.get("label") or c.get("value"))}
                    for c in (style.get("color_classes") or [])],
        "color_classes": style.get("color_classes") or None,
        "colormap": style.get("colormap"),
        # Without this a client draws the ramp forwards and its legend contradicts the map.
        "colormap_reverse": bool(style.get("colormap_reverse")),
        "rescale": rescale,
        "algorithm": style.get("algorithm"),
        # A hillshade without its Z FACTOR is a different hillshade. The exaggeration is the whole
        # visible difference between `b1*1` and `b1*5` relief, and this route is the only styling a
        # PUBLIC raster has — a client falling back to it drew flat terrain where the portal shows
        # a modelled surface. Verified on the live instance, where `Degfert_DEM_restr` is stored
        # with `zfactor: 5.0` and reported it nowhere.
        "zfactor": style.get("zfactor"),
        # CONTOUR spacing and line width, and the range the background is coloured over. Same
        # reasoning as `zfactor`: this route is the only styling a PUBLIC raster has, and contours
        # drawn at the algorithm's default 35 m interval instead of the author's 10 m is a
        # different map.
        "increment": style.get("increment"),
        "thickness": style.get("thickness"),
        "minz": style.get("minz"),
        "maxz": style.get("maxz"),
        "bidx": style.get("bidx"),
        "band_count": layer.band_count,
    }


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
    from ...json_safe import SafeJSONResponse

    result = await db.execute(select(RasterLayer).where(by_ref(RasterLayer, layer_ref)))
    layer = result.scalar_one_or_none()
    if not await _describable(layer, db):
        raise HTTPException(404, "No shared raster for this layer.")

    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = request.headers.get("host") or request.url.netloc
    base = f"{proto}://{host}"
    ds = json.loads(layer.default_style) if layer.default_style else {}
    tj = {
        "tilejson": "3.0.0",
        "name": layer.name,
        "scheme": "xyz",
        "tiles": [base + raster_tile_url(layer.s3_key, ds, band_count=layer.band_count)],
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
    return SafeJSONResponse(tj, headers={"Access-Control-Allow-Origin": "*",
                                     "Cache-Control": "public, max-age=300"})


#: WebMercatorQuad, the tile grid every XYZ/slippy client already uses. QGIS will not zoom to a
#: layer from a `ScaleDenominator`-less capabilities document, so the matrix set is written out in
#: full rather than referenced by identifier.
_WMQ_TOP_LEFT = "-20037508.3427892 20037508.3427892"
_WMQ_SCALE_0 = 559082264.028717
_WMTS_MAX_ZOOM = 24


def _wmts_matrix_set() -> str:
    rows = []
    for z in range(_WMTS_MAX_ZOOM + 1):
        rows.append(
            "      <TileMatrix>\n"
            f"        <ows:Identifier>{z}</ows:Identifier>\n"
            f"        <ScaleDenominator>{_WMQ_SCALE_0 / (2 ** z):.9f}</ScaleDenominator>\n"
            f"        <TopLeftCorner>{_WMQ_TOP_LEFT}</TopLeftCorner>\n"
            "        <TileWidth>256</TileWidth>\n"
            "        <TileHeight>256</TileHeight>\n"
            f"        <MatrixWidth>{2 ** z}</MatrixWidth>\n"
            f"        <MatrixHeight>{2 ** z}</MatrixHeight>\n"
            "      </TileMatrix>"
        )
    return "\n".join(rows)


@router.get("/{layer_ref}/wmts")
async def raster_wmts(layer_ref: str, request: Request, db: AsyncSession = Depends(get_db)):
    """PUBLIC WMTS GetCapabilities for a shared raster — the one URL QGIS can zoom to.

    QGIS does not read TileJSON for a RASTER layer, so the bounds that make "Zoom to Layer" work
    reach it through `ows:WGS84BoundingBox` in a capabilities document instead. An XYZ connection
    cannot carry them at all — the template has nowhere to put an extent — which is why adding the
    XYZ link left QGIS guessing at the whole world.

    Why not TiTiler's own `/cog/…/WMTSCapabilities.xml`? Exactly the reason given on the TileJSON
    route above: its self-referencing URLs are built from the container's internal origin
    (`http://titiler:8000/…`), so they carry the wrong host and no `/raster` prefix. This emits the
    same document against the public origin, with the layer's saved styling baked into the tile
    template.
    """
    import json
    from xml.sax.saxutils import escape, quoteattr

    from fastapi.responses import Response

    result = await db.execute(select(RasterLayer).where(by_ref(RasterLayer, layer_ref)))
    layer = result.scalar_one_or_none()
    if not await _describable(layer, db):
        raise HTTPException(404, "No shared raster for this layer.")

    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = request.headers.get("host") or request.url.netloc
    base = f"{proto}://{host}"
    ds = json.loads(layer.default_style) if layer.default_style else {}

    # The XYZ template, re-labelled with WMTS's placeholders. `&` inside it MUST be escaped or the
    # document is not well-formed XML and QGIS rejects the whole connection.
    tmpl = base + raster_tile_url(layer.s3_key, ds, band_count=layer.band_count)
    tmpl = (tmpl.replace("{z}", "{TileMatrix}").replace("{x}", "{TileCol}")
                .replace("{y}", "{TileRow}"))

    bbox = None
    try:
        bbox = json.loads(layer.bbox) if layer.bbox else None
    except ValueError:
        bbox = None
    if not (isinstance(bbox, list) and len(bbox) == 4):
        bbox = await _titiler_bounds(layer.s3_key)
    # Without an extent QGIS falls back to the whole world, which is the behaviour this endpoint
    # exists to fix — but a document with no bbox still beats no document.
    bbox_xml = ""
    if bbox:
        bbox_xml = (
            "      <ows:WGS84BoundingBox crs=\"urn:ogc:def:crs:OGC:2:84\">\n"
            f"        <ows:LowerCorner>{bbox[0]} {bbox[1]}</ows:LowerCorner>\n"
            f"        <ows:UpperCorner>{bbox[2]} {bbox[3]}</ows:UpperCorner>\n"
            "      </ows:WGS84BoundingBox>\n"
        )
    abstract = f"      <ows:Abstract>{escape(layer.abstract)}</ows:Abstract>\n" if layer.abstract else ""

    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<Capabilities xmlns="http://www.opengis.net/wmts/1.0"\n'
        '  xmlns:ows="http://www.opengis.net/ows/1.1"\n'
        '  xmlns:xlink="http://www.w3.org/1999/xlink" version="1.0.0">\n'
        "  <ows:ServiceIdentification>\n"
        f"    <ows:Title>{escape(layer.name)}</ows:Title>\n"
        "    <ows:ServiceType>OGC WMTS</ows:ServiceType>\n"
        "    <ows:ServiceTypeVersion>1.0.0</ows:ServiceTypeVersion>\n"
        "  </ows:ServiceIdentification>\n"
        "  <Contents>\n"
        "    <Layer>\n"
        f"      <ows:Title>{escape(layer.name)}</ows:Title>\n"
        f"{abstract}"
        f"      <ows:Identifier>{escape(str(layer.id))}</ows:Identifier>\n"
        f"{bbox_xml}"
        "      <Style isDefault=\"true\"><ows:Identifier>default</ows:Identifier></Style>\n"
        "      <Format>image/png</Format>\n"
        "      <TileMatrixSetLink>\n"
        "        <TileMatrixSet>WebMercatorQuad</TileMatrixSet>\n"
        "      </TileMatrixSetLink>\n"
        "      <ResourceURL format=\"image/png\" resourceType=\"tile\"\n"
        f"        template={quoteattr(tmpl)}/>\n"
        "    </Layer>\n"
        "    <TileMatrixSet>\n"
        "      <ows:Identifier>WebMercatorQuad</ows:Identifier>\n"
        "      <ows:SupportedCRS>urn:ogc:def:crs:EPSG::3857</ows:SupportedCRS>\n"
        f"{_wmts_matrix_set()}\n"
        "    </TileMatrixSet>\n"
        "  </Contents>\n"
        "</Capabilities>\n"
    )
    return Response(xml, media_type="application/xml",
                    headers={"Access-Control-Allow-Origin": "*",
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
    # MERGE, do not replace. `model_dump()` fills every field the model knows about, so a client
    # that has not been taught about a newer one — the web UI's raster panel does not yet edit
    # `color_classes` — silently sent null for it and DESTROYED a paletted raster's palette just by
    # saving an unrelated change. `exclude_unset` keeps only what the caller actually sent, so a
    # field can still be cleared deliberately (send it as null) but never by omission.
    stored = {}
    if layer.default_style:
        try:
            stored = json.loads(layer.default_style) or {}
        except ValueError:
            stored = {}
    stored.update(body.model_dump(exclude_unset=True))
    layer.default_style = json.dumps(stored)
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
