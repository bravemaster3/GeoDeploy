"""Restore — put a backup back.

A backup nobody can restore is a guess, so this exists as a real, admin-usable operation rather
than a page of shell commands. It is also the single most destructive thing in the product, and the
design reflects that:

* **It replaces, it does not merge.** `pg_restore --clean` drops and recreates every object it
  owns; objects are synced back over the bucket; `portal_assets` is untarred over the directory.
  Anything created after the backup is gone. The caller must therefore be explicit — see
  `routers/backups.py`, which is owner-only and demands the backup name be typed.
* **It refuses a half-backup.** A run interrupted before its `manifest.json` was written is
  listed (so it can be deleted) but must never be restorable — the artifacts are incomplete and
  restoring them silently produces a broken instance.
* **The secret key is the trap.** `GEODEPLOY_SECRET_KEY` encrypts SMTP/OIDC/backup credentials at
  rest (`crypto.EncryptedText`). Restoring onto an install with a DIFFERENT key leaves those rows
  undecryptable — the data is there and unreadable. `check_secret_key_match` detects it up front
  so the UI can warn instead of the user discovering it weeks later.

Since state moved into PostgreSQL, restore is ONE `pg_restore` (catalog + spatial together)
rather than the old SQLite-file-plus-dump pair, which could never be consistent with each other.
"""
import hashlib
import json
import logging
import os
import subprocess
import tempfile

from botocore.exceptions import ClientError

from ..config import get_settings
from . import backup as bk

logger = logging.getLogger(__name__)

MANIFEST = "manifest.json"


def read_manifest(cfg, key: str) -> dict:
    """The inventory of a stored backup. Raises ValueError when absent — see the module note: a
    run without a manifest never finished, and its artifacts are not a restorable set."""
    s3 = bk.make_client(cfg.backup_endpoint, cfg.backup_access_key, cfg.backup_secret_key,
                        cfg.backup_region or bk.infer_region(cfg.backup_endpoint))
    try:
        body = s3.get_object(Bucket=cfg.backup_bucket, Key=f"{key}/{MANIFEST}")["Body"]
    except ClientError as exc:
        raise ValueError(
            f"No manifest at {key} — that backup did not finish and cannot be restored. "
            "Delete it and use a complete one.") from exc
    return json.loads(body.read())


def _secret_fingerprint(secret: str | None) -> str | None:
    """A comparable, non-reversible marker for the encryption key. Stored in the manifest so a
    restore can tell "same key" from "different key" WITHOUT the backup ever carrying the key."""
    if not secret:
        return None
    return hashlib.sha256(secret.encode()).hexdigest()[:16]


def check_secret_key_match(manifest: dict) -> dict:
    """Would the encrypted settings survive this restore?

    Returns `{known, matches, message}`. `known=False` for backups taken before the fingerprint
    existed — we cannot tell, so the UI must warn rather than reassure.
    """
    stored = (manifest or {}).get("secret_key_fingerprint")
    current = _secret_fingerprint(get_settings().secret_key)
    if not stored:
        return {"known": False, "matches": None, "message":
                "This backup predates encryption-key tracking. If it was taken on a different "
                "install, the stored SMTP/OIDC/backup credentials will not be readable after "
                "restoring and must be re-entered."}
    if stored == current:
        return {"known": True, "matches": True, "message": "Encryption key matches."}
    return {"known": True, "matches": False, "message":
            "This backup was taken with a DIFFERENT GEODEPLOY_SECRET_KEY. Your data — layers, "
            "portals, users, files — restores normally. What does not survive is the handful of "
            "settings stored ENCRYPTED: the SMTP password and the OIDC client secret come back "
            "unreadable and have to be re-entered afterwards. (The backup destination is kept: the "
            "one this restore is reading from is preserved, since it demonstrably works.) "
            "Putting the old key into .env before restoring avoids the re-entry entirely."}


def download(cfg, key: str, name: str, dest_path: str) -> int:
    s3 = bk.make_client(cfg.backup_endpoint, cfg.backup_access_key, cfg.backup_secret_key,
                        cfg.backup_region or bk.infer_region(cfg.backup_endpoint))
    s3.download_file(cfg.backup_bucket, f"{key}/{name}", dest_path)
    return os.path.getsize(dest_path)


#: Lines pg_restore prints under `--clean` that are NOT failures.
#:
#: `cannot drop extension postgis because other objects depend on it` is the one that matters. The
#: dump contains `CREATE EXTENSION postgis`, so `--clean` emits a matching DROP — which Postgres
#: refuses whenever the live database still holds geometry columns, i.e. on ANY instance with a
#: PostGIS vector layer. The refusal is the outcome we want: PostGIS must stay installed, and
#: pg_restore carries on regardless. Treating it as fatal aborted the whole restore.
#:
#: It went unnoticed because a GeoParquet-only instance has no geometry columns, so nothing depends
#: on the extension and the DROP succeeds — the restore path was proven on exactly the instance
#: shape that cannot hit this.
_BENIGN_RESTORE_ERRORS = (
    "does not exist",
    "cannot drop extension",
    "must be owner of extension",       # a managed Postgres refuses the drop for a different reason
    "extension \"postgis\" already exists",
    # KEEPING the extensions has a consequence: the schemas they own (`topology` and `tiger` from
    # postgis_topology and postgis_tiger_geocoder, which the standard postgis image installs) now
    # survive the restore too. The dump still carries their DROP and CREATE, so pg_restore reports
    # that it cannot drop a schema things depend on, and then that the schema already exists. Both
    # are the intended outcome stated as an error — the schema is exactly where it should be.
    "cannot drop schema",
    "must be owner of schema",
)


