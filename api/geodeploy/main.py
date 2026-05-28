import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .config import get_settings
from .database import engine, Base
from .routers import setup, auth, portals, templates, admin
from .routers.data import vector, raster, sources


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
        "ALTER TABLE vector_layers ADD COLUMN default_style TEXT",
        "ALTER TABLE raster_layers ADD COLUMN default_style TEXT",
    ]
    for sql in pending:
        try:
            conn.execute(text(sql))
        except Exception:
            pass  # Column already exists


def _ensure_martin_config(settings) -> None:
    """Write an empty-tables Martin config if none exists, so the Martin container can start."""
    import yaml
    config_path = settings.martin_config_path
    if os.path.exists(config_path):
        return
    if not settings.postgis_host:
        return  # PostGIS not configured yet — Martin won't start anyway
    try:
        config = {
            "postgres": {
                "connection_string": settings.postgis_sync_dsn,
                "pool_size": 5,
                "tables": {},
            },
            "srv": {"listen_addresses": "0.0.0.0:3000"},
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
for router in [setup.router, auth.router, portals.router, templates.router, admin.router,
               vector.router, raster.router, sources.router]:
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
