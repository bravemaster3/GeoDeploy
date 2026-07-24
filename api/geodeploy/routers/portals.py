import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..database import get_db
from ..deps import require_scope, resolve_cookie_user
from ..models import ExternalSource, Portal, RasterLayer, User, VectorLayer
from ..schemas import PortalCreate, PortalOut, PortalUpdate
from ..services.portal_generator import build_portal_bundle, generate_style, read_deck_core_bbox
from .common import creator_names, record_audit
from .data.vector import invalidate_public_layers

router = APIRouter(prefix="/portals", tags=["portals"])
_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")


def _unlock_cookie_name(portal_id: int) -> str:
    return f"gd_pu_{portal_id}"  # per-portal so unlocking one doesn't clobber another


def _make_unlock_token(portal_id: int) -> str:
    """Signed proof that the correct password was entered for this portal (7-day expiry)."""
    payload = {"pu": portal_id, "exp": datetime.now(timezone.utc) + timedelta(days=7)}
    return jwt.encode(payload, get_settings().secret_key, algorithm="HS256")


def _is_unlocked(request: Request, portal_id: int) -> bool:
    token = request.cookies.get(_unlock_cookie_name(portal_id))
    if not token:
        return False
    try:
        payload = jwt.decode(token, get_settings().secret_key, algorithms=["HS256"])
        return int(payload.get("pu")) == portal_id
    except (JWTError, TypeError, ValueError):
        return False


async def _new_slug(db: AsyncSession) -> str:
    """A short, opaque, unique URL slug for a portal — an id, NOT derived from the title. Generated
    once at creation and immutable, so the public /portals/{slug}/ URL stays stable across renames
    and never leaks/depends on the title. Collision-checked against existing portals."""
    while True:
        slug = uuid.uuid4().hex[:10]
        if (await db.execute(select(Portal).where(Portal.slug == slug))).scalar_one_or_none() is None:
            return slug


async def _assemble_bundle(db: AsyncSession, *, slug: str, title: str, layer_configs: list[dict],
                           layer_groups, layout_config, story, theme, initial_view, template_id: str,
                           basemap, description, access_type: str, password_sha256, owner_id) -> None:
    """Resolve the layers referenced by `layer_configs`, build the MapLibre style, and write the static
    bundle to data/portals/{slug}/. Shared by publish (persisted values) and preview (unsaved values)."""
    vector_ids = [cfg["layer_id"] for cfg in layer_configs if cfg.get("layer_type") == "vector"]
    raster_ids = [cfg["layer_id"] for cfg in layer_configs if cfg.get("layer_type") == "raster"]
    external_ids = [cfg["layer_id"] for cfg in layer_configs if cfg.get("layer_type") == "external"]

    vector_layers = []
    if vector_ids:
        r = await db.execute(select(VectorLayer).where(VectorLayer.id.in_(vector_ids), VectorLayer.status == "ready"))
        vector_layers = r.scalars().all()
    raster_layers = []
    if raster_ids:
        r = await db.execute(select(RasterLayer).where(RasterLayer.id.in_(raster_ids), RasterLayer.status == "ready"))
        raster_layers = r.scalars().all()
    external_sources = []
    if external_ids:
        r = await db.execute(select(ExternalSource).where(ExternalSource.id.in_(external_ids)))
        external_sources = r.scalars().all()

    # Bake each deck (GeoParquet) layer's manifest CORE extent so the published map opens there and
    # doesn't snap once on load. Best-effort + off the event loop; a miss falls back to the full bbox.
    from starlette.concurrency import run_in_threadpool
    deck_core_bounds: dict[int, list] = {}
    for l in vector_layers:
        if (getattr(l, "storage_backend", "postgis") == "geoparquet"
                and not (l.tile_status == "ready" and l.pmtiles_key)):  # PMTiles-tiled = not a deck layer
            bbox = await run_in_threadpool(read_deck_core_bbox, l.s3_key)
            if bbox:
                deck_core_bounds[l.id] = bbox

    user_data = generate_style(layer_configs, vector_layers, raster_layers, external_sources,
                               deck_core_bounds=deck_core_bounds, layer_groups=layer_groups)
    build_portal_bundle(
        slug, title, user_data, template_id, layer_configs,
        access_type=access_type,
        password_sha256=password_sha256,
        owner_id=owner_id,   # baked so the 'owner' gate can check the viewer is the creator
        initial_view=initial_view,
        description=description,
        basemap=basemap,
        layout_config=layout_config,   # V-11: resolved into style.geodeploy.layout
        story=story,                   # V-11: storymap sections baked when archetype == storymap
        theme=theme,                   # V-11 R3: colour theme → CSS-var overrides after theme.css
    )


