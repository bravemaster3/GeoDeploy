"""Backups — admin API for the destination, the schedule, and the run history.

Admin-only and browser-only (`require_admin` rejects API tokens): these settings hold credentials
to the one place that survives losing this instance, so they are not something a scoped token
should be able to read, rewrite or point somewhere else.

The secret key is WRITE-ONLY over the API (never returned, blank keeps the stored value) — the
same rule as the SMTP and OIDC secrets.
"""
import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from ..database import get_db
from ..deps import require_admin, require_owner
from ..models import BackupRun, RestoreRun, SetupConfig, User
from ..schemas import (BackupRunOut, BackupSettingsIn, BackupSettingsOut,
                       RestoreRequest, RestoreRunOut)
from ..services import backup as bk
from ..services import restore as rs
from .common import record_audit

router = APIRouter(prefix="/backups", tags=["backups"])


async def _config(db: AsyncSession) -> SetupConfig:
    cfg = (await db.execute(select(SetupConfig).where(SetupConfig.id == 1))).scalar_one_or_none()
    if not cfg:
        raise HTTPException(409, "Run the setup wizard first.")
    return cfg


def _out(cfg: SetupConfig) -> BackupSettingsOut:
    return BackupSettingsOut(
        enabled=bool(cfg.backup_enabled), endpoint=cfg.backup_endpoint, bucket=cfg.backup_bucket,
        prefix=cfg.backup_prefix or "geodeploy-backups", access_key=cfg.backup_access_key,
        region=cfg.backup_region or "us-east-1", schedule=cfg.backup_schedule or "off",
        hour=cfg.backup_hour if cfg.backup_hour is not None else 3,
        keep=cfg.backup_keep if cfg.backup_keep is not None else 7,
        include_postgis=bool(cfg.backup_include_postgis),
        include_objects=bool(cfg.backup_include_objects),
        include_state=bool(cfg.backup_include_state),
        secret_set=bool(cfg.backup_secret_key),      # so the UI can say "stored" without leaking it
    )


