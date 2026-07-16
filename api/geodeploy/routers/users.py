"""User management (RBAC, A-01): members list, invitations, roles, ownership transfer,
delete-with-reassign, password-reset links. Admin-gated (owner where noted).

Invitation tokens: 256-bit `secrets.token_urlsafe`, stored ONLY as a sha256 hash — the
raw token appears once in the create/regenerate response (the UI shows the copyable
link there and never again; "Regenerate" mints a fresh token for a pending invite).
"""
import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete as sa_delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..deps import require_admin, require_owner
from ..models import ExternalSource, Invitation, Portal, RasterLayer, User, VectorLayer
from ..schemas import InvitationOut, InviteCreate, RoleUpdate, UserAdminOut
from ..services import notifications

router = APIRouter(prefix="/users", tags=["users"])

INVITE_TTL = timedelta(days=7)
RESET_TTL = timedelta(hours=24)


def utcnow() -> datetime:
    """Naive UTC — matches how SQLite stores the model's DateTime columns."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def new_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _sync_is_admin(user: User) -> None:
    """Keep the deprecated boolean coherent with `role` (write-only sync — never read it)."""
    user.is_admin = user.role in ("admin", "owner")


async def _get_user(user_id: int, db: AsyncSession) -> User:
    target = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not target:
        raise HTTPException(404, "User not found.")
    return target


# ── Members ───────────────────────────────────────────────────────────────────────────────────

@router.get("", response_model=list[UserAdminOut])
async def list_users(_: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    users = (await db.execute(select(User).order_by(User.created_at))).scalars().all()

    async def _counts(model):
        rows = (await db.execute(
            select(model.user_id, func.count()).group_by(model.user_id))).all()
        return dict(rows)

    vec, ras, por, src = (await _counts(VectorLayer), await _counts(RasterLayer),
                          await _counts(Portal), await _counts(ExternalSource))
    out = []
    for u in users:
        o = UserAdminOut.model_validate(u)
        o.vector_count = vec.get(u.id, 0)
        o.raster_count = ras.get(u.id, 0)
        o.portal_count = por.get(u.id, 0)
        o.source_count = src.get(u.id, 0)
        out.append(o)
    return out


@router.put("/{user_id}/role", response_model=UserAdminOut)
async def update_role(user_id: int, body: RoleUpdate,
                      caller: User = Depends(require_admin),
                      db: AsyncSession = Depends(get_db)):
    target = await _get_user(user_id, db)
    if target.role == "owner":
        raise HTTPException(403, "The owner's role cannot be changed. Use ownership transfer.")
    if target.id == caller.id:
        raise HTTPException(400, "You cannot change your own role.")
    target.role = body.role  # schema pattern already excludes "owner"
    _sync_is_admin(target)
    await db.commit()
    await db.refresh(target)
    return UserAdminOut.model_validate(target)


@router.post("/{user_id}/transfer-ownership", response_model=UserAdminOut)
async def transfer_ownership(user_id: int,
                             caller: User = Depends(require_owner),
                             db: AsyncSession = Depends(get_db)):
    """Owner only: make `user_id` the workspace owner; the caller becomes an admin.
    The caller is demoted FIRST (separate flush) — the partial unique index forbids
    two 'owner' rows, so the order matters."""
    target = await _get_user(user_id, db)
    if target.id == caller.id:
        raise HTTPException(400, "You already are the owner.")
    caller.role = "admin"
    _sync_is_admin(caller)
    await db.flush()
    target.role = "owner"
    _sync_is_admin(target)
    await db.commit()
    await db.refresh(target)
    return UserAdminOut.model_validate(target)


@router.delete("/{user_id}", status_code=204)
async def delete_user(user_id: int,
                      caller: User = Depends(require_admin),
                      db: AsyncSession = Depends(get_db)):
    """Delete a member. Their layers/portals/sources are REASSIGNED to the workspace owner —
    nothing is destroyed and published portals keep working (S3 keys + schema names are stored
    as full strings per layer, so catalog reassignment never moves data). Their outstanding
    JWTs die immediately (get_current_user reloads the row per request)."""
    target = await _get_user(user_id, db)
    if target.role == "owner":
        raise HTTPException(403, "The owner cannot be deleted. Transfer ownership first.")
    if target.id == caller.id:
        raise HTTPException(400, "You cannot delete your own account — ask another admin.")

    owner = (await db.execute(select(User).where(User.role == "owner"))).scalar_one_or_none()
    heir = owner or caller  # owner always exists by construction; caller is the safe fallback
    for model in (VectorLayer, RasterLayer, ExternalSource, Portal):
        await db.execute(update(model).where(model.user_id == target.id).values(user_id=heir.id))
    await db.execute(update(Invitation).where(Invitation.invited_by == target.id)
                     .values(invited_by=heir.id))
    # Their pending password-reset links must die with the account.
    await db.execute(sa_delete(Invitation).where(Invitation.user_id == target.id))
    await db.delete(target)
    await db.commit()


# ── Invitations ───────────────────────────────────────────────────────────────────────────────

@router.get("/invitations", response_model=list[InvitationOut])
async def list_invitations(_: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Pending (unused, unexpired) signup invitations. Tokens are hashes server-side and are
    NOT recoverable here — use regenerate to mint a fresh link."""
    rows = (await db.execute(
        select(Invitation).where(Invitation.purpose == "invite",
                                 Invitation.used_at.is_(None),
                                 Invitation.expires_at > utcnow())
        .order_by(Invitation.created_at.desc()))).scalars().all()
    return [InvitationOut.model_validate(i) for i in rows]