def _pjson(val):  # portal JSON column → python (None-safe)
    return json.loads(val) if val else None


async def _rebuild_bundle(portal: Portal, db: AsyncSession) -> None:
    """(Re)generate the published static bundle at data/portals/{portal.slug}/ from the portal's
    persisted config. Shared by publish (explicit) and rename (re-publish under the new slug)."""
    await _assemble_bundle(
        db, slug=portal.slug, title=portal.title,
        layer_configs=json.loads(portal.layer_configs or "[]"),
        layer_groups=_pjson(portal.layer_groups), layout_config=_pjson(portal.layout_config),
        story=_pjson(portal.story), theme=_pjson(portal.theme), initial_view=_pjson(portal.initial_view),
        template_id=portal.template_id, basemap=portal.basemap, description=portal.description,
        access_type=portal.access_type, password_sha256=portal.access_password_sha256,
        owner_id=portal.user_id,
    )


@router.get("", response_model=list[PortalOut])
async def list_portals(user: User = Depends(require_scope("portal:read")), db: AsyncSession = Depends(get_db)):
    # Shared workspace: all members see all portals (role gates WRITES, not reads). Portals have no
    # per-resource visibility — a portal's audience is its published access_type, not a workspace flag.
    result = await db.execute(select(Portal).order_by(Portal.created_at.desc()))
    portals = result.scalars().all()
    names = await creator_names(db, portals)
    out = []
    for p in portals:
        o = PortalOut.from_orm_json(p)
        o.created_by = names.get(p.user_id)
        out.append(o)
    return out


