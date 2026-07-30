"""Backups — copy everything that cannot be regenerated to a SEPARATE object store.

Design rules, in order of importance:

1. **A different destination.** Backing up into the same bucket (or the same disk) protects against
   nothing that actually happens: a deleted bucket, revoked credentials, a wiped volume. The
   destination has its OWN endpoint/credentials (`SetupConfig.backup_*`), and `verify_destination`
   refuses one that points at the live data bucket.
2. **Server-side copy for objects.** Rasters and GeoParquet are the bulk of an instance and can be
   hundreds of GB. When source and destination are the same S3 provider we `copy_object`, so the
   bytes never travel through this container. Cross-provider falls back to streaming.
3. **A consistent state snapshot.** The SQLite state DB is being written while the backup runs, so
   it is copied with SQLite's own online-backup API (via `VACUUM INTO`), never a file copy — a
   half-written page set restores as a corrupt database.
4. **Every run is recorded**, success or failure, with a `manifest.json` inventory. A backup you
   cannot inspect is a backup you do not have.

What is included: PostGIS (`pg_dump`, custom format), object storage (everything under the bucket),
the state DB, and `portal_assets` (About-page uploads — NOT regenerable, unlike portal bundles,
which a re-publish recreates from the DB).

RESTORE IS DELIBERATELY NOT ONE-CLICK — see `docs/backups.md`. Restoring over a live instance is
how people lose the data they were trying to protect; the manifest + documented procedure is the
supported path.
"""
import io
import json
import os
import subprocess
import tempfile
from datetime import datetime, timezone

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from ..config import get_settings

MANIFEST = "manifest.json"


def make_client(endpoint: str | None, access_key: str, secret_key: str, region: str | None):
    """A client for the BACKUP destination. `endpoint=None` means AWS proper (boto picks the
    regional endpoint); anything else is an S3-compatible provider (R2, B2, Hetzner, MinIO)."""
    return boto3.client(
        "s3",
        endpoint_url=endpoint or None,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name=region or "us-east-1",
        config=Config(signature_version="s3v4", retries={"max_attempts": 5, "mode": "standard"}),
    )


def _norm(endpoint: str | None) -> str:
    return (endpoint or "").rstrip("/").lower()


def verify_destination(cfg) -> dict:
    """Check the destination is reachable, writable, and NOT the live data bucket. Returns a small
    report for the settings page; raises ValueError with a user-facing message on refusal."""
    settings = get_settings()
    if _norm(cfg.backup_endpoint) == _norm(settings.storage_endpoint) \
            and (cfg.backup_bucket or "") == (settings.storage_bucket or ""):
        raise ValueError(
            "The backup destination is the same endpoint AND bucket as your live data. "
            "A copy that dies with the original is not a backup — use a different bucket, "
            "ideally a different provider.")
    s3 = make_client(cfg.backup_endpoint, cfg.backup_access_key, cfg.backup_secret_key,
                     cfg.backup_region)
    probe = f"{(cfg.backup_prefix or 'geodeploy-backups').strip('/')}/.geodeploy-write-test"
    try:
        s3.put_object(Bucket=cfg.backup_bucket, Key=probe, Body=b"ok")
        s3.delete_object(Bucket=cfg.backup_bucket, Key=probe)
    except ClientError as exc:
        raise ValueError(f"Could not write to the destination bucket: "
                         f"{exc.response.get('Error', {}).get('Message', exc)}") from exc
    return {"ok": True, "bucket": cfg.backup_bucket, "prefix": cfg.backup_prefix}


def run_key(prefix: str | None, when: datetime | None = None) -> str:
    when = when or datetime.now(timezone.utc)
    return f"{(prefix or 'geodeploy-backups').strip('/')}/{when.strftime('%Y-%m-%dT%H-%M-%SZ')}"


# ── the pieces ───────────────────────────────────────────────────────────────────────────────

def dump_postgis(dest_path: str) -> dict:
    """`pg_dump -Fc` of the whole database (every schema, including each member's
    `geodeploy_u*`). Custom format so a restore can be selective and parallel."""
    settings = get_settings()
    env = dict(os.environ, PGPASSWORD=settings.postgis_password or "")
    cmd = ["pg_dump", "-h", settings.postgis_host or "postgres",
           "-p", str(settings.postgis_port or 5432),
           "-U", settings.postgis_user or "geodeploy",
           "-d", settings.postgis_db or "geodeploy",
           "-Fc", "--no-owner", "--no-acl", "-f", dest_path]
    proc = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=6 * 3600)
    if proc.returncode != 0:
        raise RuntimeError(f"pg_dump failed: {(proc.stderr or '').strip()[:500]}")
    return {"bytes": os.path.getsize(dest_path)}


def snapshot_state_db(dest_path: str) -> dict:
    """A CONSISTENT copy of the state DB. `VACUUM INTO` takes SQLite's own read lock and writes a
    complete, compacted database — a plain file copy of a live DB (especially in WAL mode, where
    recent commits sit in the -wal sidecar) can restore corrupt or missing the newest rows."""
    import sqlite3
    settings = get_settings()
    src = f"{settings.data_dir}/sqlite/geodeploy.db"
    if os.path.exists(dest_path):
        os.unlink(dest_path)            # VACUUM INTO refuses to overwrite
    with sqlite3.connect(src, timeout=60) as conn:
        conn.execute("VACUUM INTO ?", (dest_path,))
    return {"bytes": os.path.getsize(dest_path)}


