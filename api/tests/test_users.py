"""Users API tests (A-01 phase 4): invitations, roles, ownership transfer,
delete-with-reassign, password change/reset."""
import json
from datetime import timedelta

import pytest
from jose import jwt
from passlib.context import CryptContext
from sqlalchemy import select

from geodeploy.config import get_settings
from geodeploy.models import Invitation, Portal, User, VectorLayer
from geodeploy.routers.users import hash_token, utcnow

_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")

ROLES = {"owner": 1, "admin": 2, "editor": 3, "viewer": 4}


def _auth(uid):
    token = jwt.encode({"sub": str(uid)}, get_settings().secret_key, algorithm="HS256")
    return {"Authorization": f"Bearer {token}"}


async def _seed_users(db, roles=("owner", "admin", "editor", "viewer")):
    for role in roles:
        uid = ROLES[role]
        db.add(User(id=uid, email=f"{role}@example.com", name=role.capitalize(),
                    hashed_password=_pwd.hash("pw"), is_admin=role in ("admin", "owner"),
                    role=role))
    await db.commit()


# ── Invitation lifecycle ──────────────────────────────────────────────────────────────────────

async def test_invite_lifecycle(client, db):
    await _seed_users(db)
    # editor/viewer cannot invite
    for role in ("editor", "viewer"):
        r = await client.post("/api/users/invitations",
                              json={"email": "new@example.com", "role": "editor"},
                              headers=_auth(ROLES[role]))
        assert r.status_code == 403
    # admin invites → raw token returned ONCE
    r = await client.post("/api/users/invitations",
                          json={"email": "new@example.com", "role": "editor"},
                          headers=_auth(ROLES["admin"]))
    assert r.status_code == 201
    token = r.json()["token"]
    assert token
    # pending list shows it WITHOUT the token
    r = await client.get("/api/users/invitations", headers=_auth(ROLES["admin"]))
    assert len(r.json()) == 1 and r.json()[0]["token"] is None

    # public info endpoint
    r = await client.get(f"/api/auth/invitations/{token}")
    assert r.status_code == 200
    assert r.json() == {"email": "new@example.com", "role": "editor", "purpose": "invite"}

    # accept → auto-login token; user exists with the invited role
    r = await client.post(f"/api/auth/invitations/{token}/accept",
                          json={"name": "New Member", "password": "s3cret-pw"})
    assert r.status_code == 200 and r.json()["access_token"]
    me = await client.get("/api/auth/me",
                          headers={"Authorization": f"Bearer {r.json()['access_token']}"})
    assert me.json()["role"] == "editor" and me.json()["email"] == "new@example.com"

    # single-use: second accept → 410
    r = await client.post(f"/api/auth/invitations/{token}/accept",
                          json={"name": "Again", "password": "s3cret-pw"})
    assert r.status_code == 410


async def test_invite_guards(client, db):
    await _seed_users(db)
    h = _auth(ROLES["admin"])
    # role=owner is rejected by the schema pattern
    r = await client.post("/api/users/invitations",
                          json={"email": "x@example.com", "role": "owner"}, headers=h)
    assert r.status_code == 422
    # existing email → 400
    r = await client.post("/api/users/invitations",
                          json={"email": "viewer@example.com", "role": "editor"}, headers=h)
    assert r.status_code == 400
    # bad token → 404
    assert (await client.get("/api/auth/invitations/not-a-real-token")).status_code == 404