@router.post("", response_model=PortalOut, status_code=201)
async def create_portal(req: PortalCreate, user: User = Depends(require_scope("portal:write")), db: AsyncSession = Depends(get_db)):
    slug = await _new_slug(db)  # opaque id, stable for the life of the portal

    portal = Portal(
        user_id=user.id,
        title=req.title,
        slug=slug,
        description=req.description,
        template_id=req.template_id,
        layer_configs=json.dumps([lc.model_dump() for lc in req.layer_configs]),
        layer_groups=json.dumps(req.layer_groups) if req.layer_groups else None,
        layout_config=json.dumps(req.layout_config) if req.layout_config else None,
        story=json.dumps(req.story) if req.story else None,
        theme=json.dumps(req.theme) if req.theme else None,
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
    await record_audit(db, user, "portal.create", "portal", portal.id, {"title": portal.title})
    return PortalOut.from_orm_json(portal)


@router.get("/authz")
async def portal_authz(request: Request, db: AsyncSession = Depends(get_db)):
    """nginx `auth_request` target for the SERVER-SIDE published-portal access gate. Returns 200
    (allow) or 401/403 (deny) — nginx serves the static bundle only on a 2xx. Allows public/password
    portals, SPA routes, and unknown slugs; enforces the login-based tiers (organization / owner)
    against the session COOKIE. Declared before `/{portal_id}` so this static path wins the match.

    Password stays a client-side gate for now (there's no login for a password visitor — a
    server-side version needs a password→cookie unlock flow; tracked as a follow-up)."""
    # nginx forwards the ORIGINAL request path as X-Original-URI; fall back to our own path in tests.
    uri = request.headers.get("x-original-uri") or request.url.path
    parts = [p for p in uri.split("?", 1)[0].split("/") if p]  # ['portals', <slug>, ...]
    slug = parts[1] if len(parts) >= 2 and parts[0] == "portals" else None
    if not slug:
        return Response(status_code=200)  # /portals/ list route — the SPA handles it
    portal = (await db.execute(select(Portal).where(Portal.slug == slug))).scalar_one_or_none()
    if not portal or not portal.published:
        return Response(status_code=200)  # SPA route (by numeric id) or a missing bundle → nginx 404s
    if portal.access_type == "public":
        return Response(status_code=200)
    if portal.access_type == "password":
        # Served only once the correct password minted the per-portal unlock cookie (POST /unlock).
        return Response(status_code=200 if _is_unlocked(request, portal.id) else 401)
    # Login-based tiers: organization (any member) | owner (creator + admins). Legacy 'private' == org.
    user = await resolve_cookie_user(request, db)
    if user is None:
        return Response(status_code=401)
    if portal.access_type == "owner" and not (
            user.id == portal.user_id or user.role in ("admin", "owner")):
        return Response(status_code=403)
    return Response(status_code=200)


@router.get("/preview-authz")
async def preview_authz(request: Request, db: AsyncSession = Depends(get_db)):
    """nginx `auth_request` target for the R2 editor PREVIEW bundles (served at /portals/_preview/{id}/).
    These are unpublished, unlisted renders of a portal's CURRENT editor state — only a signed-in
    workspace member may view them. 200 (allow) if a valid session cookie resolves to a user, else 401.
    Declared before `/{portal_id}` so this static path wins the match."""
    user = await resolve_cookie_user(request, db)
    return Response(status_code=200 if user is not None else 401)


class PortalUnlock(BaseModel):
    password: str


@router.get("/{slug}/gate")
async def portal_gate_info(slug: str, db: AsyncSession = Depends(get_db)):
    """PUBLIC: minimal info the /portal-gate page needs to render the right prompt (a password box
    for a password portal, or a 'sign in' hand-off for the login tiers). By slug like the bundle."""
    portal = (await db.execute(select(Portal).where(Portal.slug == slug))).scalar_one_or_none()
    if not portal or not portal.published:
        raise HTTPException(404, "Portal not found.")
    return {"access_type": portal.access_type, "title": portal.title}


@router.post("/{slug}/unlock", status_code=204)
async def portal_unlock(slug: str, body: PortalUnlock, request: Request,
                        db: AsyncSession = Depends(get_db)):
    """PUBLIC: verify a password portal's password server-side and mint the per-portal unlock cookie
    that the access gate (authz) checks. Wrong password → 401. Only valid for password portals."""
    portal = (await db.execute(select(Portal).where(Portal.slug == slug))).scalar_one_or_none()
    if not portal or not portal.published or portal.access_type != "password":
        raise HTTPException(404, "Portal not found.")
    if not portal.access_password_hash or not _pwd.verify(body.password, portal.access_password_hash):
        raise HTTPException(401, "Incorrect password.")
    resp = Response(status_code=204)
    secure = request.headers.get("x-forwarded-proto", request.url.scheme) == "https"
    resp.set_cookie(_unlock_cookie_name(portal.id), _make_unlock_token(portal.id),
                    max_age=7 * 24 * 3600, httponly=True, samesite="lax", secure=secure, path="/")
    return resp


@router.get("/{portal_id}", response_model=PortalOut)
async def get_portal(portal_id: int, user: User = Depends(require_scope("portal:read")), db: AsyncSession = Depends(get_db)):
    portal = await _get_portal(portal_id, db)
    return PortalOut.from_orm_json(portal)


@router.put("/{portal_id}", response_model=PortalOut)
async def update_portal(portal_id: int, req: PortalUpdate, user: User = Depends(require_scope("portal:write")), db: AsyncSession = Depends(get_db)):
    portal = await _get_portal(portal_id, db)
    if req.title is not None:
        # The slug (URL) is STABLE — set once at creation, never changed by a rename, so a portal's
        # public URL never breaks when its title changes. Only the display title updates here.
        portal.title = req.title
    if req.description is not None:
        portal.description = req.description
    if req.template_id is not None:
        portal.template_id = req.template_id
    if req.basemap is not None:
        portal.basemap = req.basemap
    if req.layer_configs is not None:
        portal.layer_configs = json.dumps([lc.model_dump() for lc in req.layer_configs])
    if req.layer_groups is not None:
        # An empty list clears the tree back to a flat list; a populated list sets the folder tree.
        portal.layer_groups = json.dumps(req.layer_groups) if req.layer_groups else None
    if req.layout_config is not None:
        # An empty dict clears back to the webmap default; a populated manifest sets the experience.
        portal.layout_config = json.dumps(req.layout_config) if req.layout_config else None
    if req.story is not None:
        # An empty dict / no sections clears the story; a populated one sets the storymap content.
        portal.story = json.dumps(req.story) if req.story else None
    if req.theme is not None:
        # An empty dict clears back to the template's own theme; a populated one sets the colour theme.
        portal.theme = json.dumps(req.theme) if req.theme else None
    if req.initial_view is not None:
        portal.initial_view = json.dumps(req.initial_view)
    if req.access_type is not None:
        portal.access_type = req.access_type
    if req.access_password is not None:
        from hashlib import sha256
        from passlib.context import CryptContext
        portal.access_password_hash = CryptContext(schemes=["bcrypt"], deprecated="auto").hash(req.access_password)
        portal.access_password_sha256 = sha256(req.access_password.encode()).hexdigest()

    # The slug (and therefore the published URL) is immutable, so nothing to move here. Edits to a
    # published portal (title/layers/basemap) take effect when the user re-publishes, as before.
    await db.commit()
    await db.refresh(portal)
    # A published portal's layer_configs may have changed which layers it exposes → refresh the
    # public-read cache so added layers become reachable and removed ones stop being served.
    if portal.published:
        invalidate_public_layers()
    return PortalOut.from_orm_json(portal)


@router.post("/{portal_id}/publish", response_model=PortalOut)
async def publish_portal(portal_id: int, user: User = Depends(require_scope("portal:publish")), db: AsyncSession = Depends(get_db)):
    portal = await _get_portal(portal_id, db)
    await _rebuild_bundle(portal, db)

    portal.published = True
    portal.published_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(portal)
    invalidate_public_layers()  # this portal's layers are now publicly readable
    await record_audit(db, user, "portal.publish", "portal", portal.id,
                       {"title": portal.title, "slug": portal.slug, "access": portal.access_type})
    return PortalOut.from_orm_json(portal)


class PortalPreview(BaseModel):
    # The editor's CURRENT (possibly unsaved) state — same shape as PortalUpdate, all optional. Any
    # field left None falls back to the portal's persisted value, so a partial payload still renders.
    title: str | None = None
    description: str | None = None
    template_id: str | None = None
    layer_configs: list | None = None
    layer_groups: list | None = None
    layout_config: dict | None = None
    story: dict | None = None
    theme: dict | None = None
    initial_view: dict | None = None
    basemap: str | None = None


@router.post("/{portal_id}/preview")
async def preview_portal(portal_id: int, req: PortalPreview,
                         user: User = Depends(require_scope("portal:write")), db: AsyncSession = Depends(get_db)):
    """R2: render the portal's CURRENT editor state to an UNLISTED bundle at data/portals/_preview/{id}/
    (served, logged-in-only, at /portals/_preview/{id}/) so the editor can iframe the REAL portal runtime
    as a faithful WYSIWYG preview. Nothing is persisted; access_type is forced public (the nginx
    preview gate does the auth). Returns the preview URL (the caller cache-busts on reload)."""
    portal = await _get_portal(portal_id, db)
    lc = req.layer_configs if req.layer_configs is not None else json.loads(portal.layer_configs or "[]")
    await _assemble_bundle(
        db, slug=f"_preview/{portal_id}", title=req.title or portal.title,
        layer_configs=lc,
        layer_groups=req.layer_groups if req.layer_groups is not None else _pjson(portal.layer_groups),
        layout_config=req.layout_config if req.layout_config is not None else _pjson(portal.layout_config),
        story=req.story if req.story is not None else _pjson(portal.story),
        theme=req.theme if req.theme is not None else _pjson(portal.theme),
        initial_view=req.initial_view if req.initial_view is not None else _pjson(portal.initial_view),
        template_id=req.template_id or portal.template_id,
        basemap=req.basemap if req.basemap is not None else portal.basemap,
        description=req.description if req.description is not None else portal.description,
        access_type="public", password_sha256=None, owner_id=portal.user_id,
    )
    return {"url": f"/portals/_preview/{portal_id}/"}


# Images embedded in the About page (uploaded from the editor's WYSIWYG). Served by the public
# GET below because both the editor preview and the published about.html reference them by URL.
# SVG is deliberately excluded (script-capable when opened directly).
_ASSET_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
_MAX_ASSET_SIZE = 10 * 1024 * 1024  # 10 MB


@router.post("/{portal_id}/assets")
async def upload_portal_asset(
    portal_id: int,
    file: UploadFile = File(...),
    user: User = Depends(require_scope("portal:publish")),
    db: AsyncSession = Depends(get_db),
):
    """Upload an image for the portal's About documentation. Returns the public URL to embed."""
    portal = await _get_portal(portal_id, db)
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in _ASSET_EXTENSIONS:
        raise HTTPException(400, f"Unsupported image type ({ext or 'no extension'}). "
                                 f"Use: {', '.join(sorted(_ASSET_EXTENSIONS))}")
    data = await file.read()
    if len(data) > _MAX_ASSET_SIZE:
        raise HTTPException(400, "Image too large (max 10 MB).")
    settings = get_settings()
    asset_dir = f"{settings.data_dir}/portal_assets/{portal.id}"
    os.makedirs(asset_dir, exist_ok=True)
    name = f"{uuid.uuid4().hex}{ext}"
    with open(os.path.join(asset_dir, name), "wb") as f:
        f.write(data)
    return {"url": f"/api/portals/{portal.id}/assets/{name}"}


@router.get("/{portal_id}/assets/{filename}")
async def portal_asset(portal_id: int, filename: str):
    """PUBLIC image serving for About documentation (published portals are unauthenticated).
    The filename is a server-minted uuid + extension — the strict pattern below is the
    traversal guard AND limits exposure to exactly what the upload endpoint can create."""
    import re
    from fastapi.responses import FileResponse
    if not re.fullmatch(r"[0-9a-f]{32}\.(png|jpe?g|gif|webp)", filename):
        raise HTTPException(404, "Not found.")
    settings = get_settings()
    path = f"{settings.data_dir}/portal_assets/{portal_id}/{filename}"
    if not os.path.isfile(path):
        raise HTTPException(404, "Not found.")
    return FileResponse(path, headers={"Cache-Control": "public, max-age=86400, immutable"})


@router.post("/{portal_id}/unpublish", response_model=PortalOut)
async def unpublish_portal(portal_id: int, user: User = Depends(require_scope("portal:publish")), db: AsyncSession = Depends(get_db)):
    import shutil, os
    settings = get_settings()
    portal = await _get_portal(portal_id, db)
    portal_dir = f"{settings.data_dir}/portals/{portal.slug}"
    if os.path.exists(portal_dir):
        shutil.rmtree(portal_dir)
    portal.published = False
    portal.published_at = None
    await db.commit()
    await db.refresh(portal)
    invalidate_public_layers()  # its layers are no longer publicly readable (unless shared/in another portal)
    await record_audit(db, user, "portal.unpublish", "portal", portal.id, {"title": portal.title})
    return PortalOut.from_orm_json(portal)


@router.delete("/{portal_id}", status_code=204)
async def delete_portal(portal_id: int, user: User = Depends(require_scope("portal:write")), db: AsyncSession = Depends(get_db)):
    import shutil, os
    settings = get_settings()
    portal = await _get_portal(portal_id, db)
    portal_dir = f"{settings.data_dir}/portals/{portal.slug}"
    if os.path.exists(portal_dir):
        shutil.rmtree(portal_dir)
    # R2: the editor preview bundle for this portal (data/portals/_preview/{id}) — remove it too.
    preview_dir = f"{settings.data_dir}/portals/_preview/{portal.id}"
    if os.path.exists(preview_dir):
        shutil.rmtree(preview_dir, ignore_errors=True)
    # About-page + story images belong to this portal alone — remove them too (they'd leak otherwise).
    assets_dir = f"{settings.data_dir}/portal_assets/{portal.id}"
    if os.path.exists(assets_dir):
        shutil.rmtree(assets_dir, ignore_errors=True)
    was_published = portal.published
    portal_title = portal.title
    await db.delete(portal)
    await db.commit()
    if was_published:
        invalidate_public_layers()  # its layers are no longer exposed via this portal
    await record_audit(db, user, "portal.delete", "portal", portal_id, {"title": portal_title})


# ── Area-select export (offloaded to Celery) ─────────────────────────────────

class ExportItem(BaseModel):
    layer_id: int
    layer_type: str          # vector | raster
    format: str = "geojson"  # geojson | gpkg | csv | tif


class ExportBundleRequest(BaseModel):
    bbox: str                # "minx,miny,maxx,maxy" in EPSG:4326
    items: list[ExportItem]
    target_crs: str = "4326"  # '4326' (default) | 'native' (GPKG/CSV keep each layer's own CRS)


def _sweep_old_exports(settings, max_age: int = 3600) -> None:
    import time
    d = f"{settings.data_dir}/temp/exports"
    if not os.path.isdir(d):
        return
    now = time.time()
    for f in os.listdir(d):
        fp = os.path.join(d, f)
        try:
            if now - os.path.getmtime(fp) > max_age:
                os.unlink(fp)
        except Exception:
            pass


@router.post("/{slug}/export-bundle", status_code=202)
async def start_export_bundle(slug: str, req: ExportBundleRequest, db: AsyncSession = Depends(get_db)):
    """Validate + enqueue a clip job; returns a job_id to poll. Heavy work runs in Celery."""
    from ..tasks.export import export_bundle as export_task
    portal = (await db.execute(select(Portal).where(Portal.slug == slug))).scalar_one_or_none()
    if not portal:
        raise HTTPException(404, "Portal not found.")
    configs = json.loads(portal.layer_configs or "[]")
    try:
        parts = [float(v) for v in req.bbox.split(",")]
        assert len(parts) == 4
    except Exception:
        raise HTTPException(400, "bbox must be 'minx,miny,maxx,maxy'.")

    resolved: list[dict] = []
    for it in req.items:
        if not any(c.get("layer_id") == it.layer_id and c.get("layer_type") == it.layer_type for c in configs):
            continue
        if it.layer_type == "vector":
            layer = (await db.execute(select(VectorLayer).where(
                VectorLayer.id == it.layer_id, VectorLayer.status == "ready"))).scalar_one_or_none()
            if layer and layer.storage_backend == "geoparquet" and layer.s3_key:
                # File-backed layer: the clip runs on the GeoParquet via DuckDB, not PostGIS.
                resolved.append({"type": "geoparquet", "s3_key": layer.s3_key,
                                 "crs": layer.crs or "EPSG:4326",  # for a lossless native download
                                 "name": layer.name, "format": it.format})
            elif layer:
                resolved.append({"type": "vector", "schema": layer.schema_name,
                                 "table": layer.table_name, "name": layer.name, "format": it.format})
        else:
            layer = (await db.execute(select(RasterLayer).where(
                RasterLayer.id == it.layer_id, RasterLayer.status == "ready"))).scalar_one_or_none()
            if layer:
                resolved.append({"type": "raster", "s3_key": layer.s3_key, "name": layer.name})
    if not resolved:
        raise HTTPException(400, "No exportable layers in the request.")

    _sweep_old_exports(get_settings())
    task = export_task.delay(req.bbox, resolved, target_crs=req.target_crs)
    return {"job_id": task.id}


@router.get("/{slug}/export-status/{job_id}")
async def export_status(slug: str, job_id: str):
    from ..celery_app import celery_app
    settings = get_settings()
    if os.path.exists(f"{settings.data_dir}/temp/exports/{job_id}.zip"):
        return {"status": "ready"}
    state = celery_app.AsyncResult(job_id).state
    if state == "FAILURE":
        return {"status": "error"}
    if state == "STARTED":
        return {"status": "processing"}
    return {"status": "queued"}


@router.get("/{slug}/export-download/{job_id}")
async def export_download(slug: str, job_id: str):
    import re
    from fastapi.responses import FileResponse
    if not re.fullmatch(r"[A-Za-z0-9_-]+", job_id):
        raise HTTPException(400, "Invalid job id.")
    settings = get_settings()
    path = f"{settings.data_dir}/temp/exports/{job_id}.zip"
    if not os.path.exists(path):
        raise HTTPException(404, "Export not ready or expired.")
    return FileResponse(path, media_type="application/zip", filename="selection.zip")


async def _get_portal(portal_id: int, db: AsyncSession) -> Portal:
    """Id-only lookup (shared workspace: every member sees every portal; the ROLE dependency already
    gated whether the caller may act, and a portal's audience is its published access_type)."""
    result = await db.execute(select(Portal).where(Portal.id == portal_id))
    portal = result.scalar_one_or_none()
    if not portal:
        raise HTTPException(404, "Portal not found.")
    return portal
