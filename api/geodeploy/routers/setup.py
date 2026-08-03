from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import database
from ..database import Base, get_db
from ..deps import ROLE_ORDER, resolve_bearer_user
from ..models import SetupConfig, User
from ..schemas import (
    ConfigureDBRequest, ConfigureStorageRequest, CreateAdminRequest, SetupStatus
)
from ..services import postgis as postgis_svc, minio as minio_svc, martin as martin_svc
from ..config import get_settings

router = APIRouter(prefix="/setup", tags=["setup"])

# ── BOOTSTRAP ORDER (changed 2026-07-30, state moved to PostgreSQL) ──────────────────────────
# State now lives IN the database the wizard configures, so there is a genuine chicken-and-egg:
# nothing can be stored until a connection exists. Hence:
#
#   1. `/status` must answer with NO database — it reads the environment, not a row.
#   2. `/configure-db` provisions or validates, writes `.env`, rebuilds the engine, creates the
#      schema, and only THEN persists the SetupConfig row.
#   3. Everything after that (storage, admin) is ordinary DB work.
#
# So these two endpoints must NOT `Depends(get_db)` — that dependency answers 503 until an engine
# exists, which is exactly the state the wizard runs in. They open a session by hand afterwards.


def _session():
    """A session, or None when no database is configured yet."""
    database.configure()
    return database.AsyncSessionLocal() if database.AsyncSessionLocal else None


async def _get_or_create_config(db: AsyncSession) -> SetupConfig:
    result = await db.execute(select(SetupConfig).where(SetupConfig.id == 1))
    config = result.scalar_one_or_none()
    if config is None:
        config = SetupConfig(id=1)
        db.add(config)
        await db.commit()
        await db.refresh(config)
    return config


async def _guard_setup_mutation(request: Request, db: AsyncSession) -> None:
    """The DB/storage config endpoints are unauthenticated during FIRST-RUN so the wizard works
    before any account exists. Once setup is completed (or an admin exists), they would otherwise let
    ANYONE repoint storage/DB on a live instance (data hijack / DoS) — so from that point on they
    require a valid ADMIN bearer token. First run stays open; everything after is admin-only."""
    config = await _get_or_create_config(db)
    has_admin = bool((await db.execute(select(User))).scalars().first())
    if not config.completed and not has_admin:
        return  # first-run: setup is still open

    user = await resolve_bearer_user(request, db)
    if user is None:
        raise HTTPException(403, "Setup is already complete. Sign in as an admin to reconfigure.")
    if ROLE_ORDER.get(user.role, -1) < ROLE_ORDER["admin"]:
        raise HTTPException(403, "Admin access required to reconfigure a running instance.")


def _looks_encrypted(value: str | None) -> bool:
    """True when a "decrypted" value is still a Fernet token.

    `crypto.decrypt_secret` returns the ciphertext UNCHANGED when decryption fails, because a failure
    is indistinguishable from a legacy plaintext value written before encryption existed. That is the
    right default for reading, and a trap here: with the wrong GEODEPLOY_SECRET_KEY we would write a
    Fernet blob into `.env` as if it were a storage secret, and every S3 call would fail with a
    signature error that says nothing about keys.

    Fernet tokens are version byte 0x80 base64url-encoded, which always yields the `gAAAAA` prefix.
    """
    return bool(value) and value.startswith("gAAAAA")


async def _describe_existing_install(db: AsyncSession, stored: SetupConfig) -> dict | None:
    """What this database already contains, or None if it is a fresh one.

    Pointing a new install at an existing GeoDeploy database is a supported thing to do — a rebuilt
    server, new hardware, or recovery without a backup. The database holds every answer the wizard
    would ask for, so the wizard should stop asking and adopt them.
    """
    has_admin = bool((await db.execute(select(User))).scalars().first())
    if not has_admin and not stored.completed:
        return None                       # genuinely fresh — the normal wizard applies

    users = len((await db.execute(select(User))).scalars().all())
    # The secret is the one field that may be unreadable: it is encrypted with the key of the
    # install that WROTE it, and this server generated a new one unless the operator carried it.
    secret_ok = not _looks_encrypted(stored.storage_secret_key)
    return {
        "users": users,
        "storage_configured": bool(stored.storage_endpoint),
        "storage_endpoint": stored.storage_endpoint,
        "storage_bucket": stored.storage_bucket,
        # False means: everything else was recovered, but the stored storage secret cannot be read
        # with this instance's GEODEPLOY_SECRET_KEY. Carrying the old key across fixes it; nothing
        # else can, because the key is deliberately not in the database or in any backup.
        "storage_secret_recovered": secret_ok,
    }


