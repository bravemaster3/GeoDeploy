import os
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

# ─────────────────────────────────────────────────────────────────────────────
# CRITICAL SAFETY: this suite is DESTRUCTIVE (setup_db teardown drops all tables;
# _isolate runs DELETE FROM on every table before each test). It MUST only ever
# touch a throwaway database.
#
# History: an earlier version used os.environ.setdefault("GEODEPLOY_DATA_DIR", ...).
# setdefault is a no-op when the var is ALREADY set — and inside the geodeploy-api
# container it is always set to /data. Running `pytest` in that container therefore
# pointed the test engine at the PRODUCTION sqlite DB and wiped it. Never again:
#   1) HARD-set the data dir (assignment, not setdefault) BEFORE importing the app,
#      so the engine is built against the test path.
#   2) A fail-safe guard below ABORTS collection if the engine URL is not the
#      throwaway test DB — defence in depth if step 1 ever regresses.
# See notes_temp/notes_for_future.md ("NEVER run the test suite against a real DB").
# ─────────────────────────────────────────────────────────────────────────────
TEST_DATA_DIR = "/tmp/geodeploy-test"
os.environ["GEODEPLOY_DATA_DIR"] = TEST_DATA_DIR
os.environ["GEODEPLOY_SECRET_KEY"] = "test-secret"
os.environ["GEODEPLOY_ENV"] = "development"
os.makedirs(f"{TEST_DATA_DIR}/sqlite", exist_ok=True)

from geodeploy.main import app
from geodeploy.database import engine, Base, AsyncSessionLocal

# Fail-safe: refuse to run if the engine is not pointed at the throwaway test DB.
_ENGINE_URL = str(engine.url)
if TEST_DATA_DIR not in _ENGINE_URL:
    raise RuntimeError(
        f"REFUSING to run the test suite: engine URL {_ENGINE_URL!r} is not the throwaway "
        f"test database (expected a path under {TEST_DATA_DIR!r}). This suite drops/DELETEs "
        "every table. Do NOT run pytest inside the production api container without overriding "
        "GEODEPLOY_DATA_DIR to a scratch path."
    )


def _assert_test_db():
    """Guard the destructive fixtures too — belt-and-suspenders against a mid-run env change."""
    assert TEST_DATA_DIR in str(engine.url), "destructive fixture blocked: not the test DB"


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
    async with AsyncSessionLocal() as s:
        for tbl in ("portals", "vector_layers", "raster_layers", "external_sources",
                    "upload_jobs", "invitations", "users", "setup_config"):
            try:
                await s.execute(text(f"DELETE FROM {tbl}"))
            except Exception:
                pass
        await s.commit()
    yield


@pytest_asyncio.fixture
async def db(setup_db):
    """A DB session for seeding fixtures/asserting directly against the test database."""
    async with AsyncSessionLocal() as s:
        yield s


@pytest_asyncio.fixture
async def client(setup_db):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
