from celery import Celery
from .config import get_settings

settings = get_settings()

celery_app = Celery(
    "geodeploy",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["geodeploy.tasks.vector_ingest", "geodeploy.tasks.raster_ingest",
             "geodeploy.tasks.export", "geodeploy.tasks.csv_import",
             "geodeploy.tasks.geoparquet_import", "geodeploy.tasks.pmtiles_tile",
             "geodeploy.tasks.geoparquet_prep", "geodeploy.tasks.convert_upload"],
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
    },
    task_track_started=True,
    worker_prefetch_multiplier=1,
)
