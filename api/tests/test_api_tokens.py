"""Scoped personal access tokens (A-03).

A `gdp_…` token authenticates as its owner via the Bearer path, capped by its scopes and never above
the owner's live role. These pin: mint/list/revoke, token auth, the scope-enforcement matrix, the
role floor at mint, mandatory/clamped expiry, the anti-escalation rule (a token can't mint tokens),
and that a browser session is unaffected by the Phase-3 scope refactor.
"""
import hashlib
from datetime import datetime, timedelta

from jose import jwt
from passlib.context import CryptContext

from geodeploy.config import get_settings
from geodeploy.models import ApiToken, User

_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")

OWNER, ADMIN, EDITOR, VIEWER = 1, 2, 3, 4
_ROLE = {OWNER: "owner", ADMIN: "admin", EDITOR: "editor", VIEWER: "viewer"}


def _jwt(uid):  # a browser session (JWT), used to MINT tokens
    return jwt.encode({"sub": str(uid)}, get_settings().secret_key, algorithm="HS256")


def _hdr(bearer):
    return {"Authorization": f"Bearer {bearer}"}


async def _seed(db):
    for uid, role in _ROLE.items():
        db.add(User(id=uid, email=f"u{uid}@e.com", name=f"U{uid}",
                    hashed_password=_pwd.hash("pw"), is_admin=role in ("admin", "owner"), role=role))
    await db.commit()


async def _mint(client, uid, scopes, days=90):
    return await client.post("/api/tokens", headers=_hdr(_jwt(uid)),
                             json={"name": "t", "scopes": scopes, "expires_in_days": days})


# ── Lifecycle ─────────────────────────────────────────────────────────────────────────────────

async def test_create_list_and_secret_shown_once(client, db):
    await _seed(db)
    r = await _mint(client, EDITOR, ["data:read", "data:write"])
    assert r.status_code == 201
    body = r.json()
    assert body["token"].startswith("gdp_") and body["prefix"].startswith("gdp_")
    assert set(body["scopes"]) == {"data:read", "data:write"}

    lst = await client.get("/api/tokens", headers=_hdr(_jwt(EDITOR)))
    assert lst.status_code == 200
    rows = lst.json()
    assert len(rows) == 1 and "token" not in rows[0]  # secret never listed


async def test_revoke(client, db):
    await _seed(db)
    tid = (await _mint(client, EDITOR, ["data:read"])).json()["id"]
    assert (await client.delete(f"/api/tokens/{tid}", headers=_hdr(_jwt(EDITOR)))).status_code == 204
    assert (await client.get("/api/tokens", headers=_hdr(_jwt(EDITOR)))).json() == []


# ── Token authenticates + scope enforcement matrix ──────────────────────────────────────────────

async def test_token_authenticates_read(client, db):
    await _seed(db)
    raw = (await _mint(client, EDITOR, ["data:read"])).json()["token"]
    assert (await client.get("/api/data/vector", headers=_hdr(raw))).status_code == 200


async def test_read_only_token_cannot_write(client, db):
    await _seed(db)
    raw = (await _mint(client, EDITOR, ["data:read"])).json()["token"]
    # DELETE fires the data:write scope check (a dependency) → 403 BEFORE the 404 for a missing layer.
    r = await client.delete("/api/data/vector/999", headers=_hdr(raw))
    assert r.status_code == 403 and "data:write" in r.json()["detail"]


async def test_write_token_cannot_publish(client, db):
    await _seed(db)
    raw = (await _mint(client, EDITOR, ["data:write"])).json()["token"]
    r = await client.post("/api/portals/999/publish", headers=_hdr(raw))
    assert r.status_code == 403 and "portal:publish" in r.json()["detail"]


async def test_publish_token_clears_scope(client, db):
    await _seed(db)
    raw = (await _mint(client, EDITOR, ["portal:publish"])).json()["token"]
    # Scope passes → the handler runs and 404s on the missing portal (NOT a 403).
    assert (await client.post("/api/portals/999/publish", headers=_hdr(raw))).status_code == 404


# ── Role floor + mint validation ────────────────────────────────────────────────────────────────

async def test_scope_above_role_rejected_at_mint(client, db):
    await _seed(db)
    r = await _mint(client, EDITOR, ["users:admin"])   # editor can't grant an admin scope
    assert r.status_code == 400


async def test_admin_can_grant_users_admin(client, db):
    await _seed(db)
    assert (await _mint(client, ADMIN, ["users:admin"])).status_code == 201


async def test_unknown_scope_rejected(client, db):
    await _seed(db)
    assert (await _mint(client, EDITOR, ["data:bogus"])).status_code == 400


async def test_expiry_must_be_allowed_value(client, db):
    await _seed(db)
    assert (await _mint(client, EDITOR, ["data:read"], days=999)).status_code == 400
    ok = await _mint(client, EDITOR, ["data:read"], days=90)
    assert ok.status_code == 201


# ── Expired / revoked / anti-escalation / user deletion ─────────────────────────────────────────

async def test_expired_token_401(client, db):
    await _seed(db)
    raw = "gdp_" + "x" * 40
    db.add(ApiToken(user_id=EDITOR, name="old", token_hash=hashlib.sha256(raw.encode()).hexdigest(),
                    prefix=raw[:12], scopes="data:read",
                    expires_at=datetime.utcnow() - timedelta(days=1)))
    await db.commit()
    assert (await client.get("/api/data/vector", headers=_hdr(raw))).status_code == 401


async def test_revoked_token_401(client, db):
    await _seed(db)
    minted = (await _mint(client, EDITOR, ["data:read"])).json()
    await client.delete(f"/api/tokens/{minted['id']}", headers=_hdr(_jwt(EDITOR)))
    assert (await client.get("/api/data/vector", headers=_hdr(minted["token"]))).status_code == 401


async def test_token_cannot_mint_tokens(client, db):
    await _seed(db)
    raw = (await _mint(client, ADMIN, ["users:admin", "data:read"])).json()["token"]
    # Even a users:admin token can't manage tokens — that requires a browser session.
    r = await client.post("/api/tokens", headers=_hdr(raw),
                          json={"name": "x", "scopes": ["data:read"], "expires_in_days": 30})
    assert r.status_code == 403


async def test_deleted_user_tokens_die(client, db):
    await _seed(db)
    raw = (await _mint(client, EDITOR, ["data:read"])).json()["token"]
    # Admin deletes the editor — the FK cleanup must not crash, and the token must stop working.
    assert (await client.delete(f"/api/users/{EDITOR}", headers=_hdr(_jwt(ADMIN)))).status_code == 204
    assert (await client.get("/api/data/vector", headers=_hdr(raw))).status_code == 401


# ── Browser session unaffected by the scope refactor (regression) ───────────────────────────────

async def test_browser_session_still_works(client, db):
    await _seed(db)
    # A JWT (no api_token on request.state) reads with no scope constraints, exactly as before.
    assert (await client.get("/api/data/vector", headers=_hdr(_jwt(VIEWER)))).status_code == 200
    assert (await client.get("/api/data/vector")).status_code == 401  # still requires auth
