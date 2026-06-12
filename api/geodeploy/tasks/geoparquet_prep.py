"""Spatially prepare a GeoParquet layer (Celery, background).

Rewrites the file Z-order-sorted with a GeoParquet 1.1 `bbox` covering column (see
`services/duckdb_engine.sort_with_covering`) so DuckDB prunes row-groups on a spatial filter —
fast analysis AND the deck.gl viewport feed (`query_features_geojson`). The file STAYS
GeoParquet on object storage: no PostGIS, no PMTiles. Overwrites the object **in place**
(`out_key == s3_key`) so the catalog `s3_key` stays stable and re-running is idempotent.
"""
import json
import logging
import os
from datetime import datetime, timezone

from ..celery_app import celery_app
from ..config import get_settings
from .raster_ingest import _get_storage_creds
from .vector_ingest import _update_job, _update_layer

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, name="geodeploy.tasks.geoparquet_prep.prepare_geoparquet")
def prepare_geoparquet(self, layer_id, s3_key, job_id=None):
    """Sort `s3_key` + add a bbox covering column, then refresh the layer's metadata."""
    from ..services import duckdb_engine
    settings = get_settings()
    db_path = f"{settings.data_dir}/sqlite/geodeploy.db"
    # Storage creds from SQLite (NOT env) — celery's env isn't reliably populated. See §0f.
    creds = _get_storage_creds(db_path)
    try:
        logger.info("prepare_geoparquet: layer %s — sorting %s + bbox covering", layer_id, s3_key)
        # PREP_MEMORY_LIMIT lets a small VPS cap DuckDB's RAM without an image rebuild (the sort
        # spills to data/temp); PREP_BBOX_CHUNK caps shapely geometries parsed per UDF slice.
        duckdb_engine.sort_with_covering(
            s3_key, creds, out_key=s3_key,
            memory_limit=os.getenv("PREP_MEMORY_LIMIT", "4GB"),
            bbox_chunk=int(os.getenv("PREP_BBOX_CHUNK", "50000")),
            max_temp_dir_size=os.getenv("PREP_MAX_TEMP_DIR", "100GiB"))

        # Re-inspect the rewritten file: the covering column is now present, and inspect drops it
        # from the catalog columns. bbox/feature_count are unchanged but cheap to refresh.
        info = duckdb_engine.inspect_parquet(f"s3://{creds['bucket']}/{s3_key}", creds)
        _update_layer(
            db_path, layer_id, status="ready",
            geometry_type=info.get("geometry_type"),
            geometry_column=info.get("geometry_column"),
            crs=info.get("crs"),
            feature_count=info.get("feature_count"),
            bbox=json.dumps(info["bbox"]) if info.get("bbox") else None,
            columns=json.dumps(info.get("columns") or []),
            updated_at=datetime.now(timezone.utc).isoformat(),
        )
        if job_id:
            _update_job(db_path, job_id, status="ready", progress=100,
                        completed_at=datetime.now(timezone.utc).isoformat())
        logger.info("prepare_geoparquet: layer %s — READY (%s features)",
                    layer_id, info.get("feature_count"))
    except Exception as exc:
        _update_layer(db_path, layer_id, status="error", error_message=str(exc))
        if job_id:
            _update_job(db_path, job_id, status="error", error_message=str(exc),
                        completed_at=datetime.now(timezone.utc).isoformat())
        raise
