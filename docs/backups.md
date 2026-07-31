# Backups and restore

GeoDeploy can copy everything that cannot be regenerated to a **separate object store**: the
PostGIS database, your files (rasters, GeoParquet, PMTiles), this instance's own database, and
the images uploaded to portal About pages.

## Set it up

**Settings → Backups.** Administrators and the owner can configure a destination, run a backup and
manage what is stored. **Restoring is owner-only** — it overwrites live data, so it sits behind the
single account that cannot be locked out, and asks you to type the backup's name back before it
runs.

| Field | Notes |
|---|---|
| Endpoint | Blank for AWS S3; otherwise the S3-compatible endpoint (Wasabi, Backblaze B2, Cloudflare R2, Hetzner, another MinIO) |
| Bucket | **Must not be your data bucket** |
| Access / secret key | Credentials for the destination only. The secret is encrypted at rest and never shown again |
| Region | **Leave blank** — it is derived from the endpoint. It is only a SigV4 signing input: most S3-compatible providers ignore it, AWS validates it strictly, Cloudflare R2 wants the literal `auto`, Backblaze encodes the region in the hostname (`s3.us-west-004.backblazeb2.com` → `us-west-004`), and Hetzner uses its network zone `eu-central` for every location (the `hel1`/`fsn1`/`nbg1` in the hostname is the location, not the region). Set it by hand only if your provider rejects the derived value |
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
manifest.json          inventory: what was included, sizes, source bucket, encryption fingerprint
postgis.dump           pg_dump custom format — the catalog, users, portals AND the spatial data
portal_assets.tar.gz   images uploaded to portal About pages
objects/…              every object from your data bucket, same key layout
```

Since GeoDeploy's own state lives in the same PostgreSQL database as the spatial data, **one
`pg_dump` captures both in a single consistent snapshot**. There is no separate state file: the
old arrangement (a SQLite copy plus a PostGIS dump) could not be atomic, so a layer created
between the two ended up in one and not the other.

Two things are **not** backed up because they are regenerated from the above:

- **Published portal bundles** (`data/portals/`) — re-publish each portal after a restore.
- **Martin's config** — rebuilt by Settings → Infrastructure → *Reload Martin*.

Notes on how it is done, because they matter if you ever inspect the artifacts:

- **Objects are copied server-side** when the destination is the same provider as your data
  (`copy_object`), so the bytes never pass through the GeoDeploy container. A cross-provider
  destination streams instead — correct, but slower and bandwidth-billed.
- **The dump is one transaction-consistent snapshot**, so the catalog can never disagree with the
  spatial tables it describes.

## Restore

**Settings → Backups → Manage backups → Restore.** Restore is in the app, because a backup you
cannot restore is a guess. It is also the one action that can destroy an instance and cannot be
undone by re-running it, so it sits in a danger zone and is guarded:

- **Owner only** — not admin. That also makes it browser-only: an API token can never trigger it.
- **You must type the backup's name** to confirm.
- **A backup with no `manifest.json` is refused.** That run never finished; restoring its
  artifacts would silently produce a broken instance.
- It will not start while a backup or another restore is running.
- It is recorded in the activity log, with who confirmed it.

**Preflight** runs first and tells you what you are about to do: what the backup contains, what
currently exists (layers, portals, users), and the encryption-key verdict below.

### The encryption-key trap

`GEODEPLOY_SECRET_KEY` encrypts stored credentials at rest — the SMTP password, the OIDC client
secret, and the backup destination's own key. **Restoring onto an install with a different key
leaves those rows present but unreadable.** Everything else restores perfectly, which is what makes
this easy to miss.

Backups therefore record a *fingerprint* of the key (a hash, never the key), and preflight reports:

| Verdict | Meaning |
|---|---|
| Key matches | Encrypted settings will work |
| Different key | Everything restores; SMTP/OIDC/backup credentials must be re-entered |
| Unknown | Backup predates fingerprinting — assume they must be re-entered |

To avoid it entirely, copy the old `GEODEPLOY_SECRET_KEY` into `.env` before restoring.

### What restore does

1. **Files first**, back into the data bucket (server-side copy when the destination is the same
   provider). Existing keys are overwritten; keys absent from the backup are left alone.
2. **Portal assets** untarred over `data/portal_assets`.
3. **The database last**, `pg_restore --clean` — it drops and recreates, so anything created after
   the backup is gone.
4. Martin's tile config is regenerated.

The order is deliberate: restoring the database first would mean a failed file copy leaves a
catalog advertising layers whose files are not there — every one 404s and the instance looks
corrupt. This way a mid-failure leaves orphaned files that nothing references.

**Afterwards:** re-publish your portals (bundles are rebuilt from the database, not restored).

### Doing it by hand

If the API is down, the same result from a shell:

```bash
BK=s3://YOUR-BUCKET/geodeploy-backups/2026-07-30T03-00-00Z
aws s3 cp "$BK/manifest.json" . && cat manifest.json      # confirm what you are restoring
aws s3 cp "$BK/postgis.dump" .
aws s3 sync "$BK/objects/" s3://YOUR-DATA-BUCKET/

cd ~/geodeploy
docker compose stop geodeploy-api celery
docker compose exec -T postgres pg_restore -U geodeploy -d geodeploy   --clean --if-exists --no-owner --no-acl < postgis.dump
docker compose start geodeploy-api celery
```

### Verifying a backup

Do this occasionally — an unverified backup is a guess:

1. `manifest.json` lists every part with a non-zero size.
2. `pg_restore --list postgis.dump | head` prints a table of contents.
3. Best of all: restore into a scratch instance and log in — the round trip is the only real
   proof, and Manage backups makes it a few clicks.

## Troubleshooting

**"The specified bucket does not exist"** — good news, oddly: to answer `NoSuchBucket` the
provider had to accept your signature, so the credentials, endpoint and region are all correct.
Only the bucket **name** or its **location** is wrong. On location-scoped providers (Hetzner,
Backblaze B2) a bucket lives in one region and is reachable **only** through that region's
endpoint — a bucket created in `fsn1` returns "does not exist" via `hel1.your-objectstorage.com`.
The test lists the buckets your key can actually see, which settles name-vs-location immediately.

**"pg_dump failed: server version mismatch"** — the client in the API image is pinned to the
PostGIS server's major version (16). If you upgrade the PostGIS image, bump
`postgresql-client-16` in `api/Dockerfile` and `services/postgis.py::IMAGE` together.

**Scheduled backups never run** — they are driven by the Celery worker's embedded beat
(`docker-compose.yml`, `-B`). Check the worker is up and that the schedule is not "Manual only";
the tick fires every 15 minutes and starts a run once the configured UTC hour has passed.

**A run sits at "running" forever** — the worker was restarted mid-backup. The row keeps its last
step; start a new run. Partial backups have no `manifest.json`, are never counted as one of the
kept copies by retention, and can be deleted from the destination.
