"""OIDC SSO endpoints (A-04). Public status + the login/callback redirect dance (Authlib).

The callback sets the `gd_session` cookie and redirects to the SPA's `/sso-callback`, which pulls the
JWT into localStorage via `GET /auth/session-token` (a top-level redirect can't populate localStorage).
Any refusal redirects to `/login?sso_error=…`. The account-linking policy lives in `services/oidc.py`.
"""
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..services.oidc import OidcError, build_oauth, get_oidc_config, resolve_user
from .auth import _create_token, _set_session_cookie
from .users import request_origin

router = APIRouter(prefix="/auth/oidc", tags=["auth"])


@router.get("/status")
async def oidc_status(db: AsyncSession = Depends(get_db)):
    """PUBLIC: whether SSO is on + the button label (drives the Login page). No secrets."""
    cfg = await get_oidc_config(db)
    return {"enabled": bool(cfg), "label": cfg["label"] if cfg else "Single sign-on"}


@router.get("/login")
async def oidc_login(request: Request, db: AsyncSession = Depends(get_db)):
    cfg = await get_oidc_config(db)
    if not cfg:
        raise HTTPException(404, "Single sign-on is not enabled.")
    oauth = build_oauth(cfg)
    redirect_uri = request_origin(request) + "/api/auth/oidc/callback"
    return await oauth.oidc.authorize_redirect(request, redirect_uri)


@router.get("/callback")
async def oidc_callback(request: Request, db: AsyncSession = Depends(get_db)):
    cfg = await get_oidc_config(db)
    if not cfg:
        return RedirectResponse("/login?sso_error=" + quote("Single sign-on is not enabled."))
    oauth = build_oauth(cfg)
    try:
        token = await oauth.oidc.authorize_access_token(request)  # validates id_token (JWKS + nonce)
        claims = token.get("userinfo") or {}
        user = await resolve_user(claims, cfg, db)
    except OidcError as exc:
        return RedirectResponse("/login?sso_error=" + quote(str(exc)))
    except Exception:  # noqa: BLE001 — any protocol/network failure → a generic, safe message
        return RedirectResponse("/login?sso_error=" + quote("Single sign-on failed. Please try again."))

    jwt_token = _create_token(user)
    resp = RedirectResponse("/sso-callback", status_code=302)
    _set_session_cookie(resp, jwt_token, request)  # SPA reads it back via /auth/session-token
    return resp
