"""Activity & audit log (A-05) — read side. Append-only entries are written by
`routers/common.record_audit` from the mutation endpoints; this exposes them to admins, filterable
(also powers a per-resource history via `resource_type` + `resource_id`). Admin-only + browser-only
(require_admin denies API tokens)."""
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..deps import require_admin
from ..models import AuditLog, User
from ..schemas import AuditLogOut

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("", response_model=list[AuditLogOut])
async def list_audit(resource_type: str | None = None, resource_id: str | None = None,
                     actor_id: int | None = None, action: str | None = None,
                     limit: int = 100, offset: int = 0,
                     _: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    q = select(AuditLog).order_by(AuditLog.created_at.desc())
    if resource_type:
        q = q.where(AuditLog.resource_type == resource_type)
    if resource_id:
        q = q.where(AuditLog.resource_id == str(resource_id))
    if actor_id:
        q = q.where(AuditLog.actor_id == actor_id)
    if action:
        q = q.where(AuditLog.action == action)
    q = q.limit(min(max(limit, 1), 500)).offset(max(offset, 0))
    rows = (await db.execute(q)).scalars().all()
    return [AuditLogOut.model_validate(r) for r in rows]