def _benign(line: str) -> bool:
    low = line.lower()
    if any(marker.lower() in low for marker in _BENIGN_RESTORE_ERRORS):
        return True
    # `schema "tiger" already exists` — tolerated, while a TABLE that already exists is not, because
    # that would mean `--clean` failed to drop something it was supposed to replace.
    return 'already exists' in low and 'schema "' in low


#: Entry types in a `pg_restore -l` table of contents that must never be replayed.
#:
#: A TOC line looks like `2; 3079 16385 EXTENSION - postgis geodeploy`: id, catalog oid, object oid,
#: TYPE, schema, name, owner. We drop the extension itself and any comment attached to one.
_TOC_EXTENSION_TYPES = ("EXTENSION",)


def toc_without_extensions(toc: str) -> str:
    """Strip EXTENSION entries from a `pg_restore -l` listing.

    THE BUG THIS FIXES. The dump contains `CREATE EXTENSION postgis`, so `--clean` emits a matching
    `DROP EXTENSION`. Tables are dropped first, so by the time that DROP runs nothing depends on the
    extension any more and it SUCCEEDS — then `CREATE EXTENSION` builds a new one, and the
    `geometry` type and `gist_geometry_ops_2d` operator family come back with NEW OIDs.

    Every database connection that was open across the restore is then holding PostGIS's
    per-backend operator cache pointed at objects that no longer exist. `COUNT(*)` still works
    because it touches no spatial operator; anything using `&&` or `ST_Intersects` fails with
    `no spatial operator found for 'st_intersects': opfamily N type M` until the process is
    restarted. Observed in production on the demo instance, where the hourly reset restores under a
    live API: two errors an hour apart named two different pairs of OIDs, which is the extension
    being rebuilt underneath the running service.

    `pool_pre_ping` does not help — those connections are alive and healthy, and `SELECT 1` passes.
    The only reliable fix is to stop the OIDs changing at all: a data restore has no business
    replacing the extension. The dump's tables and indexes bind to it by name, so leaving the
    installed one alone is both correct and what every other object in the dump expects.
    """
    kept = []
    for line in toc.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(";"):
            kept.append(line)               # comments and blanks are inert; keep the file readable
            continue
        # `id; catalog oid TYPE ...` — the type is the token after the two numeric oids.
        parts = stripped.split()
        obj_type = parts[3] if len(parts) > 3 else ""
        if obj_type in _TOC_EXTENSION_TYPES:
            continue
        # `COMMENT - EXTENSION postgis` — a comment whose target is an extension.
        if obj_type == "COMMENT" and "EXTENSION" in parts[4:6]:
            continue
        kept.append(line)
    return "\n".join(kept) + "\n"


def _pg_env(settings):
    return dict(os.environ, PGPASSWORD=settings.postgis_password or "")


def _pg_conn_args(settings) -> list:
    return ["-h", settings.postgis_host or "postgres",
            "-p", str(settings.postgis_port or 5432),
            "-U", settings.postgis_user or "geodeploy",
            "-d", settings.postgis_db or "geodeploy"]


