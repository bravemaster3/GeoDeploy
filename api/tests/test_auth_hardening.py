"""A-04 session / token revocation via `token_version`.

The browser JWT carries `tv`; get_current_user rejects a token whose tv != the user's. A password
change/reset and "log out everywhere" bump it, killing outstanding JWTs — while the acting session
gets a re-issued token so it stays signed in. Pre-A-04 tv-less tokens read as tv=0 (no forced
re-login). API tokens (A-03) are NOT tv-versioned and are unaffected.
"""
from jose import jwt
from passlib.context import CryptContext

from geodeploy.config import get_settings
from geodeploy.models import Invitation, User

_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")


async def _seed(db, pw="pw"):
    db.add(User(id=1, email="u1@e.com", name="U1", hashed_password=_pwd.hash(pw),
                is_admin=True, role="owner"))
    await db.commit()


def _hdr(token):
    return {"Authorization": f"Bearer {token}"}


async def _login(client):
    r = await client.post("/api/auth/login", data={"username": "u1@e.com", "password": "pw"})
    assert r.status_code == 200
    return r.json()["access_token"]


async def test_password_change_revokes_old_sessions(client, db):
    await _seed(db)
    old = await _login(client)
    assert (await client.get("/api/auth/me", headers=_hdr(old))).status_code == 200
    r = await client.put("/api/auth/password", headers=_hdr(old),
                         json={"current_password": "pw", "new_password": "new-pw-123"})
    assert r.status_code == 200
    fresh = r.json()["access_token"]
    assert (await client.get("/api/auth/me", headers=_hdr(old))).status_code == 401     # revoked
    assert (await client.get("/api/auth/me", headers=_hdr(fresh))).status_code == 200   # caller stays in


async def test_logout_all_revokes_other_sessions(client, db):
    await _seed(db)
    a = await _login(client)
    b = await _login(client)   # a second browser
    r = await client.post("/api/auth/logout-all", headers=_hdr(a))
    assert r.status_code == 200
    fresh = r.json()["access_token"]
    assert (await client.get("/api/auth/me", headers=_hdr(b))).status_code == 401       # other one out
    assert (await client.get("/api/auth/me", headers=_hdr(fresh))).status_code == 200   # caller stays in


async def test_tv_less_token_valid_at_zero(client, db):
    await _seed(db)
    # A pre-A-04 JWT (no `tv`) stays valid while token_version is still 0 — nobody is force-logged-out.
    legacy = jwt.encode({"sub": "1"}, get_settings().secret_key, algorithm="HS256")
    assert (await client.get("/api/auth/me", headers=_hdr(legacy))).status_code == 200


async def test_reset_revokes_sessions(client, db):
    from geodeploy.routers.users import RESET_TTL, hash_token, new_token, utcnow
    await _seed(db)
    old = await _login(client)
    raw = new_token()
    db.add(Invitation(purpose="password_reset", email="u1@e.com", user_id=1,
                      token_hash=hash_token(raw), expires_at=utcnow() + RESET_TTL))
    await db.commit()
    assert (await client.post(f"/api/auth/password-reset/{raw}",
                              json={"password": "reset-pw-123"})).status_code == 204
    assert (await client.get("/api/auth/me", headers=_hdr(old))).status_code == 401


async def test_api_token_unaffected_by_tv(client, db):
    await _seed(db)
    jwt_tok = await _login(client)
    api_token = (await client.post("/api/tokens", headers=_hdr(jwt_tok),
                 json={"name": "t", "scopes": ["data:read"], "expires_in_days": 30})).json()["token"]
    await client.post("/api/auth/logout-all", headers=_hdr(jwt_tok))  # bump tv
    # The API token is not a tv-versioned browser JWT → still authenticates.
    assert (await client.get("/api/data/vector", headers=_hdr(api_token))).status_code == 200
