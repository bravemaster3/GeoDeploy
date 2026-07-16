"""RBAC matrix tests (A-01 phase 3): shared-workspace visibility + editor gating.

The workspace is shared — every member sees all data and portals; the ROLE decides
what they may mutate. These tests pin the permission matrix for representative routes:
list reads (viewer+), mutations (editor+), and the untouched PUBLIC surface.
"""
import json

import pytest
from jose import jwt
from passlib.context import CryptContext

from geodeploy.config import get_settings
from geodeploy.models import Portal, User, VectorLayer

_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")

ROLES = {"owner": 1, "admin": 2, "editor": 3, "viewer": 4}


def _token(uid):
    return jwt.encode({"sub": str(uid)}, get_settings().secret_key, algorithm="HS256")


def _auth(uid):
    return {"Authorization": f"Bearer {_token(uid)}"}


async def _seed_workspace(db):
    """One user per role + a vector layer and portal CREATED BY THE OWNER (so editor
    mutations on them prove cross-creator write access)."""
    for role, uid in ROLES.items():
        db.add(User(id=uid, email=f"{role}@example.com", name=role.capitalize(),
                    hashed_password=_pwd.hash("pw"), is_admin=role in ("admin", "owner"),
                    role=role))
    # schema_name outside geodeploy_u* → delete skips the PostGIS DROP branch (attached table).
    db.add(VectorLayer(id=5, user_id=1, name="Owner layer", table_name="t5",
                       schema_name="ext_schema", status="ready", storage_backend="postgis"))
    db.add(Portal(id=7, user_id=1, title="Owner portal", slug="ownerp",
                  layer_configs=json.dumps([])))
    await db.commit()


# ── Shared-workspace visibility: every role sees everything ───────────────────────────────────

@pytest.mark.parametrize("role", list(ROLES))
async def test_lists_are_workspace_wide(client, db, role):
    await _seed_workspace(db)
    uid = ROLES[role]
    r = await client.get("/api/data/vector", headers=_auth(uid))
    assert r.status_code == 200
    rows = r.json()
    assert [x["id"] for x in rows] == [5]
    assert rows[0]["created_by"] == "Owner"          # creator provenance survives
    assert rows[0]["user_id"] == 1

    r = await client.get("/api/portals", headers=_auth(uid))
    assert r.status_code == 200
    assert [x["id"] for x in r.json()] == [7]
    assert r.json()[0]["created_by"] == "Owner"

    r = await client.get("/api/portals/7", headers=_auth(uid))
    assert r.status_code == 200                      # portal detail is a read → viewer OK


# ── Editor gating: mutations 403 for viewer, succeed for editor+ (cross-creator) ───────────────

async def test_viewer_cannot_mutate(client, db):
    await _seed_workspace(db)
    h = _auth(ROLES["viewer"])
    assert (await client.put("/api/data/vector/5/sharing",
                             json={"is_public": True}, headers=h)).status_code == 403
    assert (await client.delete("/api/data/vector/5", headers=h)).status_code == 403
    assert (await client.post("/api/portals", json={"title": "X"}, headers=h)).status_code == 403
    assert (await client.put("/api/portals/7", json={"title": "X"}, headers=h)).status_code == 403
    assert (await client.delete("/api/portals/7", headers=h)).status_code == 403
    assert (await client.get("/api/data/discover/storage", headers=h)).status_code == 403


async def test_editor_can_mutate_other_creators_resources(client, db):
    await _seed_workspace(db)
    h = _auth(ROLES["editor"])
    r = await client.put("/api/data/vector/5/sharing", json={"is_public": True}, headers=h)
    assert r.status_code == 200 and r.json()["is_public"] is True
    r = await client.put("/api/portals/7", json={"title": "Renamed by editor"}, headers=h)
    assert r.status_code == 200 and r.json()["title"] == "Renamed by editor"
    r = await client.post("/api/portals", json={"title": "Editor portal"}, headers=h)
    assert r.status_code == 201 and r.json()["created_by"] is None  # created_by set on LIST only
    assert (await client.delete("/api/data/vector/5", headers=h)).status_code == 204


@pytest.mark.parametrize("role,expected", [("admin", 200), ("owner", 200)])
async def test_admin_and_owner_mutate_too(client, db, role, expected):
    await _seed_workspace(db)
    r = await client.put("/api/portals/7", json={"title": f"By {role}"}, headers=_auth(ROLES[role]))
    assert r.status_code == expected


# ── 403-vs-404 ordering: the role dependency fires before the lookup ──────────────────────────

async def test_role_check_precedes_lookup(client, db):
    await _seed_workspace(db)
    # Sufficient role + missing id → 404
    assert (await client.put("/api/portals/999", json={"title": "X"},
                             headers=_auth(ROLES["editor"]))).status_code == 404
    # Insufficient role + missing id → 403 (dependency first; existence never probed)
    assert (await client.put("/api/portals/999", json={"title": "X"},
                             headers=_auth(ROLES["viewer"]))).status_code == 403


# ── PUBLIC surface untouched ──────────────────────────────────────────────────────────────────

async def test_public_endpoints_stay_gated_not_authed(client, db):
    """The public display endpoints must NOT demand auth (published portals are anonymous),
    and a private layer must stay a 404 — not a 401 — to an anonymous caller."""
    await _seed_workspace(db)
    r = await client.get("/api/data/vector/5/features.geojson")
    assert r.status_code == 404          # private (not shared, no published portal) → hidden
    r = await client.get("/api/portals/7/assets/deadbeefdeadbeefdeadbeefdeadbeef.png")
    assert r.status_code == 404          # public route reachable without auth (file just missing)
