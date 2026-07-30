# Backups and restore

GeoDeploy can copy everything that cannot be regenerated to a **separate object store**: the
PostGIS database, your files (rasters, GeoParquet, PMTiles), this instance's own database, and
the images uploaded to portal About pages.

## Set it up

**Settings → Backups** (admin only).

| Field | Notes |
|---|---|
| Endpoint | Blank for AWS S3; otherwise the S3-compatible endpoint (Wasabi, Backblaze B2, Cloudflare R2, Hetzner, another MinIO) |
| Bucket | **Must not be your data bucket** |
| Access / secret key | Credentials for the destination only. The secret is encrypted at rest and never shown again |
| Path prefix | Default `geodeploy-backups` |
| Schedule | Manual, daily, or weekly, at a chosen UTC hour |
| Keep last | Retention. Older *complete* backups are deleted after each successful run |

Press **Test destination** before relying on it. The test writes and deletes a probe object, and
**refuses a destination that is the same endpoint *and* bucket as your live data** — a copy that
dies with the original is not a backup. Prefer a different provider entirely: that also protects
you from an account suspension or a mistaken bucket deletion.

> **Use a dedicated, least-privilege key.** The destination key only needs `PutObject`,
> `GetObject`, `ListBucket` and `DeleteObject` on that one bucket. If the destination provider
> supports object lock / versioning, turn it on — that is what protects you from ransomware and
> from GeoDeploy itself deleting the wrong thing.

## What a backup contains

Each run writes to `s3://<bucket>/<prefix>/<UTC timestamp>/`:

```
manifest.json          inventory: what was included, sizes, source bucket, when
postgis.dump           pg_dump custom format (all schemas, including every member's geodeploy_u*)
state.db               the instance database (SQLite), a consistent snapshot
portal_assets.tar.gz   images uploaded to portal About pages
objects/…              every object from your data bucket, same key layout
```

Two things are **not** backed up because they are regenerated from the above:

- **Published portal bundles** (`data/portals/`) — re-publish each portal after a restore.
- **Martin's config** — rebuilt by Settings → Infrastructure → *Reload Martin*.

Notes on how it is done, because they matter if you ever inspect the artifacts:

- **Objects are copied server-side** when the destination is the same provider as your data
  (`copy_object`), so the bytes never pass through the GeoDeploy container. A cross-provider
  destination streams instead — correct, but slower and bandwidth-billed.
- **`state.db` is captured with SQLite's `VACUUM INTO`**, not a file copy. The database is being
  written while the backup runs, and in WAL mode the newest commits live in a sidecar file — a
  plain copy can restore corrupt or silently missing the most recent changes.

## Restore

**Restore is deliberately not a button.** Restoring over a live instance is how people destroy the
data they were trying to protect. The procedure below is explicit, and you should practise it once
on a throwaway instance *before* you need it.

Restore onto a **freshly installed** GeoDeploy of the same version (check `manifest.json`).

```bash
# 0. Pick the backup and fetch it (any S3 client; example uses the AWS CLI)
BK=s3://YOUR-BUCKET/geodeploy-backups/2026-07-30T03-00-00Z
aws s3 cp "$BK/manifest.json" .   && cat manifest.json     # confirm what you are restoring
aws s3 cp "$BK/postgis.dump" .
aws s3 cp "$BK/state.db" .
aws s3 cp "$BK/portal_assets.tar.gz" .

# 1. Stop the services that write, so nothing races the restore
cd ~/geodeploy
docker compose stop geodeploy-api celery

# 2. PostGIS
docker compose exec -T postgres psql -U geodeploy -c \
  "DROP DATABASE IF EXISTS geodeploy; CREATE DATABASE geodeploy;"
docker compose exec -T postgres pg_restore -U geodeploy -d geodeploy --no-owner < postgis.dump

# 3. Files — restore into the data bucket (server-side copy, same layout)
aws s3 sync "$BK/objects/" s3://YOUR-DATA-BUCKET/

# 4. Instance database + portal assets
cp state.db data/sqlite/geodeploy.db
rm -f data/sqlite/geodeploy.db-wal data/sqlite/geodeploy.db-shm   # stale sidecars of the OLD db
tar xzf portal_assets.tar.gz -C data/portal_assets

# 5. Start, then rebuild what is derived
docker compose start geodeploy-api celery
#   Settings -> Infrastructure -> Reload Martin, then re-publish each portal.
```

Step 4's `rm` matters: leaving the previous database's `-wal`/`-shm` files next to a restored
`geodeploy.db` can corrupt it on first open.

### Verifying a backup

Do this occasionally — an unverified backup is a guess:

1. `manifest.json` lists every part with a non-zero size.
2. `pg_restore --list postgis.dump | head` prints a table of contents.
3. `sqlite3 state.db "PRAGMA integrity_check;"` prints `ok`.
4. Best of all: run the restore into a scratch instance and log in.

## Troubleshooting

**"pg_dump failed: server version mismatch"** — the client in the API image is pinned to the
PostGIS server's major version (16). If you upgrade the PostGIS image, bump
`postgresql-client-16` in `api/Dockerfile` and `services/postgis.py::IMAGE` together.

**Scheduled backups never run** — they are driven by the Celery worker's embedded beat
(`docker-compose.yml`, `-B`). Check the worker is up and that the schedule is not "Manual only";
the tick fires every 15 minutes and starts a run once the configured UTC hour has passed.

**A run sits at "running" forever** — the worker was restarted mid-backup. The row keeps its last
step; start a new run. Partial backups have no `manifest.json`, are never counted as one of the
kept copies by retention, and can be deleted from the destination.
