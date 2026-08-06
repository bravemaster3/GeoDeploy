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


def _finish(run_id: int, status: str, seed: dict | None = None, **fields) -> None:
    """Record the final status — RE-INSERTING the row if the restore removed it.

    A restore replaces the database this row lives in, and the snapshot predates the restore, so
    after the database step the row is simply gone. The UPDATE then matched nothing and the result
    was never recorded anywhere: the restore that just ran did not appear in its own history, and
    the UI had nothing to poll to learn whether it worked. `seed` is the row as it was BEFORE the
    database was replaced, captured at the start of the task for exactly this.
    """
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    sets = ["status = ?", "finished_at = ?"]
    vals = [status, now]
    for k, v in fields.items():
        sets.append(f"{k} = ?")
        vals.append(v)
    vals.append(run_id)
    try:
        with state_db.connect() as conn:
            cur = conn.execute(f"UPDATE restore_runs SET {', '.join(sets)} WHERE id = ?", vals)
            if cur.rowcount:
                return
            if not seed:
                logger.warning("restore %s: row is gone and no seed was captured", run_id)
                return
            row = {"id": run_id, "key": seed.get("key"), "confirmed_by": seed.get("confirmed_by"),
                   "started_at": seed.get("started_at") or now, "status": status,
                   "finished_at": now, **fields}
            cols = [c for c in ("id", "key", "status", "confirmed_by", "started_at", "finished_at",
                                "error_message", "current_step", "progress", "detail")
                    if c in row]
            conn.execute(
                f"INSERT INTO restore_runs ({', '.join(cols)}) "
                f"VALUES ({', '.join('?' for _ in cols)})", [row[c] for c in cols])
            # The id was forced rather than drawn from the sequence, and the sequence came from the
            # snapshot — so without this the NEXT restore collides on the primary key.
            conn.execute("SELECT setval(pg_get_serial_sequence('restore_runs', 'id'), "
                         "GREATEST((SELECT MAX(id) FROM restore_runs), 1))")
            logger.info("restore %s: re-inserted its run row after the database was replaced", run_id)
    except Exception:
        logger.exception("restore %s: could not record final status %s", run_id, status)


def restore_snapshot(cfg, key: str, manifest: dict, tmp_dir: str, on_step=None) -> dict:
    """Put a snapshot back: objects, portal assets, database, then the post-database repairs.

    THE single implementation. The demo reset previously had its own copy of this and every
    divergence was a bug: it downloaded `database.dump` (the file is `postgis.dump`), passed the
    composed path as the `key` argument so the request became `<key>/database.dump/database.dump`,
    never restored portal assets at all, and restored the database BEFORE the objects — the exact
    ordering this module's header explains is wrong. It failed with a 404 at the top of every hour.

    Ordering is not arbitrary; see the module docstring. Objects first, database last, repairs after.
    """
    # Imported here, not at module scope: `services.restore` pulls in boto3 and the settings, and
    # this module is imported by celery_app at worker start.
    from ..services import restore as rs

    step = on_step or (lambda *_a, **_k: None)
    parts = manifest.get("parts", {})
    detail: dict = {}
    # BEFORE anything is replaced: these are the credentials that just read the snapshot.
    saved_destination = _capture_backup_destination(cfg)

    if "objects" in parts:
        step("Restoring files", 15)
        detail["objects"] = rs.restore_objects(
            cfg, key, on_progress=lambda n, _b: step(f"Restoring files ({n:,} objects)", 15))

    if "portal_assets" in parts:
        step("Restoring portal assets", 60)
        path = os.path.join(tmp_dir, "portal_assets.tar.gz")
        rs.download(cfg, key, "portal_assets.tar.gz", path)
        detail["portal_assets"] = rs.restore_portal_assets(path)
        os.unlink(path)

    # DATABASE LAST. Everything after this point talks to a replaced database.
    if "postgis" in parts:
        step("Downloading database dump", 70)
        path = os.path.join(tmp_dir, "postgis.dump")
        rs.download(cfg, key, "postgis.dump", path)
        step("Restoring database", 80)
        detail["database"] = rs.restore_database(path)
        os.unlink(path)

        # Repair what replacing the database broke — the schema is now the SNAPSHOT'S, and its
        # in-flight rows are frozen. Inside this function so no caller can forget them.
        # FIRST, before anything reads setup_config: the row now describes the snapshot's instance.
        detail["own_db"] = _restore_own_db_settings()
        detail["own_storage"] = _restore_own_storage_settings()
        detail["schema"] = _reapply_schema_migrations()
        detail["stale_runs_cleared"] = _clear_restored_running_rows()
        # Keep the destination we just read the backup FROM, when the restored copy is unreadable.
        detail["backup_destination"] = _restore_backup_destination(saved_destination)

        # Rebuild the DERIVED state that lives outside the database and is therefore not in the
        # snapshot: Martin's table list and the published portal bundles on disk. Both are here, not
        # in `run_restore`, for the same reason as the repairs above — the demo reset calls this
        # function too, and it had neither. A demo visitor could delete a portal (which rmtree's
        # data/portals/<slug>) and the hourly reset brought the ROW back with no bundle behind it:
        # the portal was listed, said published, and opened BLANK until somebody pressed Publish.
        # Blank, not 404 — nginx serves portals with `try_files $uri $uri/index.html @spa`, so a
        # missing bundle falls through to the dashboard SPA, which has no route for a public portal
        # URL. The visitor gets 200 and an empty page, and nothing anywhere logs an error.
        step("Rebuilding tile configuration", 95)
        try:
            _regenerate_martin()
            detail["martin"] = "reloaded"
        except Exception as exc:      # noqa: BLE001 — the data is back; Settings → Reload Martin fixes this
            logger.warning("restore: Martin regeneration failed: %s", exc)
            detail["martin"] = f"failed: {exc}"
        # AFTER Martin: a bundle's style points at tile URLs Martin should already be serving. In its
        # OWN try, though — a failed Martin reload must not skip the republish, since a portal with
        # no bundle at all (a blank page) is worse than one whose tiles arrive a reload later.
        step("Republishing portals", 97)
        detail["portals"] = _republish_portals()

    return detail


