# api/geodeploy/tasks/

## Purpose
Celery background workers that run the upload → ready pipelines so HTTP requests return immediately and the UI polls job status.

## Contents
- `vector_ingest.py` — `ingest_vector(job_id, layer_id, file_path, layer_name, schema_name, table_name)`:
  validate (Fiona) → reproject to EPSG:4326 (pyproj/Fiona) → load into PostGIS (psycopg2, batched inserts, `geometry(Geometry,4326)`) → GiST spatial index → save metadata (bbox, columns, geometry_type) → regenerate Martin config. Updates `upload_jobs`/`vector_layers` rows directly via **raw sqlite3** (not the async ORM — it runs in the Celery process).
- `raster_ingest.py` — `ingest_raster(job_id, layer_id, file_path, s3_key)`:
  inspect (rasterio) → COG-convert if needed (`services.cog_converter`) → upload to MinIO (boto3) → save metadata (crs, bbox, band_count, nodata). Same raw-sqlite3 status updates. Reads storage creds from the `setup_config` table first, falling back to settings.
- `csv_import.py` — `import_csv(job_id, layer_id, key, schema, table, x_col, y_col, srid)`: reads a CSV
  from object storage → infers each column's type (`bigint`/`double precision`/`date`/`text`; leading-zero
  ints like zip codes stay text, ISO dates only) → loads points into PostGIS (`ST_MakePoint`→`ST_Transform`
  4326, GiST index) → saves metadata → regenerates Martin. Dispatched from `routers/data/discover.py`
  (the "Import existing → CSV" flow). Caps: 1M rows, 200 MB read (in-memory `executemany`; COPY would lift
  these). Reuses `vector_ingest`'s sqlite status helpers.
- `export.py` — `export_bundle(bbox, items)`: clips the chosen portal layers to a bbox and writes a
  ZIP to `data/temp/exports/{task_id}.zip` (served by the API's `export-download`). Vector via
  psycopg2 (GeoJSON/CSV) + `ogr2ogr` (GeoPackage); raster via rasterio windowed read with an output
  cap (`MAX_PIXELS`, downsamples huge selections via overviews). Offloads the heavy clip off the API
  process. Routed to the `ingest` queue (the only one the worker consumes). **Writes to `{id}.zip.part`
  then `os.replace()`** to the final name — see known issues for why.
- `__init__.py` — package marker.

## Dependencies / relationships
- Registered in `celery_app.py` (`include=[...]`), routed to the `ingest` queue.
- `vector_ingest` calls `services.martin.regenerate_config` (so a successful upload makes the layer immediately tileable).
- `raster_ingest` calls `services.cog_converter` and writes to MinIO; TiTiler reads the COG straight from MinIO at tile time (no registration step needed).
- Both write to the same SQLite DB the API reads (`{data_dir}/sqlite/geodeploy.db`) — that file is a shared bind mount across api + celery containers.
- Dispatched from `routers/data/vector.py`, `routers/data/raster.py`, and `routers/data/discover.py` (CSV) via `.delay(...)`.

## Current status & known issues
- **Raster pixels keep their source CRS** (e.g. UTM 31N); TiTiler reprojects at tile time via the TileMatrixSet. **The stored bbox IS now reprojected to EPSG:4326** (`services/cog_converter.py::inspect()`, 2026-06-01) so `raster_layers.bbox` is lon/lat like vector. Before this, the UTM bbox crashed portal `fitBounds` with "Invalid LngLat latitude", aborting the whole portal init script (no layer switcher, no tiles). **Raster rows uploaded before the fix still hold UTM bbox — re-upload or backfill.**
- Vector reprojection uses a per-feature Fiona transform; fine for typical files, not optimized for huge datasets.
- Status updates use raw sqlite3 with string-built `SET` clauses over a fixed set of internal keys — safe here but don't pass user input as column names.
- Errors set both job and layer `status='error'` with the message; the UI surfaces `error_message`.
- **`export.py` traps (all hit + fixed 2026-06-03; full detail in `notes_temp/notes_for_future.md`):**
  (1) the status endpoint calls the job ready when `{id}.zip` exists, but `zipfile.ZipFile(path,'w')`
  creates it empty immediately → poller downloaded an empty zip → **build to `.part` then `os.replace`**.
  (2) **GeoTIFF to a `BytesIO` is silently truncated** (GTiff needs a seekable dataset) → use
  `rasterio.io.MemoryFile`; also strip `tiled/blockxsize/blockysize/...` from the source profile.
  (3) **rasterio ≥1.4 forbids AWS creds in `rasterio.Env`** → pass them via
  `rasterio.session.AWSSession(endpoint_url=...)`, keep only `AWS_S3_ENDPOINT`/`AWS_HTTPS`/etc as Env kwargs.
- **Deploy:** the worker shares the api image — any task change needs
  `docker compose build geodeploy-api && up -d --force-recreate geodeploy-api celery` (recreating only
  api leaves celery running stale code → tasks fail as "unregistered" or run the old logic).

## Last updated
2026-06-04
