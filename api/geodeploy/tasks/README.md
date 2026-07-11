# api/geodeploy/tasks/

## Purpose
Celery background workers that run the upload → ready pipelines so HTTP requests return immediately and the UI polls job status.

## Contents
- `vector_ingest.py` — `ingest_vector(job_id, layer_id, file_path, layer_name, schema_name, table_name)`:
  **COPY-based** (`_ingest_via_copy`): stream Fiona features → temp CSV (attributes + geometry as **WKB
  hex**) → `COPY` into an UNLOGGED staging `geometry` column → one `INSERT…SELECT` that **reprojects in
  PostGIS** (`ST_Transform`, using the source EPSG; client-side pyproj only as a fallback when the EPSG
  is unknown) into the final `geometry(Geometry,4326)` table → GiST index → `ST_Extent` bbox. Streams from
  disk (no in-memory feature list), bulk-loads, set-based reprojection — fast on large files. Int columns
  use `BIGINT`. Updates `upload_jobs`/`vector_layers` via **raw sqlite3** (runs in the Celery process).
  **HEAVY files skip PostGIS (2026-07-11):** a source whose uncompressed size (`_source_size`, whole
  sidecar set for shapefiles) ≥ `VECTOR_GEOPARQUET_THRESHOLD_MB` (default **200**, env-tunable on celery,
  `0` disables) goes through `_ingest_as_geoparquet` instead — `_convert_to_geoparquet` streams Fiona →
  **GeoParquet 1.1** (WKB, EPSG:4326, zstd, batched shapely conversion; `geo` footer attached at close
  via `ParquetWriter.add_key_value_metadata`, **pyarrow ≥ 18** — guarded, absent footer falls back to
  name-heuristics downstream) → upload to `vectors/{uid}/{uuid}/` → repoint the layer
  (`storage_backend='geoparquet'`) → chain `geoparquet_prep` (which marks layer + job ready). Exactly
  the pipeline a direct `.parquet` upload takes; no PostGIS table, no Martin entry.
- `raster_ingest.py` — `ingest_raster(job_id, layer_id, file_path, s3_key)`:
  inspect (rasterio) → COG-convert if needed (`services.cog_converter`) → upload to MinIO (boto3) → save metadata (crs, bbox, band_count, nodata). Same raw-sqlite3 status updates. Reads storage creds from the `setup_config` table first, falling back to settings.
