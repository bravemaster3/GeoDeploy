"""Restore job (Celery).

Runs on the `backup` queue for the same reason backups do: it can take as long as the backup did,
and must not occupy the ingest slots.

**Order matters and is not arbitrary.** Objects go back BEFORE the database. If the database is
restored first and the object copy then fails, the catalog advertises layers whose files are not
there yet — every one of them 404s and the instance looks corrupt. The other way round, a failure
between the steps leaves orphaned objects that nothing references: wasted space, not broken data.
"""
import json
import logging
import os
import tempfile
from datetime import datetime, timezone

from ..celery_app import celery_app
from ..config import get_settings
from .. import state_db

logger = logging.getLogger(__name__)


def _step(run_id: int, step: str, progress: int) -> None:
    try:
        with state_db.connect() as conn:
            conn.execute("UPDATE restore_runs SET current_step = ?, progress = ? WHERE id = ?",
                         (step, progress, run_id))
    except Exception:
        pass       # progress reporting must never fail the restore


def _finish(run_id: int, status: str, **fields) -> None:
    sets = ["status = ?", "finished_at = ?"]
    vals = [status, datetime.now(timezone.utc).replace(tzinfo=None)]
    for k, v in fields.items():
        sets.append(f"{k} = ?")
        vals.append(v)
    vals.append(run_id)
    try:
        with state_db.connect() as conn:
            conn.execute(f"UPDATE restore_runs SET {', '.join(sets)} WHERE id = ?", vals)
    except Exception:
        # The restore just replaced this very database — the row we were updating may no longer
        # exist (it belonged to the pre-restore state). Nothing to do; the log is the record.
        logger.warning("restore %s: could not record final status %s", run_id, status)


@celery_app.task(name="geodeploy.tasks.restore.run_restore")
def run_restore(run_id: int, key: str):
    from ..services import backup as bk, restore as rs
    from ..tasks.backup import _load_cfg

    settings = get_settings()
    cfg = _load_cfg()
    if not cfg or not cfg.backup_bucket:
        _finish(run_id, "error", error_message="Backups are not configured.")
        return

    detail = {}
    try:
        _step(run_id, "Reading manifest", 5)
        manifest = rs.read_manifest(cfg, key)
        parts = manifest.get("parts", {})
        detail["key_check"] = rs.check_secret_key_match(manifest)

        with tempfile.TemporaryDirectory(dir=f"{settings.data_dir}/temp") as tmp:
            # 1. OBJECTS FIRST — see the module note.
            if "objects" in parts:
                _step(run_id, "Restoring files", 15)

                def _progress(n, _b):
                    _step(run_id, f"Restoring files ({n:,} objects)", 15)

                detail["objects"] = rs.restore_objects(cfg, key, on_progress=_progress)

            if "portal_assets" in parts:
                _step(run_id, "Restoring portal assets", 60)
                path = os.path.join(tmp, "portal_assets.tar.gz")
                rs.download(cfg, key, "portal_assets.tar.gz", path)
                detail["portal_assets"] = rs.restore_portal_assets(path)
                os.unlink(path)

            # 2. DATABASE LAST. Everything after this point talks to a replaced database.
            if "postgis" in parts:
                _step(run_id, "Downloading database dump", 70)
                path = os.path.join(tmp, "postgis.dump")
                rs.download(cfg, key, "postgis.dump", path)
                _step(run_id, "Restoring database", 80)
                detail["database"] = rs.restore_database(path)
                os.unlink(path)

        _step(run_id, "Rebuilding tile configuration", 95)
        try:
            _regenerate_martin()
            detail["martin"] = "reloaded"
        except Exception as exc:
            # Not fatal: the data is back, and Settings → Infrastructure → Reload Martin fixes it.
            logger.warning("restore: Martin regeneration failed: %s", exc)
            detail["martin"] = f"failed: {exc}"

        _finish(run_id, "success", progress=100, current_step="Done",
                detail=json.dumps(detail))
        logger.info("restore of %s complete", key)
    except Exception as exc:
        logger.exception("restore failed")
        _finish(run_id, "error", error_message=str(exc)[:1000], current_step="Failed",
                detail=json.dumps(detail))


def _regenerate_martin() -> None:
    """The restored catalog describes different tables than the running Martin knows about, so its
    config must be rebuilt or vector tiles 404 until the next upload."""
    import asyncio

    from ..services import martin as martin_svc

    with state_db.connect() as conn:
        conn.row_factory = state_db.dict_row
        layers = conn.execute(
            "SELECT schema_name, table_name, geometry_column, id_column, crs FROM vector_layers "
            "WHERE status = 'ready' AND storage_backend = 'postgis'").fetchall()
    asyncio.run(martin_svc.regenerate_config([dict(r) for r in layers]))
