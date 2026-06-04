# api/

## Purpose
FastAPI backend — GeoDeploy's control plane: auth, setup wizard, data ingestion orchestration, portal CRUD/publish, and lifecycle management of the Docker-based tile/storage services.

## Contents
- `geodeploy/main.py` — FastAPI app factory, lifespan (creates SQLite tables via `Base.metadata.create_all`, runs ad-hoc `_apply_schema_migrations`, writes an empty Martin config on first boot), mounts `/portals` static dir and `/templates-static`, registers all routers under `/api`.
- `geodeploy/config.py` — `Settings` (pydantic-settings) loaded from `.env`; `get_settings()` is `@lru_cache`d and `.cache_clear()`d after the setup wizard writes new creds. Exposes `sqlite_url`, `postgis_dsn`/`postgis_sync_dsn`, etc.
- `geodeploy/database.py` — async SQLAlchemy engine + `AsyncSessionLocal`; `get_db()` dependency. SQLite only (internal state — never user spatial data).
- `geodeploy/deps.py` — `get_current_user` (JWT/HS256 via python-jose) and `require_admin`.
- `geodeploy/models.py` — ORM: `SetupConfig`, `User`, `VectorLayer`, `RasterLayer`, `UploadJob`, `Portal`. JSON-ish fields (`bbox`, `columns`, `layer_configs`, `default_style`) stored as `Text`.
- `geodeploy/schemas.py` — Pydantic request/response models; `*Out.from_orm_json()` helpers parse the Text-encoded JSON columns.
- `geodeploy/celery_app.py` — Celery app (broker+backend = Redis), single queue `ingest`.
- `geodeploy/routers/` — HTTP endpoints. See `routers/README.md`.
- `geodeploy/services/` — provisioning + tile-URL + storage helpers. See `services/README.md`.
- `geodeploy/tasks/` — Celery ingest pipelines. See `tasks/README.md`.
- `alembic/`, `alembic.ini` — present but **effectively unused**: `versions/` holds only `.gitkeep`. Real schema management is `Base.metadata.create_all` + the hand-written `ALTER TABLE` list in `main.py::_apply_schema_migrations`. Add new columns there, not via Alembic, unless you intend to wire Alembic up properly.
- `tests/` — pytest-asyncio; `conftest.py` builds an ASGI test client over an in-memory-ish SQLite; `test_health.py` covers `/health` and initial `/api/setup/status`.
- `Dockerfile` — multi-stage (has a `development` target used by `docker-compose.dev.yml`).
- `requirements.txt`, `pytest.ini`.

## Dependencies / relationships
- **Imports from nothing else in the repo** (it is the bottom of the stack) but **controls** the `martin`, `titiler`, `postgres`, `minio` containers via the Docker socket (`/var/run/docker.sock`).
- **Consumed by** `ui/` (all calls go through `/api/*`, proxied by `nginx/`).
- **Writes** `data/martin/martin-config.yaml` (read by the Martin container) and `data/portals/{slug}/` static bundles (served by `nginx/` and also mounted by the API).
- `templates/` is bind-mounted read-only at `/templates`; `routers/templates.py` and `services/portal_generator.py` read from it.
- Celery worker shares the same image/code and the SQLite + `data/martin` + docker.sock mounts.

## Current status & known issues
- **`.env.example` has `TITILER_URL=http://titiler:8080` — WRONG.** The current TiTiler image listens on port **80**. `martin_url`/`titiler_url` are used by the admin health check and `get_tilejson_url`. Real installs get the value written by the wizard, but the example (and anyone copying it) is stale.
- Tile-serving had a cluster of bugs fixed in this session (TiTiler TileMatrixSet path, S3 endpoint scheme, nginx `merge_slashes`/rewrites, absolute tile URLs). See `notes_temp/notes_for_future.md` for the full chronology before touching tile code.
- `services/duckdb_engine.py` — `inspect_parquet()` is now used (the GeoParquet upload/import path reads file metadata + geometry with it). `query_geojson` is still placeholder (the deck.gl display/analysis path isn't wired yet).
- **GeoParquet vector layers** (`storage_backend='geoparquet'`) live as a file on object storage (`vectors/{uid}/{uuid}/x.parquet`), NOT in PostGIS — uploaded via presigned direct-to-MinIO (`/data/vector/geoparquet/presign`+`complete` → nginx `/s3/` proxy) and inspected by `tasks/geoparquet_import.py`. They are not yet tileable/displayable (deck.gl is the next increment).
- `routers/data/sources.py` is a stub (returns `[]`).
- Setup-wizard credential changes restart the `celery` container so it picks up new env; this relies on the docker.sock mount.

## Last updated
2026-06-04
