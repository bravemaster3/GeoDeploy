# api/geodeploy/routers/

## Purpose
All HTTP endpoints. Every router is registered in `main.py` under the `/api` prefix.

## Contents
- `setup.py` — first-run wizard: `/setup/status`, `/setup/configure-db`, `/setup/configure-storage`, `/setup/create-admin`. Provisions PostGIS/MinIO (via `services.postgis`/`services.minio`), then `_write_env()` persists creds to `.env` and `_apply_to_process()` pushes them into `os.environ`, clears the settings cache, and restarts the celery container. **`_write_env` also writes `TITILER_S3_ENDPOINT`** (the storage endpoint with the `http://` scheme stripped) for the TiTiler container.
- `auth.py` — `/auth/login` (OAuth2 password form → JWT, 7-day expiry) and `/auth/me`. Bcrypt via passlib.
- `portals.py` — portal CRUD + `/portals/{id}/publish` and `/unpublish`. Publish loads ready layers, calls `services.portal_generator.generate_style` + `build_portal_bundle` to write the static site. Slugs are auto-deduped. Passwords stored as both bcrypt (future server-side) and SHA-256 (embedded in the published HTML gate).
- `templates.py` — `/templates` lists template folders from `/templates` that have `template.json` + `style.json` (layout.html is optional — shared skeleton fallback).
- `portals.py::export_bundle` — **public** `POST /portals/{slug}/export-bundle` (body: `{bbox, items:[{layer_id, layer_type, format}]}`) returns a single **ZIP** of the selected layers clipped to the bbox. Vector formats: `geojson`/`gpkg`(ogr2ogr)/`csv`; raster: `tif` (rasterio windowed read from S3). Only portal-owned layers; 50k-feature cap per vector. Used by the portal's "select area & download" tool (per-layer format dropdown + one Download button). Helpers `_vec_geojson`/`_vec_csv`/`_gj_to_gpkg`/`_clip_raster` are reusable.
- `admin.py` — `/admin/health` (HTTP-pings Martin/TiTiler + reports container status for postgres/minio/redis/martin/titiler/nginx/celery/ui/api, each flagged `controllable`), `/admin/services/{name}/{action}` (Coolify-style start/stop/restart via the Docker socket; `api` is non-controllable since it serves the request; resolves both fixed `container_name`s and Compose auto-names), `/admin/reload-martin` (regenerates Martin config from all ready PostGIS layers — the manual recovery hook), `/admin/storage-stats`.
- `data/vector.py` — vector layer list/upload/job-status/default-style/delete. Upload streams to `data/temp`, creates the `VectorLayer` + `UploadJob` rows, dispatches `tasks.vector_ingest`. Delete drops the PostGIS table and regenerates Martin config.
- `data/raster.py` — raster equivalent; list endpoint attaches a computed `tile_url` for ready layers; `/colormaps` lists TiTiler colormaps; `/{id}/stats` proxies TiTiler `/cog/statistics` and returns a suggested `rescale` ("min,max", 2–98th percentile) for auto-stretch. Dispatches `tasks.raster_ingest`.
- `data/sources.py` — stub for future external WMS/WFS/PostGIS sources (returns `[]`).
- `data/__init__.py`, `__init__.py` — package markers.

## Dependencies / relationships
- Depends on `..services` (provisioning, tile URLs, portal generation), `..tasks` (Celery dispatch), `..models`, `..schemas`, `..deps` (auth), `..database`.
- All vector tile URLs handed to the frontend are built by `services.martin.get_tile_url`; raster by `services.titiler.get_tile_url`. If a tile path format changes, change it there, not here.

## Current status & known issues
- `reload-martin` exists because Martin can silently end up with an empty/stale config; the Settings page now has a button that calls it.
- Vector ingest reprojects to EPSG:4326; raster ingest currently does **not** reproject (COG keeps source CRS, e.g. UTM) — TiTiler reprojects on the fly via the TileMatrixSet, but the stored bbox is in source CRS and must be handled carefully by callers computing map bounds. See `tasks/README.md` and notes.
- No rate limiting beyond nginx; no pagination on list endpoints (fine at current scale).

## Last updated
2026-06-01
