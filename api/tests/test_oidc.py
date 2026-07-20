"""OIDC SSO (A-04). The redirect dance needs a live IdP, so we unit-test the security-critical
`resolve_user` account-linking/provisioning policy directly, plus the public status endpoint and
that the admin settings endpoint never returns the client secret.
"""
import pytest
from jose import jwt
from passlib.context import CryptContext

from geodeploy.config import get_settings
from geodeploy.models import SetupConfig, User
from geodeploy.services.oidc import OidcError, resolve_user

_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")


def _cfg(auto=False, domains=None, role="viewer"):
    return {"issuer": "https://idp", "client_id": "cid", "client_secret": "sec", "label": "SSO",
            "auto_provision": auto, "allowed_domains": domains or [], "default_role": role}


def _admin_jwt(uid=1):
    return jwt.encode({"sub": str(uid)}, get_settings().secret_key, algorithm="HS256")


# ── resolve_user policy matrix ──────────────────────────────────────────────────────────────────

async def test_unverified_email_rejected(db):
    with pytest.raises(OidcError):
        await resolve_user({"sub": "s1", "email": "a@x.org", "email_verified": False}, _cfg(), db)


async def test_links_existing_user_by_verified_email(db):
    db.add(User(id=1, email="a@x.org", name="A", hashed_password=_pwd.hash("pw"), role="editor"))
    await db.commit()
    u = await resolve_user({"sub": "sub-123", "email": "a@x.org", "email_verified": True}, _cfg(), db)
    assert u.id == 1 and u.oidc_sub == "sub-123"  # subject pinned on first link


async def test_matches_by_sub_regardless_of_email(db):
    db.add(User(id=1, email="a@x.org", name="A", hashed_password="!", role="viewer", oidc_sub="sub-9"))
    await db.commit()
    u = await resolve_user({"sub": "sub-9", "email": "changed@x.org", "email_verified": True}, _cfg(), db)
    assert u.id == 1


async def test_no_account_without_auto_provision_rejected(db):
    with pytest.raises(OidcError):
        await resolve_user({"sub": "s", "email": "new@x.org", "email_verified": True}, _cfg(auto=False), db)


async def test_auto_provision_allowed_domain_creates_user(db):
    u = await resolve_user({"sub": "s", "email": "new@allowed.org", "email_verified": True, "name": "New"},
                           _cfg(auto=True, domains=["allowed.org"], role="editor"), db)
    assert u.email == "new@allowed.org" and u.role == "editor" and u.oidc_sub == "s"
    # A random bcrypt hash → password login can't succeed but verify still returns False cleanly.
    assert _pwd.verify("whatever", u.hashed_password) is False


async def test_auto_provision_disallowed_domain_rejected(db):
    with pytest.raises(OidcError):
        await resolve_user({"sub": "s", "email": "x@evil.org", "email_verified": True},
                           _cfg(auto=True, domains=["allowed.org"]), db)


# ── Endpoints ─────────────────────────────────────────────────────────────────────────────────

async def test_status_reflects_config(client, db):
    assert (await client.get("/api/auth/oidc/status")).json()["enabled"] is False
    db.add(SetupConfig(id=1, oidc_enabled=True, oidc_issuer="https://idp",
                       oidc_client_id="cid", oidc_client_secret="sec", oidc_label="Corp SSO"))
    await db.commit()
    body = (await client.get("/api/auth/oidc/status")).json()
    assert body["enabled"] is True and body["label"] == "Corp SSO"


async def test_settings_never_returns_secret(client, db):
    db.add(User(id=1, email="o@x.org", name="O", hashed_password=_pwd.hash("pw"), is_admin=True, role="owner"))
    db.add(SetupConfig(id=1, oidc_client_secret="topsecret"))
    await db.commit()
    r = await client.get("/api/admin/oidc-settings", headers={"Authorization": f"Bearer {_admin_jwt()}"})
    assert r.status_code == 200
    body = r.json()
    assert body["has_client_secret"] is True
    assert "oidc_client_secret" not in body and "topsecret" not in str(body)
    assert body["redirect_uri"].endswith("/api/auth/oidc/callback")
