import asyncio
import os
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

os.environ.setdefault("GEODEPLOY_SECRET_KEY", "test-secret")
os.environ.setdefault("GEODEPLOY_DATA_DIR", "/tmp/geodeploy-test")
os.environ.setdefault("GEODEPLOY_ENV", "development")

from geodeploy.main import app
from geodeploy.database import engine, Base, AsyncSessionLocal


@pytest_asyncio.fixture(scope="session")
async def setup_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture(autouse=True)
async def _isolate(setup_db):
    """Wipe the mutable tables before each test so the shared test DB doesn't leak state between
    tests (the engine/schema is session-scoped for speed)."""
    from sqlalchemy import text
    async with AsyncSessionLocal() as s:
        for tbl in ("portals", "vector_layers", "raster_layers", "external_sources",
                    "upload_jobs", "users", "setup_config"):
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
