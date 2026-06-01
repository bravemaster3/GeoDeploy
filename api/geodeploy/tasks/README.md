# api/geodeploy/tasks/

## Purpose
Celery background workers that run the upload → ready pipelines so HTTP requests return immediately and the UI polls job status.

## Contents
- `vector_ingest.py` — `ingest_vector(job_id, layer_id, file_path, layer_name, schema_name, table_name)`:
  validate (Fiona) → reproject to EPSG:4326 (pyproj/Fiona) → load into PostGIS (psycopg2, batched inserts, `geometry(Geometry,4326)`) → GiST spatial index → save metadata (bbox, columns, geometry_type) → regenerate Martin config. Updates `upload_jobs`/`vector_layers` rows directly via **raw sqlite3** (not the async ORM — it runs in the Celery process).
- `raster_ingest.py` — `ingest_raster(job_id, layer_id, file_path, s3_key)`:
  inspect (rasterio) → COG-convert if needed (`services.cog_converter`) → upload to MinIO (boto3) → save metadata (crs, bbox, band_count, nodata). Same raw-sqlite3 status updates. Reads storage creds from the `setup_config` table first, falling back to settings.
- `__init__.py` — package marker.

## Dependencies / relationships
- Registered in `celery_app.py` (`include=[...]`), routed to the `ingest` queue.
- `vector_ingest` calls `services.martin.regenerate_config` (so a successful upload makes the layer immediately tileable).
- `raster_ingest` calls `services.cog_converter` and writes to MinIO; TiTiler reads the COG straight from MinIO at tile time (no registration step needed).
- Both write to the same SQLite DB the API reads (`{data_dir}/sqlite/geodeploy.db`) — that file is a shared bind mount across api + celery containers.
- Dispatched from `routers/data/vector.py` and `routers/data/raster.py` via `.delay(...)`.

## Current status & known issues
- **Raster pixels keep their source CRS** (e.g. UTM 31N); TiTiler reprojects at tile time via the TileMatrixSet. **The stored bbox IS now reprojected to EPSG:4326** (`services/cog_converter.py::inspect()`, 2026-06-01) so `raster_layers.bbox` is lon/lat like vector. Before this, the UTM bbox crashed portal `fitBounds` with "Invalid LngLat latitude", aborting the whole portal init script (no layer switcher, no tiles). **Raster rows uploaded before the fix still hold UTM bbox — re-upload or backfill.**
- Vector reprojection uses a per-feature Fiona transform; fine for typical files, not optimized for huge datasets.
- Status updates use raw sqlite3 with string-built `SET` clauses over a fixed set of internal keys — safe here but don't pass user input as column names.
- Errors set both job and layer `status='error'` with the message; the UI surfaces `error_message`.

## Last updated
2026-06-01
