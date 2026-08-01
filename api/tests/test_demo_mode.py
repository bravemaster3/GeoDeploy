"""Demo mode — and the promise that a NORMAL install is unaffected by its existence.

The requirement was explicit: do not introduce a bug that non-demo GeoDeploy starts having. So the
first half of this file tests the flag being OFF, which is the state every real install runs in. Those
tests would fail if any demo behaviour ever leaked into the default path.

The second half tests the flag ON, where the notable design decision is that a demo visitor is an
ordinary EDITOR: the terminal, environment variables, service control, backups and user management
are withheld by the role system that already exists, not by demo-specific gates that could drift.
"""
import pytest
from sqlalchemy import select

from geodeploy.config import get_settings
from geodeploy.models import User


@pytest.fixture
def demo_on():
    """Turn demo mode on for one test. get_settings is cached, so the object is patched in place and
    restored afterwards — a leaked True here would make the 'off' tests meaningless."""
    s = get_settings()
    before = s.geodeploy_demo_mode
    s.geodeploy_demo_mode = True
    yield s
    s.geodeploy_demo_mode = before


# ── flag OFF: the state every real install is in ────────────────────────────────────────────

async def test_demo_endpoints_are_absent_by_default(client):
    """404, not 403: on a normal install this route should look like it does not exist."""
    r = await client.post("/api/auth/demo/join", json={"name": "Mallory"})
    assert r.status_code == 404


async def test_no_account_is_created_when_demo_is_off(client, db):
    await client.post("/api/auth/demo/join", json={"name": "Mallory"})
    assert (await db.execute(select(User))).scalars().all() == []


async def test_demo_info_reports_false_rather_than_failing(client):
    """The UI asks on every load; a 404 would be indistinguishable from a broken endpoint."""
    r = await client.get("/api/auth/demo")
    assert r.status_code == 200
    assert r.json() == {"demo": False}


async def test_default_settings_have_demo_off():
    """The flag must default OFF. If this ever fails, every install becomes a public sandbox."""
    assert get_settings().geodeploy_demo_mode is False


# ── flag ON ─────────────────────────────────────────────────────────────────────────────────

async def test_join_with_only_a_name(client, db, demo_on):
    r = await client.post("/api/auth/demo/join", json={"name": "Ada"})
    assert r.status_code == 200
    tok = r.json()["access_token"]
    me = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {tok}"})
    assert me.json()["name"] == "Ada"


async def test_joiner_is_an_editor_not_an_admin(client, db, demo_on):
    """The whole safety model. Editor already excludes terminal, environment, services, backups and
    user management — so those need no demo-specific gate."""
    tok = (await client.post("/api/auth/demo/join", json={"name": "Ada"})).json()["access_token"]
    me = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {tok}"})
    assert me.json()["role"] == "editor"
    u = (await db.execute(select(User))).scalars().one()
    assert u.is_admin is False


async def test_joiner_cannot_reach_admin_surfaces(client, demo_on):
    """Proves the claim above against the real routes rather than trusting the role string."""
    tok = (await client.post("/api/auth/demo/join", json={"name": "Ada"})).json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}
    for path in ("/api/admin/env", "/api/users", "/api/backups/settings"):
        assert (await client.get(path, headers=h)).status_code in (401, 403), path


async def test_each_join_is_a_separate_account(client, db, demo_on):
    """Two visitors must not collide on the synthetic email."""
    await client.post("/api/auth/demo/join", json={"name": "Ada"})
    await client.post("/api/auth/demo/join", json={"name": "Ada"})
    users = (await db.execute(select(User))).scalars().all()
    assert len({u.email for u in users}) == 2


async def test_demo_accounts_cannot_be_signed_back_into(client, db, demo_on):
    """The password is random and never shown, so the session minted at join is the only way in."""
    await client.post("/api/auth/demo/join", json={"name": "Ada"})
    u = (await db.execute(select(User))).scalars().one()
    r = await client.post("/api/auth/login", data={"username": u.email, "password": ""})
    assert r.status_code != 200


async def test_blank_name_is_rejected_or_defaulted(client, demo_on):
    r = await client.post("/api/auth/demo/join", json={"name": "   "})
    assert r.status_code in (200, 422)
    if r.status_code == 200:
        tok = r.json()["access_token"]
        me = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {tok}"})
        assert me.json()["name"].strip()


async def test_demo_info_advertises_the_upload_cap(client, demo_on):
    body = (await client.get("/api/auth/demo")).json()
    assert body["demo"] is True
    assert body["max_upload_mb"] == 500


# ── upload cap ──────────────────────────────────────────────────────────────────────────────

async def test_upload_cap_is_off_by_default(client):
    """A normal install keeps its full limits — the middleware must not touch it."""
    from geodeploy.routers.common import demo_upload_cap
    demo_upload_cap(50 * 1024 * 1024 * 1024)        # 50 GB, no demo mode → allowed


async def test_direct_upload_over_the_cap_is_refused(demo_on):
    """Direct-to-storage bytes never reach the API, so the size declared at initiate is the only
    place it can be refused."""
    from fastapi import HTTPException
    from geodeploy.routers.common import demo_upload_cap
    demo_upload_cap(400 * 1024 * 1024)              # under 500 MB → fine
    with pytest.raises(HTTPException) as exc:
        demo_upload_cap(600 * 1024 * 1024)
    assert exc.value.status_code == 413
    # The message must say the limit is the DEMO's, or it reads as a product limitation.
    assert "install yourself" in exc.value.detail


async def test_request_body_over_the_cap_is_refused(client, demo_on):
    r = await client.post("/api/auth/demo/join", json={"name": "Ada"},
                          headers={"content-length": str(600 * 1024 * 1024)})
    assert r.status_code == 413
    assert "500 MB" in r.json()["detail"]
