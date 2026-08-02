"""Passwords must survive the trip into a `postgresql://` URL.

Every DSN in the codebase was built by f-string interpolation, so a password containing `@` made the
URL's authority split in the wrong place — libpq then connected to whatever followed the last `@`
rather than the configured host — and a `%` began an invalid percent-escape. The operator sees
"Cannot connect to PostGIS" for credentials that are perfectly correct, which sends them to check
firewalls and pg_hba.conf.

Generated passwords hit this routinely, and the setup wizard is where it lands: the very first thing
a new install does with an external database.
"""
from geodeploy.config import Settings

# Every character that means something in a URL, in one value.
NASTY = "p@ss:w/rd?x#y%z"


def _settings() -> Settings:
    s = Settings()
    s.postgis_host = "db.example.com"
    s.postgis_port = 5432
    s.postgis_db = "geodeploy"
    s.postgis_user = "geo user"      # a space is legal in a role name and illegal in a URL
    s.postgis_password = NASTY
    s.postgis_sslmode = ""
    return s


def test_password_is_encoded_in_the_async_dsn():
    dsn = _settings().postgis_dsn
    assert NASTY not in dsn, "the raw password reached the URL — @ and % change what it means"
    assert "%40" in dsn and "%25" in dsn          # @ and % encoded
    # The host must still be the host. This is the failure that looks like a firewall problem: with
    # a raw `@` in the password the authority splits late and libpq dials 'ss:w/rd?x#y%z@db...'.
    assert dsn.rsplit("@", 1)[1].startswith("db.example.com:5432/")


def test_password_is_encoded_in_the_sync_dsn():
    dsn = _settings().postgis_sync_dsn
    assert NASTY not in dsn
    assert dsn.rsplit("@", 1)[1].startswith("db.example.com:5432/")


def test_user_is_encoded_too():
    """A role name may contain characters a URL may not — a space among them."""
    assert "geo%20user" in _settings().postgis_dsn


def test_setup_connection_test_passes_credentials_as_arguments():
    """`services/postgis` must not build a URL at all. asyncpg accepts host/user/password directly,
    so there is nothing to quote and nothing to get wrong — and this is the SETUP path, where a
    misleading failure costs a new user their first impression."""
    import inspect

    from geodeploy.services import postgis as pg

    for fn in (pg.test_connection, pg.create_user_schema, pg._wait_healthy):
        src = inspect.getsource(fn)
        code = "\n".join(l.split("#")[0] for l in src.splitlines() if not l.strip().startswith("#"))
        assert "postgresql://" not in code, (
            f"{fn.__name__} builds a DSN string; pass host/user/password to asyncpg.connect instead")


def test_martin_dsn_is_encoded():
    """Martin's connection string is written into martin-config.yaml verbatim."""
    import inspect

    from geodeploy.services import martin

    src = inspect.getsource(martin._pg_sync_dsn)
    assert "quote(" in src, "martin's DSN must percent-encode the credentials"


# ── sslmode is a libpq spelling, and asyncpg does not have it ──────────────────────────────────

def test_async_dsn_carries_no_sslmode():
    """`postgresql+asyncpg://...?sslmode=prefer` fails the ENGINE with

        connect() got an unexpected keyword argument 'sslmode'

    because SQLAlchemy's asyncpg dialect forwards unrecognised query parameters straight into
    `asyncpg.connect()`. The setup wizard sets sslmode=prefer for every EXTERNAL database, so this
    broke external PostGIS and nothing else — and it broke it at schema creation, AFTER the
    connection test had passed, which made it read as a permissions problem.
    """
    s = _settings()
    s.postgis_sslmode = "prefer"
    assert "sslmode" not in s.postgis_dsn
    assert "ssl" not in s.postgis_dsn.split("://", 1)[1]      # no ssl param smuggled in either


def test_sslmode_becomes_asyncpg_connect_args():
    s = _settings()
    s.postgis_sslmode = "require"
    assert s.postgis_connect_args == {"ssl": "require"}


def test_no_ssl_setting_means_no_connect_args():
    """A local database sets sslmode empty. Passing ssl=None or ssl='' would change behaviour."""
    s = _settings()
    s.postgis_sslmode = ""
    assert s.postgis_connect_args == {}


def test_disable_is_not_passed_as_a_string():
    """asyncpg treats an unknown truthy string as 'use SSL'. 'disable' must not silently enable it."""
    s = _settings()
    s.postgis_sslmode = "disable"
    assert s.postgis_connect_args == {}


def test_sync_dsn_keeps_sslmode():
    """psycopg2 and pg_dump DO speak libpq, so the sync URL must keep the libpq spelling."""
    s = _settings()
    s.postgis_sslmode = "require"
    assert "sslmode=require" in s.postgis_sync_dsn
