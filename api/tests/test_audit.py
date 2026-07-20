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
    log = (await client.get("/api/audit?action=user.role_change", headers=_h(ADMIN))).json()
    assert len(log) == 1
    e = log[0]
    assert e["actor_id"] == ADMIN and e["resource_type"] == "user" and e["resource_id"] == str(EDITOR)
    assert e["detail"]["to"] == "viewer"


async def test_login_is_audited(client, db):
    await _seed(db)
    await client.post("/api/auth/login", data={"username": "u4@e.com", "password": "pw"})
    log = (await client.get("/api/audit?action=auth.login", headers=_h(ADMIN))).json()
    assert any(e["actor_id"] == VIEWER and e["detail"]["method"] == "password" for e in log)


async def test_audit_requires_admin(client, db):
    await _seed(db)
    assert (await client.get("/api/audit", headers=_h(VIEWER))).status_code == 403


async def test_audit_filter_by_resource(client, db):
    await _seed(db)
    await client.put(f"/api/users/{EDITOR}/role", headers=_h(ADMIN), json={"role": "viewer"})
    await client.post("/api/auth/login", data={"username": "u4@e.com", "password": "pw"})
    users_only = (await client.get("/api/audit?resource_type=user", headers=_h(ADMIN))).json()
    assert users_only and all(e["resource_type"] == "user" for e in users_only)


async def test_audit_survives_user_delete(client, db):
    await _seed(db)
    await client.put(f"/api/users/{EDITOR}/role", headers=_h(ADMIN), json={"role": "viewer"})
    assert (await client.delete(f"/api/users/{EDITOR}", headers=_h(ADMIN))).status_code == 204
    log = (await client.get("/api/audit", headers=_h(ADMIN))).json()
    actions = [e["action"] for e in log]
    assert "user.role_change" in actions and "user.delete" in actions
    # Denormalized actor/target names survive the deletion.
    deleted = next(e for e in log if e["action"] == "user.delete")
    assert deleted["detail"]["name"] == "U3"
