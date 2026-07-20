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

from ..models import AuditLog, Portal, UploadJob, User

logger = logging.getLogger(__name__)


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
