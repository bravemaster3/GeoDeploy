import os
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession

# ─────────────────────────────────────────────────────────────────────────────
# CRITICAL SAFETY: this suite is DESTRUCTIVE (setup_db teardown drops all tables;
# _isolate runs DELETE FROM on every table before each test). It MUST only ever
# touch a throwaway database.
#
# History: an earlier version used os.environ.setdefault(...). setdefault is a no-op
# when the var is ALREADY set — and inside the geodeploy-api container it always is —
# so `pytest` in that container pointed the engine at the PRODUCTION database and
# wiped it. The rule that came out of it has not changed now that state lives in
# PostgreSQL, only its shape:
#   1) HARD-set the connection (assignment, not setdefault) BEFORE importing the app.
#   2) A fail-safe guard ABORTS collection unless the target database NAME is clearly
#      a test database — defence in depth if step 1 ever regresses.
# See notes_temp/notes_for_future.md ("NEVER run the test suite against a real DB").
#
# The database must exist and be reachable: CI provides it as a service container,
# locally use e.g.
#   docker run -d --rm -p 55432:5432 -e POSTGRES_PASSWORD=test \
#     -e POSTGRES_USER=geodeploy -e POSTGRES_DB=geodeploy_test postgis/postgis:16-3.4
# then POSTGIS_PORT=55432 pytest
# ─────────────────────────────────────────────────────────────────────────────
TEST_DATA_DIR = "/tmp/geodeploy-test"
TEST_DB = os.environ.get("POSTGIS_DB", "geodeploy_test")

os.environ["GEODEPLOY_DATA_DIR"] = TEST_DATA_DIR
os.environ["GEODEPLOY_SECRET_KEY"] = "test-secret"
os.environ["GEODEPLOY_ENV"] = "development"
os.environ["POSTGIS_HOST"] = os.environ.get("POSTGIS_HOST", "127.0.0.1")
os.environ["POSTGIS_PORT"] = os.environ.get("POSTGIS_PORT", "5432")
os.environ["POSTGIS_DB"] = TEST_DB
os.environ["POSTGIS_USER"] = os.environ.get("POSTGIS_USER", "geodeploy")
os.environ["POSTGIS_PASSWORD"] = os.environ.get("POSTGIS_PASSWORD", "test")
# Martin's config path is its OWN env var, NOT derived from GEODEPLOY_DATA_DIR. Several tests
# exercise layer-delete, which calls martin.regenerate_config → writes the config + reloads Martin.
# Without this override, running the suite where /data is the PRODUCTION volume would overwrite the
# live Martin config from an empty test DB and break real tile serving.
os.environ["MARTIN_CONFIG_PATH"] = f"{TEST_DATA_DIR}/martin-config.yaml"
os.makedirs(TEST_DATA_DIR, exist_ok=True)

# The name must SAY it is a test database. Refusing on anything else is the whole guard: a DSN
# typo that pointed at `geodeploy` would otherwise drop every table in production.
if not ("test" in TEST_DB.lower() or TEST_DB.lower().endswith("_ci")):
    raise RuntimeError(
        f"REFUSING to run the test suite: POSTGIS_DB={TEST_DB!r} does not look like a throwaway "
        "database (its name must contain 'test'). This suite DROPS EVERY TABLE."
    )

from geodeploy.main import app
from geodeploy import database
from geodeploy.database import Base

# main.py's lifespan builds the engine, but fixtures need it at import time too.
if database.configure(force=True) is None:
    raise RuntimeError("Test database is not configured - see the header of this file.")

# NullPool for tests, deliberately. pytest-asyncio gives each test its OWN event loop, and an
# asyncpg connection belongs to the loop that opened it — so a POOLED connection outlives its loop,
# is never properly closed, and the server accumulates them until it answers TooManyConnections
# partway through the run (which is exactly what happened: 103 failures, all connection errors).
# NullPool opens and closes per use: marginally slower, and correct across loops.
from sqlalchemy.ext.asyncio import async_sessionmaker as _sessionmaker, create_async_engine as _mk
from sqlalchemy.pool import NullPool as _NullPool

from geodeploy.config import get_settings as _settings_for_engine

engine = _mk(_settings_for_engine().postgis_dsn, poolclass=_NullPool)
database.engine = engine
database.AsyncSessionLocal = _sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

# Fail-safe: the engine's own URL must name the throwaway database.
_ENGINE_URL = str(engine.url)
if TEST_DB not in _ENGINE_URL:
    raise RuntimeError(
        f"REFUSING to run the test suite: engine URL {_ENGINE_URL!r} is not the throwaway test "
        f"database {TEST_DB!r}. This suite drops/DELETEs every table."
    )

from geodeploy.config import get_settings as _get_settings
_MARTIN_PATH = _get_settings().martin_config_path
if TEST_DATA_DIR not in _MARTIN_PATH:
    raise RuntimeError(
        f"REFUSING to run the test suite: martin_config_path {_MARTIN_PATH!r} is not under the "
        f"throwaway test dir {TEST_DATA_DIR!r}. Set MARTIN_CONFIG_PATH to a scratch path."
    )


def _assert_test_db():
    """Guard the destructive fixtures too - belt-and-suspenders against a mid-run env change."""
    assert TEST_DB in str(engine.url), "destructive fixture blocked: not the test DB"


@pytest_asyncio.fixture(scope="session")
async def setup_db():
    _assert_test_db()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    _assert_test_db()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture(autouse=True)
async def _isolate(setup_db):
    """Wipe the mutable tables before each test so the shared test DB doesn't leak state between
    tests (the engine/schema is session-scoped for speed)."""
    _assert_test_db()
    from sqlalchemy import text
    async with database.AsyncSessionLocal() as s:
        for tbl in ("portals", "vector_layers", "raster_layers", "external_sources",
                    "upload_jobs", "invitations", "api_tokens", "audit_log", "backup_runs",
                    "restore_runs", "deployment_runs", "users", "setup_config"):
            try:
                # TRUNCATE ... RESTART IDENTITY: unlike SQLite, Postgres sequences keep climbing
                # after a DELETE, so tests that assert on a specific id (audit ids, layer ids)
                # would pass alone and fail in a full run. CASCADE because of the FKs between them.
                await s.execute(text(f"TRUNCATE TABLE {tbl} RESTART IDENTITY CASCADE"))
            except Exception:
                pass
        # Start every id sequence ABOVE the range the fixtures hand-seed (they use small explicit
        # ids like 1..10). Postgres sequences are independent of explicitly-inserted ids, so after
        # RESTART IDENTITY an app-created row would be handed id=1 and collide with a seeded one —
        # SQLite never showed this because its rowid picks max+1. Seeded ids stay small and
        # readable; anything the app creates lands at 1000+.
        for tbl in ("users", "vector_layers", "raster_layers", "portals", "external_sources",
                    "audit_log", "api_tokens", "invitations", "backup_runs", "restore_runs",
                    "deployment_runs"):
            try:
                await s.execute(text(
                    f"SELECT setval(pg_get_serial_sequence('{tbl}', 'id'), 1000, false)"))
            except Exception:
                pass
        await s.commit()
    yield


@pytest_asyncio.fixture
async def db(setup_db):
    """A DB session for seeding fixtures/asserting directly against the test database."""
    async with database.AsyncSessionLocal() as s:
        yield s


@pytest_asyncio.fixture
async def client(setup_db):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
