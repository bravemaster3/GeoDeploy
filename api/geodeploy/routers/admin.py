import os
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..database import get_db
from ..deps import require_admin
from ..models import Portal, RasterLayer, User, VectorLayer
from ..schemas import ServiceHealth, StorageStats

router = APIRouter(prefix="/admin", tags=["admin"])

# Services shown on the Settings page, in display order.
SERVICE_KEYS = ["postgres", "minio", "redis", "martin", "titiler", "nginx", "celery", "ui", "api"]
# The API container serves this very request — don't let the panel stop/restart itself.
NON_CONTROLLABLE = {"api"}


def _resolve_container(client, key: str):
    """Find a container for a service key whether it uses a fixed container_name
    (geodeploy-<key>) or Compose's auto name (geodeploy[-geodeploy]-<key>-N)."""
    try:
        return client.containers.get(f"geodeploy-{key}")
    except Exception:
        pass
    for c in client.containers.list(all=True):
        if "geodeploy" in c.name and key in c.name:
            return c
    return None


@router.get("/health", response_model=list[ServiceHealth])
async def service_health(_: User = Depends(require_admin)):
    import httpx
    import docker
    settings = get_settings()

    async def check_http(url: str):
        try:
            async with httpx.AsyncClient(timeout=3) as client:
                r = await client.get(url)
                return r.status_code < 400
        except Exception:
            return None

    http_ok = {
        "martin": await check_http(f"{settings.martin_url}/catalog"),
        "titiler": await check_http(f"{settings.titiler_url}/healthz"),
    }

    results = []
    try:
        client = docker.from_env()
        for key in SERVICE_KEYS:
            c = _resolve_container(client, key)
            if c is None:
                status = "stopped"
            else:
                status = c.status  # running | exited | paused | restarting | ...
                if status == "running" and http_ok.get(key) is not None:
                    status = "healthy" if http_ok[key] else "unhealthy"
            results.append(ServiceHealth(name=key, status=status, controllable=key not in NON_CONTROLLABLE))
    except Exception as e:
        results.append(ServiceHealth(name="docker", status="unhealthy", message=str(e)))

    return results


@router.post("/services/{name}/{action}")
async def control_service(name: str, action: str, _: User = Depends(require_admin)):
    """Start / stop / restart a GeoDeploy container (Coolify-style controls)."""
    import docker
    if name not in SERVICE_KEYS or name in NON_CONTROLLABLE:
        raise HTTPException(400, f"Service '{name}' cannot be controlled.")
    if action not in ("start", "stop", "restart"):
        raise HTTPException(400, "Action must be start, stop, or restart.")
    try:
        client = docker.from_env()
        c = _resolve_container(client, name)
        if c is None:
            raise HTTPException(404, f"Container for '{name}' not found.")
        getattr(c, action)()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, f"Failed to {action} {name}: {exc}") from exc
    return {"status": "ok", "service": name, "action": action}


@router.post("/reload-martin")
async def reload_martin(_: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    from ..services.martin import regenerate_config
    result = await db.execute(
        select(VectorLayer).where(VectorLayer.status == "ready", VectorLayer.storage_backend == "postgis")
    )
    layers = [{"schema_name": l.schema_name, "table_name": l.table_name,
               "geometry_column": l.geometry_column, "id_column": l.id_column, "crs": l.crs}
              for l in result.scalars().all()]
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
