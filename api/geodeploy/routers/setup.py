from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models import SetupConfig, User
from ..schemas import (
    ConfigureDBRequest, ConfigureStorageRequest, CreateAdminRequest, SetupStatus
)
from ..services import postgis as postgis_svc, minio as minio_svc
from ..config import get_settings

router = APIRouter(prefix="/setup", tags=["setup"])


async def _get_or_create_config(db: AsyncSession) -> SetupConfig:
    result = await db.execute(select(SetupConfig).where(SetupConfig.id == 1))
    config = result.scalar_one_or_none()
    if config is None:
        config = SetupConfig(id=1)
        db.add(config)
        await db.commit()
        await db.refresh(config)
    return config


@router.get("/status", response_model=SetupStatus)
async def setup_status(db: AsyncSession = Depends(get_db)):
    config = await _get_or_create_config(db)
    has_admin = bool((await db.execute(select(User))).scalars().first())
    return SetupStatus(
        completed=config.completed,
        postgis_configured=bool(config.postgis_host),
        storage_configured=bool(config.storage_endpoint),
        admin_created=has_admin,
    )


@router.post("/configure-db")
async def configure_db(req: ConfigureDBRequest, db: AsyncSession = Depends(get_db)):
    config = await _get_or_create_config(db)

    if req.type == "local":
        try:
            creds = await postgis_svc.provision_local()
        except Exception as exc:
            raise HTTPException(500, f"Failed to start PostGIS: {exc}") from exc
        config.postgis_type = "local"
        config.postgis_host = creds["host"]
        config.postgis_port = creds["port"]
        config.postgis_db = creds["db"]
        config.postgis_user = creds["user"]
        config.postgis_password = creds["password"]
    else:
        try:
            await postgis_svc.test_connection(req.host, req.port, req.db, req.user, req.password)
        except Exception as exc:
            raise HTTPException(400, f"Cannot connect to PostGIS: {exc}") from exc
        config.postgis_type = "external"
        config.postgis_host = req.host
        config.postgis_port = req.port
        config.postgis_db = req.db
        config.postgis_user = req.user
        config.postgis_password = req.password
        # Martin is a core always-on service now, so external DBs need nothing special here:
        # it boots on a sources-less config and `regenerate_config` rewrites + restarts it
        # when the first layer is uploaded.

    await db.commit()
    return {"status": "ok", "type": config.postgis_type}


@router.post("/configure-storage")
async def configure_storage(req: ConfigureStorageRequest, db: AsyncSession = Depends(get_db)):
    config = await _get_or_create_config(db)

    if req.type == "local":
        try:
            creds = await minio_svc.provision_local()
        except Exception as exc:
            raise HTTPException(500, f"Failed to start MinIO: {exc}") from exc
        config.storage_type = "local"
        config.storage_endpoint = creds["endpoint"]
        config.storage_bucket = creds["bucket"]
        config.storage_access_key = creds["access_key"]
        config.storage_secret_key = creds["secret_key"]
        config.storage_region = creds["region"]
    else:
        try:
            await minio_svc.test_connection(req.endpoint, req.bucket, req.access_key, req.secret_key, req.region)
        except Exception as exc:
            raise HTTPException(400, f"Cannot connect to storage: {exc}") from exc
        config.storage_type = req.type
        config.storage_endpoint = req.endpoint
        config.storage_bucket = req.bucket
        config.storage_access_key = req.access_key
        config.storage_secret_key = req.secret_key
        config.storage_region = req.region
        # The local branch starts TiTiler inside provision_local(); for an existing
        # store we must (re)create it here with provider-correct GDAL flags (HTTPS for a
        # real S3). Non-fatal: `docker compose --profile raster up` is a fallback (it now
        # reads TITILER_AWS_HTTPS from .env too).
        try:
            minio_svc.restart_titiler(req.endpoint, req.access_key, req.secret_key, req.region)
        except Exception:
            pass

    await db.commit()
    _write_env(config)
    _apply_to_process(config)
    return {"status": "ok", "type": config.storage_type}


@router.post("/create-admin")
async def create_admin(req: CreateAdminRequest, db: AsyncSession = Depends(get_db)):
    from passlib.context import CryptContext
    config = await _get_or_create_config(db)
    if not config.postgis_host or not config.storage_endpoint:
        raise HTTPException(400, "Configure database and storage before creating the admin account.")

    has_admin = bool((await db.execute(select(User))).scalars().first())
    if has_admin:
        raise HTTPException(400, "An admin account already exists. Please log in.")

    existing = (await db.execute(select(User).where(User.email == req.email))).scalar_one_or_none()
    if existing:
        raise HTTPException(400, "Email already registered.")

    pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
    user = User(
        email=req.email,
        name=req.name,
        hashed_password=pwd_ctx.hash(req.password),
        is_admin=True,
    )
    db.add(user)

    config.completed = True
    await db.commit()
    await db.refresh(user)

    # Persist credentials to .env and apply to running process
    _write_env(config)
    _apply_to_process(config)

    return {"status": "ok", "user_id": user.id}