def archive_dir(src_dir: str, dest_path: str) -> dict:
    """tar.gz a directory (portal_assets). Missing dir → an empty archive, not an error."""
    import tarfile
    count = 0
    with tarfile.open(dest_path, "w:gz") as tar:
        if os.path.isdir(src_dir):
            for root, _dirs, files in os.walk(src_dir):
                for f in files:
                    full = os.path.join(root, f)
                    tar.add(full, arcname=os.path.relpath(full, src_dir))
                    count += 1
    return {"bytes": os.path.getsize(dest_path), "files": count}


def copy_objects(dest_s3, cfg, key_prefix: str, on_progress=None) -> dict:
    """Copy every object from the live data bucket into `{key_prefix}/objects/`.

    Same provider → `copy_object` (server-side; the bytes never enter this process, which is what
    makes a 200 GB instance feasible on a small VPS). Different provider → stream through.
    """
    from .minio import get_s3_client
    settings = get_settings()
    src = get_s3_client()
    same_host = _norm(cfg.backup_endpoint) == _norm(settings.storage_endpoint)

    total = copied = 0
    paginator = src.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=settings.storage_bucket):
        for obj in page.get("Contents", []) or []:
            key, size = obj["Key"], obj.get("Size", 0)
            target = f"{key_prefix}/objects/{key}"
            if same_host:
                dest_s3.copy_object(Bucket=cfg.backup_bucket, Key=target,
                                    CopySource={"Bucket": settings.storage_bucket, "Key": key})
            else:
                body = src.get_object(Bucket=settings.storage_bucket, Key=key)["Body"]
                dest_s3.upload_fileobj(body, cfg.backup_bucket, target)
            copied += 1
            total += size
            if on_progress and copied % 25 == 0:
                on_progress(copied, total)
    return {"objects": copied, "bytes": total, "server_side": same_host}


def upload_file(dest_s3, bucket: str, key: str, path: str) -> None:
    dest_s3.upload_file(path, bucket, key)


def write_manifest(dest_s3, bucket: str, key_prefix: str, manifest: dict) -> None:
    dest_s3.put_object(Bucket=bucket, Key=f"{key_prefix}/{MANIFEST}",
                       Body=json.dumps(manifest, indent=2).encode(),
                       ContentType="application/json")


# ── retention + listing ──────────────────────────────────────────────────────────────────────

def list_runs(cfg) -> list[dict]:
    """Backups actually PRESENT at the destination, read from their manifests — the source of
    truth, independent of our own DB (which is itself one of the things being backed up)."""
    s3 = make_client(cfg.backup_endpoint, cfg.backup_access_key, cfg.backup_secret_key,
                     cfg.backup_region)
    prefix = (cfg.backup_prefix or "geodeploy-backups").strip("/") + "/"
    out = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=cfg.backup_bucket, Prefix=prefix, Delimiter="/"):
        for cp in page.get("CommonPrefixes", []) or []:
            key = cp["Prefix"].rstrip("/")
            entry = {"key": key, "name": key.rsplit("/", 1)[-1]}
            try:
                body = s3.get_object(Bucket=cfg.backup_bucket, Key=f"{key}/{MANIFEST}")["Body"]
                entry["manifest"] = json.loads(body.read())
            except ClientError:
                entry["manifest"] = None      # interrupted run — listed so it can be cleaned up
            out.append(entry)
    out.sort(key=lambda e: e["name"], reverse=True)   # keys are timestamps → newest first
    return out


def delete_run(cfg, key: str) -> int:
    """Delete one backup (all objects under its prefix). Guarded to the configured prefix so a
    crafted key can't reach the rest of the bucket."""
    root = (cfg.backup_prefix or "geodeploy-backups").strip("/")
    if not key.startswith(root + "/") or ".." in key:
        raise ValueError("Refusing to delete outside the backup prefix.")
    s3 = make_client(cfg.backup_endpoint, cfg.backup_access_key, cfg.backup_secret_key,
                     cfg.backup_region)
    removed = 0
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=cfg.backup_bucket, Prefix=key + "/"):
        batch = [{"Key": o["Key"]} for o in page.get("Contents", []) or []]
        if batch:
            s3.delete_objects(Bucket=cfg.backup_bucket, Delete={"Objects": batch})
            removed += len(batch)
    return removed


def prune(cfg, keep: int) -> list[str]:
    """Keep the newest `keep` COMPLETE backups; delete older ones. An incomplete run (no manifest)
    is never counted as one of the kept copies — otherwise a string of failures would silently age
    out every good backup."""
    if keep <= 0:
        return []
    runs = list_runs(cfg)
    complete = [r for r in runs if r.get("manifest")]
    doomed = complete[keep:]
    for r in doomed:
        delete_run(cfg, r["key"])
    return [r["key"] for r in doomed]
