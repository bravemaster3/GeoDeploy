"""Removing entries from the backup HISTORY.

A run that failed for a reason since fixed — a wrong key, a bucket that did not exist yet, a 2.1 GB
overflow — stayed red forever on the one page whose job is to tell an operator at a glance whether
their backups are healthy. A history that cannot be tidied stops being read, which is worse than an
untidy one.

The distinction these tests exist to protect: **history is a log of attempts, not the inventory.**
`GET /backups/stored` reads the destination's own manifests and answers "what backups do I have";
deleting a row here destroys nothing. `DELETE /backups/stored/{key}` is the one that does, and the
two must never be confusable — in the API or in the UI.
"""
from datetime import datetime, timedelta, timezone

import pytest
from jose import jwt
from passlib.context import CryptContext

from geodeploy.config import get_settings
from geodeploy.models import BackupRun, RestoreRun, SetupConfig, User

_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
ADMIN = 2
VIEWER = 3


def _h(uid=ADMIN):
    return {"Authorization":
            f"Bearer {jwt.encode({'sub': str(uid)}, get_settings().secret_key, algorithm='HS256')}"}


async def _seed(db):
    db.add(User(id=ADMIN, email="a@e.com", name="A", hashed_password=_pwd.hash("pw"),
                is_admin=True, role="admin"))
    db.add(User(id=VIEWER, email="v@e.com", name="V", hashed_password=_pwd.hash("pw"),
                is_admin=False, role="viewer"))
    db.add(SetupConfig(id=1))
    await db.commit()


def _naive(dt):
    return dt.replace(tzinfo=None)


def _run(status="error", **kw):
    return BackupRun(key=f"geodeploy-backups/{status}", status=status, trigger="manual",
                     started_at=_naive(datetime.now(timezone.utc)), **kw)


@pytest.mark.asyncio
async def test_a_failed_entry_can_be_removed(client, db):
    await _seed(db)
    row = _run("error", error_message="SignatureDoesNotMatch")
    db.add(row)
    await db.commit()
    await db.refresh(row)

    assert (await client.delete(f"/api/backups/runs/{row.id}", headers=_h())).status_code == 204
    assert (await client.get("/api/backups/runs", headers=_h())).json() == []


@pytest.mark.asyncio
async def test_a_running_entry_is_refused(client, db):
    """The worker still owns that row: deleting it would make its final UPDATE match nothing, so a
    backup that then SUCCEEDED would be missing from its own history. Something genuinely stuck is
    marked `error` by the stale-run reaper and becomes deletable then — that is the escape hatch,
    not this."""
    await _seed(db)
    row = _run("running", progress=40)
    db.add(row)
    await db.commit()
    await db.refresh(row)

    r = await client.delete(f"/api/backups/runs/{row.id}", headers=_h())
    assert r.status_code == 409
    assert "running" in r.json()["detail"].lower()


@pytest.mark.asyncio
async def test_clearing_failures_leaves_the_successes(client, db):
    """"Clear failed" must mean exactly that. A bulk action that quietly took the good entries too
    would destroy the record of every backup this instance ever made."""
    await _seed(db)
    for status in ("error", "error", "success"):
        db.add(_run(status))
    await db.commit()

    r = await client.delete("/api/backups/runs", params={"status": "error"}, headers=_h())
    assert r.status_code == 200 and r.json()["removed"] == 2
    left = (await client.get("/api/backups/runs", headers=_h())).json()
    assert [x["status"] for x in left] == ["success"]


@pytest.mark.asyncio
async def test_clearing_never_removes_a_running_row(client, db):
    """Even with status=all. The one row that is not history yet is the one still being written."""
    await _seed(db)
    db.add(_run("error"))
    db.add(_run("running", progress=10))
    await db.commit()

    r = await client.delete("/api/backups/runs", params={"status": "all"}, headers=_h())
    assert r.status_code == 200 and r.json()["removed"] == 1
    left = (await client.get("/api/backups/runs", headers=_h())).json()
    assert [x["status"] for x in left] == ["running"]


@pytest.mark.asyncio
async def test_an_unknown_status_filter_is_refused(client, db):
    """Only 'error' and 'all'. A log you can carve arbitrarily is a log nobody trusts."""
    await _seed(db)
    r = await client.delete("/api/backups/runs", params={"status": "success"}, headers=_h())
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_history_deletion_is_admin_only(client, db):
    await _seed(db)
    row = _run("error")
    db.add(row)
    await db.commit()
    await db.refresh(row)

    assert (await client.delete(f"/api/backups/runs/{row.id}",
                                headers=_h(VIEWER))).status_code == 403
    assert (await client.delete("/api/backups/runs", headers=_h(VIEWER))).status_code == 403


@pytest.mark.asyncio
async def test_removing_an_entry_is_audited(client, db):
    """The log is the thing being edited, so removing from it must itself leave a record — otherwise
    "where did that failure go?" has no answer."""
    from sqlalchemy import select

    from geodeploy.models import AuditLog

    await _seed(db)
    row = _run("error")
    db.add(row)
    await db.commit()
    await db.refresh(row)

    await client.delete(f"/api/backups/runs/{row.id}", headers=_h())
    actions = [a.action for a in (await db.execute(select(AuditLog))).scalars().all()]
    assert "backup.history.delete" in actions


@pytest.mark.asyncio
async def test_a_restore_entry_can_be_removed_too(client, db):
    await _seed(db)
    row = RestoreRun(key="geodeploy-backups/2026-01-01T00-00-00Z", status="error",
                     confirmed_by="A", started_at=_naive(datetime.now(timezone.utc)))
    db.add(row)
    await db.commit()
    await db.refresh(row)

    assert (await client.delete(f"/api/backups/restore/runs/{row.id}",
                                headers=_h())).status_code == 204
    assert (await client.get("/api/backups/restore/runs", headers=_h())).json() == []


@pytest.mark.asyncio
async def test_deleting_history_does_not_touch_the_destination(client, db, monkeypatch):
    """THE property that makes this safe to offer. `services.backup.delete_run` is what removes real
    objects; the history endpoints must never reach it — the two live next to each other in the UI
    and one of them is unrecoverable."""
    from geodeploy.services import backup as bk

    def _boom(*a, **k):      # pragma: no cover — reaching this is the failure
        raise AssertionError("history deletion called into the destination")

    monkeypatch.setattr(bk, "delete_run", _boom)

    await _seed(db)
    row = _run("error")
    db.add(row)
    await db.commit()
    await db.refresh(row)

    assert (await client.delete(f"/api/backups/runs/{row.id}", headers=_h())).status_code == 204
    assert (await client.delete("/api/backups/runs", headers=_h())).status_code == 200


@pytest.mark.asyncio
async def test_a_stale_running_row_becomes_deletable_after_the_reaper(client, db):
    """The two mechanisms have to compose: the reaper turns an abandoned `running` row into `error`
    (which is what a restored snapshot leaves behind), and only then can it be cleared away. Without
    that, the rows this feature most needs to remove would be the ones it refuses."""
    from geodeploy.services import backup as bk

    await _seed(db)
    row = _run("running", progress=40)
    row.started_at = _naive(datetime.now(timezone.utc)
                            - timedelta(hours=bk.STALE_RUN_HOURS + 1))
    db.add(row)
    await db.commit()
    await db.refresh(row)

    # The GET is one of the two reaping points.
    assert (await client.get("/api/backups/runs", headers=_h())).status_code == 200
    assert (await client.delete(f"/api/backups/runs/{row.id}", headers=_h())).status_code == 204
