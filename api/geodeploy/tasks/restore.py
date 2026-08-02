"""Restore job (Celery).

Runs on the `backup` queue for the same reason backups do: it can take as long as the backup did,
and must not occupy the ingest slots.

**Order matters and is not arbitrary.** Objects go back BEFORE the database. If the database is
restored first and the object copy then fails, the catalog advertises layers whose files are not
there yet — every one of them 404s and the instance looks corrupt. The other way round, a failure
between the steps leaves orphaned objects that nothing references: wasted space, not broken data.
"""
import json
import logging
import os
import tempfile
from datetime import datetime, timezone

from ..celery_app import celery_app
from ..config import get_settings
from .. import state_db

logger = logging.getLogger(__name__)


def _step(run_id: int, step: str, progress: int) -> None:
    try:
        with state_db.connect() as conn:
            conn.execute("UPDATE restore_runs SET current_step = ?, progress = ? WHERE id = ?",
                         (step, progress, run_id))
    except Exception:
        pass       # progress reporting must never fail the restore


def _finish(run_id: int, status: str, seed: dict | None = None, **fields) -> None:
    """Record the final status — RE-INSERTING the row if the restore removed it.

    A restore replaces the database this row lives in, and the snapshot predates the restore, so
    after the database step the row is simply gone. The UPDATE then matched nothing and the result
    was never recorded anywhere: the restore that just ran did not appear in its own history, and
    the UI had nothing to poll to learn whether it worked. `seed` is the row as it was BEFORE the
    database was replaced, captured at the start of the task for exactly this.
    """
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    sets = ["status = ?", "finished_at = ?"]
    vals = [status, now]
    for k, v in fields.items():
        sets.append(f"{k} = ?")
        vals.append(v)
    vals.append(run_id)
    try:
        with state_db.connect() as conn:
            cur = conn.execute(f"UPDATE restore_runs SET {', '.join(sets)} WHERE id = ?", vals)
            if cur.rowcount:
                return
            if not seed:
                logger.warning("restore %s: row is gone and no seed was captured", run_id)
                return
            row = {"id": run_id, "key": seed.get("key"), "confirmed_by": seed.get("confirmed_by"),
                   "started_at": seed.get("started_at") or now, "status": status,
                   "finished_at": now, **fields}
            cols = [c for c in ("id", "key", "status", "confirmed_by", "started_at", "finished_at",
                                "error_message", "current_step", "progress", "detail")
                    if c in row]
            conn.execute(
                f"INSERT INTO restore_runs ({', '.join(cols)}) "
                f"VALUES ({', '.join('?' for _ in cols)})", [row[c] for c in cols])
            # The id was forced rather than drawn from the sequence, and the sequence came from the
            # snapshot — so without this the NEXT restore collides on the primary key.
            conn.execute("SELECT setval(pg_get_serial_sequence('restore_runs', 'id'), "
                         "GREATEST((SELECT MAX(id) FROM restore_runs), 1))")
            logger.info("restore %s: re-inserted its run row after the database was replaced", run_id)
    except Exception:
        logger.exception("restore %s: could not record final status %s", run_id, status)


def _reapply_schema_migrations() -> dict:
    """Re-apply the additive column migrations over the just-restored schema.

    `pg_restore --clean` DROPS and recreates every table from the dump, so the schema becomes the
    SNAPSHOT'S schema — every column added by a release after that backup is gone. The API applies
    these at startup and nothing re-applies them mid-run, so an instance stayed on the old schema
    until someone happened to restart it.

    That is not theoretical. Restoring a backup taken before `portals.thumbnail_url` existed removed
    the column from a running instance: publishing a portal succeeded, recording its thumbnail
    failed, and portal cards silently lost their images. The BIGINT widening of `size_bytes` reverts
    the same way, bringing back the 'integer out of range' that broke backups over 2.1 GB.

    Every statement is `IF NOT EXISTS` or guarded, so this is a no-op when the snapshot is current.
    """
    from ..schema_migrations import PG_MIGRATIONS

    applied, failed = 0, []
    for stmt in PG_MIGRATIONS:
        try:
            with state_db.connect() as conn:
                conn.execute(stmt)
            applied += 1
        except Exception as exc:      # one bad statement must not stop the rest
            logger.warning("restore: schema migration failed (%s): %s",
                           stmt.strip().splitlines()[0], exc)
            failed.append(str(exc)[:200])
    return {"applied": applied, "failed": failed}


def _reaudit_restore(key: str, seed: dict | None, status: str, detail: dict) -> None:
    """Write the audit entry for THIS restore back into the restored database.

    `POST /backups/restore` already audits `backup.restore` — and then the restore replaces the
    database, and the snapshot does not contain that entry. So the log lost the record of the single
    most destructive operation in the product, which is precisely the one an operator needs to find
    later ("why is last week's data back?"). The Activity page showed nothing at all.

    Append-only means append: this adds an entry describing what just finished, rather than trying to
    resurrect the pre-restore row (whose id belonged to a table that no longer exists).
    """
    try:
        with state_db.connect() as conn:
            conn.execute(
                "INSERT INTO audit_log (actor_id, actor_name, action, resource_type, resource_id, "
                "detail, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (None, (seed or {}).get("confirmed_by") or "unknown",
                 "backup.restore.finished", "backup", key,
                 json.dumps({"status": status, "key": key,
                             "database": (detail or {}).get("database"),
                             "objects": (detail or {}).get("objects"),
                             "schema": (detail or {}).get("schema")})[:2000],
                 datetime.now(timezone.utc).replace(tzinfo=None)))
    except Exception as exc:      # never fail a good restore over bookkeeping
        logger.warning("restore: could not record the audit entry: %s", exc)


