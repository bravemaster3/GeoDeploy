from celery import Celery
from celery.signals import worker_ready
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
             "geodeploy.tasks.geolibre_publish"],
)

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
    },
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
    import sqlite3
    import logging
    try:
        from .tasks.pmtiles_tile import tile_geoparquet
        db_path = f"{settings.data_dir}/sqlite/geodeploy.db"
        with sqlite3.connect(db_path, timeout=30) as conn:
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