BACKUP_DEST_FIELDS = ("backup_endpoint", "backup_bucket", "backup_prefix",
                      "backup_access_key", "backup_secret_key", "backup_region")

#: How THIS instance reaches its own database. Never taken from a snapshot — see below.
OWN_DB_FIELDS = ("postgis_type", "postgis_host", "postgis_port", "postgis_db",
                 "postgis_user", "postgis_password")

#: How THIS instance reaches its own object storage. Same reasoning as OWN_DB_FIELDS.
OWN_STORAGE_FIELDS = ("storage_type", "storage_endpoint", "storage_bucket",
                      "storage_access_key", "storage_secret_key", "storage_region")


def _restore_own_db_settings() -> dict:
    """Put this instance's OWN database coordinates back into setup_config after a restore.

    `setup_config` holds how the instance reaches its database, and a restore replaces that row with
    the snapshot's — which describes the database of whatever instance took the backup, with the
    password encrypted under THAT instance's GEODEPLOY_SECRET_KEY.

    Two ways this hurts, and the second is silent:

      * different host/port/db → anything reading setup_config aims at another server entirely.
      * same host, different key → `decrypt_secret` cannot decrypt it and returns the CIPHERTEXT
        unchanged (it cannot distinguish failure from legacy plaintext). `services/martin._pg_creds`
        reads from here, so martin-config.yaml gets a Fernet blob as its password and Martin
        crash-loops with `password authentication failed for user "geodeploy"` — vector tiles gone,
        portals rendering basemap only.

    The running process is, by definition, connected to the right database with the right password.
    So these fields are ALWAYS restored from the live configuration, not merely when unreadable:
    a snapshot's copy is never authoritative about where THIS instance's database is.
    """
    from ..config import get_settings
    from ..crypto import encrypt_secret

    settings = get_settings()
    if not settings.postgis_password:
        return {"kept": False, "reason": "no live password to write"}
    values = {
        "postgis_type": "local" if (settings.postgis_host or "").startswith("geodeploy-")
                        else "external",
        "postgis_host": settings.postgis_host,
        "postgis_port": int(settings.postgis_port or 5432),
        "postgis_db": settings.postgis_db,
        "postgis_user": settings.postgis_user,
        "postgis_password": encrypt_secret(settings.postgis_password),
    }
    try:
        with state_db.connect() as conn:
            sets = ", ".join(f"{f} = ?" for f in OWN_DB_FIELDS)
            conn.execute(f"UPDATE setup_config SET {sets} WHERE id = 1",
                         [values[f] for f in OWN_DB_FIELDS])
        return {"kept": True, "host": settings.postgis_host, "db": settings.postgis_db}
    except Exception as exc:      # never fail a good restore over bookkeeping
        logger.warning("restore: could not restore own DB settings: %s", exc)
        return {"kept": False, "error": str(exc)[:200]}


