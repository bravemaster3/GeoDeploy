import os
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..database import get_db
from ..deps import require_admin
from ..models import Portal, RasterLayer, SetupConfig, User, VectorLayer
from ..schemas import (EmailSettings, EmailSettingsOut, OidcSettings, OidcSettingsOut,
                       ServiceHealth, StorageStats)
from ..services import notifications
from .users import request_origin

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


async def _postgis_bytes(layers) -> int | None:
    """Sum pg_total_relation_size (data + indexes + TOAST) over the catalog's PostGIS tables.
    None when the DB can't be reached; a missing table just contributes nothing."""
    if not layers:
        return 0
    import asyncpg
    settings = get_settings()
    try:
        conn = await asyncpg.connect(settings.postgis_sync_dsn)
    except Exception:
        return None
    total = 0
    try:
        for l in layers:
            try:
                size = await conn.fetchval(
                    "SELECT pg_total_relation_size($1::regclass)",
                    f'"{l.schema_name}"."{l.table_name}"')
                total += size or 0
            except Exception:
                pass  # dropped/renamed table — the row is stale, not a reason to fail the panel
    finally:
        await conn.close()
    return total


def _s3_bytes(raster_layers, gpq_layers) -> tuple[int | None, int | None]:
    """(raster_bytes, geoparquet_bytes) from object storage — per-layer, so ATTACHED data
    (import-existing, keys outside rasters/ / vectors/) is counted too. Blocking (boto3);
    call via run_in_threadpool."""
    from ..services.minio import get_s3_client
    settings = get_settings()
    try:
        s3 = get_s3_client()
        bucket = settings.storage_bucket

        def key_size(key: str) -> int:
            try:
                return s3.head_object(Bucket=bucket, Key=key)["ContentLength"]
            except Exception:
                return 0

        def prefix_size(prefix: str) -> int:
            total = 0
            for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
                for obj in page.get("Contents", []):
                    total += obj["Size"]
            return total

        raster_total = sum(key_size(l.s3_key) for l in raster_layers if l.s3_key)

        gpq_total = 0
        for l in gpq_layers:
            key = (l.s3_key or "").rstrip("/")
            if key:
                # A prepped layer is a partitioned PREFIX (parts-<hex>/); before prep (or for a
                # raw large upload awaiting conversion) it's a single object with an extension.
                if "." in key.rsplit("/", 1)[-1]:
                    gpq_total += key_size(key)
                else:
                    gpq_total += prefix_size(key + "/")
            if l.pmtiles_key:
                gpq_total += key_size(l.pmtiles_key)
        return raster_total, gpq_total
    except Exception:
        return None, None


