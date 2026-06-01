# api/geodeploy/services/

## Purpose
The "hard parts" GeoDeploy hides from users: provisioning Docker containers, generating tile-server config, building tile URLs, COG conversion, and assembling published portals. Most tile-serving bugs live or die here.

## Contents
- `postgis.py` — provisions the PostGIS container (random password, named volume `geodeploy_postgres`, `postgres` network alias), waits healthy, writes the initial Martin config, and starts the Martin container. Also `create_user_schema`, `test_connection`. Exposes constants reused elsewhere: `MARTIN_NAME`, `MARTIN_IMAGE`, `NETWORK`, `_get_host_bind_path`.
- `minio.py` — provisions the MinIO container (named volume `geodeploy_minio`, `minio` alias), ensures the bucket, and **starts the TiTiler container** via `_start_titiler()`. `_start_titiler` strips the `http://` scheme from the endpoint for GDAL's VSI S3 (`AWS_S3_ENDPOINT` must be `host:port`) and always recreates the container so credential changes take effect.
- `martin.py` — `regenerate_config(layers)` rebuilds `martin-config.yaml` and reloads Martin (SIGHUP, else restart, else create the container if missing). `get_tile_url(schema, table)` → `/tiles/{schema}.{table}/{z}/{x}/{y}`. **Config notes:** (1) `listen_addresses` is top-level (Martin v1.x); the old `srv:` key is ignored. (2) `_attach_properties()` queries `information_schema.columns` and writes a `properties` map per table — **required**, because a configured Martin table source with no `properties` serves geometry only (feature popups would show no attributes).
- `titiler.py` — `get_tile_url(s3_key, colormap)` → `/raster/cog/tiles/WebMercatorQuad/{z}/{x}/{y}?url=s3://...` (the `WebMercatorQuad` TileMatrixSet segment is **required** by the current TiTiler API). `get_tilejson_url` uses `/cog/WebMercatorQuad/tilejson.json`. `COLORMAPS` list.
- `cog_converter.py` — rasterio-based: `is_cog`, `convert_to_cog` (512×512 tiles, overviews 2–64, LZW + dtype-aware predictor), `inspect` (CRS/bbox/bands/nodata).
- `portal_generator.py` — `generate_style()` returns user sources+layers (with `geodeploy:*` metadata for the layer switcher) and a merged bbox; `build_portal_bundle()` merges the template basemap + user style into a full MapLibre style and writes `data/portals/{slug}/index.html` + `style.json` by substituting `{{TITLE}}`, `{{STYLE_JSON}}`, `{{THEME_CSS}}`, `{{POPUP_CONFIG}}`, `{{ACCESS_TYPE}}`, `{{PASSWORD_SHA256}}` into the template's `layout.html`.
- `duckdb_engine.py` — Phase 2 scaffolding: in-process DuckDB with spatial+httpfs, S3 configured from settings. `query_geojson` returns placeholder geometry; not yet used by any router.

## Dependencies / relationships
- `postgis.py`/`minio.py` talk to the Docker daemon (`docker.from_env()`) and reuse each other's constants. They are called from `routers/setup.py`.
- `martin.py` is called from `routers/data/vector.py` (on upload/delete), `routers/admin.py` (manual reload), and `tasks/vector_ingest.py` (after ingest).
- `titiler.py` is called from `routers/data/raster.py` and `portal_generator.py`.
- `portal_generator.py` reads `templates/` (mounted at `/templates`) and writes `data/portals/`.
- `cog_converter.py` is called from `tasks/raster_ingest.py`.

## Current status & known issues
- **Tile URL formats are version-coupled to Martin and TiTiler `:latest` images.** The TiTiler `WebMercatorQuad` path segment and the Martin top-level `listen_addresses` were both breaking changes discovered this session. If raster/vector tiles 404 after an image bump, re-verify these paths first (see `notes_temp/notes_for_future.md`).
- GDAL needs `AWS_S3_ENDPOINT` **without** scheme; both `minio.py::_start_titiler` and `routers/setup.py` strip it. The compose file reads `${TITILER_S3_ENDPOINT}` for the same reason — keep all three in sync.
- `martin.py` and `minio.py` start containers programmatically (Docker SDK) AND those services exist in `docker-compose.yml` under profiles. Mixing the two can cause name conflicts / lost network aliases — see the "profile management" note in notes_temp.
- All tile URLs returned are **root-relative** (`/tiles/...`, `/raster/...`); callers that feed MapLibre must make them absolute (MapLibre's web worker can't resolve relative URLs). Done in `portal_generator` output consumer (`layout.html`) and `PortalEditor.vue`.

## Last updated
2026-06-01
