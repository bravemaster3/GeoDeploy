"""Hourly wipe for a demo instance.

Restores the seed snapshot on the hour, then deletes objects the snapshot does not contain. Both
steps are needed and neither is sufficient alone:

  * `pg_restore --clean` replaces the DATABASE, so visitor accounts, layers and portals disappear.
  * `restore_objects` is a restore-OVER, not a mirror — it overwrites keys present in the backup and
    deliberately leaves everything else alone, because deleting data an operator may still want is
    not its call. On a demo that is exactly backwards: every file a visitor uploads would survive
    forever, invisible to the database but still consuming disk. Hence the sweep.

The sweep is why this lives in its own task rather than reusing the restore endpoint. It deletes
data, and it must be impossible to trigger anywhere but a demo — every entry point checks the flag,
and the sweep additionally refuses to run against a bucket it did not just restore into.
"""
import logging
from datetime import datetime, timezone

from ..celery_app import celery_app
from ..config import get_settings

logger = logging.getLogger(__name__)


def _demo_guard() -> bool:
    """Nothing in this module does anything unless demo mode is on. Checked at every entry point
    rather than once at import, so flipping the flag off takes effect on the next tick."""
    if not get_settings().geodeploy_demo_mode:
        logger.debug("demo reset: not a demo instance, skipping")
        return False
    return True


@celery_app.task(name="geodeploy.tasks.demo_reset.tick")
def tick():
    """Runs every minute; acts only at the top of the hour.

    A fixed CLOCK rather than a fixed interval, so the banner can promise something exact — "resets
    at the top of the hour" — instead of "about once an hour", which a visitor cannot plan around.
    A minute tick that mostly no-ops is cheaper than the alternative of a beat schedule the operator
    has to keep in step with what the UI claims.
    """
    if not _demo_guard():
        return {"skipped": "not a demo"}
    now = datetime.now(timezone.utc)
    if now.minute != 0:
        return {"skipped": f"minute={now.minute}"}
    return reset_now()


@celery_app.task(name="geodeploy.tasks.demo_reset.reset_now")
def reset_now():
    """Restore the seed snapshot and sweep orphaned objects. Also callable by hand for a fresh start
    without waiting for the hour."""
    if not _demo_guard():
        return {"skipped": "not a demo"}

    settings = get_settings()
    key = (settings.geodeploy_demo_snapshot or "").strip()
    if not key:
        logger.warning("demo reset: GEODEPLOY_DEMO_SNAPSHOT is not set — nothing to restore")
        return {"error": "no snapshot configured"}

    from ..services import backup as bk, restore as rs
    from ..state_db import connect

    # The destination config lives in the same row the Backups page writes.
    with connect() as conn:
        row = conn.execute(
            "SELECT backup_endpoint, backup_bucket, backup_access_key, backup_secret_key, "
            "backup_region FROM setup_config LIMIT 1").fetchone()
    if not row:
        return {"error": "no backup destination configured"}

    cfg = type("Cfg", (), {
        "backup_endpoint": row["backup_endpoint"], "backup_bucket": row["backup_bucket"],
        "backup_access_key": row["backup_access_key"], "backup_secret_key": row["backup_secret_key"],
        "backup_region": row["backup_region"],
    })()

    result = {"at": datetime.now(timezone.utc).isoformat(), "snapshot": key}
    try:
        manifest = rs.read_manifest(cfg, key)
    except Exception as exc:
        logger.exception("demo reset: cannot read the snapshot manifest")
        return {"error": f"snapshot unreadable: {exc}"}

    # 1. Database — a true replace, so visitor accounts and their rows are gone.
    import os
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        dump = os.path.join(td, "db.dump")
        rs.download(cfg, f"{key}/database.dump", "database.dump", dump)
        result["database"] = rs.restore_database(dump)

    # 2. Objects — put the seed layers back.
    result["objects"] = rs.restore_objects(cfg, key)

    # 3. Sweep — remove what visitors added. THIS is the demo-only part.
    result["swept"] = _sweep_orphans(cfg, key)

    logger.info("demo reset complete: %s", result)
    return result


