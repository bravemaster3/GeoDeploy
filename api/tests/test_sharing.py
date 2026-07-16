"""Per-resource sharing tests (A-02): the workspace `visibility` axis on DATA resources.

private ⊂ organization ⊂ public, on vector/raster layers and external sources. A `private` resource
is seen (and mutated) only by its creator and admins/owner; `organization` by every member; `public`
additionally opts a LAYER into the STAC catalog + raw-asset access (the derived `is_public`). Changing
sharing is an editor+ power over resources they can see.

(Portals deliberately have NO workspace visibility — a portal's audience is its published access_type;
see test_portal_access / the portals router.)
"""
import pytest
from jose import jwt
from passlib.context import CryptContext

from geodeploy.config import get_settings
from geodeploy.models import ExternalSource, User, VectorLayer

_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")

# uid → role. Two editors so peer-vs-creator private access is distinguishable.
OWNER, ADMIN, EDITOR_A, VIEWER, EDITOR_B = 1, 2, 3, 4, 5
_ROLE = {OWNER: "owner", ADMIN: "admin", EDITOR_A: "editor", VIEWER: "viewer", EDITOR_B: "editor"}


def _auth(uid):
    tok = jwt.encode({"sub": str(uid)}, get_settings().secret_key, algorithm="HS256")
    return {"Authorization": f"Bearer {tok}"}


async def _seed(db):
    """Users of every role (+ a 2nd editor) and resources created by EDITOR_A: one private, one
    organization, per data type. schema_name outside geodeploy_u* → delete skips the PostGIS DROP."""
    for uid, role in _ROLE.items():
        db.add(User(id=uid, email=f"u{uid}@example.com", name=f"U{uid}",
                    hashed_password=_pwd.hash("pw"), is_admin=role in ("admin", "owner"), role=role))
    db.add(VectorLayer(id=10, user_id=EDITOR_A, name="Private layer", table_name="t10",
                       schema_name="ext_schema", status="ready", storage_backend="postgis",
                       visibility="private"))
    db.add(VectorLayer(id=11, user_id=EDITOR_A, name="Org layer", table_name="t11",
                       schema_name="ext_schema", status="ready", storage_backend="postgis",
                       visibility="organization"))
    db.add(ExternalSource(id=30, user_id=EDITOR_A, name="Private src", source_type="xyz",
                          kind="raster", url="https://tiles.example/{z}/{x}/{y}.png", visibility="private"))
    db.add(ExternalSource(id=31, user_id=EDITOR_A, name="Org src", source_type="xyz",
                          kind="raster", url="https://tiles.example/{z}/{x}/{y}.png", visibility="organization"))
    await db.commit()


def _ids(rows):
    return {r["id"] for r in rows}


# ── Private is hidden from peers, visible to creator + admins/owner ────────────────────────────

@pytest.mark.parametrize("uid,sees_private", [
    (EDITOR_A, True),   # creator
    (ADMIN, True),      # governance
    (OWNER, True),      # governance
    (EDITOR_B, False),  # peer editor
    (VIEWER, False),    # peer viewer
])
async def test_private_visibility_in_lists(client, db, uid, sees_private):
    await _seed(db)
    for path, priv, org in (("/api/data/vector", 10, 11),
                            ("/api/data/sources", 30, 31)):
        rows = (await client.get(path, headers=_auth(uid))).json()
        assert org in _ids(rows)                       # organization always visible
        assert (priv in _ids(rows)) is sees_private    # private gated


# ── A peer can't reach a private resource by id (404, not 403 — no existence leak) ─────────────

async def test_peer_cannot_touch_private_resource(client, db):
    await _seed(db)
    h = _auth(EDITOR_B)
    assert (await client.delete("/api/data/vector/10", headers=h)).status_code == 404
    assert (await client.put("/api/data/vector/10/sharing",
                             json={"visibility": "public"}, headers=h)).status_code == 404
    assert (await client.delete("/api/data/sources/30", headers=h)).status_code == 404


async def test_admin_and_creator_reach_private_resource(client, db):
    await _seed(db)
    # creator may delete their own private layer; an admin may delete anyone's.
    assert (await client.delete("/api/data/vector/10", headers=_auth(EDITOR_A))).status_code == 204
    assert (await client.delete("/api/data/sources/30", headers=_auth(ADMIN))).status_code == 204


# ── Re-sharing is an editor+ power over resources they can SEE (workspace model) ───────────────

async def test_editor_can_reshare_visible_resource(client, db):
    await _seed(db)
    # A peer editor can re-share the ORG layer (they can see it — and could delete it entirely).
    assert (await client.put("/api/data/vector/11/sharing", json={"visibility": "private"},
                             headers=_auth(EDITOR_B))).status_code == 200
    # ...but the PRIVATE layer they don't own stays a 404 (hidden, not just uneditable).
    assert (await client.put("/api/data/vector/10/sharing", json={"visibility": "public"},
                             headers=_auth(EDITOR_B))).status_code == 404
    # the creator can always re-share their own.
    assert (await client.put("/api/data/vector/10/sharing", json={"visibility": "public"},
                             headers=_auth(EDITOR_A))).status_code == 200


# ── visibility ⇄ is_public sync ────────────────────────────────────────────────────────────────

async def test_public_visibility_syncs_is_public(client, db):
    await _seed(db)
    h = _auth(EDITOR_A)
    r = await client.put("/api/data/vector/11/sharing", json={"visibility": "public"}, headers=h)
    assert r.status_code == 200 and r.json()["visibility"] == "public" and r.json()["is_public"] is True
    r = await client.put("/api/data/vector/11/sharing", json={"visibility": "organization"}, headers=h)
    assert r.json()["visibility"] == "organization" and r.json()["is_public"] is False


async def test_legacy_is_public_maps_to_visibility(client, db):
    await _seed(db)
    h = _auth(EDITOR_A)
    r = await client.put("/api/data/vector/11/sharing", json={"is_public": True}, headers=h)
    assert r.json()["visibility"] == "public" and r.json()["is_public"] is True
    r = await client.put("/api/data/vector/11/sharing", json={"is_public": False}, headers=h)
    assert r.json()["visibility"] == "organization" and r.json()["is_public"] is False


# ── Sources have NO public tier ────────────────────────────────────────────────────────────────

async def test_source_rejects_public_tier(client, db):
    await _seed(db)
    r = await client.put("/api/data/sources/31/sharing",
                         json={"visibility": "public"}, headers=_auth(EDITOR_A))
    assert r.status_code == 422  # pattern private|organization only


# ── Public display surface still hidden for a private layer (regression) ───────────────────────

async def test_private_layer_public_endpoint_still_404(client, db):
    await _seed(db)
    # anonymous public-by-id endpoint: private + not in any published portal → 404
    assert (await client.get("/api/data/vector/10/features.geojson")).status_code == 404
