"""A backup snapshot always contains its own run row marked `running`.

The row is created `running` before `pg_dump` and set to `success` after, so the dump necessarily
captures it mid-flight. Restore that snapshot and the row comes back into a live instance, where
nothing will ever finish it: `POST /backups/run` answers 409 "A backup is already running" forever,
and the settings page shows the same, permanently.

That is not a hypothetical — it happened on the first real backup-and-restore round trip. Two
independent defences are tested here: the reaper on read/start (which also covers a worker killed
mid-run), and the restore task clearing the rows it has just imported.
"""
from datetime import datetime, timedelta, timezone

import pytest
from jose import jwt
from passlib.context import CryptContext

from geodeploy.config import get_settings
from geodeploy.models import BackupRun, RestoreRun, SetupConfig, User
from geodeploy.services import backup as bk

_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
ADMIN = 2


def _h(uid=ADMIN):
    return {"Authorization":
            f"Bearer {jwt.encode({'sub': str(uid)}, get_settings().secret_key, algorithm='HS256')}"}


async def _seed_admin(db):
    db.add(User(id=ADMIN, email="a@e.com", name="A", hashed_password=_pwd.hash("pw"),
                is_admin=True, role="admin"))
    db.add(SetupConfig(id=1))
    await db.commit()


def _naive(dt):
    return dt.replace(tzinfo=None)


@pytest.mark.asyncio
async def test_a_stale_running_backup_does_not_block_a_new_one(client, db):
    """THE regression: an instance must not be permanently unable to back up."""
    old = BackupRun(key="geodeploy-backups/2026-01-01T00-00-00Z", status="running",
                    trigger="manual", progress=40,
                    started_at=_naive(datetime.now(timezone.utc)
                                      - timedelta(hours=bk.STALE_RUN_HOURS + 1)))
    await _seed_admin(db)
    db.add(old)
    await db.commit()

    # The history endpoint the UI polls is one of the two reaping points.
    r = await client.get("/api/backups/runs", headers=_h())
    assert r.status_code == 200
    assert all(run["status"] != "running" for run in r.json()), \
        "a run older than STALE_RUN_HOURS is still reported as running"


@pytest.mark.asyncio
async def test_a_recent_running_backup_is_left_alone(client, db):
    """The reaper must not cancel a backup that is genuinely in flight — a large instance can copy
    objects for a long time, and reporting that as interrupted would be worse than the bug."""
    fresh = BackupRun(key="geodeploy-backups/2026-08-02T00-00-00Z", status="running",
                      trigger="manual", progress=40,
                      started_at=_naive(datetime.now(timezone.utc) - timedelta(minutes=5)))
    await _seed_admin(db)
    db.add(fresh)
    await db.commit()

    r = await client.get("/api/backups/runs", headers=_h())
    assert r.status_code == 200
    assert any(run["status"] == "running" for run in r.json())


@pytest.mark.asyncio
async def test_stale_restore_runs_are_reaped_too(client, db):
    """A restore interrupted by a reboot leaves the same permanent 'running' row."""
    old = RestoreRun(key="geodeploy-backups/2026-01-01T00-00-00Z", status="running",
                     confirmed_by="someone@example.com", progress=70,
                     started_at=_naive(datetime.now(timezone.utc)
                                       - timedelta(hours=bk.STALE_RUN_HOURS + 1)))
    await _seed_admin(db)
    db.add(old)
    await db.commit()

    await client.get("/api/backups/runs", headers=_h())      # reaps both tables

    r = await client.get("/api/backups/restore/runs", headers=_h())
    assert r.status_code == 200
    assert all(run["status"] != "running" for run in r.json())


def test_restore_clears_running_rows_without_waiting_for_the_reaper():
    """The restore task knows immediately that the rows it just imported are stale — nothing
    recorded in a snapshot can still be running after that snapshot is restored. Waiting six hours
    for the reaper would leave the instance unable to back up in the meantime."""
    from geodeploy.tasks import restore as rt
    assert hasattr(rt, "_clear_restored_running_rows")

    import inspect
    src = inspect.getsource(rt.run_restore)
    # It must run AFTER the database is replaced — clearing before would only clear rows the restore
    # is about to overwrite, which is the one ordering that does nothing at all.
    assert src.index("restore_database") < src.index("_clear_restored_running_rows")


def test_restore_reapplies_schema_migrations_after_the_database_is_replaced():
    """`pg_restore --clean` installs the SNAPSHOT's schema, so every column added since that backup
    disappears from a RUNNING instance — and the API only applies migrations at startup.

    This was found in production: restoring a backup taken before `portals.thumbnail_url` existed
    dropped the column, after which publishing a portal succeeded while recording its thumbnail
    silently failed. The BIGINT widening reverts the same way, bringing back 'integer out of range'.
    """
    import inspect

    from geodeploy.schema_migrations import PG_MIGRATIONS
    from geodeploy.tasks import restore as rt

    assert PG_MIGRATIONS, "the migration list must be importable without importing the FastAPI app"

    src = inspect.getsource(rt.run_restore)
    assert "_reapply_schema_migrations" in src
    # AFTER the restore, or it only migrates a schema that is about to be thrown away.
    assert src.index("restore_database") < src.index("_reapply_schema_migrations")
    # BEFORE the row bookkeeping, which writes to tables those migrations may have just repaired.
    assert src.index("_reapply_schema_migrations") < src.index("_clear_restored_running_rows")


def test_restore_records_itself_even_though_it_deletes_its_own_row():
    """The restored database predates the restore, so the run row it was updating is gone. Without
    a re-insert the restore leaves NO record: nothing to poll for a verdict, nothing in history."""
    import inspect

    from geodeploy.tasks import restore as rt

    src = inspect.getsource(rt._finish)
    assert "INSERT INTO restore_runs" in src
    assert "rowcount" in src, "it must detect that the UPDATE matched nothing"
    # A forced id with a sequence inherited from the snapshot collides on the NEXT restore.
    assert "setval" in src
