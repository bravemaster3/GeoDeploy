import hashlib
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import get_settings
from .database import get_db
from .models import ApiToken, User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

# RBAC role ladder (A-01). `get_current_user` alone = viewer-level access.
# The JWT carries only sub+exp; the role is re-read from the DB row on every
# request, so role changes and user deletion take effect immediately.
ROLE_ORDER = {"viewer": 0, "editor": 1, "admin": 2, "owner": 3}

# A-03 scoped API tokens. Each scope → the minimum role required to grant OR exercise it. A token's
# request is allowed on a scoped route only when the token carries the scope AND its owner's live role
# clears the floor. Browser (JWT/cookie) sessions ignore scopes — they're governed by role alone.
TOKEN_PREFIX = "gdp_"
SCOPES = {
    "data:read": "viewer",
    "data:write": "editor",
    "portal:read": "viewer",
    "portal:write": "editor",
    "portal:publish": "editor",
    "users:admin": "admin",
}
_LAST_USED_THROTTLE = timedelta(minutes=5)  # don't write last_used_at on every request


def _naive_utcnow() -> datetime:
    """Naive UTC — matches how SQLite stores the model's DateTime columns (see users.utcnow)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def authenticate_api_token(raw: str, db: AsyncSession) -> ApiToken | None:
    """Resolve a `gdp_…` bearer value to a live ApiToken (None if unknown/revoked/expired).
    Bumps `last_used_at` at most once per throttle window so token auth in a loop isn't a write
    per request."""
    if not raw or not raw.startswith(TOKEN_PREFIX):
        return None
    token_hash = hashlib.sha256(raw.encode()).hexdigest()
    tok = (await db.execute(select(ApiToken).where(ApiToken.token_hash == token_hash))).scalar_one_or_none()
    if tok is None or tok.revoked_at is not None:
        return None
    now = _naive_utcnow()
    if tok.expires_at <= now:
        return None
    if tok.last_used_at is None or (now - tok.last_used_at) > _LAST_USED_THROTTLE:
        tok.last_used_at = now
        await db.commit()  # runs at dependency time; the session has no other pending changes yet
    return tok


async def get_current_user(
    request: Request,
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    credentials_error = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    # A-03: a `gdp_` bearer value is an API token, not a JWT. Resolve it to its owner and stash the
    # token on request.state so require_scope can enforce the token's scopes.
    if token.startswith(TOKEN_PREFIX):
        tok = await authenticate_api_token(token, db)
        if tok is None:
            raise credentials_error
        user = (await db.execute(select(User).where(User.id == tok.user_id))).scalar_one_or_none()
        if user is None:
            raise credentials_error
        request.state.api_token = tok
        return user

    try:
        settings = get_settings()
        payload = jwt.decode(token, settings.secret_key, algorithms=["HS256"])
        user_id: int = payload.get("sub")
        if user_id is None:
            raise credentials_error
    except JWTError:
        raise credentials_error

    result = await db.execute(select(User).where(User.id == int(user_id)))
    user = result.scalar_one_or_none()
    if user is None:
        raise credentials_error
    return user


def require_role(min_role: str):
    """Dependency factory: current user must have at least `min_role` on the ladder.

    DENIES API tokens (deny-by-default): a route on a role-only dep is browser-only. Anything a token
    should reach must use `require_scope` instead, so a token can never exceed the scopes it was
    granted by slipping through an unscoped route."""
    async def _dep(request: Request, user: User = Depends(get_current_user)) -> User:
        if getattr(request.state, "api_token", None) is not None:
            raise HTTPException(status_code=403, detail="This endpoint isn't available to API tokens.")
        if ROLE_ORDER.get(user.role, -1) < ROLE_ORDER[min_role]:
            raise HTTPException(status_code=403, detail=f"{min_role.capitalize()} access required")
        return user
    return _dep


def require_scope(scope: str):
    """Dependency factory (A-03): enforce a capability. The user must clear the scope's role floor
    (same 403 the browser gets today) AND, when the request is API-token-authed, the token must carry
    `scope`. For browser sessions the scope check is a no-op — role governs, exactly as before."""
    min_role = SCOPES[scope]

    async def _dep(request: Request, user: User = Depends(get_current_user)) -> User:
        if ROLE_ORDER.get(user.role, -1) < ROLE_ORDER[min_role]:
            raise HTTPException(status_code=403, detail=f"{min_role.capitalize()} access required")
        tok = getattr(request.state, "api_token", None)
        if tok is not None and scope not in tok.scopes.split():
            raise HTTPException(status_code=403, detail=f"Token missing scope: {scope}")
        return user
    return _dep


require_editor = require_role("editor")
require_admin = require_role("admin")   # admin OR owner
require_owner = require_role("owner")


# Name of the HttpOnly session cookie set at login (in ADDITION to the localStorage token the SPA
# uses for its XHR Authorization header). The cookie exists solely so a top-level browser navigation
# to a published portal carries credentials — an <a href="/portals/…"> GET can't send an Authorization
# header, but it does send cookies. Consumed by the portal `auth_request` (server-side access gate).
SESSION_COOKIE = "gd_session"


async def _user_from_token(token: str | None, db: AsyncSession) -> User | None:
    if not token:
        return None
    try:
        payload = jwt.decode(token, get_settings().secret_key, algorithms=["HS256"])
        uid = int(payload.get("sub"))
    except (JWTError, TypeError, ValueError):
        return None
    return (await db.execute(select(User).where(User.id == uid))).scalar_one_or_none()


async def resolve_bearer_user(request: Request, db: AsyncSession) -> User | None:
    """Best-effort user resolution from a raw Authorization header (no dependency wiring).

    For guards that are conditionally open (setup first-run) and therefore can't use the
    oauth2 dependency, which would 401 before the guard's own logic runs. Returns None on
    any failure — the caller decides what missing/invalid credentials mean.
    """
    auth = request.headers.get("Authorization", "")
    token = auth[7:].strip() if auth[:7].lower() == "bearer " else None
    return await _user_from_token(token, db)


async def resolve_cookie_user(request: Request, db: AsyncSession) -> User | None:
    """Best-effort user resolution from the session COOKIE (see SESSION_COOKIE). Powers the portal
    access gate's nginx auth_request, where the credential arrives as a cookie, not a header."""
    return await _user_from_token(request.cookies.get(SESSION_COOKIE), db)
