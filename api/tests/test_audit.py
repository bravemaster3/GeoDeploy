"""Activity & audit log (A-05). Mutations write append-only entries via common.record_audit;
GET /audit exposes them to admins (filterable), and entries survive the actor/target being deleted.
"""
from jose import jwt
from passlib.context import CryptContext

from geodeploy.config import get_settings
from geodeploy.models import User

_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
OWNER, ADMIN, EDITOR, VIEWER = 1, 2, 3, 4
_ROLE = {OWNER: "owner", ADMIN: "admin", EDITOR: "editor", VIEWER: "viewer"}


def _h(uid):
    return {"Authorization": f"Bearer {jwt.encode({'sub': str(uid)}, get_settings().secret_key, algorithm='HS256')}"}


async def _seed(db):
    for uid, role in _ROLE.items():
        db.add(User(id=uid, email=f"u{uid}@e.com", name=f"U{uid}", hashed_password=_pwd.hash("pw"),
                    is_admin=role in ("admin", "owner"), role=role))
    await db.commit()


async def test_role_change_is_audited(client, db):
    await _seed(db)
    assert (await client.put(f"/api/users/{EDITOR}/role", headers=_h(ADMIN),
                             json={"role": "viewer"})).status_code == 200
    page = (await client.get("/api/audit?action=user.role_change", headers=_h(ADMIN))).json()
    # `total` is the count AFTER filtering, server-side — the whole reason /audit returns a page.
    assert page["total"] == 1 and len(page["items"]) == 1
    e = page["items"][0]
    assert e["actor_id"] == ADMIN and e["resource_type"] == "user" and e["resource_id"] == str(EDITOR)
    assert e["detail"]["to"] == "viewer"


async def test_login_is_audited(client, db):
    await _seed(db)
    await client.post("/api/auth/login", data={"username": "u4@e.com", "password": "pw"})
    page = (await client.get("/api/audit?action=auth.login", headers=_h(ADMIN))).json()
    assert any(e["actor_id"] == VIEWER and e["detail"]["method"] == "password"
               for e in page["items"])


async def test_audit_requires_admin(client, db):
    await _seed(db)
    assert (await client.get("/api/audit", headers=_h(VIEWER))).status_code == 403


async def test_audit_filter_by_resource(client, db):
    await _seed(db)
    await client.put(f"/api/users/{EDITOR}/role", headers=_h(ADMIN), json={"role": "viewer"})
    await client.post("/api/auth/login", data={"username": "u4@e.com", "password": "pw"})
    page = (await client.get("/api/audit?resource_type=user", headers=_h(ADMIN))).json()
    items = page["items"]
    assert items and all(e["resource_type"] == "user" for e in items)
    # The filter must be applied SERVER-side, before the page is cut — a client filtering one
    # fetched page would only ever search the rows it happened to download.
    assert page["total"] == len(items)


async def test_audit_survives_user_delete(client, db):
    await _seed(db)
    await client.put(f"/api/users/{EDITOR}/role", headers=_h(ADMIN), json={"role": "viewer"})
    assert (await client.delete(f"/api/users/{EDITOR}", headers=_h(ADMIN))).status_code == 204
    items = (await client.get("/api/audit", headers=_h(ADMIN))).json()["items"]
    actions = [e["action"] for e in items]
    assert "user.role_change" in actions and "user.delete" in actions
    # Denormalized actor/target names survive the deletion.
    deleted = next(e for e in items if e["action"] == "user.delete")
    assert deleted["detail"]["name"] == "U3"


async def test_audit_pagination_and_page_shape(client, db):
    """The envelope itself: `total` counts every match, `items` only the requested slice.

    Regression guard — /audit returned a bare list until 2026-07-30, and the callers that assumed
    that (including these tests) broke silently on a dict, where `len()` counts KEYS.
    """
    await _seed(db)
    for role in ("viewer", "editor", "viewer"):
        await client.put(f"/api/users/{EDITOR}/role", headers=_h(ADMIN), json={"role": role})

    page = (await client.get("/api/audit?limit=2", headers=_h(ADMIN))).json()
    assert set(page) >= {"items", "total", "limit", "offset"}
    assert page["limit"] == 2 and page["offset"] == 0
    assert len(page["items"]) <= 2
    assert page["total"] >= 3          # every match, not just this page

    second = (await client.get("/api/audit?limit=2&offset=2", headers=_h(ADMIN))).json()
    assert second["offset"] == 2
    first_ids = {e["id"] for e in page["items"]}
    assert not (first_ids & {e["id"] for e in second["items"]})   # pages must not overlap
