"""Shared helpers for the resource routers (A-01 shared-workspace model).

Since A-01, GeoDeploy is a single shared workspace: every member SEES all data and
portals; the ROLE (viewer/editor/admin/owner) controls what they may do. `user_id`
on a resource is "created by" provenance, not an access boundary.
"""
from sqlalchemy import select, true
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import User


def visible_to(user: User):
    """Workspace visibility filter for list queries — THE A-02 SEAM.

    Currently every member sees every resource (`TRUE`). When per-resource sharing
    (A-02: private / organization / public) lands, this becomes something like
    `or_(X.visibility == "org", X.user_id == user.id)` — change it HERE, not in the
    individual list endpoints.
    """
    return true()


async def creator_names(db: AsyncSession, rows) -> dict[int, str]:
    """user_id → display name for a list of resource rows (ONE query, no per-row lookups).
    Powers the "created by" chips + creator filter in My Data / Portals."""
    ids = {r.user_id for r in rows if getattr(r, "user_id", None) is not None}
    if not ids:
        return {}
    res = await db.execute(select(User.id, User.name).where(User.id.in_(ids)))
    return {uid: name for uid, name in res.all()}
