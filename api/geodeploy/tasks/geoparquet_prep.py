"""Spatially prepare a GeoParquet layer (Celery, background).

Rewrites the upload into a spatially-partitioned GeoParquet dataset with a GeoParquet 1.1 `bbox`
covering column (see `services/duckdb_engine.partition_with_covering`) so DuckDB prunes row-groups
on a spatial filter — fast analysis AND the deck.gl viewport feed (`query_features_geojson`). The
data STAYS GeoParquet on object storage: no PostGIS, no PMTiles. The output is a PREFIX of
`__cell=N/*.parquet` files (not a single file); the layer's `s3_key` is repointed to that prefix and
the original upload is deleted. A coarse-grid PARTITION scatter (one pass) replaces the old
total-order Z-order sort, which hung for hours on millions of large polygons.
"""
import json
import logging
import os
import uuid
from datetime import datetime, timezone

from ..celery_app import celery_app
from ..config import get_settings
from .raster_ingest import _get_storage_creds
from .vector_ingest import _get_layer_user, _update_job, _update_layer

logger = logging.getLogger(__name__)


def _s3_client(creds: dict):
    import boto3
    from botocore.client import Config
    return boto3.client(
        "s3", endpoint_url=creds["endpoint"],
        aws_access_key_id=creds["access_key"], aws_secret_access_key=creds["secret_key"],
        region_name=creds["region"], config=Config(signature_version="s3v4"))


def _write_manifest(creds: dict, prefix: str) -> None:
    """Build + upload `manifest.json` under the partitioned prefix — the partition/cell map the
    portal.js duckdb-wasm client needs (a browser cannot LIST S3; it can only fetch keys it can
    name through the public `/parquet/{path}` range proxy)."""
    from ..services import duckdb_engine
    manifest = duckdb_engine.build_manifest(prefix, creds)
    _s3_client(creds).put_object(
        Bucket=creds["bucket"], Key=prefix.rstrip("/") + "/manifest.json",
        Body=json.dumps(manifest).encode(), ContentType="application/json")


def _delete_s3_location(creds: dict, key: str) -> None:
    """Delete a single `.parquet` object, or every object under a prefix (partitioned dataset)."""
    s3 = _s3_client(creds)
    bkt = creds["bucket"]
    if key.rstrip("/").endswith(".parquet"):
        s3.delete_object(Bucket=bkt, Key=key)
        return
    prefix = key.rstrip("/") + "/"
    paginator = s3.get_paginator("list_objects_v2")
    batch = []
    for page in paginator.paginate(Bucket=bkt, Prefix=prefix):
        for obj in page.get("Contents", []):
            batch.append({"Key": obj["Key"]})
            if len(batch) >= 1000:
                s3.delete_objects(Bucket=bkt, Delete={"Objects": batch})
                batch = []
    if batch:
        s3.delete_objects(Bucket=bkt, Delete={"Objects": batch})


