"""Backup jobs (Celery).

`run_backup` does the work; `check_scheduled_backups` is a cheap tick that decides whether it is
time. The schedule lives in the DB and is READ each tick rather than compiled into celery beat's
config — changing "daily at 03:00" in Settings then takes effect immediately, with no worker
restart and no beat reconfiguration.

Like every other task module here, DB access goes through `state_db` (the worker has no async
session) — see tasks/README.md.
"""
import json
import logging
import os
import tempfile
from datetime import datetime, timedelta, timezone

from .. import state_db
from ..celery_app import celery_app
from ..config import get_settings

logger = logging.getLogger(__name__)


def _db():
    return state_db.connect()


class _Cfg:
    """The backup settings as a plain object (services/backup takes attributes, not a row)."""
    FIELDS = ("backup_enabled", "backup_endpoint", "backup_bucket", "backup_prefix",
              "backup_access_key", "backup_secret_key", "backup_region", "backup_schedule",
              "backup_hour", "backup_keep", "backup_include_postgis", "backup_include_objects",
              "backup_include_state")

    def __init__(self, row):
        for name, value in zip(self.FIELDS, row):
            setattr(self, name, value)


def _load_cfg():
    from ..crypto import decrypt_secret
    with _db() as conn:
        row = conn.execute(
            f"SELECT {', '.join(_Cfg.FIELDS)} FROM setup_config WHERE id = 1").fetchone()
    if not row:
        return None
    cfg = _Cfg(row)
    # The worker reads SQLite directly, so EncryptedText's decrypt never runs — do it here.
    cfg.backup_secret_key = decrypt_secret(cfg.backup_secret_key)
    return cfg


def _secret_fingerprint() -> str | None:
    import hashlib
    secret = get_settings().secret_key
    return hashlib.sha256(secret.encode()).hexdigest()[:16] if secret else None


def _step(run_id: int, step: str, progress: int) -> None:
    try:
        with _db() as conn:
            conn.execute("UPDATE backup_runs SET current_step = ?, progress = ? WHERE id = ?",
                         (step, progress, run_id))
    except Exception:
        pass      # progress reporting must never fail the backup


def _finish(run_id: int, status: str, **fields) -> None:
    sets = ["status = ?", "finished_at = ?"]
    vals = [status, datetime.now(timezone.utc).replace(tzinfo=None)]
    for k, v in fields.items():
        sets.append(f"{k} = ?")
        vals.append(v)
    vals.append(run_id)
    with _db() as conn:
        conn.execute(f"UPDATE backup_runs SET {', '.join(sets)} WHERE id = ?", vals)