def _apply_to_process(config: SetupConfig) -> None:
    """Push new credentials into the running process and restart celery."""
    import os
    import docker
    updates = {
        "POSTGIS_HOST": config.postgis_host or "",
        "POSTGIS_PORT": str(config.postgis_port) if config.postgis_port else "",
        "POSTGIS_DB": config.postgis_db or "",
        "POSTGIS_USER": config.postgis_user or "",
        "POSTGIS_PASSWORD": config.postgis_password or "",
        # Managed/external DBs usually require SSL; the local provisioned DB has none.
        "POSTGIS_SSLMODE": ("prefer" if config.postgis_type == "external" else ""),
        "STORAGE_TYPE": config.storage_type or "",
        "STORAGE_ENDPOINT": config.storage_endpoint or "",
        "STORAGE_BUCKET": config.storage_bucket or "",
        "STORAGE_ACCESS_KEY": config.storage_access_key or "",
        "STORAGE_SECRET_KEY": config.storage_secret_key or "",
        "STORAGE_REGION": config.storage_region or "us-east-1",
        # TiTiler/GDAL must speak HTTPS to a real S3; MinIO/local stays HTTP.
        "TITILER_AWS_HTTPS": ("YES" if (config.storage_endpoint or "").lower().startswith("https") else "NO"),
    }
    for key, val in updates.items():
        os.environ[key] = val
    get_settings.cache_clear()

    try:
        client = docker.from_env()
        for c in client.containers.list():
            if "celery" in c.name and "geodeploy" in c.name:
                c.restart()
    except Exception:
        pass


def _write_env(config: SetupConfig) -> None:
    import os
    env_path = "/geodeploy/.env" if os.path.exists("/geodeploy") else ".env"
    lines = []
    if os.path.exists(env_path):
        with open(env_path) as f:
            lines = f.readlines()

    # Which optional (profile-gated) local containers this install runs. Persisting this to
    # COMPOSE_PROFILES means `docker compose up` (install/update) keeps managing them — without
    # it, `--remove-orphans` would delete the wizard-provisioned postgres/minio. External users
    # leave the relevant profile off so the local container never starts. (notes_for_future §1)
    profiles = []
    if config.postgis_type == "local":
        profiles.append("local-db")
    if config.storage_type == "local":
        profiles.append("local-storage")

    updates = {
        "COMPOSE_PROFILES": ",".join(profiles),
        "POSTGIS_HOST": config.postgis_host,
        "POSTGIS_PORT": str(config.postgis_port),
        "POSTGIS_DB": config.postgis_db,
        "POSTGIS_USER": config.postgis_user,
        "POSTGIS_PASSWORD": config.postgis_password,
        # Managed/external DBs usually require SSL; the local provisioned DB has none.
        "POSTGIS_SSLMODE": ("prefer" if config.postgis_type == "external" else ""),
        "STORAGE_TYPE": config.storage_type,
        "STORAGE_ENDPOINT": config.storage_endpoint,
        "STORAGE_BUCKET": config.storage_bucket,
        "STORAGE_ACCESS_KEY": config.storage_access_key,
        "STORAGE_SECRET_KEY": config.storage_secret_key,
        "STORAGE_REGION": config.storage_region or "us-east-1",
        # GDAL VSI S3 needs endpoint without http:// scheme
        "TITILER_S3_ENDPOINT": (config.storage_endpoint or "").removeprefix("https://").removeprefix("http://"),
        # TiTiler/GDAL must speak HTTPS to a real S3; MinIO/local stays HTTP.
        "TITILER_AWS_HTTPS": ("YES" if (config.storage_endpoint or "").lower().startswith("https") else "NO"),
    }

    existing_keys = set()
    new_lines = []
    for line in lines:
        key = line.split("=")[0].strip()
        if key in updates:
            new_lines.append(f"{key}={updates[key]}\n")
            existing_keys.add(key)
        else:
            new_lines.append(line)

    for key, val in updates.items():
        if key not in existing_keys:
            new_lines.append(f"{key}={val}\n")

    with open(env_path, "w") as f:
        f.writelines(new_lines)
