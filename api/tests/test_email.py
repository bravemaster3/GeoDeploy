"""Outgoing-email tests (C-08a): SMTP config endpoints, forgot-password anti-enumeration,
email_sent flags. The actual SMTP send is monkeypatched — no network in tests."""
import pytest
from jose import jwt
from passlib.context import CryptContext
from sqlalchemy import select

from geodeploy.config import get_settings
from geodeploy.models import Invitation, SetupConfig, User

_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")

ROLES = {"owner": 1, "admin": 2, "viewer": 4}


def _auth(uid):
    token = jwt.encode({"sub": str(uid)}, get_settings().secret_key, algorithm="HS256")
    return {"Authorization": f"Bearer {token}"}


async def _seed(db, email_configured=False):
    for role, uid in ROLES.items():
        db.add(User(id=uid, email=f"{role}@example.com", name=role.capitalize(),
                    hashed_password=_pwd.hash("pw"), is_admin=role in ("admin", "owner"),
                    role=role))
    cfg = SetupConfig(id=1, completed=True)
    if email_configured:
        cfg.smtp_host, cfg.smtp_port = "smtp.example.com", 587
        cfg.smtp_security, cfg.email_from = "starttls", "geodeploy@example.com"
    db.add(cfg)
    await db.commit()


@pytest.fixture
def sent(monkeypatch):
    """Capture outgoing emails instead of touching the network."""
    box = []
    def fake_send(cfg, to, subject, text):
        box.append({"to": to, "subject": subject, "text": text})
    monkeypatch.setattr("geodeploy.services.notifications._send_smtp", fake_send)
    return box


# ── Settings endpoints ────────────────────────────────────────────────────────────────────────

async def test_email_settings_admin_gated_and_masked(client, db):
    await _seed(db)
    assert (await client.get("/api/admin/email-settings",
                             headers=_auth(ROLES["viewer"]))).status_code == 403
    h = _auth(ROLES["admin"])
    r = await client.put("/api/admin/email-settings", headers=h, json={
        "smtp_host": "smtp.example.com", "smtp_port": 465, "smtp_security": "tls",
        "smtp_username": "resend", "smtp_password": "sekrit", "email_from": "gd@example.com"})
    assert r.status_code == 200 and r.json()["configured"] is True
    r = await client.get("/api/admin/email-settings", headers=h)
    body = r.json()
    assert body["has_password"] is True and "sekrit" not in str(body)  # never returned
    # blank password on update keeps the stored one
    r = await client.put("/api/admin/email-settings", headers=h,
                         json={"smtp_password": "", "smtp_port": 587})
    assert r.json()["has_password"] is True and r.json()["smtp_port"] == 587
    # public setup status now reports email_enabled
    r = await client.get("/api/setup/status")
    assert r.json()["email_enabled"] is True


async def test_email_test_send(client, db, sent):
    await _seed(db, email_configured=True)
    r = await client.post("/api/admin/email-settings/test", headers=_auth(ROLES["admin"]))
    assert r.status_code == 200 and r.json()["to"] == "admin@example.com"
    assert sent and sent[0]["to"] == "admin@example.com"


async def test_email_test_send_reports_relay_error(client, db, monkeypatch):
    await _seed(db, email_configured=True)
    def boom(cfg, to, subject, text):
        raise ConnectionRefusedError("relay says no")
    monkeypatch.setattr("geodeploy.services.notifications._send_smtp", boom)
    r = await client.post("/api/admin/email-settings/test", headers=_auth(ROLES["admin"]))
    assert r.status_code == 502 and "relay says no" in r.json()["detail"]


# ── Forgot password (public, anti-enumeration) ────────────────────────────────────────────────

async def test_forgot_password_known_email(client, db, sent):
    await _seed(db, email_configured=True)
    r = await client.post("/api/auth/forgot-password", json={"email": "viewer@example.com"})
    assert r.status_code == 202 and r.json() == {"status": "ok"}
    inv = (await db.execute(select(Invitation).where(
        Invitation.user_id == ROLES["viewer"]))).scalar_one()
    assert inv.purpose == "password_reset"
    assert sent and sent[0]["to"] == "viewer@example.com"
    assert "/reset-password?token=" in sent[0]["text"]
    assert "http" in sent[0]["text"]  # absolute URL, not a bare path


async def test_forgot_password_unknown_email_same_response(client, db, sent):
    await _seed(db, email_configured=True)
    r = await client.post("/api/auth/forgot-password", json={"email": "nobody@example.com"})
    assert r.status_code == 202 and r.json() == {"status": "ok"}   # indistinguishable
    assert (await db.execute(select(Invitation))).scalars().all() == []
    assert sent == []


async def test_forgot_password_unconfigured_noop(client, db, sent):
    await _seed(db, email_configured=False)
    r = await client.post("/api/auth/forgot-password", json={"email": "viewer@example.com"})
    assert r.status_code == 202                                     # still the same answer
    assert (await db.execute(select(Invitation))).scalars().all() == []


async def test_forgot_password_token_actually_works(client, db, sent):
    await _seed(db, email_configured=True)
    await client.post("/api/auth/forgot-password", json={"email": "viewer@example.com"})
    token = sent[0]["text"].split("?token=")[1].split()[0]
    r = await client.post(f"/api/auth/password-reset/{token}", json={"password": "new-s3cret"})
    assert r.status_code == 204
    r = await client.post("/api/auth/login",
                          data={"username": "viewer@example.com", "password": "new-s3cret"})
    assert r.status_code == 200


# ── email_sent flags on invite / admin reset ──────────────────────────────────────────────────

async def test_invite_email_sent_flag(client, db, sent):
    await _seed(db, email_configured=True)
    r = await client.post("/api/users/invitations", headers=_auth(ROLES["admin"]),
                          json={"email": "new@example.com", "role": "editor"})
    assert r.json()["email_sent"] is True and r.json()["token"]
    assert sent[0]["to"] == "new@example.com" and "/accept-invite?token=" in sent[0]["text"]


async def test_invite_email_sent_false_when_unconfigured(client, db, sent):
    await _seed(db, email_configured=False)
    r = await client.post("/api/users/invitations", headers=_auth(ROLES["admin"]),
                          json={"email": "new@example.com", "role": "editor"})
    assert r.status_code == 201 and r.json()["email_sent"] is False  # copy link still works
    assert sent == []


async def test_send_failure_never_fails_the_invite(client, db, monkeypatch):
    await _seed(db, email_configured=True)
    def boom(cfg, to, subject, text):
        raise TimeoutError("smtp timeout")
    monkeypatch.setattr("geodeploy.services.notifications._send_smtp", boom)
    r = await client.post("/api/users/invitations", headers=_auth(ROLES["admin"]),
                          json={"email": "new@example.com", "role": "editor"})
    assert r.status_code == 201                                     # invite created regardless
    assert r.json()["email_sent"] is False and r.json()["token"]
