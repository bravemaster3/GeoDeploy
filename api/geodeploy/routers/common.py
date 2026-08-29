from fastapi import HTTPException
"""Shared helpers for the resource routers (A-01 shared-workspace + A-02 per-resource sharing).

Since A-01, GeoDeploy is a single shared workspace and the ROLE (viewer/editor/admin/owner)
controls what a member may DO. A-02 adds a per-resource `visibility` axis on top:
`private` (creator + admins only) ⊂ `organization` (every member) ⊂ `public` (organization +
exposed to the internet via STAC / raw assets). `user_id` is "created by" provenance AND the
owner-check for a private resource.
"""
import json
import logging

from sqlalchemy import or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..models import AuditLog, Portal, UploadJob, User

logger = logging.getLogger(__name__)


def by_ref(model, ref):
    """Match a layer by its STABLE PUBLIC `uid` or its legacy integer id.

    Public URLs carry `uid` (`models.new_uid`). The integer is accepted only so links minted before
    that column existed keep resolving — nothing emits one now (`share_links.public_ref`). Keep it
    that way: an integer id is unique only within one kind and one database, so it says nothing
    durable about *which* dataset a saved link meant. A `vector-`/`raster-` prefix is tolerated so a
    STAC item id or an OGC API - Features collection id can be passed straight through.

    Returns a SQLAlchemy condition; combine it with `visible_to(...)` on authenticated routes.
    """
    raw = str(ref or "").strip()
    if raw.startswith(("vector-", "raster-")):
        raw = raw.split("-", 1)[1]
    conds = []
    if raw:
        conds.append(model.uid == raw)
    # A uid is hex, so it CAN be all digits — check both rather than either/or. But only when the
    # number FITS the id column: `id` is a 32-bit integer in Postgres, and comparing it to a bigger
    # value is not "no match", it is a DataError — a 500 where the caller should have seen a 404.
    # A twelve-digit uid is a perfectly ordinary uid, and one turned up in testing straight away.
    if raw.isdigit():
        try:
            number = int(raw)
        except ValueError:
            number = None
        if number is not None and -2147483648 <= number <= 2147483647:
            conds.append(model.id == number)
    if not conds:
        return model.id == -1        # matches nothing, without special-casing at every call site
    return or_(*conds)


async def portals_using(db: AsyncSession, layer_type: str, layer_id: int) -> list[Portal]:
    """Portals whose `layer_configs` reference (layer_type, layer_id). Powers the delete-confirmation
    'used in these portals' warning AND the prune-on-delete. Portals are few, so a full scan + JSON
    parse is fine."""
    portals = (await db.execute(select(Portal))).scalars().all()
    hits = []
    for p in portals:
        try:
            configs = json.loads(p.layer_configs or "[]")
        except Exception:  # noqa: BLE001
            configs = []
        if any(c.get("layer_type") == layer_type and c.get("layer_id") == layer_id for c in configs):
            hits.append(p)
    return hits


async def prune_layer_from_portals(db: AsyncSession, layer_type: str, layer_id: int) -> list[Portal]:
    """Remove a (now-deleted) layer from every portal's `layer_configs` and re-publish the PUBLISHED
    ones so the live map + editor stop showing a dangling 'ghost' layer. Best-effort re-publish (a
    failure never blocks the delete). Returns the affected portals. Call AFTER the layer row is gone."""
    affected = await portals_using(db, layer_type, layer_id)
    if not affected:
        return []
    for p in affected:
        configs = [c for c in json.loads(p.layer_configs or "[]")
                   if not (c.get("layer_type") == layer_type and c.get("layer_id") == layer_id)]
        p.layer_configs = json.dumps(configs)
        if p.layer_groups:  # V-13: also drop the layer node from the folder tree
            tree = _strip_layer_from_tree(json.loads(p.layer_groups), layer_type, layer_id)
            p.layer_groups = json.dumps(tree) if tree else None
    await db.commit()
    from .portals import _rebuild_bundle  # lazy import avoids a circular import at module load
    for p in affected:
        if p.published:
            try:
                await _rebuild_bundle(p, db)
            except Exception:  # noqa: BLE001 — a re-publish failure must not fail the delete
                logger.warning("re-publish after layer prune failed for portal %s", p.id, exc_info=True)
    return affected


def _strip_layer_from_tree(nodes: list, layer_type: str, layer_id: int) -> list:
    """Recursively remove a layer node (matching layer_type+layer_id) from a V-13 folder tree,
    keeping the group structure intact."""
    out = []
    for n in nodes or []:
        if "layer_id" in n:
            if n.get("layer_type") == layer_type and n.get("layer_id") == layer_id:
                continue
            out.append(n)
        elif "children" in n:
            out.append({**n, "children": _strip_layer_from_tree(n.get("children") or [], layer_type, layer_id)})
        else:
            out.append(n)
    return out


async def record_audit(db: AsyncSession, actor, action: str, resource_type: str | None = None,
                       resource_id=None, detail: dict | None = None) -> None:
    """Append an audit entry (A-05). BEST-EFFORT + self-committing — a failed audit write must NEVER
    break the operation being logged, so call this AFTER the mutation has committed. `actor` is the
    acting User (or None for system/anonymous)."""
    try:
        db.add(AuditLog(
            actor_id=getattr(actor, "id", None),
            actor_name=(getattr(actor, "name", None) or getattr(actor, "email", None)),
            action=action,
            resource_type=resource_type,
            resource_id=None if resource_id is None else str(resource_id),
            detail=json.dumps(detail) if detail else None,
        ))
        await db.commit()
    except Exception:  # noqa: BLE001 — auditing is never allowed to fail the real operation
        logger.warning("audit write failed for action=%s", action, exc_info=True)
        try:
            await db.rollback()
        except Exception:  # noqa: BLE001
            pass

