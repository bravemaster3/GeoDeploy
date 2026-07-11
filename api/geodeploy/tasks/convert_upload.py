"""Convert a LARGE vector upload to GeoParquet in the background (Celery).

Files above the API-passthrough limit (default 2 GB) can't be POSTed through the API, so the
browser uploads them DIRECT to object storage via a presigned URL (like the GeoParquet upload) and
this task converts them to GeoParquet in place:
  - CSV                        → `_csv_to_geoparquet` (DuckDB streams the CSV, shapely builds the
                                 geometry from X/Y or a WKT column) — points, lines OR polygons.
  - shapefile.zip / GeoJSON / GeoPackage → `_convert_to_geoparquet` (Fiona, shared with the
                                 heavy-upload path in vector_ingest).
The result is registered as a `storage_backend='geoparquet'` layer and chained into the normal
spatial prep (partition + covering + manifest), so a big upload ends up exactly like a direct
`.parquet` upload: deck.gl display + DuckDB analysis, no PostGIS table. The layer is marked ready
by the prep task. NOTE: converted GeoParquet layers are NOT served through Martin (Web Mercator
tiles), so the §0g polar-latitude clamp is unnecessary here — deck.gl renders any latitude.
"""
import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone

from ..celery_app import celery_app
from ..config import get_settings
from .raster_ingest import _get_storage_creds
from .vector_ingest import (
    _bbox_struct_array, _convert_to_geoparquet, _geom_wkb_array, _get_layer_user,
    _kind_from_types, _resolve_source, _update_job, _update_layer, _write_geo_footer,
)

logger = logging.getLogger(__name__)

_DELIM_CHAR = {"comma": ",", "semicolon": ";", "tab": "\t", "pipe": "|", "space": " "}


def _s3(creds: dict):
    import boto3
    from botocore.client import Config
    return boto3.client(
        "s3", endpoint_url=creds["endpoint"],
        aws_access_key_id=creds["access_key"], aws_secret_access_key=creds["secret_key"],
        region_name=creds["region"], config=Config(signature_version="s3v4"))


def _csv_to_geoparquet(csv_path: str, out_path: str, x_col, y_col, wkt_col, srid,
                       delimiter: str = "comma", batch_size: int = 50_000) -> dict:
    """Stream a CSV → GeoParquet 1.1 (WKB, EPSG:4326, zstd). DuckDB reads the CSV (fast, typed,
    out-of-core); shapely builds the geometry per Arrow batch (points from X/Y, or any geometry
    from a WKT column). Rows whose geometry is missing/unparseable are dropped. The WKT source
    column is dropped from the output (redundant with `geometry`, and often huge); X/Y are kept."""
    import duckdb
    import numpy as np
    import pyarrow as pa
    import pyarrow.parquet as pq
    import shapely

    delim = _DELIM_CHAR.get(delimiter, ",")
    srid = int(srid) or 4326
    reproject = None
    if srid != 4326:
        from pyproj import Transformer
        _tr = Transformer.from_crs(f"EPSG:{srid}", "EPSG:4326", always_xy=True)

        def _tr_coords(coords):
            x, y = _tr.transform(coords[:, 0], coords[:, 1])
            return np.column_stack([x, y])
        reproject = lambda geoms: shapely.transform(geoms, _tr_coords)  # noqa: E731

    # DuckDB reads a LOCAL file here (no httpfs / no spatial): header + chosen delimiter, generous
    # sample for stable type inference, bad rows ignored rather than aborting the whole file.
    esc = csv_path.replace("'", "''")
    src = (f"read_csv('{esc}', delim='{delim}', header=true, "
           f"sample_size=200000, ignore_errors=true)")
    con = duckdb.connect()
    try:
        all_cols = [r[0] for r in con.execute(f"DESCRIBE SELECT * FROM {src}").fetchall()]
        if wkt_col and wkt_col not in all_cols:
            raise ValueError("Selected WKT geometry column is not in the CSV header.")
        if not wkt_col and (x_col not in all_cols or y_col not in all_cols):
            raise ValueError("Selected X/Y columns are not in the CSV header.")
        # Keep every column as an attribute EXCEPT the WKT geometry column (redundant + large).
        attr_cols = [c for c in all_cols if c != wkt_col]
        # Emit a GeoParquet 1.1 covering bbox column so the downstream spatial prep skips its own
        # WKB parse pass (it reuses an existing covering). Pick a name that won't clash with a
        # real attribute column.
        cov_name = "bbox" if "bbox" not in attr_cols else "gd_bbox_cov"
        bbox_field = pa.field(cov_name, pa.struct([(f, pa.float64()) for f in
                                                   ("xmin", "ymin", "xmax", "ymax")]))

        reader = con.execute(f"SELECT * FROM {src}").fetch_record_batch(batch_size)
        writer = None
        schema = None
        count = 0
        bbox = [float("inf"), float("inf"), float("-inf"), float("-inf")]
        geom_types: set[str] = set()
        t0 = time.monotonic()
        next_log = 500_000
        logger.info("_csv_to_geoparquet: started (%s geometry, srid=%s)",
                    "WKT" if wkt_col else "X/Y", srid)

        for batch in reader:
            if wkt_col:
                geoms = shapely.from_wkt(batch.column(wkt_col).to_numpy(zero_copy_only=False),
                                         on_invalid="ignore")
            else:
                xa = np.asarray(batch.column(x_col).to_numpy(zero_copy_only=False), dtype="float64")
                ya = np.asarray(batch.column(y_col).to_numpy(zero_copy_only=False), dtype="float64")
                geoms = shapely.points(xa, ya)
                geoms[np.isnan(xa) | np.isnan(ya)] = None
            mask = np.array([g is not None for g in geoms])
            if not mask.any():
                continue

            pa_mask = pa.array(mask)
            wkb_arr, per = _geom_wkb_array(geoms[mask], reproject, bbox, geom_types, return_bounds=True)
            attr_arrays = [batch.column(c).filter(pa_mask) for c in attr_cols]
            if writer is None:
                schema = pa.schema([pa.field(c, batch.schema.field(c).type) for c in attr_cols]
                                   + [pa.field("geometry", pa.binary()), bbox_field])
                writer = pq.ParquetWriter(out_path, schema, compression="zstd")
            writer.write_table(pa.Table.from_arrays(
                attr_arrays + [wkb_arr, _bbox_struct_array(per)], schema=schema))
            count += int(mask.sum())
            if count >= next_log:
                logger.info("_csv_to_geoparquet: %d features (%.0f/s)",
                            count, count / max(time.monotonic() - t0, 0.001))
                next_log += 500_000

        if writer is None or count == 0:
            if writer is not None:
                writer.close()
            raise ValueError("No rows had valid geometry.")
        _write_geo_footer(writer, geom_types, bbox, covering_col=cov_name)
        writer.close()
        logger.info("_csv_to_geoparquet: DONE %d features in %.0fs", count, time.monotonic() - t0)
        return {"count": count, "bbox": bbox if bbox[0] != float("inf") else None,
                "geom_type": _kind_from_types(geom_types),
                "columns": [{"name": c, "type": str(schema.field(c).type)} for c in attr_cols]}
    finally:
        con.close()


