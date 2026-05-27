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
    os.makedirs(f"{settings.data_dir}/sqlite", exist_ok=True)
    os.makedirs(f"{settings.data_dir}/portals", exist_ok=True)
    os.makedirs(f"{settings.data_dir}/temp", exist_ok=True)
    os.makedirs(f"{settings.data_dir}/martin", exist_ok=True)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield


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
