from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.security import OAuth2PasswordRequestForm
from jose import jwt
from passlib.context import CryptContext
from sqlalchemy import delete as sa_delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..database import get_db
from ..deps import SESSION_COOKIE, get_current_user
from ..models import Invitation, User
from ..schemas import (
    AcceptInviteRequest, ForgotPasswordRequest, InvitePublicOut, PasswordChangeRequest,
    PasswordResetRequest, TokenResponse, UserOut,
)
from ..services import notifications

router = APIRouter(prefix="/auth", tags=["auth"])
_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")


def _create_token(user_id: int, expires_delta: timedelta = timedelta(days=7)) -> str:
    settings = get_settings()
    payload = {
        "sub": str(user_id),
        "exp": datetime.now(timezone.utc) + expires_delta,
    }
    return jwt.encode(payload, settings.secret_key, algorithm="HS256")


def _set_session_cookie(response: Response, token: str, request: Request) -> None:
    """Mirror the JWT into an HttpOnly cookie so top-level navigations to a published portal are
    authorized server-side (auth_request). Secure only under https (else the cookie won't ride an
    http dev instance). 7-day Max-Age matches the token; SameSite=Lax sends it on link navigations."""
    secure = request.headers.get("x-forwarded-proto", request.url.scheme) == "https"
    response.set_cookie(
        key=SESSION_COOKIE, value=token, max_age=7 * 24 * 3600,
        httponly=True, samesite="lax", secure=secure, path="/",
    )


@router.post("/login", response_model=TokenResponse)
async def login(request: Request, response: Response,
                form: OAuth2PasswordRequestForm = Depends(), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.email == form.username))
    user = result.scalar_one_or_none()
    if not user or not _pwd.verify(form.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = _create_token(user.id)
    _set_session_cookie(response, token, request)
    return TokenResponse(access_token=token)


@router.post("/session", status_code=204)
async def sync_session(request: Request, user: User = Depends(get_current_user)):
    """Mirror the caller's Bearer token into the session cookie — the SPA calls this after login and
    on boot so an EXISTING localStorage session (from before cookies existed) also gets the cookie
    that the published-portal access gate needs. Idempotent."""
    resp = Response(status_code=204)
    auth = request.headers.get("Authorization", "")
    token = auth[7:].strip() if auth[:7].lower() == "bearer " else None
    if token:
        _set_session_cookie(resp, token, request)
    return resp


@router.post("/logout", status_code=204)
async def logout():
    """Clear the session cookie (the SPA also drops its localStorage token)."""
    resp = Response(status_code=204)
    resp.delete_cookie(SESSION_COOKIE, path="/")
    return resp


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(get_current_user)):
    return user


# ── Invitation accept + password flows (RBAC, A-01) ──────────────────────────────────────────
# The token endpoints are PUBLIC by design: the recipient has no account yet (invite) or can't
# log in (reset). A valid 256-bit single-use token IS the credential.

async def _valid_invitation(token: str, db: AsyncSession) -> Invitation:
    from .users import hash_token, utcnow
    inv = (await db.execute(select(Invitation).where(
        Invitation.token_hash == hash_token(token)))).scalar_one_or_none()
    if not inv:
        raise HTTPException(404, "Invitation not found.")
    if inv.used_at is not None or inv.expires_at <= utcnow():
        raise HTTPException(410, "This link has expired or was already used.")
    return inv


@router.get("/invitations/{token}", response_model=InvitePublicOut)
async def invitation_info(token: str, db: AsyncSession = Depends(get_db)):
    """PUBLIC: what the accept/reset page shows for a valid link (email, role, purpose)."""
    inv = await _valid_invitation(token, db)
    return InvitePublicOut(email=inv.email, role=inv.role, purpose=inv.purpose)


@router.post("/invitations/{token}/accept", response_model=TokenResponse)
async def accept_invitation(token: str, body: AcceptInviteRequest, request: Request, response: Response,
                            db: AsyncSession = Depends(get_db)):
    """PUBLIC: redeem a signup invitation — set name + password, become a member with the
    invited role, and get logged in (a TokenResponse) in one step."""
    from .users import utcnow
    inv = await _valid_invitation(token, db)
    if inv.purpose != "invite":
        raise HTTPException(404, "Invitation not found.")
    existing = (await db.execute(select(User).where(User.email == inv.email))).scalar_one_or_none()
    if existing:
        raise HTTPException(409, "This email is already registered. Log in instead.")
    role = inv.role or "viewer"
    user = User(email=inv.email, name=body.name.strip() or inv.email,
                hashed_password=_pwd.hash(body.password),
                role=role, is_admin=role in ("admin", "owner"))
    db.add(user)
    inv.used_at = utcnow()
    await db.commit()
    await db.refresh(user)
    token_str = _create_token(user.id)
    _set_session_cookie(response, token_str, request)
    return TokenResponse(access_token=token_str)


@router.post("/forgot-password", status_code=202)
async def forgot_password(body: ForgotPasswordRequest, request: Request,
                          db: AsyncSession = Depends(get_db)):
    """PUBLIC self-service reset (C-08a): emails a single-use reset link. ALWAYS answers 202
    with the same body — whether the email exists, whether SMTP is configured, whether the send
    worked — so the endpoint can't be used to enumerate accounts. Rate-limited in nginx
    (zone pwreset). The owner may use this too: proving inbox control is the trust anchor,
    unlike admin-minted links (which are owner-guarded in users.py)."""
    from .users import hash_token, new_token, request_origin, utcnow, RESET_TTL
    email = body.email.lower().strip()
    user = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if user and await notifications.get_email_config(db):
        await db.execute(sa_delete(Invitation).where(Invitation.purpose == "password_reset",
                                                     Invitation.user_id == user.id))
        raw = new_token()
        db.add(Invitation(purpose="password_reset", email=user.email, user_id=user.id,
                          token_hash=hash_token(raw), expires_at=utcnow() + RESET_TTL))
        await db.commit()
        await notifications.send_password_reset_email(
            db, user.email, f"{request_origin(request)}/reset-password?token={raw}")
    return {"status": "ok"}


@router.post("/password-reset/{token}", status_code=204)
async def reset_password(token: str, body: PasswordResetRequest,
                         db: AsyncSession = Depends(get_db)):
    """PUBLIC: redeem an admin-minted reset link and set a new password."""
    from .users import utcnow
    inv = await _valid_invitation(token, db)
    if inv.purpose != "password_reset" or not inv.user_id:
        raise HTTPException(404, "Invitation not found.")
    target = (await db.execute(select(User).where(User.id == inv.user_id))).scalar_one_or_none()
    if not target:
        raise HTTPException(404, "User not found.")
    target.hashed_password = _pwd.hash(body.password)
    inv.used_at = utcnow()
    await db.commit()


@router.put("/password", status_code=204)
async def change_password(body: PasswordChangeRequest,
                          user: User = Depends(get_current_user),
                          db: AsyncSession = Depends(get_db)):
    """Self-service password change (any role). NOTE: outstanding 7-day JWTs are NOT revoked
    (no session store yet — planned with A-04 auth hardening)."""
    if not _pwd.verify(body.current_password, user.hashed_password):
        raise HTTPException(403, "Current password is incorrect.")
    user.hashed_password = _pwd.hash(body.new_password)
    await db.commit()
