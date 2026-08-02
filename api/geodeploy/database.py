"""State database — PostgreSQL (2026-07-30; previously SQLite).

**One database, not two.** GeoDeploy's own state (users, the layer catalog, portals, settings,
audit log) now lives in the SAME PostgreSQL database as the spatial data, in the `public` schema —
user layers live in `geodeploy_u*` schemas and never collide. Three things fall out of that, and
they are the reasons for the move:

* **One backup.** `pg_dump` captures state and spatial data together, atomically. The old split
  meant a SQLite file snapshot plus a PostGIS dump, taken at different instants.
* **Nothing to delete by accident.** The state was a file on disk that a stray command (or a test
  run pointed at the wrong path) could wipe. See `api/tests/conftest.py` for how that happened.
* **Real concurrency.** The API and the Celery worker both write; SQLite gave them a single global
  write lock (mitigated with WAL, now unnecessary).

**Boot order.** The DB connection is configured by the setup wizard, so the app MUST start with no
database at all and still serve `/api/setup/*`. Hence a rebuildable engine: `engine` is None until
credentials exist in the environment, `configure()` builds it once they do, and `get_db` answers
503 in between. Credentials come from `.env` — never from `SetupConfig`, which lives inside the
database being configured.
"""
import logging

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from .config import get_settings

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


engine = None
AsyncSessionLocal = None


def is_configured() -> bool:
    """True when the environment carries enough to reach a database. Deliberately checks the
    SETTINGS (i.e. `.env`), not any stored row — the stored row is in the database.

    The PASSWORD is part of the test on purpose. `install.sh` copies `.env.example`, which ships
    POSTGIS_HOST=postgres / DB / USER already filled in but the password EMPTY — so a host/db/user
    check alone reports "configured" on a brand-new install, builds an engine, and tries to reach a
    server the wizard has not provisioned yet. That put the API in a restart loop on first boot."""
    s = get_settings()
    return bool(s.postgis_host and s.postgis_db and s.postgis_user and s.postgis_password)


def configure(force: bool = False):
    """(Re)build the engine from the current settings. Called at startup and again by the setup
    wizard the moment it writes credentials, so the running process picks them up without a
    restart. Returns the engine, or None when nothing is configured yet."""
    global engine, AsyncSessionLocal
    if engine is not None and not force:
        return engine
    if not is_configured():
        return None
    settings = get_settings()
    engine = create_async_engine(
        settings.postgis_dsn,
        # SSL as a real connect argument, not a URL query parameter: asyncpg has no `sslmode`
        # keyword, and the dialect would forward it verbatim into asyncpg.connect().
        connect_args=settings.postgis_connect_args,
        echo=settings.is_dev,
        pool_pre_ping=True,     # a recycled/idle connection after a DB restart must not 500 a request
        pool_size=10,
        max_overflow=20,
    )
    AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    logger.info("state database engine configured (%s/%s)", settings.postgis_host, settings.postgis_db)
    return engine


configure()      # no-op before the wizard has run


async def get_db() -> AsyncSession:
    if AsyncSessionLocal is None and configure() is None:
        # Only the setup routes should ever be reachable in this state, and they don't depend on
        # the DB until they have configured it.
        raise HTTPException(503, "No database configured yet — finish setup first.")
    async with AsyncSessionLocal() as session:
        yield session