def _restore_own_storage_settings() -> dict:
    """Put this instance's OWN object-storage coordinates back into setup_config after a restore.

    Exactly the `_restore_own_db_settings` argument, one row over. A restore replaces `setup_config`
    with the snapshot's, whose storage block describes the bucket and keys of whatever instance took
    the backup — with `storage_secret_key` encrypted under THAT instance's GEODEPLOY_SECRET_KEY.

    The running process is, by definition, using the credentials in its own environment: the restore
    just READ the snapshot and WROTE every object back through them. `.env` never changed. So the
    live values are authoritative and the snapshot's copy never is.

    Two things went wrong without this, and neither announced itself:

      * Settings → Infrastructure → Connection details showed the snapshot's access key, or the raw
        Fernet ciphertext of its secret when the keys differ (`decrypt_secret` returns the input
        unchanged when it cannot decrypt — it cannot tell failure from legacy plaintext). The
        operator compares it with `.env`, finds a different value, and has no way to tell which one
        the instance is actually using. That is GitHub issue #2.
      * `tasks/raster_ingest._get_storage_creds` reads these columns FIRST and only falls back to
        the environment, so raster ingest starts signing S3 requests with the snapshot's keys.
    """
    from ..config import get_settings

    settings = get_settings()
    if not settings.storage_access_key:
        # Nothing trustworthy to write. Leave the restored row alone rather than blanking a working
        # configuration — an instance with no storage in its environment has nothing better to offer.
        return {"kept": False, "reason": "no live storage credentials"}
    values = {
        "storage_type": settings.storage_type or None,
        "storage_endpoint": settings.storage_endpoint,
        "storage_bucket": settings.storage_bucket,
        "storage_access_key": settings.storage_access_key,
        # Encrypted at rest, and the worker writes the column RAW (no ORM type), so encrypt here.
        "storage_secret_key": _encrypt(settings.storage_secret_key),
        "storage_region": settings.storage_region or "us-east-1",
    }
    fields = [f for f in OWN_STORAGE_FIELDS if values[f] is not None]
    try:
        with state_db.connect() as conn:
            sets = ", ".join(f"{f} = ?" for f in fields)
            conn.execute(f"UPDATE setup_config SET {sets} WHERE id = 1", [values[f] for f in fields])
        return {"kept": True, "bucket": settings.storage_bucket,
                "endpoint": settings.storage_endpoint}
    except Exception as exc:      # never fail a good restore over bookkeeping
        logger.warning("restore: could not restore own storage settings: %s", exc)
        return {"kept": False, "error": str(exc)[:200]}


def _encrypt(value: str | None) -> str | None:
    from ..crypto import encrypt_secret
    return encrypt_secret(value) if value else value


def _capture_backup_destination(cfg) -> dict:
    """The destination settings THIS instance is using, taken before the database is replaced."""
    return {f: getattr(cfg, f, None) for f in BACKUP_DEST_FIELDS}


def _restore_backup_destination(saved: dict) -> dict:
    """Put the working destination settings back after the database step.

    A restore replaces `setup_config` wholesale, so the backup destination comes back as the SNAPSHOT
    held it — encrypted with whatever key the instance that took the backup was using. On any
    instance with a different GEODEPLOY_SECRET_KEY that value cannot be decrypted, and
    `decrypt_secret` returns the ciphertext rather than raising, so the next backup signs its S3
    request with a Fernet blob and fails as `SignatureDoesNotMatch` — blaming the credentials rather
    than the key.

    The operator proved these credentials seconds ago: the restore could not have read the backup
    without them. Throwing them away and asking for them again after every restore is a loop, and
    the failure at the end of it names the wrong cause.

    Only applied when the restored value is UNREADABLE. A snapshot whose secret decrypts here was
    written by an instance sharing this key, and its settings are as valid as ours.
    """
    from ..crypto import encrypt_secret, looks_encrypted

    result = {"kept": False}
    try:
        with state_db.connect() as conn:
            conn.row_factory = state_db.dict_row
            row = conn.execute(
                "SELECT backup_secret_key FROM setup_config WHERE id = 1").fetchone()
            restored_secret = (row or {}).get("backup_secret_key")
            # The worker reads the column RAW (no ORM), so decrypt before judging it.
            from ..crypto import decrypt_secret
            if not looks_encrypted(decrypt_secret(restored_secret)):
                return {"kept": False, "reason": "restored destination is readable"}

            if not saved.get("backup_secret_key"):
                return {"kept": False, "reason": "no working destination to keep"}

            sets = ", ".join(f"{f} = ?" for f in BACKUP_DEST_FIELDS)
            vals = [encrypt_secret(saved[f]) if f == "backup_secret_key" else saved[f]
                    for f in BACKUP_DEST_FIELDS]
            conn.execute(f"UPDATE setup_config SET {sets} WHERE id = 1", vals)
        result = {"kept": True, "bucket": saved.get("backup_bucket")}
    except Exception as exc:      # never fail a good restore over bookkeeping
        logger.warning("restore: could not keep the backup destination settings: %s", exc)
        result = {"kept": False, "error": str(exc)[:200]}
    return result


