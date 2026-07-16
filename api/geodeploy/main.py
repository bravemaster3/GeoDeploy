import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .config import get_settings
from .database import engine, Base
from .routers import setup, auth, portals, stac, templates, admin, basemaps, users
from .routers.data import vector, raster, sources, discover


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    for subdir in ("sqlite", "portals", "temp", "martin"):
        os.makedirs(f"{settings.data_dir}/{subdir}", exist_ok=True)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_apply_schema_migrations)

    # Write a minimal Martin config on first start so Martin can boot without layers
    _ensure_martin_config(settings)

    yield


def _apply_schema_migrations(conn) -> None:
    """Add columns that may be missing on databases created before the current schema."""
    from sqlalchemy import text
    pending = [
        "ALTER TABLE portals ADD COLUMN access_password_sha256 VARCHAR(64)",
        "ALTER TABLE portals ADD COLUMN initial_view TEXT",
        "ALTER TABLE portals ADD COLUMN basemap VARCHAR(64)",
        "ALTER TABLE vector_layers ADD COLUMN default_style TEXT",
        "ALTER TABLE vector_layers ADD COLUMN geometry_column VARCHAR(128)",
        "ALTER TABLE vector_layers ADD COLUMN id_column VARCHAR(128)",
        "ALTER TABLE vector_layers ADD COLUMN storage_backend VARCHAR(16) DEFAULT 'postgis'",
        "ALTER TABLE vector_layers ADD COLUMN s3_key VARCHAR(512)",
        "ALTER TABLE vector_layers ADD COLUMN pmtiles_key VARCHAR(512)",
        "ALTER TABLE vector_layers ADD COLUMN tile_status VARCHAR(16)",
        # Import-existing GeoParquet: the ORIGINAL attached key (prep repoints s3_key at a copy)
        "ALTER TABLE vector_layers ADD COLUMN source_s3_key VARCHAR(512)",
        # Large-upload convert options (CSV X/Y or WKT, srid, delimiter) — persisted for restart
        "ALTER TABLE vector_layers ADD COLUMN convert_opts TEXT",
        "ALTER TABLE raster_layers ADD COLUMN default_style TEXT",
        # Data sharing + STAC catalog metadata (notes §0h-addendum)
        "ALTER TABLE vector_layers ADD COLUMN is_public BOOLEAN DEFAULT 0",
        "ALTER TABLE vector_layers ADD COLUMN abstract TEXT",
        "ALTER TABLE vector_layers ADD COLUMN keywords VARCHAR(512)",
        "ALTER TABLE vector_layers ADD COLUMN license VARCHAR(128)",
        "ALTER TABLE vector_layers ADD COLUMN attribution VARCHAR(256)",
        "ALTER TABLE raster_layers ADD COLUMN is_public BOOLEAN DEFAULT 0",
        "ALTER TABLE raster_layers ADD COLUMN abstract TEXT",
        "ALTER TABLE raster_layers ADD COLUMN keywords VARCHAR(512)",
        "ALTER TABLE raster_layers ADD COLUMN license VARCHAR(128)",
        "ALTER TABLE raster_layers ADD COLUMN attribution VARCHAR(256)",
        # RBAC (A-01): role column + backfill. SQLite ADD COLUMN can't be NOT NULL
        # without a constant default, so the column is nullable on migrated DBs —
        # the guarded UPDATEs below fill it, and every user-creating code path sets
        # role explicitly. Pre-RBAC non-admins had full CRUD on their data → editor;
        # the earliest admin becomes the single workspace owner.
        "ALTER TABLE users ADD COLUMN role VARCHAR(16)",
        "UPDATE users SET role = CASE WHEN is_admin THEN 'admin' ELSE 'editor' END WHERE role IS NULL",
        "UPDATE users SET role = 'owner' WHERE id = (SELECT MIN(id) FROM users WHERE is_admin) "
        "AND NOT EXISTS (SELECT 1 FROM users WHERE role = 'owner')",
        # DB-level single-owner invariant (SQLite partial unique index)
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_users_single_owner ON users(role) WHERE role = 'owner'",
    ]
    for sql in pending:
        try:
            conn.execute(text(sql))
        except Exception:
            pass  # Column already exists


def _ensure_martin_config(settings) -> None:
    """Write a Martin config if none exists so the always-on Martin container can boot.

    Martin is a core service (started by the installer / compose, not profile-gated), so it
    may start before any database is configured. With no PostGIS yet we write a sources-less
    config (just `listen_addresses`) — Martin boots and serves an empty catalog instead of
    crash-looping on an unreachable DB. Once a DB is set up + a layer uploaded,
    `services.martin.regenerate_config` rewrites this with the `postgres` source and restarts.
    """
    import yaml
    config_path = settings.martin_config_path
    if os.path.exists(config_path):
        return
    try:
        config = {"listen_addresses": "0.0.0.0:3000"}
        if settings.postgis_host:
            config["postgres"] = {
                "connection_string": settings.postgis_sync_dsn,
                "pool_size": 5,
                "tables": {},
            }
        with open(config_path, "w") as f:
            yaml.dump(config, f, default_flow_style=False)
    except Exception:
        pass  # Non-fatal — Martin will emit an error on start but won't crash GeoDeploy


app = FastAPI(
    title="GeoDeploy API",
    version="0.3.0",
    description="Self-hosted spatial data management and geoportal builder",
    lifespan=lifespan,
)

settings = get_settings()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if settings.is_dev else [
        "http://localhost",
        "https://localhost",
        os.getenv("GEODEPLOY_ORIGIN", ""),
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# API routes
for router in [setup.router, auth.router, users.router, portals.router, templates.router,
               admin.router, basemaps.router, vector.router, raster.router, sources.router,
               discover.router, stac.router]:
    app.include_router(router, prefix="/api")

# Serve published portals as static files
portals_dir = f"{settings.data_dir}/portals"
os.makedirs(portals_dir, exist_ok=True)
app.mount("/portals", StaticFiles(directory=portals_dir, html=True), name="portals")

# Serve template preview images
templates_dir = "/templates"
if os.path.exists(templates_dir):
    app.mount("/templates-static", StaticFiles(directory=templates_dir), name="templates-static")


@app.get("/health")
async def health():
    return {"status": "ok", "version": "0.3.0"}