@router.get("/settings", response_model=BackupSettingsOut)
async def get_settings_(_: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    return _out(await _config(db))


@router.put("/settings", response_model=BackupSettingsOut)
async def save_settings(body: BackupSettingsIn, user: User = Depends(require_admin),
                        db: AsyncSession = Depends(get_db)):
    cfg = await _config(db)
    cfg.backup_enabled = body.enabled
    cfg.backup_endpoint = (body.endpoint or "").strip() or None
    cfg.backup_bucket = (body.bucket or "").strip() or None
    cfg.backup_prefix = (body.prefix or "geodeploy-backups").strip("/") or "geodeploy-backups"
    cfg.backup_access_key = (body.access_key or "").strip() or None
    # Blank = derive it from the endpoint (bk.infer_region). Region is only a signing input;
    # asking an operator to know their provider's magic string is a bad default.
    cfg.backup_region = ((body.region or "").strip()
                         or bk.infer_region(cfg.backup_endpoint) or "us-east-1")
    cfg.backup_schedule = body.schedule
    cfg.backup_hour = max(0, min(int(body.hour), 23))
    cfg.backup_keep = max(1, min(int(body.keep), 365))
    cfg.backup_include_postgis = body.include_postgis
    cfg.backup_include_objects = body.include_objects
    cfg.backup_include_state = body.include_state
    if body.secret_key:                      # blank = keep what is stored (write-only field)
        cfg.backup_secret_key = body.secret_key
    await db.commit()
    await db.refresh(cfg)
    await record_audit(db, user, "backup.settings", "backup", None,
                       {"enabled": bool(cfg.backup_enabled), "bucket": cfg.backup_bucket,
                        "schedule": cfg.backup_schedule})
    return _out(cfg)


@router.post("/settings/test")
async def test_destination(_: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Prove the destination is reachable, writable, and NOT the live data bucket — before anyone
    relies on it. Surfaces the provider's real error rather than a generic failure."""
    cfg = await _config(db)
    if not cfg.backup_bucket or not cfg.backup_access_key or not cfg.backup_secret_key:
        raise HTTPException(400, "Set the destination bucket and credentials first.")
    try:
        return await run_in_threadpool(bk.verify_destination, cfg)
    except bk.BucketMissing as exc:
        # A STRUCTURED detail for this one case, so the settings page can offer to create the
        # bucket instead of only printing the sentence. `message` carries the same text every other
        # failure sends as a bare string — the UI reads either shape.
        raise HTTPException(400, {"code": "bucket_missing", "bucket": exc.bucket,
                                  "message": str(exc)}) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, f"Could not reach the destination: {exc}") from exc


@router.post("/settings/bucket")
async def create_bucket(user: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Create the configured destination bucket.

    Reachable from the "Test destination" failure, which is the moment the operator has the problem
    and the app already holds credentials the provider has just accepted. Sending them to a
    provider console to type the same name is friction with no safety benefit — the destructive
    direction is deleting a bucket, and that is not offered here.
    """
    cfg = await _config(db)
    if not cfg.backup_bucket or not cfg.backup_access_key or not cfg.backup_secret_key:
        raise HTTPException(400, "Set the destination bucket and credentials first.")
    try:
        result = await run_in_threadpool(bk.create_destination_bucket, cfg)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, f"Could not reach the destination: {exc}") from exc
    await record_audit(db, user, "backup.bucket.create", "backup", None,
                       {"bucket": cfg.backup_bucket, "endpoint": cfg.backup_endpoint})
    return result


@router.get("/runs", response_model=list[BackupRunOut])
async def list_runs(limit: int = 20, _: User = Depends(require_admin),
                    db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(BackupRun).order_by(BackupRun.started_at.desc())
                             .limit(max(1, min(limit, 100))))).scalars().all()
    return [BackupRunOut.model_validate(r) for r in rows]


@router.get("/stored")
async def list_stored(_: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """What is ACTUALLY at the destination, read from the manifests. This is the answer that
    matters — our own run history lives in the state DB, which is one of the things being backed
    up, so it cannot be the source of truth about whether a backup exists."""
    cfg = await _config(db)
    if not cfg.backup_bucket:
        return []
    try:
        return await run_in_threadpool(bk.list_runs, cfg)
    except Exception as exc:
        raise HTTPException(502, f"Could not list the destination: {exc}") from exc


@router.post("/run", response_model=BackupRunOut, status_code=202)
async def start_backup(user: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    cfg = await _config(db)
    if not cfg.backup_enabled or not cfg.backup_bucket or not cfg.backup_secret_key:
        raise HTTPException(400, "Configure and enable a backup destination first.")
    busy = (await db.execute(select(BackupRun).where(BackupRun.status == "running")
                             .limit(1))).scalar_one_or_none()
    if busy:
        raise HTTPException(409, "A backup is already running.")

    run = BackupRun(key=bk.run_key(cfg.backup_prefix), status="running", trigger="manual",
                    current_step="Queued", progress=0)
    db.add(run)
    await db.commit()
    await db.refresh(run)
    await record_audit(db, user, "backup.start", "backup", str(run.id), {"key": run.key})

    from ..tasks.backup import run_backup
    run_backup.delay(run.id, "manual")
    return BackupRunOut.model_validate(run)


@router.delete("/stored/{key:path}", status_code=204)
async def delete_stored(key: str, user: User = Depends(require_admin),
                        db: AsyncSession = Depends(get_db)):
    """Delete one stored backup. `services.backup.delete_run` refuses any key outside the
    configured prefix, so this cannot be walked into the rest of the bucket."""
    cfg = await _config(db)
    try:
        removed = await run_in_threadpool(bk.delete_run, cfg, key)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, f"Could not delete: {exc}") from exc
    await record_audit(db, user, "backup.delete", "backup", None,
                       {"key": key, "objects_removed": removed})


# ── Restore ──────────────────────────────────────────────────────────────────────────────────
# OWNER-ONLY, not admin-only. Restoring replaces the database and the files: it is the one action
# that can destroy an instance, and unlike everything else here it cannot be undone by re-running
# it. `require_owner` also rejects API tokens, so it is browser-only by construction.

@router.get("/stored/{key:path}/preflight")
async def restore_preflight(key: str, _: User = Depends(require_owner),
                            db: AsyncSession = Depends(get_db)):
    """What this restore would do — shown BEFORE the confirmation box.

    Returns the manifest, the encryption-key verdict, and what currently exists. The key check is
    the one that surprises people: everything restores, but with a different GEODEPLOY_SECRET_KEY
    the stored SMTP/OIDC/backup credentials become unreadable.
    """
    cfg = await _config(db)
    if not cfg.backup_bucket:
        raise HTTPException(400, "No backup destination configured.")
    try:
        manifest = await run_in_threadpool(rs.read_manifest, cfg, key)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, f"Could not read the backup: {exc}") from exc

    from ..models import Portal, RasterLayer, VectorLayer
    current = {
        "vector_layers": await db.scalar(select(func.count()).select_from(VectorLayer)) or 0,
        "raster_layers": await db.scalar(select(func.count()).select_from(RasterLayer)) or 0,
        "portals": await db.scalar(select(func.count()).select_from(Portal)) or 0,
        "users": await db.scalar(select(func.count()).select_from(User)) or 0,
    }
    return {
        "key": key,
        "name": key.rsplit("/", 1)[-1],
        "manifest": manifest,
        "secret_key": rs.check_secret_key_match(manifest),
        "current": current,
        "is_empty": all(v == 0 for k, v in current.items() if k != "users"),
    }


@router.post("/restore", response_model=RestoreRunOut, status_code=202)
async def start_restore(body: RestoreRequest, user: User = Depends(require_owner),
                        db: AsyncSession = Depends(get_db)):
    """Start a restore. Guarded three ways, deliberately:
    owner-only · the backup's name must be typed back · no concurrent run."""
    cfg = await _config(db)
    if not cfg.backup_bucket or not cfg.backup_secret_key:
        raise HTTPException(400, "No backup destination configured.")

    expected = body.key.rsplit("/", 1)[-1]
    if (body.confirm_name or "").strip() != expected:
        raise HTTPException(400, f"Type the backup name exactly ({expected}) to confirm.")

    # A manifest read also proves the backup is complete — a half-written run must never restore.
    try:
        await run_in_threadpool(rs.read_manifest, cfg, body.key)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    for model, label in ((RestoreRun, "restore"), (BackupRun, "backup")):
        busy = (await db.execute(select(model).where(model.status == "running")
                                 .limit(1))).scalar_one_or_none()
        if busy:
            raise HTTPException(409, f"A {label} is already running.")

    run = RestoreRun(key=body.key, status="running", confirmed_by=user.email,
                     current_step="Queued", progress=0)
    db.add(run)
    await db.commit()
    await db.refresh(run)
    await record_audit(db, user, "backup.restore", "backup", str(run.id),
                       {"key": body.key})

    from ..tasks.restore import run_restore
    run_restore.delay(run.id, body.key)
    return RestoreRunOut.model_validate(run)


@router.get("/restore/runs", response_model=list[RestoreRunOut])
async def list_restore_runs(limit: int = 10, _: User = Depends(require_admin),
                            db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(RestoreRun).order_by(RestoreRun.started_at.desc())
                             .limit(max(1, min(limit, 50))))).scalars().all()
    return [RestoreRunOut.model_validate(r) for r in rows]
