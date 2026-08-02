"""Backups — copy everything that cannot be regenerated to a SEPARATE object store.

Design rules, in order of importance:

1. **A different destination.** Backing up into the same bucket (or the same disk) protects against
   nothing that actually happens: a deleted bucket, revoked credentials, a wiped volume. The
   destination has its OWN endpoint/credentials (`SetupConfig.backup_*`), and `verify_destination`
   refuses one that points at the live data bucket.
2. **Server-side copy for objects.** Rasters and GeoParquet are the bulk of an instance and can be
   hundreds of GB. When source and destination are the same S3 provider we `copy_object`, so the
   bytes never travel through this container. Cross-provider falls back to streaming.
3. **One dump, one instant.** Since state moved into PostgreSQL (2026-07-30) `pg_dump` captures
   the catalog, users, portals AND the spatial data in a single consistent snapshot. The old split
   — a SQLite file copy plus a separate PostGIS dump — could not be atomic: a layer created between
   the two ended up in one and not the other.
4. **Every run is recorded**, success or failure, with a `manifest.json` inventory. A backup you
   cannot inspect is a backup you do not have.

What is included: the database (`pg_dump -Fc` — state and spatial together), object storage
(everything under the bucket), and `portal_assets` (About-page uploads — NOT regenerable, unlike
portal bundles, which a re-publish recreates from the DB).

RESTORE lives in `services/restore.py`. It is destructive by nature, so it is owner-gated and
guarded rather than sitting next to "Back up now" — see that module and `docs/backups.md`.
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

# After this long, a run still marked "running" is not running.
#
# A backup row is created as `running` BEFORE pg_dump and set to `success` after, so every backup
# necessarily contains ITSELF frozen as `running`. Restore that snapshot and the instance believes a
# backup is permanently in flight: "Back up now" refuses with 409 forever. The same happens when a
# worker is OOM-killed or the host reboots mid-run — nothing is left alive to write the final status.
#
# 6 hours is chosen to be longer than any plausible run on the hardware this targets while still
# clearing within a working day. Reaping a run that IS somehow still alive is harmless: the task
# writes its own final status when it finishes, which overwrites the reap.
STALE_RUN_HOURS = 6


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


def infer_region(endpoint: str | None) -> str | None:
    """Best-effort region from an S3 endpoint, so nobody has to look it up.

    `region` is part of the SigV4 signature, so client and server must agree on the STRING. AWS
    validates it strictly (a mismatch is `AuthorizationHeaderMalformed`); most S3-compatible
    providers don't validate at all, which is why `us-east-1` works nearly everywhere. The
    exceptions are worth encoding: Cloudflare R2 expects `auto`, and Backblaze/Hetzner put the
    location in the hostname, so it can simply be read off.

    Returns None when nothing is recognisable — the caller keeps its default rather than guessing.
    """
    host = (endpoint or "").strip().lower()
    for scheme in ("https://", "http://"):
        if host.startswith(scheme):
            host = host[len(scheme):]
    host = host.split("/")[0].split(":")[0]
    if not host:
        return None
    parts = host.split(".")
    if host.endswith("r2.cloudflarestorage.com"):
        return "auto"                                   # R2 signs with a literal "auto"
    if host.endswith("amazonaws.com"):
        # s3.eu-central-1.amazonaws.com / s3-eu-central-1.amazonaws.com
        if len(parts) >= 3 and parts[0] == "s3" and parts[1] not in ("amazonaws",):
            return parts[1]
        if parts[0].startswith("s3-"):
            return parts[0][3:]
        return "us-east-1"
    if host.endswith("backblazeb2.com") and len(parts) >= 3:
        return parts[1]                                 # s3.us-west-004.backblazeb2.com
    if host.endswith("wasabisys.com") and len(parts) >= 3 and parts[0] == "s3":
        return parts[1]                                 # s3.eu-central-1.wasabisys.com
    if host.endswith("your-objectstorage.com"):
        # Hetzner: the hostname carries the LOCATION (hel1 / fsn1 / nbg1) but the region string is
        # the NETWORK ZONE, which their console shows as `eu-central` for all three. Verified
        # against a live bucket's Overview page — an earlier version returned the location prefix.
        return "eu-central"
    return None


def _norm(endpoint: str | None) -> str:
    return (endpoint or "").rstrip("/").lower()


class BucketMissing(ValueError):
    """The destination is reachable and the credentials are good — the bucket simply is not there.

    A distinct type because it is the one failure the operator can fix from where they are standing:
    the same credentials that just proved themselves can create it. Everything else
    (`verify_destination` raising plain ValueError) needs them to go and change something.
    """

    def __init__(self, message: str, bucket: str):
        super().__init__(message)
        self.bucket = bucket


def _same_as_live_data(cfg) -> bool:
    """True when the destination IS the live data bucket. Backing up into the bucket being backed up
    protects against nothing, so both `verify_destination` and bucket creation refuse it."""
    settings = get_settings()
    return (_norm(cfg.backup_endpoint) == _norm(settings.storage_endpoint)
            and (cfg.backup_bucket or "") == (settings.storage_bucket or ""))


def verify_destination(cfg) -> dict:
    """Check the destination is reachable, writable, and NOT the live data bucket. Returns a small
    report for the settings page; raises ValueError with a user-facing message on refusal."""
    if _same_as_live_data(cfg):
        raise ValueError(
            "The backup destination is the same endpoint AND bucket as your live data. "
            "A copy that dies with the original is not a backup — use a different bucket, "
            "ideally a different provider.")
    region = cfg.backup_region or infer_region(cfg.backup_endpoint) or "us-east-1"
    s3 = make_client(cfg.backup_endpoint, cfg.backup_access_key, cfg.backup_secret_key, region)
    probe = f"{(cfg.backup_prefix or 'geodeploy-backups').strip('/')}/.geodeploy-write-test"
    try:
        s3.put_object(Bucket=cfg.backup_bucket, Key=probe, Body=b"ok")
        s3.delete_object(Bucket=cfg.backup_bucket, Key=probe)
    except ClientError as exc:
        err = exc.response.get("Error", {})
        code, message = err.get("Code", ""), err.get("Message", str(exc))
        # "NoSuchBucket" is authoritative and GOOD news: to answer it the provider had to accept
        # the signature, so the credentials, endpoint and region are all fine — only the bucket
        # name or its LOCATION is wrong. Say so, and list what this key can actually see, because
        # "does not exist" alone sends people to re-check credentials that were never the problem.
        if code in ("NoSuchBucket", "404"):
            hint = (f"Bucket '{cfg.backup_bucket}' does not exist at {cfg.backup_endpoint or 'AWS S3'}. "
                    "Your credentials worked, so this is the bucket name or its location — on "
                    "location-scoped providers (Hetzner, Backblaze) the endpoint must match the "
                    "region the bucket was created in.")
            try:
                names = [b["Name"] for b in (s3.list_buckets().get("Buckets") or [])]
                hint += (f" Buckets visible here: {', '.join(names)}." if names
                         else " This key can see no buckets at this endpoint.")
            except ClientError:
                pass      # key may lack ListAllMyBuckets — the main hint still stands
            # BucketMissing, not ValueError: the settings page turns this one into a "Create it"
            # button, because the credentials needed to create the bucket are the ones that just
            # produced this error.
            raise BucketMissing(hint, cfg.backup_bucket or "") from exc
        raise ValueError(f"Could not write to the destination bucket ({code or 'error'}): "
                         f"{message}") from exc
    return {"ok": True, "bucket": cfg.backup_bucket, "prefix": cfg.backup_prefix,
            "region": region}      # what we actually signed with, so a blank field is explainable


def create_destination_bucket(cfg) -> dict:
    """Create the configured destination bucket, then prove it is writable.

    Only reachable after `verify_destination` raised `BucketMissing`, which means the provider
    already accepted the signature — so this is not a blind attempt with unvalidated credentials.

    It refuses the live data bucket for the same reason `verify_destination` does: a "backup" that
    lives in the bucket being backed up is not one. The check matters more here, because creation is
    the one path that could bring such a destination into existence rather than merely reject it.
    """
    if _same_as_live_data(cfg):
        raise ValueError(
            "That is the bucket your live data is in. A backup has to go somewhere else — "
            "a different bucket, ideally a different provider.")
    if not (cfg.backup_bucket or "").strip():
        raise ValueError("Set the destination bucket name first.")

    region = cfg.backup_region or infer_region(cfg.backup_endpoint) or "us-east-1"
    s3 = make_client(cfg.backup_endpoint, cfg.backup_access_key, cfg.backup_secret_key, region)

    # `LocationConstraint` is required by AWS everywhere EXCEPT us-east-1, where sending it is an
    # error, and it is meaningless to R2 (which signs with the literal "auto"). MinIO ignores it.
    # So: send it only when it is both meaningful and legal.
    kwargs = {"Bucket": cfg.backup_bucket}
    if region not in ("us-east-1", "auto", ""):
        kwargs["CreateBucketConfiguration"] = {"LocationConstraint": region}

    try:
        s3.create_bucket(**kwargs)
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code not in ("BucketAlreadyOwnedByYou", "BucketAlreadyExists"):
            # Creation is commonly the thing a scoped key is NOT allowed to do, even when it can
            # read and write objects. Say that, rather than leaving them to decode AccessDenied.
            if code in ("AccessDenied", "403"):
                raise ValueError(
                    f"These credentials cannot create buckets at {cfg.backup_endpoint or 'AWS S3'} "
                    f"({code}). Create '{cfg.backup_bucket}' in your provider's console, or use a "
                    "key with permission to create buckets.") from exc
            raise ValueError(
                f"Could not create '{cfg.backup_bucket}' ({code or 'error'}): "
                f"{exc.response.get('Error', {}).get('Message', exc)}") from exc
        # Already there and ours — treat as success. The operator asked for the bucket to exist.

    # Prove it rather than announce it: creation succeeding says nothing about whether this key may
    # write objects INTO it, and that is what a backup needs.
    return verify_destination(cfg)


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