def _sweep_orphans(cfg, key: str) -> dict:
    """Delete live objects that the snapshot does not contain.

    Demo-only, and guarded again here rather than trusting the caller: this is the one function in
    GeoDeploy that deletes user data without anyone asking, and a stray call on a real instance would
    remove every layer uploaded since the last backup.
    """
    if not _demo_guard():
        return {"skipped": "not a demo"}

    from ..services import backup as bk
    from ..services.minio import get_s3_client

    settings = get_settings()

    # THE BACKUP MUST NOT LIVE IN THE BUCKET WE ARE ABOUT TO SWEEP. Keeping the demo's backups in the
    # same MinIO is fine and expected — it costs nothing and the data is disposable — but it has to be
    # a DIFFERENT BUCKET. Share one and this sweep deletes the snapshot itself (its keys are not under
    # the snapshot's own objects/ prefix), leaving the next reset nothing to restore from.
    #
    # Checked HERE, before any client is built: it is a configuration mistake, and refusing it should
    # not depend on being able to reach storage.
    #
    # Compares bucket NAMES only, not endpoints. That also refuses a genuinely separate S3 reusing the
    # name — a false refusal, chosen deliberately: if endpoint normalisation ("minio:9000" vs
    # "http://minio:9000") were wrong, the error would fall the other way and delete the snapshot. A
    # refusal is fixed by renaming a bucket; a deletion is not fixed at all.
    if (cfg.backup_bucket or "").strip() == (settings.storage_bucket or "").strip():
        logger.error(
            "demo reset: backup bucket %r is the SAME as the data bucket — sweeping would delete the "
            "snapshot this reset depends on. Point backups at a different bucket.", cfg.backup_bucket)
        return {"error": "backup and data share a bucket; sweep refused"}

    live = get_s3_client()
    src = bk.make_client(cfg.backup_endpoint, cfg.backup_access_key, cfg.backup_secret_key,
                         cfg.backup_region)

    prefix = f"{key}/objects/"
    keep = set()
    for page in src.get_paginator("list_objects_v2").paginate(Bucket=cfg.backup_bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            keep.add(obj["Key"][len(prefix):])

    if not keep:
        # An empty snapshot would mean "delete everything", which is never what was meant.
        logger.warning("demo reset: snapshot contains no objects — sweep skipped as a safety check")
        return {"skipped": "snapshot had no objects"}

    # THE BACKUP MUST NOT LIVE IN THE BUCKET WE ARE ABOUT TO SWEEP. Putting the demo's backups in the
    # same MinIO is fine and expected — it costs nothing and the data is disposable — but it has to be
    # a DIFFERENT BUCKET. Share one, and this sweep deletes the snapshot itself (its keys are not
    # under the snapshot's own objects/ prefix), leaving the next reset with nothing to restore from.
    # Refuse rather than warn: a reset that eats its own seed is unrecoverable without re-seeding.
    if (cfg.backup_bucket or "").strip() == (settings.storage_bucket or "").strip():
        logger.error(
            "demo reset: backup bucket %r is the SAME as the data bucket — sweeping would delete the "
            "snapshot this reset depends on. Point backups at a different bucket.", cfg.backup_bucket)
        return {"error": "backup and data share a bucket; sweep refused"}

    deleted, batch = 0, []
    for page in live.get_paginator("list_objects_v2").paginate(Bucket=settings.storage_bucket):
        for obj in page.get("Contents", []):
            if obj["Key"] not in keep:
                batch.append({"Key": obj["Key"]})
                if len(batch) == 1000:      # the S3 delete_objects limit
                    live.delete_objects(Bucket=settings.storage_bucket, Delete={"Objects": batch})
                    deleted += len(batch)
                    batch = []
    if batch:
        live.delete_objects(Bucket=settings.storage_bucket, Delete={"Objects": batch})
        deleted += len(batch)
    return {"deleted": deleted, "kept": len(keep)}