# Roles that see + act on EVERY resource regardless of its visibility (workspace governance:
# bulk review, delete-reassign, sharing changes). Keep in sync with deps.ROLE_ORDER's top tiers.
_GOVERNANCE_ROLES = ("admin", "owner")


def demo_upload_cap(file_size: int | None, user: User | None = None) -> None:
    """DEMO ONLY: cap a DIRECT-TO-STORAGE upload at the multipart initiate.

    The Content-Length middleware in main.py cannot see these — the bytes go browser to S3 and never
    pass through the API — so the size declared at initiate is the one chance to refuse. No-op unless
    demo mode is on, so a normal install keeps its full limits.

    THE OWNER IS EXEMPT. The cap exists so a public demo cannot be used as free storage by its
    visitors; the person who runs the instance is not one of those, and making them switch demo mode
    off to load a test layer means taking the demo down to work on it. Only `owner` — not `admin` —
    because a demo can hand out admin to show the role off, and that must not hand out the disk too.

    This is the ONE place the exemption lives, and it is deliberately not mirrored into the ASGI
    middleware: that runs before authentication, so honouring a role there would mean re-decoding the
    token, re-checking `token_version` (revocation) and hitting the database, all in a layer that
    exists to reject on Content-Length alone. A second, weaker copy of authentication is a worse
    thing to own than a cap the owner routes around. It costs nothing in practice — the client sends
    anything at or above 48 MB direct-to-storage (`LARGE_UPLOAD_THRESHOLD`), so every upload big
    enough to meet a 500 MB cap arrives HERE, where the caller is already authenticated.
    """
    settings = get_settings()
    if not settings.geodeploy_demo_mode or not file_size:
        return
    if user is not None and getattr(user, "role", None) == "owner":
        return
    limit = settings.geodeploy_demo_max_upload_mb * 1024 * 1024
    if file_size > limit:
        raise HTTPException(413, (
            f"This demo caps uploads at {settings.geodeploy_demo_max_upload_mb} MB "
            f"(yours is {file_size / 1024 / 1024:.0f} MB). The limit exists only here — a GeoDeploy "
            f"you install yourself has no such cap."))


def visible_to(user: User, model):
    """Workspace visibility filter for a resource `model`'s list / by-id lookups — THE A-02 SEAM.

    Admins/owner see everything (governance). Everyone else sees resources that are not private,
    plus their OWN private resources. `model` is the mapped class (VectorLayer / RasterLayer /
    ExternalSource / Portal) — all four carry `visibility` + `user_id`.

    Public-by-id display endpoints (tiles, viewport features, COG) that published portals depend on
    do NOT use this filter — they gate on `_publicly_readable` / portal membership instead.
    """
    if user.role in _GOVERNANCE_ROLES:
        return true()
    return or_(model.visibility != "private", model.user_id == user.id)


def apply_sharing(resource, body) -> None:
    """Apply a SharingUpdate to a layer: resolve the visibility axis (an explicit `visibility` wins;
    otherwise the legacy `is_public` bool maps True→public / False→organization), keep the derived
    `is_public` column in sync, and set whichever catalog-metadata fields were provided."""
    data = body.model_dump(exclude_unset=True)
    vis = data.pop("visibility", None)
    is_pub = data.pop("is_public", None)
    if vis is None and is_pub is not None:
        vis = "public" if is_pub else "organization"
    if vis is not None:
        resource.visibility = vis
        resource.is_public = (vis == "public")
    for field, value in data.items():   # abstract / keywords / license / attribution
        setattr(resource, field, value)


async def busy_job_progress(db: AsyncSession, layers, layer_type: str) -> dict[int, tuple[int, str | None]]:
    """`{layer_id: (progress, current_step)}` for layers still `queued`/`processing`, read from each
    layer's LATEST UploadJob (ONE query). Lets the list response carry live ingest progress even for
    CLI uploads or after a page reload — the browser's per-session `pollJob` only covers uploads made
    in that tab. Returns {} when nothing is busy (the common case → no extra query)."""
    busy = [l.id for l in layers if l.status in ("queued", "processing")]
    if not busy:
        return {}
    rows = (await db.execute(
        select(UploadJob.layer_id, UploadJob.progress, UploadJob.current_step)
        .where(UploadJob.layer_type == layer_type, UploadJob.layer_id.in_(busy))
        .order_by(UploadJob.layer_id, UploadJob.created_at.desc()))).all()
    out: dict[int, tuple[int, str | None]] = {}
    for lid, progress, step in rows:
        out.setdefault(lid, (progress, step))  # first per layer = latest (created_at desc)
    return out


async def creator_names(db: AsyncSession, rows) -> dict[int, str]:
    """user_id → display name for a list of resource rows (ONE query, no per-row lookups).
    Powers the "created by" chips + creator filter in My Data / Portals."""
    ids = {r.user_id for r in rows if getattr(r, "user_id", None) is not None}
    if not ids:
        return {}
    res = await db.execute(select(User.id, User.name).where(User.id.in_(ids)))
    return {uid: name for uid, name in res.all()}
