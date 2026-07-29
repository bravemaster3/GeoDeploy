import os
from sqlalchemy import event
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase
from .config import get_settings


class Base(DeclarativeBase):
    pass


def _make_engine():
    settings = get_settings()
    os.makedirs(f"{settings.data_dir}/sqlite", exist_ok=True)
    engine = create_async_engine(
        settings.sqlite_url,
        connect_args={"check_same_thread": False, "timeout": 30},
        echo=settings.is_dev,
    )

    # INTERIM (2026-07-29) — remove when state moves to PostgreSQL.
    # TWO processes write this file: the API (here, via SQLAlchemy) and the Celery worker (raw
    # `sqlite3.connect`, updating job progress + layer status throughout an ingest). In SQLite's
    # default DELETE journal mode a writer takes a lock that blocks *readers* too, so a long ingest
    # could surface as "database is locked" in unrelated API requests.
    #   journal_mode=WAL   readers never block on the writer. PERSISTENT — stored in the file header,
    #                      so setting it here also covers the worker's own connections.
    #   busy_timeout       wait for a contended write instead of failing instantly (per-connection,
    #                      hence `timeout` above AND in the tasks' sqlite3.connect calls).
    #   synchronous=NORMAL the standard, safe-with-WAL durability tradeoff.
    @event.listens_for(engine.sync_engine, "connect")
    def _sqlite_pragmas(dbapi_connection, _record):  # pragma: no cover - driver-level
        cur = dbapi_connection.cursor()
        try:
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA busy_timeout=30000")
            cur.execute("PRAGMA synchronous=NORMAL")
        finally:
            cur.close()

    return engine


engine = _make_engine()
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session