@router.get("/status", response_model=SetupStatus)
async def setup_status():
    """Answers BEFORE any database exists — this is the very first call the UI makes, and on a
    fresh install there is nothing to query. Everything is false until `/configure-db` runs."""
    session = _session()
    if session is None:
        return SetupStatus(completed=False, postgis_configured=False, storage_configured=False,
                           admin_created=False, email_enabled=False)
    async with session as db:
        try:
            config = await _get_or_create_config(db)
            has_admin = bool((await db.execute(select(User))).scalars().first())
        except Exception:
            # Engine configured but the schema/server isn't reachable yet (a container still
            # starting, wrong creds in .env). Report "not set up" rather than 500 the wizard.
            return SetupStatus(completed=False, postgis_configured=False,
                               storage_configured=False, admin_created=False, email_enabled=False)
        return SetupStatus(
            completed=config.completed,
            postgis_configured=bool(config.postgis_host),
            storage_configured=bool(config.storage_endpoint),
            admin_created=has_admin,
            email_enabled=bool((config.smtp_host or "").strip() and (config.email_from or "").strip()),
        )


@router.post("/configure-db")
async def configure_db(req: ConfigureDBRequest, request: Request):
    """Establish the state+spatial database. This is the ONE endpoint that runs before a database
    exists, so it takes no `get_db` dependency and does the work in order:
    provision/validate → write `.env` → rebuild the engine → create the schema → persist the row."""
    session = _session()
    if session is not None:
        async with session as db:
            await _guard_setup_mutation(request, db)
    # No engine yet ⇒ nothing is configured ⇒ first run by definition, so the guard is a no-op.

    # An UNSAVED instance: it carries the credentials for `_write_env` before any database exists
    # to store it in. It is persisted further down, once there is somewhere to put it.
    config = SetupConfig(id=1)

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
        target_db = req.db
        if req.create_database:
            # The operator asked for a NEW database on this server, having been told the one they
            # named already holds an installation. `req.db` is the maintenance database to connect
            # through; the new one becomes the target for everything below.
            try:
                await postgis_svc.create_database(req.host, req.port, req.db, req.user,
                                                  req.password, req.create_database)
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
            except Exception as exc:
                from ..services.setup_errors import postgres_error
                raise HTTPException(400, postgres_error(exc, req.host, req.port, req.db,
                                                        req.user)) from exc
            target_db = req.create_database
        try:
            await postgis_svc.test_connection(req.host, req.port, target_db, req.user, req.password)
        except Exception as exc:
            # NAME the cause. A timeout, a refusal, bad credentials and a missing PostGIS extension
            # all arrived here as the same "Cannot connect to PostGIS: <driver text>", and only two
            # of those have anything to do with what was just typed — so the first thing a new
            # install shows you was a message that sent you to check the wrong thing.
            from ..services.setup_errors import postgres_error
            raise HTTPException(400, postgres_error(exc, req.host, req.port, target_db,
                                                    req.user)) from exc
        config.postgis_type = "external"
        config.postgis_host = req.host
        config.postgis_port = req.port
        config.postgis_db = target_db
        config.postgis_user = req.user
        config.postgis_password = req.password
        # Martin is a core always-on service now, so external DBs need nothing special here:
        # it boots on a sources-less config and `regenerate_config` rewrites + restarts it
        # when the first layer is uploaded.

    # .env FIRST: it is the only place database credentials may live, since SetupConfig is inside
    # the database being configured. Then rebuild the engine so this very process can use it.
    _write_env(config)
    _apply_to_process(config)
    engine = database.configure(force=True)
    if engine is None:
        raise HTTPException(500, "Credentials were written but the engine could not be built.")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    except Exception as exc:
        raise HTTPException(500, f"Connected, but could not create the schema: {exc}") from exc

    # Now there is somewhere to persist it.
    existing = None
    async with database.AsyncSessionLocal() as db:
        stored = await _get_or_create_config(db)
        for field in ("postgis_type", "postgis_host", "postgis_port", "postgis_db",
                      "postgis_user", "postgis_password"):
            setattr(stored, field, getattr(config, field))
        await db.commit()
        await db.refresh(stored)
        # RECONNECTING to a database that already holds an installation.
        #
        # Pointing a fresh install at an existing GeoDeploy database is a legitimate thing to do —
        # rebuilding a lost server, moving the app to new hardware, or recovering without a backup.
        # It used to half-work: this step succeeded, the STORAGE step was then refused by
        # `_guard_setup_mutation` (an admin already exists), and `.env` was left holding the
        # installer's template defaults. The instance could never reach its own object storage.
        #
        # The database already knows every answer the wizard would have asked for, so take them from
        # it rather than making the operator retype what is already stored.
        existing = await _describe_existing_install(db, stored)
        if existing:
            if not existing["storage_secret_recovered"]:
                # NEVER write ciphertext as if it were a secret. `decrypt_secret` hands back the
                # token unchanged when the key is wrong, and a Fernet blob in STORAGE_SECRET_KEY
                # would fail every S3 call with a signature error that blames the key you can see
                # rather than the key you cannot. None means "say nothing", so `.env` keeps whatever
                # it had and the operator is told to supply it.
                stored.storage_secret_key = None
            # Inside the session and after the commit: this mutation is in-memory only, and closing
            # without committing discards it. The row keeps its encrypted value.
            _write_env(stored)          # storage_* included — that is the whole point
            _apply_to_process(stored)

    # Martin has been restart-looping since install: it had no reachable database (before the
    # wizard, .env carries a host but no password). Now that real credentials exist, rewrite its
    # config and restart it — otherwise it keeps failing until someone uploads the first layer,
    # which is what made a freshly-installed instance look broken.
    try:
        await martin_svc.regenerate_config([])
    except Exception as exc:      # never fail setup over the tile server
        import logging
        logging.getLogger(__name__).warning("could not start Martin after DB setup: %s", exc)

    return {"status": "ok", "type": config.postgis_type, "existing_install": existing}