@celery_app.task(bind=True, name="geodeploy.tasks.geoparquet_prep.prepare_geoparquet")
def prepare_geoparquet(self, layer_id, s3_key, job_id=None):
    """Partition `s3_key` into a spatial grid + add a bbox covering column, repoint the layer at the
    new partitioned prefix, refresh metadata, and delete the original upload."""
    from ..services import duckdb_engine
    settings = get_settings()
    db_path = f"{settings.data_dir}/sqlite/geodeploy.db"
    # Storage creds from SQLite (NOT env) — celery's env isn't reliably populated. See §0f.
    creds = _get_storage_creds(db_path)
    try:
        logger.info("prepare_geoparquet: layer %s — partitioning %s + bbox covering", layer_id, s3_key)
        # Env knobs (retunable on celery without an image rebuild): PREP_MEMORY_LIMIT caps DuckDB
        # RAM, PREP_BBOX_CHUNK caps shapely geometries per UDF slice, PREP_MAX_TEMP_DIR bounds the
        # spill, PREP_PARTITION_GRID sets the grid resolution (gridxgrid cells; more = tighter
        # pruning but more files).
        # An ATTACHED source (import-existing → key outside GeoDeploy's own vectors/ area) must
        # never be overwritten or deleted: write the prepped copy under vectors/ instead (the
        # everything-GeoDeploy-creates-lives-in-vectors/ invariant, which is also what the
        # delete-detach heuristic relies on).
        attached = not s3_key.startswith("vectors/")
        out_prefix = None
        if attached:
            uid = _get_layer_user(db_path, layer_id) or 0
            out_prefix = f"vectors/{uid}/{uuid.uuid4().hex}/parts-{uuid.uuid4().hex[:8]}"
        result = duckdb_engine.partition_with_covering(
            s3_key, creds, out_prefix=out_prefix,
            memory_limit=os.getenv("PREP_MEMORY_LIMIT", "4GB"),
            bbox_chunk=int(os.getenv("PREP_BBOX_CHUNK", "50000")),
            max_temp_dir_size=os.getenv("PREP_MAX_TEMP_DIR", "100GiB"),
            partition_grid=int(os.getenv("PREP_PARTITION_GRID", "16")),
            extent_quantile=float(os.getenv("PREP_EXTENT_QUANTILE", "0.005")),
            # Grid ceiling is PREP_PARTITION_GRID; the actual grid adapts to the dataset size
            # (~this many rows per occupied cell) so light layers aren't shredded into
            # hundreds of near-empty partition files.
            rows_per_cell=int(os.getenv("PREP_ROWS_PER_CELL", "100000")))
        new_key = result["out_key"]

        # Re-inspect the partitioned dataset (covering column now present; inspect drops it from the
        # catalog columns). inspect_parquet handles the prefix via _parquet_paths.
        info = duckdb_engine.inspect_parquet(f"s3://{creds['bucket']}/{new_key}", creds)
        _update_layer(
            db_path, layer_id, status="ready",
            s3_key=new_key,  # repoint the catalog at the partitioned prefix
            geometry_type=info.get("geometry_type"),
            geometry_column=info.get("geometry_column"),
            crs=info.get("crs"),
            feature_count=info.get("feature_count"),
            bbox=json.dumps(info["bbox"]) if info.get("bbox") else None,
            columns=json.dumps(info.get("columns") or []),
            error_message=None,
            updated_at=datetime.now(timezone.utc).isoformat(),
        )
        # Manifest for the browser-side duckdb-wasm reader. Non-fatal: without it, portal.js falls
        # back to the server features.geojson endpoint for this layer.
        try:
            _write_manifest(creds, new_key)
        except Exception:
            logger.warning("prepare_geoparquet: layer %s — manifest write failed "
                           "(portal.js will use the server fallback)", layer_id, exc_info=True)

        # Delete the original now that the layer points at the new partitioned prefix — but ONLY
        # if it was GeoDeploy's own upload (vectors/). An attached import-existing source belongs
        # to the user's bucket area and stays untouched.
        if new_key != s3_key and s3_key.startswith("vectors/"):
            try:
                _delete_s3_location(creds, s3_key)
            except Exception:
                logger.warning("prepare_geoparquet: layer %s — could not delete old source %s",
                               layer_id, s3_key, exc_info=True)
        if job_id:
            _update_job(db_path, job_id, status="ready", progress=100,
                        completed_at=datetime.now(timezone.utc).isoformat())
        logger.info("prepare_geoparquet: layer %s — READY (%s features) → %s",
                    layer_id, info.get("feature_count"), new_key)
    except Exception as exc:
        _update_layer(db_path, layer_id, status="error", error_message=str(exc))
        if job_id:
            _update_job(db_path, job_id, status="error", error_message=str(exc),
                        completed_at=datetime.now(timezone.utc).isoformat())
        raise