@router.get("/storage-stats", response_model=StorageStats)
async def storage_stats(_: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Instance-wide storage breakdown: PostGIS tables + S3 objects (rasters, GeoParquet incl.
    PMTiles) + published portal bundles. Measured per catalog layer — accurate for attached
    (import-existing) data too, and never counts orphans the catalog doesn't know about."""
    from starlette.concurrency import run_in_threadpool

    settings = get_settings()
    portals_dir = f"{settings.data_dir}/portals"
    bundle_bytes = sum(
        os.path.getsize(os.path.join(dp, f))
        for dp, _dirs, files in os.walk(portals_dir)
        for f in files
    ) if os.path.exists(portals_dir) else 0

    vectors = (await db.execute(select(VectorLayer))).scalars().all()
    rasters = (await db.execute(select(RasterLayer))).scalars().all()
    portal_count = (await db.execute(select(func.count()).select_from(Portal))).scalar()

    postgis_layers = [l for l in vectors
                      if l.storage_backend == "postgis" and l.schema_name and l.table_name]
    gpq_layers = [l for l in vectors if l.storage_backend == "geoparquet"]

    pg_bytes = await _postgis_bytes(postgis_layers)
    raster_bytes, gpq_bytes = await run_in_threadpool(_s3_bytes, rasters, gpq_layers)

    used = sum(v for v in (pg_bytes, raster_bytes, gpq_bytes, bundle_bytes) if v)
    return StorageStats(
        used_bytes=used,
        total_bytes=None,
        vector_layers=len(vectors),
        raster_layers=len(rasters),
        portals=portal_count,
        postgis_bytes=pg_bytes,
        raster_bytes=raster_bytes,
        geoparquet_bytes=gpq_bytes,
        portal_bundle_bytes=bundle_bytes,
    )


# ── Outgoing email (generic SMTP, C-08a) ─────────────────────────────────────────────────────

async def _get_config(db: AsyncSession) -> SetupConfig:
    cfg = (await db.execute(select(SetupConfig).where(SetupConfig.id == 1))).scalar_one_or_none()
    if cfg is None:
        cfg = SetupConfig(id=1)
        db.add(cfg)
        await db.commit()
        await db.refresh(cfg)
    return cfg


def _email_out(cfg: SetupConfig) -> EmailSettingsOut:
    return EmailSettingsOut(
        smtp_host=cfg.smtp_host, smtp_port=cfg.smtp_port, smtp_security=cfg.smtp_security,
        smtp_username=cfg.smtp_username, email_from=cfg.email_from,
        has_password=bool(cfg.smtp_password),
        configured=bool((cfg.smtp_host or "").strip() and (cfg.email_from or "").strip()),
    )


@router.get("/email-settings", response_model=EmailSettingsOut)
async def get_email_settings(_: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    return _email_out(await _get_config(db))


@router.put("/email-settings", response_model=EmailSettingsOut)
async def update_email_settings(body: EmailSettings,
                                _: User = Depends(require_admin),
                                db: AsyncSession = Depends(get_db)):
    """Partial update. The password is only overwritten when a non-empty value is sent (the UI
    leaves the field blank to keep the stored one); clearing smtp_host disables email entirely."""
    cfg = await _get_config(db)
    data = body.model_dump(exclude_unset=True)
    if data.get("smtp_password") == "":
        data.pop("smtp_password")  # blank = keep the stored secret
    for field, value in data.items():
        setattr(cfg, field, value)
    await db.commit()
    await db.refresh(cfg)
    return _email_out(cfg)


@router.post("/email-settings/test")
async def test_email(user: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Send a test email to the calling admin. Raises the relay's actual error back to the UI —
    this is the one email path that is NOT best-effort, because the admin is debugging."""
    try:
        await notifications.send_test_email(db, user.email)
    except Exception as exc:  # noqa: BLE001 — surface whatever the relay said
        raise HTTPException(502, f"Test email failed: {exc}") from exc
    return {"status": "ok", "to": user.email}


# ── OIDC SSO settings (A-04) ─────────────────────────────────────────────────────────────────────

def _oidc_out(cfg: SetupConfig, request: Request) -> OidcSettingsOut:
    return OidcSettingsOut(
        oidc_enabled=bool(cfg.oidc_enabled),
        oidc_issuer=cfg.oidc_issuer,
        oidc_client_id=cfg.oidc_client_id,
        oidc_label=cfg.oidc_label,
        oidc_auto_provision=bool(cfg.oidc_auto_provision),
        oidc_allowed_domains=cfg.oidc_allowed_domains,
        oidc_default_role=cfg.oidc_default_role or "viewer",
        has_client_secret=bool(cfg.oidc_client_secret),  # never return the secret itself
        redirect_uri=request_origin(request) + "/api/auth/oidc/callback",
    )


@router.get("/oidc-settings", response_model=OidcSettingsOut)
async def get_oidc_settings(request: Request, _: User = Depends(require_admin),
                            db: AsyncSession = Depends(get_db)):
    return _oidc_out(await _get_config(db), request)


@router.put("/oidc-settings", response_model=OidcSettingsOut)
async def update_oidc_settings(body: OidcSettings, request: Request,
                               _: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Partial update. The client secret is only overwritten when a non-empty value is sent (the UI
    leaves it blank to keep the stored one). The secret is encrypted at rest via EncryptedText."""
    cfg = await _get_config(db)
    data = body.model_dump(exclude_unset=True)
    if data.get("oidc_client_secret") == "":
        data.pop("oidc_client_secret")  # blank = keep the stored secret
    for field, value in data.items():
        setattr(cfg, field, value)
    await db.commit()
    await db.refresh(cfg)
    return _oidc_out(cfg, request)