@router.post("/configure-storage")
async def configure_storage(req: ConfigureStorageRequest, request: Request, db: AsyncSession = Depends(get_db)):
    await _guard_setup_mutation(request, db)
    config = await _get_or_create_config(db)

    import os
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
        # TiTiler runs on the read-only key (or root as fallback); persisted below so compose keeps it.
        os.environ["TITILER_ACCESS_KEY"] = creds.get("titiler_access_key") or creds["access_key"]
        os.environ["TITILER_SECRET_KEY"] = creds.get("titiler_secret_key") or creds["secret_key"]
    else:
        try:
            await minio_svc.test_connection(req.endpoint, req.bucket, req.access_key, req.secret_key, req.region)
        except Exception as exc:
            # Same reasoning as the database step: an unreachable endpoint, a rejected key, a bucket
            # this key may not touch, and a bucket in another region are four different fixes.
            from ..services.setup_errors import storage_error
            raise HTTPException(400, storage_error(exc, req.endpoint, req.bucket)) from exc
        config.storage_type = req.type
        config.storage_endpoint = req.endpoint
        config.storage_bucket = req.bucket
        config.storage_access_key = req.access_key
        config.storage_secret_key = req.secret_key
        config.storage_region = req.region
        # External storage: TiTiler uses the caller's creds (can't mint a scoped user remotely —
        # the operator should supply a read-only key for TiTiler when using external S3).
        os.environ["TITILER_ACCESS_KEY"] = req.access_key
        os.environ["TITILER_SECRET_KEY"] = req.secret_key
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
        role="owner",
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
        # Read-only key TiTiler runs as (falls back to the storage key if not provisioned).
        "TITILER_ACCESS_KEY": os.environ.get("TITILER_ACCESS_KEY") or config.storage_access_key or "",
        "TITILER_SECRET_KEY": os.environ.get("TITILER_SECRET_KEY") or config.storage_secret_key or "",
    }
    for key, val in updates.items():
        os.environ[key] = val
    get_settings.cache_clear()

    # Publish the DB credentials to the shared data volume BEFORE touching containers. A restart
    # does NOT re-read .env — Docker fixes a container's environment when it is CREATED — so the
    # worker would otherwise keep the install-time empty password and every task would fail with
    # "fe_sendauth: no password supplied". state_db.connect() reads this file on each connect.
    try:
        from .. import state_db
        state_db.write_runtime_credentials(
            config.postgis_host, config.postgis_port, config.postgis_db,
            config.postgis_user, config.postgis_password,
            "prefer" if config.postgis_type == "external" else "")
    except Exception:
        import logging
        logging.getLogger(__name__).exception("could not publish runtime DB credentials")

    # Still restart celery: it picks up the new storage settings and clears any cached state.
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
        # Read-only key TiTiler runs as (falls back to the storage key if not provisioned).
        "TITILER_ACCESS_KEY": os.environ.get("TITILER_ACCESS_KEY") or config.storage_access_key or "",
        "TITILER_SECRET_KEY": os.environ.get("TITILER_SECRET_KEY") or config.storage_secret_key or "",
    }

    # A None value means "this step knows nothing about that setting", NOT "set it to nothing".
    #
    # `configure_db` calls this with a BLANK SetupConfig — it has no storage fields yet — so every
    # STORAGE_* key arrived here as None and was formatted into an f-string, writing the literal
    # four characters `None`:
    #
    #     STORAGE_ENDPOINT=None
    #
    # Normally the storage step overwrote them seconds later and nobody saw it. When it does not —
    # the wizard is interrupted, or the setup guard refuses the storage step — the file keeps that
    # value, boto3 is handed it as an endpoint URL, and everything touching object storage fails
    # with `Invalid endpoint: None`, restores included.
    #
    # Skipping the key also stops one step CLOBBERING another's settings: re-running configure-db
    # on a working instance used to blank its storage configuration.
    updates = {k: v for k, v in updates.items() if v is not None}

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
    # .env holds DB + storage + JWT secrets — keep it owner-only (best-effort; no-op on filesystems
    # without POSIX modes).
    try:
        os.chmod(env_path, 0o600)
    except OSError:
        pass
