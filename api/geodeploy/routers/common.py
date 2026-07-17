"""Shared helpers for the resource routers (A-01 shared-workspace + A-02 per-resource sharing).

Since A-01, GeoDeploy is a single shared workspace and the ROLE (viewer/editor/admin/owner)
controls what a member may DO. A-02 adds a per-resource `visibility` axis on top:
`private` (creator + admins only) ⊂ `organization` (every member) ⊂ `public` (organization +
exposed to the internet via STAC / raw assets). `user_id` is "created by" provenance AND the
owner-check for a private resource.
"""
from sqlalchemy import or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import UploadJob, User

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
