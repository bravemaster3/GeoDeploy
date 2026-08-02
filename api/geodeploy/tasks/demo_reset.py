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

    logger.info("demo reset: top of the hour — starting")
    result = reset_now()
    # RETURNING an error is not REPORTING one. Celery records the return value in the result backend
    # and logs the task as succeeded, so an hourly reset that failed every time looked identical to
    # one that never ran: an hour passes, nothing is wiped, and the log says nothing at all. That is
    # exactly how the config-row crash survived undetected. Anything other than a clean run is now
    # loud in the worker log, which is the only place anyone is watching.
    if isinstance(result, dict) and result.get("error"):
        logger.error("DEMO RESET FAILED: %s", result["error"])
    elif isinstance(result, dict) and result.get("skipped"):
        logger.warning("demo reset skipped: %s", result["skipped"])
    else:
        logger.info("demo reset finished: %s", result)
    return result


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
    # The runbook shows `GEODEPLOY_DEMO_SNAPSHOT=<the backup name from step 3>` and that literal
    # placeholder gets pasted into .env verbatim. Without this it fails several steps later as
    # "snapshot unreadable: NoSuchKey", which reads like a storage problem rather than a typo.
    if key.startswith("<") or key.endswith(">"):
        logger.error("demo reset: GEODEPLOY_DEMO_SNAPSHOT is still the placeholder %r", key)
        return {"error": f"GEODEPLOY_DEMO_SNAPSHOT is still the placeholder ({key}) — set it to a "
                         "real backup name from Settings → Backups → Manage backups."}

    from ..services import backup as bk, restore as rs
    from . import restore as rt
    from .backup import _load_cfg

    # Reuse the BACKUP task's loader rather than reading the row here. The hand-rolled version had
    # two bugs that made this task fail every single time it ran:
    #
    #   1. it subscripted the row BY NAME (`row["backup_endpoint"]`) — state_db returns tuples unless
    #      `row_factory = dict_row` is set, so it raised TypeError before doing anything, and
    #   2. it never decrypted `backup_secret_key`, which is stored encrypted at rest, so even with
    #      the rows read correctly the S3 client would have signed with ciphertext.
    #
    # One loader, one place to get this right. The reset is the rarest-run code in the product, so it
    # must not have its own copy of anything.
    cfg = _load_cfg()
    if not cfg or not cfg.backup_bucket:
        return {"error": "no backup destination configured"}

    result = {"at": datetime.now(timezone.utc).isoformat(), "snapshot": key}
    try:
        manifest = rs.read_manifest(cfg, key)
    except Exception as exc:
        logger.exception("demo reset: cannot read the snapshot manifest")
        return {"error": f"snapshot unreadable: {exc}"}

    # ONE implementation of "put this snapshot back", shared with the restore task. Keeping a second
    # copy here is what produced every demo-reset bug so far: it fetched `database.dump` when the
    # file is `postgis.dump`, passed the composed path as the `key` argument (asking for
    # `<key>/database.dump/database.dump`), skipped portal assets entirely, and restored the database
    # BEFORE the objects — the ordering restore.py's header explains is wrong. The result was a 404
    # at the top of every hour, on a task nobody was watching.
    import os
    import tempfile
    with tempfile.TemporaryDirectory(dir=f"{get_settings().data_dir}/temp") as tmp:
        result.update(rt.restore_snapshot(cfg, key, manifest, tmp))

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
