"""Pointing the wizard at a database that already holds an installation.

This is a legitimate thing to do — a rebuilt server, new hardware, recovery without a backup — and
it used to half-work in the worst way: `/configure-db` succeeded, `/configure-storage` was refused
because an admin already existed, and `.env` was left holding the installer's template defaults. The
instance could then never reach its own object storage, and the first symptom was a RESTORE failing
with `Invalid endpoint: None`, in a different product area entirely.

Two intents are possible at that moment, and the wizard now supports both: adopt the installation
(its settings are already in the database), or create a fresh database on the same server.
"""
import re

import pytest

from geodeploy.routers import setup as setup_router
from geodeploy.services import postgis as pg


def test_ciphertext_is_never_mistaken_for_a_secret():
    """`crypto.decrypt_secret` returns the token UNCHANGED when the key is wrong — it cannot tell a
    failure from a legacy plaintext value. Writing that into .env would fail every S3 call with a
    signature error blaming the key you can see rather than the key you do not have."""
    assert setup_router._looks_encrypted("gAAAAABm-not-a-real-token") is True
    assert setup_router._looks_encrypted("an-ordinary-secret-key") is False
    assert setup_router._looks_encrypted("") is False
    assert setup_router._looks_encrypted(None) is False


def test_a_new_database_name_must_be_an_identifier():
    """The name cannot be a bind parameter — CREATE DATABASE takes an identifier — so it is
    validated against a strict pattern and quoted. Either alone would be a mistake."""
    ok = ("geodeploy", "geodeploy_2", "_private", "A" * 63)
    bad = ("", "1leading-digit", "has space", 'quote"inside', "drop; DROP DATABASE x", "-", "a" * 64,
           "naïve")
    for name in ok:
        assert pg.DB_NAME_RE.match(name), f"{name!r} should be accepted"
    for name in bad:
        assert not pg.DB_NAME_RE.match(name), f"{name!r} should be refused"


@pytest.mark.asyncio
async def test_create_database_refuses_a_bad_name_before_connecting(monkeypatch):
    """Validation happens FIRST. A refusal must not depend on being able to reach the server, and a
    name that would be interpolated into SQL must never get as far as a connection."""
    called = False

    async def _connect(**kwargs):
        nonlocal called
        called = True
        raise AssertionError("must not connect for an invalid name")

    monkeypatch.setattr(pg.asyncpg, "connect", _connect)
    with pytest.raises(ValueError, match="must start with"):
        await pg.create_database("h", 5432, "postgres", "u", "p", "bad name; DROP DATABASE x")
    assert called is False


def test_the_new_database_path_is_wired_into_configure_db():
    """`create_database` must replace the TARGET, not merely be created and forgotten — otherwise
    the wizard would make an empty database and carry on using the occupied one."""
    import inspect

    src = inspect.getsource(setup_router.configure_db)
    assert "create_database" in src
    assert "target_db" in src
    # The target must be what gets stored and tested, not req.db.
    assert re.search(r"config\.postgis_db\s*=\s*target_db", src)
    assert re.search(r"test_connection\(req\.host,\s*req\.port,\s*target_db", src)


def test_reconnecting_rewrites_env_from_the_database():
    """The whole point: the database already holds the storage settings, so adopt them instead of
    leaving .env on the installer's defaults."""
    import inspect

    src = inspect.getsource(setup_router.configure_db)
    assert "_describe_existing_install" in src
    assert "_write_env(stored)" in src, "the STORED config must be written, not the fresh one"


# ── recovering the one secret a reconnect cannot ───────────────────────────────────────────────

def test_recovery_endpoint_changes_only_the_secret():
    """It must not be a back door onto /configure-storage. Endpoint, bucket and access key come from
    the database, so an unauthenticated caller cannot repoint storage at their own bucket — which is
    the reason that endpoint is guarded in the first place."""
    import inspect

    src = inspect.getsource(setup_router.recover_storage_secret)
    code = "\n".join(l.split("#")[0] for l in src.splitlines() if not l.strip().startswith("#"))
    assert "config.storage_secret_key = " in code
    for field in ("storage_endpoint =", "storage_bucket =", "storage_access_key ="):
        assert field not in code, f"recovery must not write {field}"