def restore_database(dump_path: str) -> dict:
    """`pg_restore --clean --if-exists` over the live database.

    `--clean` is what makes this a RESTORE rather than a merge: without it, rows from the backup
    collide with existing ones and you get a half-merged database that looks fine until a
    duplicate key surfaces. `--if-exists` keeps it quiet on a fresh install where there is nothing
    to drop. `--no-owner/--no-acl` because the dump may come from an install whose DB role differs.
    """
    settings = get_settings()
    env = _pg_env(settings)
    conn = _pg_conn_args(settings)

    # The extension must EXIST before the restore (a fresh instance may have none) and must not be
    # touched BY it (see toc_without_extensions). Failure here is not fatal: a managed Postgres may
    # refuse `CREATE EXTENSION` to a non-superuser, and on any instance that already has PostGIS —
    # which is every instance with a vector layer — the statement is a no-op anyway.
    #
    # OSError as well as a non-zero exit: `psql` is a NEW dependency of this function, and the one
    # thing it must never do is turn a working restore into a failed one. The image pins
    # postgresql-client-16 for pg_dump, so it is there — but a missing binary raises FileNotFoundError
    # from subprocess.run rather than returning a code, and letting that escape would mean an
    # instance that can no longer restore at all. This step is best-effort by design: every instance
    # that has a vector layer already has the extension.
    try:
        ensure = subprocess.run(["psql", *conn, "-v", "ON_ERROR_STOP=0", "-c",
                                 "CREATE EXTENSION IF NOT EXISTS postgis"],
                                env=env, capture_output=True, text=True, timeout=300)
        if ensure.returncode != 0:
            logger.warning("restore: could not ensure the postgis extension (%s) — continuing, the "
                           "database may already have it", (ensure.stderr or "").strip()[:200])
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("restore: could not run psql to ensure the postgis extension (%s) — "
                       "continuing; the database almost certainly already has it", exc)

    # Filter the TOC rather than restoring the dump wholesale, so `--clean` never emits
    # `DROP EXTENSION`. If listing fails for any reason we fall back to the plain restore: a
    # restore that works and may strand open connections beats no restore at all.
    toc_path = None
    try:
        listing = subprocess.run(["pg_restore", "-l", dump_path],
                                 env=env, capture_output=True, text=True, timeout=3600)
    except (OSError, subprocess.SubprocessError) as exc:
        listing = subprocess.CompletedProcess([], 1, "", str(exc))
    if listing.returncode == 0 and listing.stdout.strip():
        fd, toc_path = tempfile.mkstemp(suffix=".toc", prefix="gd-restore-")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(toc_without_extensions(listing.stdout))
    else:
        logger.warning("restore: pg_restore -l failed (%s) — restoring without a TOC filter; open "
                       "connections may need the services restarted afterwards",
                       (listing.stderr or "").strip()[:200])

    cmd = ["pg_restore", *conn, "--clean", "--if-exists", "--no-owner", "--no-acl"]
    if toc_path:
        cmd += ["-L", toc_path]
    cmd.append(dump_path)
    try:
        proc = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=6 * 3600)
    finally:
        if toc_path:
            try:
                os.unlink(toc_path)
            except OSError:
                pass
    # pg_restore exits non-zero for benign "does not exist, skipping" noise under --clean, so a
    # failure is judged on stderr content, not the code alone.
    err = (proc.stderr or "").strip()
    fatal = [l for l in err.splitlines() if "error:" in l.lower()
             and not _benign(l)]
    if fatal:
        raise RuntimeError("pg_restore failed: " + "; ".join(fatal[:5]))
    tolerated = [l for l in err.splitlines() if "error:" in l.lower()]
    return {"warnings": len(err.splitlines()), "fatal": 0, "tolerated": len(tolerated)}


def restore_objects(cfg, key: str, on_progress=None) -> dict:
    """Copy `objects/` back into the live data bucket, preserving key layout.

    Server-side when destination and data live on the same provider — a several-hundred-GB
    instance must not stream through this container. Existing keys are OVERWRITTEN; keys absent
    from the backup are left alone, so this is a restore-over, not a mirror. (A true mirror would
    have to delete, and deleting data the operator may still want is not this function's call.)
    """
    from .minio import get_s3_client
    settings = get_settings()
    src = bk.make_client(cfg.backup_endpoint, cfg.backup_access_key, cfg.backup_secret_key,
                         cfg.backup_region or bk.infer_region(cfg.backup_endpoint))
    dest = get_s3_client()
    same_host = (cfg.backup_endpoint or "").rstrip("/").lower() == \
                (settings.storage_endpoint or "").rstrip("/").lower()

    prefix = f"{key}/objects/"
    copied = total = 0
    for page in src.get_paginator("list_objects_v2").paginate(
            Bucket=cfg.backup_bucket, Prefix=prefix):
        for obj in page.get("Contents", []) or []:
            target = obj["Key"][len(prefix):]
            if not target:
                continue
            if same_host:
                dest.copy_object(Bucket=settings.storage_bucket, Key=target,
                                 CopySource={"Bucket": cfg.backup_bucket, "Key": obj["Key"]})
            else:
                body = src.get_object(Bucket=cfg.backup_bucket, Key=obj["Key"])["Body"]
                dest.upload_fileobj(body, settings.storage_bucket, target)
            copied += 1
            total += obj.get("Size", 0)
            if on_progress and copied % 25 == 0:
                on_progress(copied, total)
    return {"objects": copied, "bytes": total, "server_side": same_host}


def restore_portal_assets(archive_path: str) -> dict:
    """Untar About-page uploads back over `data/portal_assets`.

    Members are checked for path traversal before extraction: the archive is ours, but a restore
    runs as the API user and `tar` happily writes `../../etc/...` if asked. Cheap to verify.
    """
    import tarfile
    settings = get_settings()
    dest = f"{settings.data_dir}/portal_assets"
    os.makedirs(dest, exist_ok=True)
    extracted = 0
    with tarfile.open(archive_path, "r:gz") as tar:
        for member in tar.getmembers():
            name = os.path.normpath(member.name)
            if name.startswith(("/", "..")) or os.path.isabs(name):
                logger.warning("restore: skipping suspicious archive member %r", member.name)
                continue
            tar.extract(member, dest)
            extracted += 1
    return {"files": extracted}