@celery_app.task(bind=True, name="geodeploy.tasks.convert_upload.convert_to_geoparquet")
def convert_to_geoparquet(self, job_id, layer_id, s3_key, csv_opts=None):
    """Download a large uploaded vector file from `s3_key`, convert it to GeoParquet, repoint the
    layer at the converted object, and chain the spatial prep (which marks layer + job ready)."""
    settings = get_settings()
    db_path = f"{settings.data_dir}/sqlite/geodeploy.db"
    creds = _get_storage_creds(db_path)
    ext = os.path.splitext(s3_key)[1].lower()
    tmpdir = f"{settings.data_dir}/temp"
    os.makedirs(tmpdir, exist_ok=True)
    local_in = os.path.join(tmpdir, f"{uuid.uuid4().hex}{ext}")
    out_path = os.path.join(tmpdir, f"{uuid.uuid4().hex}.parquet")

    def step(msg, pct):
        _update_job(db_path, job_id, status="processing", current_step=msg, progress=pct,
                    started_at=datetime.now(timezone.utc).isoformat())

    try:
        step("Fetching uploaded file", 15)
        _s3(creds).download_file(creds["bucket"], s3_key, local_in)

        step("Converting to GeoParquet", 40)
        if ext == ".csv":
            opts = csv_opts or {}
            res = _csv_to_geoparquet(
                local_in, out_path, opts.get("x_column"), opts.get("y_column"),
                opts.get("wkt_column"), opts.get("srid", 4326), opts.get("delimiter", "comma"))
        else:
            res = _convert_to_geoparquet(_resolve_source(local_in), out_path)

        step("Uploading GeoParquet", 65)
        uid = _get_layer_user(db_path, layer_id) or 0
        new_key = f"vectors/{uid}/{uuid.uuid4().hex}/converted.parquet"
        _s3(creds).upload_file(out_path, creds["bucket"], new_key)

        step("Queueing spatial prep", 80)
        _update_layer(
            db_path, layer_id, status="processing",
            storage_backend="geoparquet", s3_key=new_key,
            geometry_type=res["geom_type"], geometry_column="geometry", crs="EPSG:4326",
            feature_count=res["count"],
            bbox=json.dumps(res["bbox"]) if res["bbox"] else None,
            columns=json.dumps(res["columns"]), tile_status="none",
            updated_at=datetime.now(timezone.utc).isoformat())

        # Delete the original upload now that the layer points at the converted parquet (the prep
        # will further repoint to the partitioned prefix and delete this converted.parquet).
        try:
            _s3(creds).delete_object(Bucket=creds["bucket"], Key=s3_key)
        except Exception:  # noqa: BLE001 — non-fatal; a stray original just wastes space
            pass

        from .geoparquet_prep import prepare_geoparquet
        prepare_geoparquet.delay(layer_id, new_key, job_id)
    except Exception as exc:
        _update_job(db_path, job_id, status="error", error_message=str(exc),
                    completed_at=datetime.now(timezone.utc).isoformat())
        _update_layer(db_path, layer_id, status="error", error_message=str(exc))
        raise
    finally:
        for p in (local_in, out_path):
            if os.path.exists(p):
                try:
                    os.unlink(p)
                except OSError:
                    pass
        # A shapefile zip extracts to `<path>_extracted/` — clean that too.
        import shutil
        exdir = local_in + "_extracted"
        if os.path.isdir(exdir):
            shutil.rmtree(exdir, ignore_errors=True)