- `csv_import.py` — `import_csv(job_id, layer_id, source, schema, table, x_col, y_col, srid, is_s3,
  delimiter, wkt_col)`: builds a PostGIS vector layer from a CSV. **Geometry from X/Y columns (points)
  OR — since 2026-07-11 — from a `wkt_col` WKT column (ANY geometry type**, e.g. Google Open Buildings
  polygon footprints). **COPY-based** (`_load_copy`): the CSV (a temp
  file — downloaded from S3 when `is_s3`, or the uploaded local file otherwise) is `COPY`d into an UNLOGGED
  staging table, each column's type is inferred **in SQL** (regex over the staged text → `bigint`/`double
  precision`/`date`/`text`; leading-zero ints stay text, ISO dates only, 18-digit int cap), then a single
  `INSERT…SELECT` with **guarded casts** (a bad cell → NULL, never aborts) + geometry fills the final
  table; GiST index; staging dropped; temp file removed. X/Y path: `ST_MakePoint`→`ST_Transform` 4326 +
  the ±85.0511 lat clamp (§0g). WKT path: a **`pg_temp.gd_wkt_geom` plpgsql function** parses
  `ST_GeomFromText` with an EXCEPTION handler (malformed WKT → NULL row, never aborts the INSERT),
  transforms to 4326 and **clips to the Web Mercator band** only when a row crosses it; NULL-geometry
  rows are deleted after the load; the real geometry type is sampled (`GeometryType`) and saved on the
  layer (the routers create the layer with `geometry_type=None` for WKT). Streams from disk, so no
  in-memory row cap. **All** CSV columns are kept (X/Y/WKT stay as attributes too). Dispatched from
  `routers/data/discover.py` (import existing) and `routers/data/vector.py::upload-csv` (upload). The
  `delimiter` is user-chosen (comma default; comma/semicolon/tab/pipe — auto-sniffing is unreliable),
  threaded into both the header read and the `COPY … DELIMITER`. Reuses `vector_ingest`'s sqlite helpers.
- `convert_upload.py` — `convert_to_geoparquet(job_id, layer_id, s3_key, csv_opts)` (2026-07-11):
  the **large-file** path. Files too big to POST through the API (over `MAX_FILE_SIZE`, 2 GB) upload
  DIRECT to storage via a presigned URL (`routers/data/vector.py::/large/presign` + `/large/complete`,
  like the GeoParquet flow, up to `MAX_LARGE_UPLOAD` 10 GB) and this task converts them to GeoParquet:
  downloads the object, then **CSV → `_csv_to_geoparquet`** (DuckDB `read_csv` streams the file, typed,
  out-of-core; shapely builds geometry per Arrow batch — points from X/Y or any geometry from a WKT
  column; invalid-geometry rows dropped; the WKT source column is dropped from output as redundant)
  or **shapefile-zip/GeoJSON/GPKG → `vector_ingest._convert_to_geoparquet`** (Fiona, shared). Uploads
  the `.parquet` under `vectors/{uid}/`, repoints the layer (`storage_backend='geoparquet'`), deletes
  the original upload, and chains `geoparquet_prep` (partition + covering + manifest → ready). Result
  is identical to a direct `.parquet` upload: deck.gl display + DuckDB analysis, no PostGIS/Martin.
  Converted layers aren't Martin-tiled, so **no §0g polar clamp** (deck.gl renders any latitude).
  Geometry helpers `_geom_wkb_array`/`_write_geo_footer`/`_kind_from_types`/`_bbox_struct_array` are
  shared with `vector_ingest` (extracted 2026-07-11). Creds from SQLite (§0f).
  **Prep-speed optimization (2026-07-11):** BOTH converters (`_csv_to_geoparquet` and
  `_convert_to_geoparquet`) now emit the GeoParquet 1.1 **covering `bbox` struct column** as they
  write (bounds computed in the same vectorised shapely pass that builds the WKB — `_geom_wkb_array(...,
  return_bounds=True)` → `_bbox_struct_array`), and set `covering_col` in `_write_geo_footer`. So
  `geoparquet_prep`'s `partition_with_covering` hits its **"reuse existing covering — no parse" fast
  path** and skips the whole WKB re-parse + local intermediate write (previously the geometry was
  parsed twice: once at convert, once at prep). `_geom_wkb_array` was also vectorised (dropped the
  Python per-element WKB comprehension — it dominated cost on multi-million-row files). Both
  converters log feature throughput (`… N features (X/s)`) so progress is visible in `docker logs`.
- `geoparquet_import.py` — `import_geoparquet(job_id, layer_id, s3_key)`: registers a **GeoParquet**
  vector layer. Unlike CSV/shapefile (copied/ingested into PostGIS), the file STAYS on object storage
  and is read in place by DuckDB/deck.gl — so this task touches **neither PostGIS nor Martin**. The
  object is already present (the browser PUTs it direct via a presigned URL; or it's an import-existing
  attach), so the task `duckdb_engine.inspect_parquet`s it (geometry type / bbox→4326 / columns /
  CRS / count), saves metadata with `status='processing'`, then **chains
  `geoparquet_prep.prepare_geoparquet`** (which marks the layer + job ready when the spatial prep
  finishes). Storage creds from SQLite (§0f). Sets `storage_backend='geoparquet'` + `s3_key`. No longer
  auto-tiles to PMTiles (display is moving to deck.gl over the prepared GeoParquet — see `geoparquet_prep`).
- `geoparquet_prep.py` — `prepare_geoparquet(layer_id, s3_key, job_id=None)`: rewrites the GeoParquet as a
  **spatially-partitioned dataset with a GeoParquet 1.1 `bbox` covering column**
  (`duckdb_engine.partition_with_covering` — replaced the total Z-order sort 2026-06-12; the sort hung for
  hours on the 9.5 M-polygon file). Output is a PREFIX of `__cell=N/*.parquet` hive files carrying the
  covering + a `geodeploy:partition` grid key, so `query_features_geojson` prunes both row-groups (covering)
  and whole partition files (grid → `__cell IN`). The data STAYS GeoParquet (no PostGIS, no PMTiles);
  written to a NEW `parts-<hex>/` prefix, then the task **repoints `layer.s3_key` to the prefix and deletes
  the original upload** (re-prep = read old → write new → delete old; layer delete removes the whole
  prefix). Then re-inspects (covering column dropped from catalog columns) and marks layer + job ready.
  Chained from `geoparquet_import` (auto on upload) and triggerable via `POST /data/vector/{id}/prepare`.
  After the repoint it uploads **`manifest.json`** under the prefix (`_write_manifest` →
  `duckdb_engine.build_manifest`): the partition grid + cell→file map the **browser duckdb-wasm client**
  needs (a browser can't LIST S3), served via the public `GET /data/vector/{id}/parquet/{path}` range proxy.
  Manifest failure is non-fatal (warning) — portal.js falls back to the server features.geojson endpoint.
  Requires `pyarrow`; creds from SQLite (§0f). **Tunable without an image rebuild (celery env):**
  `PREP_MEMORY_LIMIT` (default `4GB`, spills to a per-run temp dir), `PREP_BBOX_CHUNK` (shapely geometries
  per UDF slice, default `50000`), `PREP_PARTITION_GRID` (default 16 → 256 cells),
  `PREP_EXTENT_QUANTILE` (default 0.005 — percentile grid extent so outliers don't collapse the grid).
  WKB is parsed **at most once**; an existing covering column skips the parse. **Layers prepped before
  commit 2d77499 lack the grid metadata → re-prep.** (The 2026-06-13 WSL validation loop was completed
  2026-07-09 — see the §0h RESUME CHECKLIST outcome in `notes_temp/notes_for_future.md`.)
  **ATTACHED sources are protected (2026-07-11):** when `s3_key` is NOT under `vectors/` (a layer
  attached via import-existing), the prepped copy is written to a fresh `vectors/{uid}/{uuid}/parts-…`
  prefix and the original object is **never deleted** (attach ≠ copy/destroy; the delete-original step
  is gated on `s3_key.startswith("vectors/")`). The layer's original key survives in
  `vector_layers.source_s3_key` so discover/storage keeps flagging it as imported.
- `pmtiles_tile.py` — `tile_geoparquet(layer_id, s3_key, pmtiles_key)`: the GeoParquet **display** path.
  Runs **tippecanoe** (built into the image, `-l geodeploy`, **`-z12` capped max zoom**, `--simplification 10`,
  `--drop-densest-as-needed`). **Two feeds (2026-07-11):** the FAST primary converts GeoParquet → **FlatGeobuf**
  natively via `duckdb_engine.export_geoparquet_to_fgb` (baked `spatial`; no shapely funnel) and tippecanoe reads
  the binary `.fgb` file with `-P`; on any FGB error it FALLS BACK to streaming `stream_geojsonseq` (shapely →
  GeoJSONSeq → `/dev/stdin`). Both are **memory-bounded** (env `PMTILES_TILE_MEMORY_LIMIT` default 1 GB, `PMTILES_TILE_THREADS` 2)
  so a huge layer tiles in bounded RAM on a cheap VPS instead of OOM-killing. Densest strategy is env-tunable
  (`PMTILES_DENSEST=drop|coalesce`, default drop — coalesce's per-tile geometry union dominated the slow pass);
  feed mode via `PMTILES_INPUT=fgb|geojsonseq`. **All scratch (`.fgb`, DuckDB spills, tippecanoe `-t`, the output
  `.pmtiles`) lives under a per-run dir removed in `finally`** — a killed run can no longer leak GBs into `data/temp`
  (it used to leave a ~5 GB tippecanoe scratch on every OOM). Uploads the `.pmtiles` to storage, sets
  `pmtiles_key`/`tile_status`. The browser streams the tiles via range requests (no per-pan server work).
  **Tiling is OPT-IN**, triggered via `POST /data/vector/{id}/tile` (the Data Manager row's tile button, or a retry
  after a workflow improvement) — NOT auto-run on upload. DuckDB keeps reading the original `.parquet` for
  analysis/download — the `.pmtiles` is display-only.
  **Progress logging (2026-06-08):** the task captures tippecanoe's stderr progress bar (it uses `\r`, which
  never newline-flushes to `docker logs` — read byte-wise, split on `\r`/`\n`, re-logged as `tippecanoe: …`)
  and logs start / stream feature-count / total elapsed; `stream_geojsonseq` logs periodic feature throughput.
  So `docker compose logs -f celery` now shows live tiling progress (the stream's feature counter is the real
  signal — tippecanoe stays silent while blocked on stdin during the slow shapely-conversion phase).
- `export.py` — `export_bundle(bbox, items)`: clips the chosen portal layers to a bbox and writes a
  ZIP to `data/temp/exports/{task_id}.zip` (served by the API's `export-download`). Vector via
  psycopg2 (GeoJSON/CSV) + `ogr2ogr` (GeoPackage); **GeoParquet (2026-07-11) via DuckDB**
  (`_gpq_features`: the covering/partition-pruned `duckdb_engine.query_features_geojson` + an exact
  shapely intersects test for ST_Intersects parity; storage creds from SQLite §0f; same
  GeoJSON/CSV/GPKG formats, `geometry_wkt` column in CSV); raster via rasterio windowed read with an
  output cap (`MAX_PIXELS`, downsamples huge selections via overviews). Offloads the heavy clip off the
  API process. Routed to the `ingest` queue (the only one the worker consumes). **Writes to
  `{id}.zip.part` then `os.replace()`** to the final name — see known issues for why.
- `__init__.py` — package marker.

## Dependencies / relationships
- Registered in `celery_app.py` (`include=[...]`), routed to the `ingest` queue.
- `vector_ingest` calls `services.martin.regenerate_config` (so a successful upload makes the layer immediately tileable).
- `raster_ingest` calls `services.cog_converter` and writes to MinIO; TiTiler reads the COG straight from MinIO at tile time (no registration step needed).
- Both write to the same SQLite DB the API reads (`{data_dir}/sqlite/geodeploy.db`) — that file is a shared bind mount across api + celery containers.
- Dispatched from `routers/data/vector.py`, `routers/data/raster.py`, and `routers/data/discover.py` (CSV) via `.delay(...)`.

## Current status & known issues
- **Raster pixels keep their source CRS** (e.g. UTM 31N); TiTiler reprojects at tile time via the TileMatrixSet. **The stored bbox IS now reprojected to EPSG:4326** (`services/cog_converter.py::inspect()`, 2026-06-01) so `raster_layers.bbox` is lon/lat like vector. Before this, the UTM bbox crashed portal `fitBounds` with "Invalid LngLat latitude", aborting the whole portal init script (no layer switcher, no tiles). **Raster rows uploaded before the fix still hold UTM bbox — re-upload or backfill.**
- Vector reprojection is now set-based in PostGIS (`ST_Transform`) over a COPY-loaded staging table; the per-feature pyproj transform only runs as a fallback when the source CRS has no resolvable EPSG code.
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
2026-07-11 (pmtiles_tile: native FlatGeobuf primary feed + GeoJSONSeq fallback, memory-bounded (cheap-VPS-safe, scales past 20 M), per-run scratch dir removed in finally so killed runs don't leak GBs, `--drop-densest` default, env knobs)