def _reapply_schema_migrations() -> dict:
    """Re-apply the additive column migrations over the just-restored schema.

    `pg_restore --clean` DROPS and recreates every table from the dump, so the schema becomes the
    SNAPSHOT'S schema — every column added by a release after that backup is gone. The API applies
    these at startup and nothing re-applies them mid-run, so an instance stayed on the old schema
    until someone happened to restart it.

    That is not theoretical. Restoring a backup taken before `portals.thumbnail_url` existed removed
    the column from a running instance: publishing a portal succeeded, recording its thumbnail
    failed, and portal cards silently lost their images. The BIGINT widening of `size_bytes` reverts
    the same way, bringing back the 'integer out of range' that broke backups over 2.1 GB.

    Every statement is `IF NOT EXISTS` or guarded, so this is a no-op when the snapshot is current.
    """
    from ..schema_migrations import PG_MIGRATIONS

    applied, failed = 0, []
    for stmt in PG_MIGRATIONS:
        try:
            with state_db.connect() as conn:
                conn.execute(stmt)
            applied += 1
        except Exception as exc:      # one bad statement must not stop the rest
            logger.warning("restore: schema migration failed (%s): %s",
                           stmt.strip().splitlines()[0], exc)
            failed.append(str(exc)[:200])
    return {"applied": applied, "failed": failed}


def _reaudit_restore(key: str, seed: dict | None, status: str, detail: dict) -> None:
    """Write the audit entry for THIS restore back into the restored database.

    `POST /backups/restore` already audits `backup.restore` — and then the restore replaces the
    database, and the snapshot does not contain that entry. So the log lost the record of the single
    most destructive operation in the product, which is precisely the one an operator needs to find
    later ("why is last week's data back?"). The Activity page showed nothing at all.

    Append-only means append: this adds an entry describing what just finished, rather than trying to
    resurrect the pre-restore row (whose id belonged to a table that no longer exists).
    """
    try:
        with state_db.connect() as conn:
            conn.execute(
                "INSERT INTO audit_log (actor_id, actor_name, action, resource_type, resource_id, "
                "detail, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (None, (seed or {}).get("confirmed_by") or "unknown",
                 "backup.restore.finished", "backup", key,
                 json.dumps({"status": status, "key": key,
                             "database": (detail or {}).get("database"),
                             "objects": (detail or {}).get("objects"),
                             "schema": (detail or {}).get("schema")})[:2000],
                 datetime.now(timezone.utc).replace(tzinfo=None)))
    except Exception as exc:      # never fail a good restore over bookkeeping
        logger.warning("restore: could not record the audit entry: %s", exc)


def _clear_restored_running_rows() -> dict:
    """Mark every `running` backup/restore row in the just-restored database as interrupted.

    Safe to be unconditional: this runs immediately after `pg_dump`'s contents replaced the database,
    so every row present came from the snapshot, and nothing recorded in a snapshot is still running
    now. The one row that legitimately IS in flight — this restore — no longer exists here, because
    the snapshot predates it (see `_finish`).
    """
    cleared = {}
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for table in ("backup_runs", "restore_runs"):
        try:
            with state_db.connect() as conn:
                cur = conn.execute(
                    f"UPDATE {table} SET status = ?, finished_at = ?, current_step = ?, "
                    f"error_message = ? WHERE status = ?",
                    ("error", now, "Interrupted",
                     "Interrupted — this run was in progress when the restored backup was taken.",
                     "running"))
                cleared[table] = getattr(cur, "rowcount", None)
        except Exception as exc:      # never fail a good restore over bookkeeping
            logger.warning("restore: could not clear stale %s rows: %s", table, exc)
            cleared[table] = f"failed: {exc}"
    return cleared