@celery_app.task(name="geodeploy.tasks.backup.run_backup")
def run_backup(run_id: int, trigger: str = "manual"):
    from ..services import backup as bk
    settings = get_settings()
    cfg = _load_cfg()
    if not cfg or not cfg.backup_enabled or not cfg.backup_bucket:
        _finish(run_id, "error", error_message="Backups are not configured.")
        return

    with _db() as conn:
        row = conn.execute("SELECT key FROM backup_runs WHERE id = ?", (run_id,)).fetchone()
    key_prefix = row[0]

    manifest = {
        "geodeploy_backup": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "trigger": trigger,
        "source": {"bucket": settings.storage_bucket, "endpoint": settings.storage_endpoint},
        # Non-reversible marker of GEODEPLOY_SECRET_KEY. It lets a RESTORE warn that the encrypted
        # settings (SMTP/OIDC/backup credentials) will be unreadable on an install with a different
        # key — without the backup ever carrying the key itself.
        "secret_key_fingerprint": _secret_fingerprint(),
        "parts": {},
    }
    total_bytes = 0
    try:
        dest = bk.make_client(cfg.backup_endpoint, cfg.backup_access_key, cfg.backup_secret_key,
                              cfg.backup_region)
        with tempfile.TemporaryDirectory(dir=f"{settings.data_dir}/temp") as tmp:
            if cfg.backup_include_postgis:
                _step(run_id, "Dumping PostGIS", 10)
                path = os.path.join(tmp, "postgis.dump")
                info = bk.dump_postgis(path)
                bk.upload_file(dest, cfg.backup_bucket, f"{key_prefix}/postgis.dump", path)
                manifest["parts"]["postgis"] = info
                total_bytes += info["bytes"]
                os.unlink(path)      # free the disk before the next part

            if cfg.backup_include_state:
                # No separate state snapshot any more: state lives in the same PostgreSQL database,
                # so the pg_dump above already contains it. Only portal_assets remain — uploaded
                # About-page images, which exist on disk and nowhere else.
                _step(run_id, "Archiving portal assets", 40)
                path = os.path.join(tmp, "portal_assets.tar.gz")
                info = bk.archive_dir(f"{settings.data_dir}/portal_assets", path)
                bk.upload_file(dest, cfg.backup_bucket, f"{key_prefix}/portal_assets.tar.gz", path)
                manifest["parts"]["portal_assets"] = info
                total_bytes += info["bytes"]
                os.unlink(path)

            if cfg.backup_include_objects:
                _step(run_id, "Copying object storage", 55)

                def _progress(n, _bytes):
                    _step(run_id, f"Copying object storage ({n:,} objects)", 55)

                info = bk.copy_objects(dest, cfg, key_prefix, on_progress=_progress)
                manifest["parts"]["objects"] = info
                total_bytes += info["bytes"]

        _step(run_id, "Writing manifest", 92)
        manifest["total_bytes"] = total_bytes
        bk.write_manifest(dest, cfg.backup_bucket, key_prefix, manifest)

        _step(run_id, "Applying retention", 96)
        pruned = []
        try:
            pruned = bk.prune(cfg, int(cfg.backup_keep or 7))
        except Exception as exc:                      # retention must not fail a good backup
            logger.warning("backup retention failed: %s", exc)

    except Exception as exc:
        logger.exception("backup failed")
        _finish(run_id, "error", error_message=str(exc)[:1000], current_step="Failed")
        return

    # RECORDING the success sits OUTSIDE the try, deliberately. Everything above either wrote the
    # backup or raised; by here the artifacts and the manifest are in the destination and the backup
    # EXISTS. If the bookkeeping then fails, that is a database problem, not a backup problem —
    # inside the try it was caught by the handler above and the run was relabelled "error", telling
    # the operator their backup had failed when it had not.
    #
    # That is not hypothetical: `size_bytes` was int4, so a >2.1 GB instance raised "integer out of
    # range" HERE, at 100%, on a complete and restorable backup.
    try:
        _finish(run_id, "success", size_bytes=total_bytes, progress=100,
                current_step=f"Done ({len(pruned)} old backup(s) pruned)" if pruned else "Done",
                manifest=json.dumps(manifest))
    except Exception:
        logger.exception("backup %s SUCCEEDED but its run row could not be updated", key_prefix)
        # Last resort: say it worked, even without the detail. A backup that exists and is recorded
        # as failed is worse than one recorded with a missing size — the operator distrusts good data.
        try:
            _finish(run_id, "success", progress=100,
                    current_step="Done (details could not be recorded)")
        except Exception:
            logger.exception("could not record backup %s at all", key_prefix)
    logger.info("backup %s complete: %s bytes", key_prefix, total_bytes)


@celery_app.task(name="geodeploy.tasks.backup.check_scheduled_backups")
def check_scheduled_backups():
    """Beat tick: start a scheduled backup when the window has arrived and today's has not run.

    Reads the schedule from the DB every tick (so a settings change is immediate), and keys "has it
    run" off the most recent run rather than a timer — a worker restart, a missed tick or a slow
    backup can't cause a double run or a silently skipped day.
    """
    cfg = _load_cfg()
    if not cfg or not cfg.backup_enabled or (cfg.backup_schedule or "off") == "off":
        return
    now = datetime.now(timezone.utc)
    if now.hour < int(cfg.backup_hour or 3):
        return
    if cfg.backup_schedule == "weekly" and now.weekday() != 0:
        return

    window = timedelta(days=7 if cfg.backup_schedule == "weekly" else 1) - timedelta(hours=2)
    with _db() as conn:
        row = conn.execute(
            "SELECT started_at FROM backup_runs WHERE trigger = 'scheduled' "
            "ORDER BY started_at DESC LIMIT 1").fetchone()
        if row and row[0]:
            try:
                last = datetime.fromisoformat(str(row[0]))
                if now.replace(tzinfo=None) - last < window:
                    return
            except ValueError:
                pass
        from ..services.backup import run_key
        key = run_key(cfg.backup_prefix, now)
        # Postgres has no lastrowid — ask for the id back (state_db raises on lastrowid so this
        # can't be forgotten silently).
        run_id = conn.execute(
            "INSERT INTO backup_runs (key, status, trigger, started_at, progress) "
            "VALUES (?, 'running', 'scheduled', ?, 0) RETURNING id",
            (key, now.replace(tzinfo=None))).fetchone()[0]
    run_backup.delay(run_id, "scheduled")