async def test_invite_expiry_and_regenerate(client, db):
    await _seed_users(db)
    h = _auth(ROLES["admin"])
    r = await client.post("/api/users/invitations",
                          json={"email": "n@example.com", "role": "viewer"}, headers=h)
    inv_id, token = r.json()["id"], r.json()["token"]

    # expire it manually → public endpoints 410, pending list empty
    inv = await db.get(Invitation, inv_id)
    inv.expires_at = utcnow() - timedelta(minutes=1)
    await db.commit()
    assert (await client.get(f"/api/auth/invitations/{token}")).status_code == 410
    r = await client.get("/api/users/invitations", headers=h)
    assert r.json() == []

    # regenerate → fresh token + expiry; the OLD token is dead
    r = await client.post(f"/api/users/invitations/{inv_id}/regenerate", headers=h)
    token2 = r.json()["token"]
    assert token2 and token2 != token
    assert (await client.get(f"/api/auth/invitations/{token}")).status_code == 404
    assert (await client.get(f"/api/auth/invitations/{token2}")).status_code == 200

    # revoke → gone
    assert (await client.delete(f"/api/users/invitations/{inv_id}", headers=h)).status_code == 204
    assert (await client.get(f"/api/auth/invitations/{token2}")).status_code == 404


async def test_accept_race_email_registered_meanwhile(client, db):
    await _seed_users(db, roles=("owner", "admin"))
    h = _auth(ROLES["admin"])
    r = await client.post("/api/users/invitations",
                          json={"email": "race@example.com", "role": "viewer"}, headers=h)
    token = r.json()["token"]
    db.add(User(id=99, email="race@example.com", name="Raced",
                hashed_password=_pwd.hash("pw"), role="viewer"))
    await db.commit()
    r = await client.post(f"/api/auth/invitations/{token}/accept",
                          json={"name": "X", "password": "s3cret-pw"})
    assert r.status_code == 409


# ── Roles + ownership transfer ────────────────────────────────────────────────────────────────

async def test_role_update_guards(client, db):
    await _seed_users(db)
    admin_h = _auth(ROLES["admin"])
    # owner is untouchable
    r = await client.put(f"/api/users/{ROLES['owner']}/role", json={"role": "viewer"}, headers=admin_h)
    assert r.status_code == 403
    # self-change blocked
    r = await client.put(f"/api/users/{ROLES['admin']}/role", json={"role": "viewer"}, headers=admin_h)
    assert r.status_code == 400
    # role=owner rejected by schema
    r = await client.put(f"/api/users/{ROLES['viewer']}/role", json={"role": "owner"}, headers=admin_h)
    assert r.status_code == 422
    # promote viewer → admin, is_admin synced
    r = await client.put(f"/api/users/{ROLES['viewer']}/role", json={"role": "admin"}, headers=admin_h)
    assert r.status_code == 200 and r.json()["role"] == "admin" and r.json()["is_admin"] is True


async def test_ownership_transfer(client, db):
    await _seed_users(db)
    # admin cannot transfer (owner-only route)
    r = await client.post(f"/api/users/{ROLES['editor']}/transfer-ownership",
                          headers=_auth(ROLES["admin"]))
    assert r.status_code == 403
    # owner self-target → 400
    r = await client.post(f"/api/users/{ROLES['owner']}/transfer-ownership",
                          headers=_auth(ROLES["owner"]))
    assert r.status_code == 400
    # owner → editor: exactly one owner after, old owner is admin
    r = await client.post(f"/api/users/{ROLES['editor']}/transfer-ownership",
                          headers=_auth(ROLES["owner"]))
    assert r.status_code == 200 and r.json()["role"] == "owner"
    owners = (await db.execute(select(User).where(User.role == "owner"))).scalars().all()
    assert [u.id for u in owners] == [ROLES["editor"]]
    old = await db.get(User, ROLES["owner"])
    await db.refresh(old)
    assert old.role == "admin" and old.is_admin is True


# ── Delete with reassign ──────────────────────────────────────────────────────────────────────