@celery_app.task(name="geodeploy.tasks.restore.run_restore")
def run_restore(run_id: int, key: str):
    from ..services import backup as bk, restore as rs
    from ..tasks.backup import _load_cfg

    settings = get_settings()

    # Capture the row BEFORE anything is replaced. After the database step it will not exist —
    # the snapshot being restored predates this restore — and `_finish` needs these values to put
    # the record back, so the restore appears in its own history.
    seed = None
    try:
        with state_db.connect() as conn:
            conn.row_factory = state_db.dict_row
            seed = conn.execute(
                "SELECT key, confirmed_by, started_at FROM restore_runs WHERE id = ?",
                (run_id,)).fetchone()
    except Exception:
        logger.warning("restore %s: could not read its own row up front", run_id)

    cfg = _load_cfg()
    if not cfg or not cfg.backup_bucket:
        _finish(run_id, "error", seed=seed, error_message="Backups are not configured.")
        return

    detail = {}
    try:
        _step(run_id, "Reading manifest", 5)
        manifest = rs.read_manifest(cfg, key)
        parts = manifest.get("parts", {})
        detail["key_check"] = rs.check_secret_key_match(manifest)

        with tempfile.TemporaryDirectory(dir=f"{settings.data_dir}/temp") as tmp:
            detail.update(restore_snapshot(
                cfg, key, manifest, tmp,
                on_step=lambda text, pct: _step(run_id, text, pct)))

        # Martin's config and the portal bundles are rebuilt INSIDE restore_snapshot (steps 95/97),
        # so the demo reset — which calls it directly — gets them too.

        # The audit entry written when this restore STARTED was destroyed by the restore itself.
        _reaudit_restore(key, seed, "success", detail)
        _finish(run_id, "success", seed=seed, progress=100, current_step="Done",
                detail=json.dumps(detail))
        logger.info("restore of %s complete", key)
    except Exception as exc:
        logger.exception("restore failed")
        # A restore that failed PART WAY still replaced things, so it must appear in the log too.
        _reaudit_restore(key, seed, "error", detail)
        _finish(run_id, "error", seed=seed, error_message=str(exc)[:1000], current_step="Failed",
                detail=json.dumps(detail))


def _republish_portals() -> dict:
    """Rebuild the bundle of every PUBLISHED portal.

    Portal bundles (`data/portals/`) are deliberately not in a backup: they are derived data — HTML,
    the MapLibre style and the runtime, all regenerated from rows that ARE backed up — and including
    them would inflate every backup with something reproducible.

    The consequence was left to the operator: "re-publish each portal after a restore", in the docs,
    and nowhere in the product. A restored portal whose page opens BLANK reads as data loss, and it is
    exactly the step nobody remembers at 2am. The rows needed are all present by now, so do it here.

    Blank rather than 404 because nginx serves this path as `try_files $uri $uri/index.html @spa`: a
    missing bundle is indistinguishable from a dashboard route, so it falls through to the SPA and
    the visitor gets 200 and an empty page. Nothing errors, nothing is logged — which is why this was
    reported as "the portal is broken" rather than "the file is missing".

    Best-effort per portal: one that fails must not stop the rest, and none of it may fail a restore
    that has already put the data back.
    """
    import asyncio

    async def _run() -> dict:
        # Imported inside: `routers.portals` pulls in a good deal of the app, and this module is
        # imported by celery_app at worker start.
        from sqlalchemy import select

        from .. import database
        from ..models import Portal
        from ..routers.portals import _rebuild_bundle

        database.configure(force=True)          # the database underneath us was just replaced
        rebuilt, failed = 0, []
        async with database.AsyncSessionLocal() as db:
            portals = (await db.execute(
                select(Portal).where(Portal.published.is_(True)))).scalars().all()
            for portal in portals:
                try:
                    await _rebuild_bundle(portal, db)
                    rebuilt += 1
                except Exception as exc:        # noqa: BLE001 — one bad portal is not a failed restore
                    logger.warning("restore: could not republish %s: %s", portal.slug, exc)
                    failed.append(f"{portal.slug}: {str(exc)[:120]}")
        return {"rebuilt": rebuilt, "failed": failed}

    try:
        return asyncio.run(_run())
    except Exception as exc:      # noqa: BLE001
        logger.warning("restore: portal republish pass failed: %s", exc)
        return {"rebuilt": 0, "failed": [str(exc)[:200]]}


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
