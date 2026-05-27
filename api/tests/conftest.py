import asyncio
import os
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

os.environ.setdefault("GEODEPLOY_SECRET_KEY", "test-secret")
os.environ.setdefault("GEODEPLOY_DATA_DIR", "/tmp/geodeploy-test")
os.environ.setdefault("GEODEPLOY_ENV", "development")

from geodeploy.main import app
from geodeploy.database import engine, Base


@pytest_asyncio.fixture(scope="session")
async def setup_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture
async def client(setup_db):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