async def test_delete_user_reassigns_to_owner(client, db):
    await _seed_users(db)
    db.add(VectorLayer(id=5, user_id=ROLES["editor"], name="L", table_name="t",
                       schema_name="ext", status="ready", storage_backend="postgis"))
    db.add(Portal(id=7, user_id=ROLES["editor"], title="P", slug="p",
                  layer_configs=json.dumps([])))
    await db.commit()

    # guards: owner undeletable, self-delete blocked
    assert (await client.delete(f"/api/users/{ROLES['owner']}",
                                headers=_auth(ROLES["admin"]))).status_code == 403
    assert (await client.delete(f"/api/users/{ROLES['admin']}",
                                headers=_auth(ROLES["admin"]))).status_code == 400

    r = await client.delete(f"/api/users/{ROLES['editor']}", headers=_auth(ROLES["admin"]))
    assert r.status_code == 204
    layer = await db.get(VectorLayer, 5)
    portal = await db.get(Portal, 7)
    await db.refresh(layer); await db.refresh(portal)
    assert layer.user_id == ROLES["owner"] and portal.user_id == ROLES["owner"]
    # the deleted user's token is dead immediately
    assert (await client.get("/api/auth/me", headers=_auth(ROLES["editor"]))).status_code == 401


# ── Passwords ─────────────────────────────────────────────────────────────────────────────────

async def test_change_password(client, db):
    await _seed_users(db, roles=("owner", "viewer"))
    h = _auth(ROLES["viewer"])
    r = await client.put("/api/auth/password",
                         json={"current_password": "wrong", "new_password": "new-s3cret"}, headers=h)
    assert r.status_code == 403
    r = await client.put("/api/auth/password",
                         json={"current_password": "pw", "new_password": "new-s3cret"}, headers=h)
    assert r.status_code == 204
    r = await client.post("/api/auth/login",
                          data={"username": "viewer@example.com", "password": "new-s3cret"})
    assert r.status_code == 200


async def test_reset_link_flow(client, db):
    await _seed_users(db)
    # admin cannot mint a reset link for the OWNER (takeover guard); owner can
    r = await client.post(f"/api/users/{ROLES['owner']}/reset-password-link",
                          headers=_auth(ROLES["admin"]))
    assert r.status_code == 403
    r = await client.post(f"/api/users/{ROLES['viewer']}/reset-password-link",
                          headers=_auth(ROLES["admin"]))
    assert r.status_code == 200
    token = r.json()["token"]
    assert r.json()["purpose"] == "password_reset"

    # a reset token cannot be used on the ACCEPT endpoint (purpose check)
    r = await client.post(f"/api/auth/invitations/{token}/accept",
                          json={"name": "X", "password": "whatever-pw"})
    assert r.status_code == 404

    r = await client.post(f"/api/auth/password-reset/{token}", json={"password": "reset-s3cret"})
    assert r.status_code == 204
    r = await client.post("/api/auth/login",
                          data={"username": "viewer@example.com", "password": "reset-s3cret"})
    assert r.status_code == 200
    # single-use
    r = await client.post(f"/api/auth/password-reset/{token}", json={"password": "again-pw"})
    assert r.status_code == 410


# ── Members list ──────────────────────────────────────────────────────────────────────────────

async def test_list_users_with_counts(client, db):
    await _seed_users(db)
    db.add(VectorLayer(id=5, user_id=ROLES["editor"], name="L", table_name="t",
                       schema_name="ext", status="ready", storage_backend="postgis"))
    db.add(Portal(id=7, user_id=ROLES["editor"], title="P", slug="p",
                  layer_configs=json.dumps([])))
    await db.commit()
    # viewer/editor can't list users
    assert (await client.get("/api/users", headers=_auth(ROLES["viewer"]))).status_code == 403
    r = await client.get("/api/users", headers=_auth(ROLES["admin"]))
    assert r.status_code == 200
    by_id = {u["id"]: u for u in r.json()}
    assert len(by_id) == 4
    assert by_id[ROLES["editor"]]["vector_count"] == 1
    assert by_id[ROLES["editor"]]["portal_count"] == 1
    assert by_id[ROLES["owner"]]["vector_count"] == 0