def test_recovery_requires_the_existing_access_key():
    """The access key is never sent to the browser, so knowing it is proof the caller owns the
    storage account. Compared with hmac.compare_digest rather than == so the check does not leak
    length or position through timing."""
    import inspect

    src = inspect.getsource(setup_router.recover_storage_secret)
    assert "compare_digest" in src
    assert "storage_access_key" in src


def test_recovery_closes_once_a_readable_secret_exists():
    """The window must be self-limiting: after a valid secret is stored it decrypts, so the guard
    refuses. Otherwise this would stay open forever as an unauthenticated write."""
    import inspect

    src = inspect.getsource(setup_router.recover_storage_secret)
    assert "_looks_encrypted" in src


def test_recovery_verifies_before_storing():
    """Storing an unchecked secret would swap one broken state for another AND close the window
    while doing it — leaving no way back through the UI."""
    import inspect

    src = inspect.getsource(setup_router.recover_storage_secret)
    assert src.index("test_connection") < src.index("config.storage_secret_key = ")


# ── creating a fresh database when the target is occupied ──────────────────────────────────────

def _req(**kw):
    from geodeploy.schemas import ConfigureDBRequest
    base = dict(type="external", host="db.example.com", port=5432, db="postgres",
                user="postgres", password="the-real-password")
    base.update(kw)
    return ConfigureDBRequest(**base)


def _bare_request():
    from starlette.requests import Request
    return Request({"type": "http", "method": "POST", "path": "/", "headers": [],
                    "query_string": b""})


async def _seed_installed(db):
    from geodeploy.models import SetupConfig, User
    db.add(SetupConfig(id=1, completed=True, postgis_type="external",
                       postgis_host="db.example.com", postgis_port=5432, postgis_db="postgres",
                       postgis_user="postgres", postgis_password="the-real-password"))
    db.add(User(id=1, email="owner@example.com", name="Owner", hashed_password="x", role="owner"))
    await db.commit()


@pytest.mark.asyncio
async def test_creating_a_new_database_is_allowed_with_the_current_credentials(db):
    """THE regression from the field. Reaching the "create a fresh database" offer requires having
    just connected — so the engine points at the OCCUPIED database, the guard sees its admin, and
    refused before `create_database` was ever read. The feature was unreachable in the only
    situation it exists for."""
    await _seed_installed(db)
    # Returns without raising.
    await setup_router._guard_setup_mutation(_bare_request(), db,
                                             req=_req(create_database="geodeploy"))


@pytest.mark.asyncio
async def test_creating_a_new_database_is_refused_on_another_server(db):
    """The exemption must not become the hijack the guard exists to prevent: pointing a live
    instance at a database the caller controls. Same intent, different server → refused."""
    await _seed_installed(db)
    with pytest.raises(Exception) as exc:
        await setup_router._guard_setup_mutation(
            _bare_request(), db, req=_req(host="attacker.example.com", create_database="geodeploy"))
    assert "403" in str(getattr(exc.value, "status_code", "")) or exc.value.status_code == 403


@pytest.mark.asyncio
async def test_creating_a_new_database_is_refused_with_a_wrong_password(db):
    """Ownership is proven by the CURRENT database password. Without it the caller cannot know what
    they would be replacing."""
    await _seed_installed(db)
    with pytest.raises(Exception) as exc:
        await setup_router._guard_setup_mutation(
            _bare_request(), db, req=_req(password="guessed", create_database="geodeploy"))
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_the_exemption_does_not_apply_without_create_database(db):
    """An ordinary reconfigure — no new database requested — stays admin-only."""
    await _seed_installed(db)
    with pytest.raises(Exception) as exc:
        await setup_router._guard_setup_mutation(_bare_request(), db, req=_req())
    assert exc.value.status_code == 403
