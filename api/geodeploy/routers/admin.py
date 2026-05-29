import os
from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..database import get_db
from ..deps import require_admin
from ..models import Portal, RasterLayer, User, VectorLayer
from ..schemas import ServiceHealth, StorageStats

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/health", response_model=list[ServiceHealth])
async def service_health(_: User = Depends(require_admin)):
    import httpx
    import docker
    settings = get_settings()
    results = []

    async def check_http(name: str, url: str) -> ServiceHealth:
        try:
            async with httpx.AsyncClient(timeout=3) as client:
                r = await client.get(url)
                return ServiceHealth(name=name, status="healthy" if r.status_code < 400 else "unhealthy")
        except Exception as e:
            return ServiceHealth(name=name, status="unhealthy", message=str(e))

    results.append(await check_http("martin", f"{settings.martin_url}/catalog"))
    results.append(await check_http("titiler", f"{settings.titiler_url}/healthz"))

    try:
        client = docker.from_env()
        for name in ["geodeploy-postgres", "geodeploy-minio", "geodeploy-redis"]:
            try:
                c = client.containers.get(name)
                results.append(ServiceHealth(name=name.replace("geodeploy-", ""), status=c.status))
            except docker.errors.NotFound:
                results.append(ServiceHealth(name=name.replace("geodeploy-", ""), status="stopped"))
    except Exception as e:
        results.append(ServiceHealth(name="docker", status="unhealthy", message=str(e)))

    return results


@router.post("/reload-martin")
async def reload_martin(_: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    from ..services.martin import regenerate_config
    result = await db.execute(
        select(VectorLayer).where(VectorLayer.status == "ready", VectorLayer.storage_backend == "postgis")
    )
    layers = [{"schema_name": l.schema_name, "table_name": l.table_name} for l in result.scalars().all()]
    await regenerate_config(layers)
    return {"status": "ok", "tables": len(layers)}


@router.get("/storage-stats", response_model=StorageStats)
async def storage_stats(_: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    settings = get_settings()
    portals_dir = f"{settings.data_dir}/portals"
    used = sum(
        os.path.getsize(os.path.join(dp, f))
        for dp, _, files in os.walk(portals_dir)
        for f in files
    ) if os.path.exists(portals_dir) else 0

    vector_count = (await db.execute(select(func.count()).select_from(VectorLayer))).scalar()
    raster_count = (await db.execute(select(func.count()).select_from(RasterLayer))).scalar()
    portal_count = (await db.execute(select(func.count()).select_from(Portal))).scalar()

    return StorageStats(
        used_bytes=used,
        total_bytes=None,
        vector_layers=vector_count,
        raster_layers=raster_count,
        portals=portal_count,
    )
