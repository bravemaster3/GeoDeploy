"""Register a GeoParquet vector layer (Celery, background).

Unlike CSV (copied into PostGIS) or shapefile/GeoJSON (ingested into PostGIS), a GeoParquet
layer STAYS as a file on object storage and is read in place by DuckDB (analysis) and deck.gl
(display) — so this task does NOT touch PostGIS or Martin. The file is already in storage by the
time this runs (the browser PUTs it directly via a presigned URL, or it was already there for
import-existing); this task just inspects it with DuckDB to learn geometry type / bbox / columns
/ CRS and marks the layer ready.
"""
import json
from datetime import datetime, timezone

from ..celery_app import celery_app
from ..config import get_settings
from .raster_ingest import _get_storage_creds
from .vector_ingest import _update_job, _update_layer


@celery_app.task(bind=True, name="geodeploy.tasks.geoparquet_import.import_geoparquet")
def import_geoparquet(self, job_id, layer_id, s3_key):
    """Inspect a GeoParquet object already present at `s3_key` and mark the layer ready."""
    from ..services import duckdb_engine
    settings = get_settings()
    db_path = f"{settings.data_dir}/sqlite/geodeploy.db"
    # Storage creds from SQLite (NOT env) — celery's env isn't reliably populated. See §0f.
    creds = _get_storage_creds(db_path)

    def step(msg, pct):
        _update_job(db_path, job_id, status="processing", current_step=msg, progress=pct,
                    started_at=datetime.now(timezone.utc).isoformat())

    try:
        step("Inspecting GeoParquet", 40)
        info = duckdb_engine.inspect_parquet(f"s3://{creds['bucket']}/{s3_key}", creds)

        step("Saving metadata", 60)
        _update_layer(
            db_path, layer_id, status="processing",
            storage_backend="geoparquet", s3_key=s3_key,
            geometry_type=info.get("geometry_type"),
            geometry_column=info.get("geometry_column"),
            crs=info.get("crs"),
            feature_count=info.get("feature_count"),
            bbox=json.dumps(info["bbox"]) if info.get("bbox") else None,
            columns=json.dumps(info.get("columns") or []),
            tile_status="none",
            updated_at=datetime.now(timezone.utc).isoformat(),
        )

        # Spatially prepare the file in the background (Z-order sort + GeoParquet 1.1 bbox covering
        # column) — fast DuckDB analysis + the deck.gl viewport feed. The file STAYS GeoParquet
        # (no PostGIS, no PMTiles); the prep task marks the layer + job ready when it finishes.
        from .geoparquet_prep import prepare_geoparquet
        prepare_geoparquet.delay(layer_id, s3_key, job_id)
    except Exception as exc:
        _update_job(db_path, job_id, status="error", error_message=str(exc),
                    completed_at=datetime.now(timezone.utc).isoformat())
        _update_layer(db_path, layer_id, status="error", error_message=str(exc))
        raise