def _clear_restored_running_rows() -> dict:
    """Mark every `running` backup/restore row in the just-restored database as interrupted.

    Safe to be unconditional: this runs immediately after `pg_dump`'s contents replaced the database,
    so every row present came from the snapshot, and nothing recorded in a snapshot is still running
    now. The one row that legitimately IS in flight — this restore — no longer exists here, because
    the snapshot predates it (see `_finish`).
    """
    cleared = {}
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for table in ("backup_runs", "restore_runs"):
        try:
            with state_db.connect() as conn:
                cur = conn.execute(
                    f"UPDATE {table} SET status = ?, finished_at = ?, current_step = ?, "
                    f"error_message = ? WHERE status = ?",
                    ("error", now, "Interrupted",
                     "Interrupted — this run was in progress when the restored backup was taken.",
                     "running"))
                cleared[table] = getattr(cur, "rowcount", None)
        except Exception as exc:      # never fail a good restore over bookkeeping
            logger.warning("restore: could not clear stale %s rows: %s", table, exc)
            cleared[table] = f"failed: {exc}"
    return cleared


@celery_app.task(name="geodeploy.tasks.restore.run_restore")
def run_restore(run_id: int, key: str):
    from ..services import backup as bk, restore as rs
    from ..tasks.backup import _load_cfg

    settings = get_settings()

    # Capture the row BEFORE anything is replaced. After the database step it will not exist —
    # the snapshot being restored predates this restore — and `_finish` needs these values to put
    # the record back, so the restore appears in its own history.
    seed = None
    try:
        with state_db.connect() as conn:
            conn.row_factory = state_db.dict_row
            seed = conn.execute(
                "SELECT key, confirmed_by, started_at FROM restore_runs WHERE id = ?",
                (run_id,)).fetchone()
    except Exception:
        logger.warning("restore %s: could not read its own row up front", run_id)

    cfg = _load_cfg()
    if not cfg or not cfg.backup_bucket:
        _finish(run_id, "error", seed=seed, error_message="Backups are not configured.")
        return

    detail = {}
    try:
        _step(run_id, "Reading manifest", 5)
        manifest = rs.read_manifest(cfg, key)
        parts = manifest.get("parts", {})
        detail["key_check"] = rs.check_secret_key_match(manifest)

        with tempfile.TemporaryDirectory(dir=f"{settings.data_dir}/temp") as tmp:
            # 1. OBJECTS FIRST — see the module note.
            if "objects" in parts:
                _step(run_id, "Restoring files", 15)

                def _progress(n, _b):
                    _step(run_id, f"Restoring files ({n:,} objects)", 15)

                detail["objects"] = rs.restore_objects(cfg, key, on_progress=_progress)

            if "portal_assets" in parts:
                _step(run_id, "Restoring portal assets", 60)
                path = os.path.join(tmp, "portal_assets.tar.gz")
                rs.download(cfg, key, "portal_assets.tar.gz", path)
                detail["portal_assets"] = rs.restore_portal_assets(path)
                os.unlink(path)

            # 2. DATABASE LAST. Everything after this point talks to a replaced database.
            if "postgis" in parts:
                _step(run_id, "Downloading database dump", 70)
                path = os.path.join(tmp, "postgis.dump")
                rs.download(cfg, key, "postgis.dump", path)
                _step(run_id, "Restoring database", 80)
                detail["database"] = rs.restore_database(path)
                os.unlink(path)
                # The restored history describes the SNAPSHOT's moment, not this one. A backup row is
                # created `running` before pg_dump and marked `success` after, so every snapshot
                # contains ITSELF frozen as `running` — restore it and the instance believes a backup
                # is in flight, refusing every new one with 409 forever. That is exactly what
                # happened on the first real restore.
                #
                # Cleared HERE rather than left to the 6-hour reaper because a restore KNOWS these
                # rows are stale: nothing that was running when the dump was taken is running now.
                # Schema FIRST: the restored schema may predate the running code, and the row
                # updates below (and everything the API does next) assume current columns.
                detail["schema"] = _reapply_schema_migrations()
                detail["stale_runs_cleared"] = _clear_restored_running_rows()

        _step(run_id, "Rebuilding tile configuration", 95)
        try:
            _regenerate_martin()
            detail["martin"] = "reloaded"
        except Exception as exc:
            # Not fatal: the data is back, and Settings → Infrastructure → Reload Martin fixes it.
            logger.warning("restore: Martin regeneration failed: %s", exc)
            detail["martin"] = f"failed: {exc}"

        # The audit entry written when this restore STARTED was destroyed by the restore itself.
        _reaudit_restore(key, seed, "success", detail)
        _finish(run_id, "success", seed=seed, progress=100, current_step="Done",
                detail=json.dumps(detail))
        logger.info("restore of %s complete", key)
    except Exception as exc:
        logger.exception("restore failed")
        # A restore that failed PART WAY still replaced things, so it must appear in the log too.
        _reaudit_restore(key, seed, "error", detail)
        _finish(run_id, "error", seed=seed, error_message=str(exc)[:1000], current_step="Failed",
                detail=json.dumps(detail))


def _regenerate_martin() -> None:
    """The restored catalog describes different tables than the running Martin knows about, so its
    config must be rebuilt or vector tiles 404 until the next upload."""
    import asyncio

    from ..services import martin as martin_svc

    with state_db.connect() as conn:
        conn.row_factory = state_db.dict_row
        layers = conn.execute(
            "SELECT schema_name, table_name, geometry_column, id_column, crs FROM vector_layers "
            "WHERE status = 'ready' AND storage_backend = 'postgis'").fetchall()
    asyncio.run(martin_svc.regenerate_config([dict(r) for r in layers]))
