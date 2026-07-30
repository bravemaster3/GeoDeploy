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
            "This backup was taken with a DIFFERENT GEODEPLOY_SECRET_KEY. Everything restores, but "
            "the encrypted settings (SMTP password, OIDC client secret, backup destination key) "
            "will be unreadable and must be re-entered. To avoid that, copy the old key into .env "
            "before restoring."}


def download(cfg, key: str, name: str, dest_path: str) -> int:
    s3 = bk.make_client(cfg.backup_endpoint, cfg.backup_access_key, cfg.backup_secret_key,
                        cfg.backup_region or bk.infer_region(cfg.backup_endpoint))
    s3.download_file(cfg.backup_bucket, f"{key}/{name}", dest_path)
    return os.path.getsize(dest_path)


def restore_database(dump_path: str) -> dict:
    """`pg_restore --clean --if-exists` over the live database.

    `--clean` is what makes this a RESTORE rather than a merge: without it, rows from the backup
    collide with existing ones and you get a half-merged database that looks fine until a
    duplicate key surfaces. `--if-exists` keeps it quiet on a fresh install where there is nothing
    to drop. `--no-owner/--no-acl` because the dump may come from an install whose DB role differs.
    """
    settings = get_settings()
    env = dict(os.environ, PGPASSWORD=settings.postgis_password or "")
    cmd = ["pg_restore", "-h", settings.postgis_host or "postgres",
           "-p", str(settings.postgis_port or 5432),
           "-U", settings.postgis_user or "geodeploy",
           "-d", settings.postgis_db or "geodeploy",
           "--clean", "--if-exists", "--no-owner", "--no-acl", dump_path]
    proc = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=6 * 3600)
    # pg_restore exits non-zero for benign "does not exist, skipping" noise under --clean, so a
    # failure is judged on stderr content, not the code alone.
    err = (proc.stderr or "").strip()
    fatal = [l for l in err.splitlines() if "error:" in l.lower()
             and "does not exist" not in l.lower()]
    if fatal:
        raise RuntimeError("pg_restore failed: " + "; ".join(fatal[:5]))
    return {"warnings": len(err.splitlines()), "fatal": 0}


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
