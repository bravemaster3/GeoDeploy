"""Scoped personal access tokens (A-03) — headless API access for scripts, CI, and the GeoLibre/QGIS
plugins.

Each user manages their OWN tokens. A token can NOT mint or manage tokens (privilege-escalation
guard), so these writes require a browser session (`_deny_token_auth`). A token's scopes can never
exceed its owner's live role at mint time; enforcement of scopes on the actual API happens in
`deps.require_scope`. Only the sha256 hash is stored — the raw `gdp_…` secret is returned once.
"""
import hashlib
import secrets
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..deps import ROLE_ORDER, SCOPES, TOKEN_PREFIX, _naive_utcnow, get_current_user
from ..models import ApiToken, User
from ..schemas import ApiTokenCreate, ApiTokenCreated, ApiTokenOut
from .common import record_audit

router = APIRouter(prefix="/tokens", tags=["tokens"])

ALLOWED_EXPIRY_DAYS = (30, 90, 365)  # mandatory expiry — no "never"; hard cap 365


def _deny_token_auth(request: Request) -> None:
    """Block token-authed callers from managing tokens (a leaked token can't spawn more)."""
    if getattr(request.state, "api_token", None) is not None:
        raise HTTPException(403, "API tokens cannot manage tokens — use a browser session.")


@router.get("", response_model=list[ApiTokenOut])
async def list_tokens(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """The caller's own active (non-revoked) tokens. Secrets are hashes server-side — not recoverable."""
    rows = (await db.execute(
        select(ApiToken).where(ApiToken.user_id == user.id, ApiToken.revoked_at.is_(None))
        .order_by(ApiToken.created_at.desc()))).scalars().all()
    return [ApiTokenOut.model_validate(t) for t in rows]


@router.post("", response_model=ApiTokenCreated, status_code=201)
async def create_token(body: ApiTokenCreate, request: Request,
                       user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    _deny_token_auth(request)
    scopes = list(dict.fromkeys(body.scopes))  # dedupe, preserve order
    for s in scopes:
        if s not in SCOPES:
            raise HTTPException(400, f"Unknown scope: {s}")
        if ROLE_ORDER[SCOPES[s]] > ROLE_ORDER.get(user.role, -1):
            raise HTTPException(400, f"Your role can't grant the scope: {s}")
    if body.expires_in_days not in ALLOWED_EXPIRY_DAYS:
        raise HTTPException(400, "expires_in_days must be one of 30, 90, 365.")

    raw = TOKEN_PREFIX + secrets.token_urlsafe(32)
    tok = ApiToken(
        user_id=user.id, name=body.name.strip(),
        token_hash=hashlib.sha256(raw.encode()).hexdigest(),
        prefix=raw[:12], scopes=" ".join(scopes),
        expires_at=_naive_utcnow() + timedelta(days=body.expires_in_days),
    )
    db.add(tok)
    await db.commit()
    await db.refresh(tok)
    await record_audit(db, user, "token.create", "token", tok.id, {"name": tok.name, "scopes": scopes})
    # ApiTokenCreated = ApiTokenOut + the raw secret (shown ONCE, never stored/listed).
    return ApiTokenCreated(**ApiTokenOut.model_validate(tok).model_dump(), token=raw)


@router.delete("/{token_id}", status_code=204)
async def revoke_token(token_id: int, request: Request,
                       user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    _deny_token_auth(request)
    tok = (await db.execute(select(ApiToken).where(
        ApiToken.id == token_id, ApiToken.user_id == user.id,
        ApiToken.revoked_at.is_(None)))).scalar_one_or_none()
    if tok is None:
        raise HTTPException(404, "Token not found.")
    tok.revoked_at = _naive_utcnow()
    await db.commit()
    await record_audit(db, user, "token.revoke", "token", tok.id, {"name": tok.name})