@router.post("/invitations", response_model=InvitationOut, status_code=201)
async def create_invitation(body: InviteCreate,
                            caller: User = Depends(require_admin),
                            db: AsyncSession = Depends(get_db)):
    email = body.email.lower().strip()
    existing = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if existing:
        raise HTTPException(400, "A user with this email already exists.")
    # Replace semantics: one live invite per email.
    await db.execute(sa_delete(Invitation).where(Invitation.purpose == "invite",
                                                 Invitation.email == email))
    raw = new_token()
    inv = Invitation(purpose="invite", email=email, role=body.role, token_hash=hash_token(raw),
                     invited_by=caller.id, expires_at=utcnow() + INVITE_TTL)
    db.add(inv)
    await db.commit()
    await db.refresh(inv)
    notifications.send_invitation_email(email, f"/accept-invite?token={raw}", body.role)
    out = InvitationOut.model_validate(inv)
    out.token = raw  # shown ONCE — the UI builds the copyable link from this
    return out


@router.post("/invitations/{invitation_id}/regenerate", response_model=InvitationOut)
async def regenerate_invitation(invitation_id: int,
                                _: User = Depends(require_admin),
                                db: AsyncSession = Depends(get_db)):
    inv = (await db.execute(select(Invitation).where(
        Invitation.id == invitation_id, Invitation.purpose == "invite"))).scalar_one_or_none()
    if not inv or inv.used_at is not None:
        raise HTTPException(404, "Invitation not found.")
    raw = new_token()
    inv.token_hash = hash_token(raw)
    inv.expires_at = utcnow() + INVITE_TTL
    await db.commit()
    await db.refresh(inv)
    out = InvitationOut.model_validate(inv)
    out.token = raw
    return out


@router.delete("/invitations/{invitation_id}", status_code=204)
async def revoke_invitation(invitation_id: int,
                            _: User = Depends(require_admin),
                            db: AsyncSession = Depends(get_db)):
    inv = (await db.execute(select(Invitation).where(
        Invitation.id == invitation_id))).scalar_one_or_none()
    if not inv:
        raise HTTPException(404, "Invitation not found.")
    await db.delete(inv)
    await db.commit()


# ── Password reset (admin-minted link) ────────────────────────────────────────────────────────

@router.post("/{user_id}/reset-password-link", response_model=InvitationOut)
async def create_reset_link(user_id: int,
                            caller: User = Depends(require_admin),
                            db: AsyncSession = Depends(get_db)):
    target = await _get_user(user_id, db)
    if target.role == "owner" and caller.role != "owner":
        # An admin minting a reset link for the owner would be an account takeover.
        raise HTTPException(403, "Only the owner can reset the owner's password.")
    # One live reset link per user.
    await db.execute(sa_delete(Invitation).where(Invitation.purpose == "password_reset",
                                                 Invitation.user_id == target.id))
    raw = new_token()
    inv = Invitation(purpose="password_reset", email=target.email, user_id=target.id,
                     token_hash=hash_token(raw), invited_by=caller.id,
                     expires_at=utcnow() + RESET_TTL)
    db.add(inv)
    await db.commit()
    await db.refresh(inv)
    notifications.send_password_reset_email(target.email, f"/reset-password?token={raw}")
    out = InvitationOut.model_validate(inv)
    out.token = raw
    return out
