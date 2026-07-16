from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import get_settings
from .database import get_db
from .models import User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

# RBAC role ladder (A-01). `get_current_user` alone = viewer-level access.
# The JWT carries only sub+exp; the role is re-read from the DB row on every
# request, so role changes and user deletion take effect immediately.
ROLE_ORDER = {"viewer": 0, "editor": 1, "admin": 2, "owner": 3}


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    credentials_error = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
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
    """Dependency factory: current user must have at least `min_role` on the ladder."""
    async def _dep(user: User = Depends(get_current_user)) -> User:
        if ROLE_ORDER.get(user.role, -1) < ROLE_ORDER[min_role]:
            raise HTTPException(status_code=403, detail=f"{min_role.capitalize()} access required")
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
