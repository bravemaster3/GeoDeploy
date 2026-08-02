# Backups and restore

GeoDeploy can copy everything that cannot be regenerated to a **separate object store**: the
PostGIS database, your files (rasters, GeoParquet, PMTiles), this instance's own database, and
the images uploaded to portal About pages.

!!! danger "Save your encryption key somewhere other than the server"

    Run this now and put the value in your password manager:

    ```bash
    grep GEODEPLOY_SECRET_KEY ~/geodeploy/.env
    ```

    `GEODEPLOY_SECRET_KEY` lives in `.env` **on the server** — deliberately not in the database, and
    therefore **not in your backups**. That is what stops a stolen backup from handing over your SMTP
    password and the keys to your backup destination.

    The cost of that design is this: **if the server is lost, the key is lost with it.** Your layers,
    portals, users and files still restore perfectly onto a new machine — but the five settings
    encrypted at rest cannot be recovered from any backup, and must be re-entered by hand.

    A copy of one line, kept somewhere else, removes the problem entirely.

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

!!! warning "A different bucket in the *same* MinIO is not a different place"

    GeoDeploy's managed MinIO keeps **every** bucket under `~/geodeploy/data/minio`. So a backup
    stored in a second bucket on the same server survives an accidental overwrite and the demo
    reset — but not `installer/reset.sh`, not a lost disk, and not a lost server, because all of
    those take the directory that holds both buckets.

    It is a fine place for a demo seed you can rebuild. For anything you would miss, the destination
    has to be **another provider or another machine**.

If the bucket does not exist yet, the test says so and offers to **create it** for you — see
[Troubleshooting](#troubleshooting).

> **Use a dedicated, least-privilege key.** The destination key only needs `PutObject`,
> `GetObject`, `ListBucket` and `DeleteObject` on that one bucket. (Add `CreateBucket` only if you
> want GeoDeploy to create the bucket for you; a key without it works fine once the bucket exists.)
> If the destination provider
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

### Restoring onto a different server

The procedure for a machine that is not the one the backup came from — a rebuild, a migration, or
proving your backups actually work.

1. **On the old instance** (if you still have it), point Backups at the destination, **Save**,
   **Test destination**, then **Run now**. Note the backup's name.
2. **Copy the encryption key.** `grep GEODEPLOY_SECRET_KEY ~/geodeploy/.env` — or take it from the
   password manager, per the warning at the top of this page.
3. **Install GeoDeploy on the new server** and run the setup wizard. Any database and storage choice
   is fine: the restore does not use what you pick here, because runtime settings come from `.env`,
   not from the restored configuration.
4. **Put the old key into the new `.env`**, replacing the generated one, then recreate the services
   so they read it:

    ```bash
    cd ~/geodeploy
    nano .env                 # set GEODEPLOY_SECRET_KEY to the old value
    docker compose up -d --force-recreate geodeploy-api celery
    ```

5. **Configure the same backup destination** on the new instance and press **Test destination**.
6. **Settings → Backups → Manage backups.** Your backup is listed — that list is read from the
   destination itself, not from any database, so it appears on an instance that has never seen it.
   Press **Restore** and type the backup's name to confirm.

!!! warning "You will log in with the OLD instance's credentials"

    A restore replaces the users table. The admin account you created in the wizard two minutes ago
    is gone, and the accounts from the backup are what exist. Have those credentials to hand before
    you start, or you will be locked out of a server you just built.

Skipping step 4 is not fatal — everything still restores. You will simply need to re-enter the SMTP
password, the OIDC client secret and the backup destination's secret key afterwards, because those
were encrypted with a key the new server does not have.

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

If the bucket simply is not there yet, the test offers **Create it** — the same credentials the
provider just accepted are used to create the bucket and then re-verify that it is writable. Keys
scoped to a single bucket often cannot create buckets; if yours cannot, the message says so and you
create it in your provider's console instead.

**"pg_dump failed: server version mismatch"** — the client in the API image is pinned to the
PostGIS server's major version (16). If you upgrade the PostGIS image, bump
`postgresql-client-16` in `api/Dockerfile` and `services/postgis.py::IMAGE` together.

**Scheduled backups never run** — they are driven by the Celery worker's embedded beat
(`docker-compose.yml`, `-B`). Check the worker is up and that the schedule is not "Manual only";
the tick fires every 15 minutes and starts a run once the configured UTC hour has passed.

**A run sits at "running" forever** — the worker was restarted mid-backup. The row keeps its last
step; start a new run. Partial backups have no `manifest.json`, are never counted as one of the
kept copies by retention, and can be deleted from the destination.
