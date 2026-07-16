"""Regression tests for the security hardening (2026-07 audit).

Each test maps to a finding fixed in that round:
  #1 setup endpoints lock after completion
  #2 vector display endpoints serve only public / published-portal layers
  #6 portal HTML embedding can't be broken out of with a crafted layer name
  plus the baseline auth flow these all depend on.
"""
import json
import types

import pytest
from jose import jwt
from passlib.context import CryptContext

from geodeploy.config import get_settings
from geodeploy.models import Portal, SetupConfig, User
from geodeploy.routers.data import vector as V
from geodeploy.routers.setup import _guard_setup_mutation
from geodeploy.services.portal_generator import _esc, _json_for_html

_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")


async def _seed_admin(db, uid=1, email="admin@example.com", pw="s3cret-pw", is_admin=True, role=None):
    role = role or ("owner" if is_admin else "editor")
    db.add(User(id=uid, email=email, name="Admin", hashed_password=_pwd.hash(pw),
                is_admin=is_admin, role=role))
    await db.commit()
    return uid, email, pw


# ── #6 — portal XSS: no </script> breakout, output is still valid JSON ────────────────────────

def test_json_for_html_blocks_script_breakout():
    payload = {"name": "</script><img src=x onerror=alert(1)>"}
    out = _json_for_html(payload)
    assert "</script>" not in out            # cannot terminate the inline <script>
    assert "\\u003c/script" in out           # escaped instead
    assert json.loads(out) == payload        # still parses back to the exact value


def test_json_for_html_escapes_all_html_sigils():
    out = _json_for_html({"a": "<>&"})
    assert "<" not in out and ">" not in out and "&" not in out.replace("\\u0026", "")


def test_esc_escapes_html():
    assert _esc('<b>"x"&</b>') == "&lt;b&gt;&quot;x&quot;&amp;&lt;/b&gt;"


# ── #2 — vector display authorization predicate ───────────────────────────────────────────────

class _Layer:
    """Minimal stand-in for a VectorLayer row (only the fields the predicate reads)."""
    def __init__(self, id, is_public):
        self.id = id
        self.is_public = is_public


async def test_publicly_readable(db):
    await _seed_admin(db)
    db.add(Portal(id=1, user_id=1, title="Published", slug="pub", published=True,
                  layer_configs=json.dumps([{"layer_id": 5, "layer_type": "vector"}])))
    db.add(Portal(id=2, user_id=1, title="Draft", slug="draft", published=False,
                  layer_configs=json.dumps([{"layer_id": 7, "layer_type": "vector"}])))
    await db.commit()
    V.invalidate_public_layers()
    try:
        # private layer, not in any published portal → denied
        assert await V._publicly_readable(_Layer(999, False), db) is False
        # only referenced by a DRAFT portal → still denied
        assert await V._publicly_readable(_Layer(7, False), db) is False
        # referenced by a PUBLISHED portal → allowed even though not is_public
        assert await V._publicly_readable(_Layer(5, False), db) is True
        # explicitly shared → allowed regardless of portals
        assert await V._publicly_readable(_Layer(999, True), db) is True
        # missing layer → denied
        assert await V._publicly_readable(None, db) is False
    finally:
        V.invalidate_public_layers()


async def test_publish_state_change_is_reflected(db):
    await _seed_admin(db)
    db.add(Portal(id=1, user_id=1, title="P", slug="p", published=False,
                  layer_configs=json.dumps([{"layer_id": 5, "layer_type": "vector"}])))
    await db.commit()
    V.invalidate_public_layers()
    try:
        assert await V._publicly_readable(_Layer(5, False), db) is False  # draft → hidden
        # publish it, then invalidate the cache (as the publish endpoint does)
        p = await db.get(Portal, 1)
        p.published = True
        await db.commit()
        V.invalidate_public_layers()
        assert await V._publicly_readable(_Layer(5, False), db) is True   # now exposed
    finally:
        V.invalidate_public_layers()


# ── #1 — setup mutation guard ─────────────────────────────────────────────────────────────────

def _req(auth=None):
    return types.SimpleNamespace(headers={"Authorization": auth} if auth else {})


async def test_setup_open_during_first_run(db):
    # no completed config and no admin → first run, allowed (no raise)
    await _guard_setup_mutation(_req(), db)


async def test_setup_blocked_after_completion_without_auth(db):
    db.add(SetupConfig(id=1, completed=True))
    await _seed_admin(db)
    with pytest.raises(Exception) as ei:
        await _guard_setup_mutation(_req(), db)
    assert getattr(ei.value, "status_code", None) == 403


async def test_setup_allows_admin_token(db):
    db.add(SetupConfig(id=1, completed=True))
    uid, _, _ = await _seed_admin(db)
    token = jwt.encode({"sub": str(uid)}, get_settings().secret_key, algorithm="HS256")
    await _guard_setup_mutation(_req(f"Bearer {token}"), db)  # must not raise


async def test_setup_rejects_nonadmin_token(db):
    db.add(SetupConfig(id=1, completed=True))
    uid, _, _ = await _seed_admin(db, uid=2, email="user@example.com", is_admin=False)
    token = jwt.encode({"sub": str(uid)}, get_settings().secret_key, algorithm="HS256")
    with pytest.raises(Exception) as ei:
        await _guard_setup_mutation(_req(f"Bearer {token}"), db)
    assert getattr(ei.value, "status_code", None) == 403


# ── #1 over HTTP + baseline auth ──────────────────────────────────────────────────────────────

async def test_setup_endpoints_locked_over_http(client, db):
    db.add(SetupConfig(id=1, completed=True))
    await _seed_admin(db)
    assert (await client.post("/api/setup/configure-storage", json={"type": "local"})).status_code == 403
    assert (await client.post("/api/setup/configure-db", json={"type": "local"})).status_code == 403


async def test_login_and_protected_route(client, db):
    _, email, pw = await _seed_admin(db)
    r = await client.post("/api/auth/login", data={"username": email, "password": pw})
    assert r.status_code == 200
    token = r.json()["access_token"]
    me = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["role"] == "owner"
    assert (await client.get("/api/auth/me")).status_code == 401
    assert (await client.post("/api/auth/login",
                              data={"username": email, "password": "wrong"})).status_code == 401


async def test_create_admin_mints_owner(client, db, monkeypatch):
    # Don't touch .env / docker from the test run.
    monkeypatch.setattr("geodeploy.routers.setup._write_env", lambda config: None)
    monkeypatch.setattr("geodeploy.routers.setup._apply_to_process", lambda config: None)
    db.add(SetupConfig(id=1, completed=False, postgis_host="db", storage_endpoint="http://s3"))
    await db.commit()
    r = await client.post("/api/setup/create-admin",
                          json={"name": "Root", "email": "root@example.com", "password": "s3cret-pw"})
    assert r.status_code == 200
    login = await client.post("/api/auth/login",
                              data={"username": "root@example.com", "password": "s3cret-pw"})
    token = login.json()["access_token"]
    me = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.json()["role"] == "owner"
    assert me.json()["is_admin"] is True
