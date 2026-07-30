"""Activity & audit log (A-05) — read side. Append-only entries are written by
`routers/common.record_audit` from the mutation endpoints; this exposes them to admins, filterable
(also powers a per-resource history via `resource_type` + `resource_id`). Admin-only + browser-only
(require_admin denies API tokens).

**Paginated (2026-07-30).** The log only grows, so the UI must never ask for "everything": this
returns a `{items, total, limit, offset}` page and EVERY filter is applied SERVER-side, before the
page is cut. Filtering client-side over one big fetch would silently search only the slice already
downloaded — the bug this shape exists to prevent. Filters combine (AND). `since`/`until` are
absolute instants: the UI turns "this week"/"this month" into a timestamp in the VIEWER's timezone,
so the server never has to guess where the week starts.
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..deps import require_admin
from ..models import AuditLog, User
from ..schemas import AuditLogOut, AuditPage

router = APIRouter(prefix="/audit", tags=["audit"])

MAX_LIMIT = 500


def _instant(raw: str | None, field: str):
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(400, f"Invalid {field} — expected an ISO 8601 timestamp.")
    # created_at is stored naive-UTC (server_default=func.now()), so compare in the same frame.
    return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo else dt


@router.get("", response_model=AuditPage)
async def list_audit(resource_type: str | None = None, resource_id: str | None = None,
                     actor_id: int | None = None, action: str | None = None,
                     q: str | None = None, since: str | None = None, until: str | None = None,
                     limit: int = 20, offset: int = 0,
                     _: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    filters = []
    if resource_type:
        filters.append(AuditLog.resource_type == resource_type)
    if resource_id:
        filters.append(AuditLog.resource_id == str(resource_id))
    if actor_id:
        filters.append(AuditLog.actor_id == actor_id)
    if action:
        # `action` is dotted (portal.publish). A bare prefix filters a whole family: "portal" →
        # every portal.* entry; an exact value still matches itself.
        filters.append(or_(AuditLog.action == action,
                           AuditLog.action.like(f"{action}.%")))
    if q:
        needle = f"%{q.strip()}%"
        filters.append(or_(AuditLog.action.like(needle), AuditLog.actor_name.like(needle),
                           AuditLog.resource_id.like(needle), AuditLog.detail.like(needle)))
    lo, hi = _instant(since, "since"), _instant(until, "until")
    if lo:
        filters.append(AuditLog.created_at >= lo)
    if hi:
        filters.append(AuditLog.created_at <= hi)

    limit = min(max(limit, 1), MAX_LIMIT)
    offset = max(offset, 0)

    # One COUNT over the same predicate so the UI can render "N of M" and a real page count.
    total = await db.scalar(select(func.count()).select_from(AuditLog).where(*filters)) or 0
    rows = (await db.execute(
        select(AuditLog).where(*filters)
        .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())   # id breaks same-second ties
        .limit(limit).offset(offset))).scalars().all()
    return AuditPage(items=[AuditLogOut.model_validate(r) for r in rows],
                     total=total, limit=limit, offset=offset)


@router.get("/actions", response_model=list[str])
async def list_actions(_: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """The action values actually present, so the UI's filter offers real options instead of a
    hardcoded list that drifts every time a new mutation is instrumented."""
    rows = (await db.execute(
        select(AuditLog.action).distinct().order_by(AuditLog.action))).scalars().all()
    return [r for r in rows if r]
