"""Server-side published-portal access gate (A-02 follow-up).

Published portals are static bundles; nginx `auth_request`s `GET /api/portals/authz` before serving
them. The endpoint returns 200 (allow) / 401 / 403 based on the portal's `access_type` and the
session COOKIE. Login mirrors the JWT into that HttpOnly cookie. These tests pin the decision matrix
and the cookie plumbing.
"""
import json

import pytest
from jose import jwt
from passlib.context import CryptContext

from geodeploy.config import get_settings
from geodeploy.deps import SESSION_COOKIE
from geodeploy.models import Portal, User

_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")

OWNER, ADMIN, EDITOR, VIEWER = 1, 2, 3, 4
_ROLE = {OWNER: "owner", ADMIN: "admin", EDITOR: "editor", VIEWER: "viewer"}


def _token(uid):
    return jwt.encode({"sub": str(uid)}, get_settings().secret_key, algorithm="HS256")


async def _seed(db):
    for uid, role in _ROLE.items():
        db.add(User(id=uid, email=f"u{uid}@example.com", name=f"U{uid}",
                    hashed_password=_pwd.hash("pw"), is_admin=role in ("admin", "owner"), role=role))
    # Portals owned by the EDITOR, one per access tier, all published.
    for pid, slug, access in ((10, "pubp", "public"), (11, "pwdp", "password"),
                              (12, "orgp", "organization"), (13, "ownp", "owner")):
        db.add(Portal(id=pid, user_id=EDITOR, title=slug, slug=slug, access_type=access,
                      published=True, layer_configs=json.dumps([])))
    await db.commit()


async def _authz(client, uri, token=None):
    headers = {"X-Original-URI": uri}
    cookies = {SESSION_COOKIE: token} if token else {}
    return await client.get("/api/portals/authz", headers=headers, cookies=cookies)


# ── Open tiers + non-portal paths always allow ────────────────────────────────────────────────

@pytest.mark.parametrize("slug", ["pubp", "pwdp"])
async def test_public_and_password_allow_without_cookie(client, db, slug):
    await _seed(db)
    assert (await _authz(client, f"/portals/{slug}/")).status_code == 200


async def test_unknown_and_spa_paths_allow(client, db):
    await _seed(db)
    assert (await _authz(client, "/portals/")).status_code == 200            # list route
    assert (await _authz(client, "/portals/3/edit")).status_code == 200      # SPA route (by id)
    assert (await _authz(client, "/portals/does-not-exist/")).status_code == 200


# ── Organization tier: any signed-in member ───────────────────────────────────────────────────

async def test_organization_requires_login(client, db):
    await _seed(db)
    assert (await _authz(client, "/portals/orgp/")).status_code == 401           # no cookie
    for uid in (VIEWER, EDITOR, ADMIN, OWNER):                                    # any member passes
        assert (await _authz(client, "/portals/orgp/", _token(uid))).status_code == 200


async def test_organization_rejects_bogus_cookie(client, db):
    await _seed(db)
    assert (await _authz(client, "/portals/orgp/", "not-a-jwt")).status_code == 401


# ── Owner tier: only the creator + admins/owner ───────────────────────────────────────────────

async def test_owner_tier_matrix(client, db):
    await _seed(db)
    assert (await _authz(client, "/portals/ownp/")).status_code == 401           # anonymous
    assert (await _authz(client, "/portals/ownp/", _token(VIEWER))).status_code == 403   # other member
    assert (await _authz(client, "/portals/ownp/", _token(EDITOR))).status_code == 200   # the creator
    assert (await _authz(client, "/portals/ownp/", _token(ADMIN))).status_code == 200    # admin
    assert (await _authz(client, "/portals/ownp/", _token(OWNER))).status_code == 200    # owner


async def test_deleted_user_cookie_denied(client, db):
    await _seed(db)
    ghost = _token(999)  # a token whose user row doesn't exist
    assert (await _authz(client, "/portals/orgp/", ghost)).status_code == 401


# ── Login mirrors the JWT into the session cookie; logout clears it ───────────────────────────

async def test_login_sets_session_cookie(client, db):
    await _seed(db)
    r = await client.post("/api/auth/login",
                          data={"username": "u3@example.com", "password": "pw"})
    assert r.status_code == 200
    assert SESSION_COOKIE in r.cookies
    # the cookie authorizes the org portal
    assert (await _authz(client, "/portals/orgp/", r.cookies[SESSION_COOKIE])).status_code == 200


async def test_logout_clears_session_cookie(client, db):
    await _seed(db)
    r = await client.post("/api/auth/logout")
    assert r.status_code == 204
    # Set-Cookie with an empty/expired value
    assert SESSION_COOKIE in r.headers.get("set-cookie", "")
