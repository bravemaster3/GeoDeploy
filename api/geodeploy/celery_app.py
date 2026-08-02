from celery import Celery
from celery.signals import worker_ready
from . import state_db
from .config import get_settings

settings = get_settings()

celery_app = Celery(
    "geodeploy",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["geodeploy.tasks.vector_ingest", "geodeploy.tasks.raster_ingest",
             "geodeploy.tasks.export", "geodeploy.tasks.csv_import",
             "geodeploy.tasks.geoparquet_import", "geodeploy.tasks.pmtiles_tile",
             "geodeploy.tasks.geoparquet_prep", "geodeploy.tasks.convert_upload",
             "geodeploy.tasks.geolibre_publish", "geodeploy.tasks.backup",
             "geodeploy.tasks.restore", "geodeploy.tasks.demo_reset"],
)

# Beat entries. Split out of `conf.update` so the DEMO entry can be added conditionally and so a test
# can assert the invariant that broke here: every task named in a schedule must also be in `include`,
# or beat cheerfully sends a message the worker has never imported and cannot run.
BEAT_SCHEDULE = {
    # Scheduled backups. The tick is cheap and does nothing unless a schedule is configured; the
    # SCHEDULE ITSELF lives in the DB and is read per tick, so changing it in Settings takes effect
    # immediately instead of needing beat reconfigured (see tasks/backup.check_scheduled_backups).
    "check-scheduled-backups": {
        "task": "geodeploy.tasks.backup.check_scheduled_backups",
        "schedule": 900.0,      # every 15 min
        "options": {"queue": "backup"},
    },
}

# Demo reset. Ticks every minute and no-ops unless it is the top of the hour — a fixed CLOCK, so the
# banner can promise an exact time rather than "about once an hour".
#
# REGISTERED ONLY ON A DEMO. The task guards itself anyway, so scheduling it everywhere looked
# harmless — it was not. A per-minute beat message costs a log line a minute on every install
# forever, for a task that can never do anything there. Demo mode is meant to be invisible to a
# normal install, and a schedule entry is not invisible.
DEMO_BEAT = {
    "demo-reset-tick": {
        "task": "geodeploy.tasks.demo_reset.tick",
        "schedule": 60.0,
        "options": {"queue": "backup"},
    },
}
if settings.geodeploy_demo_mode:
    BEAT_SCHEDULE.update(DEMO_BEAT)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_routes={
        "geodeploy.tasks.vector_ingest.*": {"queue": "ingest"},
        "geodeploy.tasks.raster_ingest.*": {"queue": "ingest"},
        "geodeploy.tasks.export.*": {"queue": "ingest"},
        "geodeploy.tasks.csv_import.*": {"queue": "ingest"},
        "geodeploy.tasks.geoparquet_import.*": {"queue": "ingest"},
        "geodeploy.tasks.pmtiles_tile.*": {"queue": "ingest"},
        "geodeploy.tasks.geoparquet_prep.*": {"queue": "ingest"},
        "geodeploy.tasks.convert_upload.*": {"queue": "ingest"},
        "geodeploy.tasks.geolibre_publish.*": {"queue": "ingest"},
        # Its OWN queue: a backup can run for hours (a full object copy), and on the shared
        # `ingest` queue with concurrency 2 it would occupy half the ingest capacity the whole
        # time. docker-compose runs the worker with -Q ingest,backup.
        "geodeploy.tasks.backup.*": {"queue": "backup"},
        "geodeploy.tasks.restore.*": {"queue": "backup"},
        # The demo reset IS a restore plus a sweep, so it belongs on the same queue — and it needs a
        # route of its own, not just the queue pinned in its beat entry. Without this a bare
        # `reset_now.delay()` (the natural way to test it by hand) goes to the DEFAULT queue, which
        # the worker does not consume (-Q ingest,backup), and the task waits there forever looking
        # exactly like a reset that silently did nothing.
        "geodeploy.tasks.demo_reset.*": {"queue": "backup"},
    },
    # Scheduled backups. The tick is cheap and does nothing unless a schedule is configured; the
    # SCHEDULE ITSELF lives in the DB and is read per tick, so changing it in Settings takes effect
    # immediately instead of needing beat reconfigured (see tasks/backup.check_scheduled_backups).
    beat_schedule=BEAT_SCHEDULE,
    task_track_started=True,
    worker_prefetch_multiplier=1,
)


@worker_ready.connect
def _resume_interrupted_tiling(sender=None, **kwargs):
    """Auto-resume tiling. A GeoParquet layer left in tile_status='tiling' has a DEAD task — a worker
    restart / deploy killed it mid-run, so the status is a stale promise and nothing is actually tiling.
    On worker startup, re-enqueue tile_geoparquet for every such layer so it resumes on its own instead
    of sitting stuck forever (the manual 're-tile' button did this by hand). Best-effort; a failure here
    must never block worker startup. Idempotent: the task flips tile_status to ready/error when it ends,
    so a layer that finishes/erros won't be re-picked; one still 'tiling' genuinely needs another go."""
    import logging
    try:
        from .tasks.pmtiles_tile import tile_geoparquet
        with state_db.connect() as conn:
            rows = conn.execute(
                "SELECT id, s3_key, pmtiles_key FROM vector_layers "
                "WHERE storage_backend='geoparquet' AND tile_status='tiling' AND s3_key IS NOT NULL"
            ).fetchall()
        for layer_id, s3_key, pmtiles_key in rows:
            if not pmtiles_key:
                pmtiles_key = (s3_key.rsplit(".", 1)[0] if "." in s3_key else s3_key) + ".pmtiles"
            tile_geoparquet.delay(layer_id, s3_key, pmtiles_key)
        if rows:
            logging.getLogger("geodeploy").info(
                "Auto-resumed tiling for %d interrupted GeoParquet layer(s)", len(rows))
    except Exception as exc:  # noqa: BLE001
        logging.getLogger("geodeploy").warning("Tiling auto-resume scan failed: %s", exc)
