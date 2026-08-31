---
description: >-
  Install GeoDeploy on a Linux server with one command: the Docker Compose stack, PostGIS, object storage and the setup wizard that takes you from a bare VPS to your first published map.
---

# Getting Started

## What you need

| | |
| --- | --- |
| **RAM** | **4 GB recommended.** A running instance is comfortable there, including tiling. **2 GB has been tested and runs well** — but see the warning below: *building* the dashboard, which happens during an update, needs more memory than running it. |
| **CPU** | 2 cores recommended; 1 is enough to get started. Tiling and raster conversion are the only CPU-heavy steps, and they run in the background. |
| **Disk** | Depends entirely on your data, not on GeoDeploy. The software itself is small; layers are what grow. |
| **Domain** | Optional, but recommended — you get HTTPS and a stable portal URL. |

!!! warning "Check you have swap — many VPS images ship with none"
    This is not only a small-server concern. **Building** GeoDeploy peaks far above what running it
    needs (an update compiles the dashboard with Vite/Node), and on a machine with no swap Linux
    *kills* the build rather than slowing it down. The symptom is nasty: the update appears to hang,
    and you are left with stopped containers and no new image.

    Most cloud images ship with no swap file at all, so check first — `free -m`, and look at the
    Swap row. If it reads 0, add some whatever your RAM:

    ```bash
    sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile
    sudo mkswap /swapfile && sudo swapon /swapfile
    echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab   # survives a reboot
    swapon --show                                                # confirm
    ```

    The less RAM you have the sooner it bites — on 2 GB it is close to certain — but a 4 GB server
    with no swap and a busy worker can hit it too.

    Swap is **insurance, not a tax**: what gets paged out is mostly idle build memory, so in practice
    the build runs at normal speed — it simply stops being killed. Measured on a 2 CPU / 2 GB VPS,
    adding swap turned a build that died into one that finished with no noticeable slowdown.

    If you would rather not add swap, build the two images one at a time
    (`docker compose build geodeploy-ui`, then `geodeploy-api`) instead of letting the updater do both.

!!! tip "Disk is the one to think about"
    Storage is the only requirement that scales with use, and you are not stuck with the disk you
    start on: point GeoDeploy at **S3-compatible object storage** during setup (or later) and
    capacity stops being a server decision. That is the right choice if you expect many layers or
    large rasters — it expands on demand, at whatever your provider charges.

**Docker** is installed for you if it is missing. **Docker Compose** is only checked for — if your
distribution does not ship it, the installer stops and tells you to install `docker-compose-plugin`
first. On a current Debian or Ubuntu it is already there.

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/bravemaster3/geodeploy/main/installer/install.sh | bash
```

This command:
1. Clones the GeoDeploy repository to `~/geodeploy`
2. Generates a `.env` file with a random secret key
3. Starts the core Docker services
4. Opens the setup wizard at `http://your-server-ip`

## Setup wizard

The wizard runs automatically on first visit and takes about 2 minutes.

**Step 1 — Database.** Either let GeoDeploy install and manage PostgreSQL + PostGIS on this server,
or point it at a database you already run. Neither is "the right one": the first is for people who do
not already run a spatial database, the second for people who do, or who want the database on
separate hardware.

**Step 2 — File storage.** Either let GeoDeploy install and manage MinIO here, or use any
S3-compatible provider. Local storage is limited by this machine's disk; S3 grows on demand and is
billed by use.

**Step 3 — Admin account.** Create your login.

After setup you land on the dashboard. You never return to the wizard.

### Connecting a database you already run

Three things to know, in the order they bite:

**The port must be reachable from this server.** A timeout at this step is a network fact — the
credentials are never examined — so check the database listens on a public address
(`listen_addresses = '*'`), that the port is published, and that no firewall, *including your
provider's*, blocks it. The wizard names which of these it hit.

**PostGIS is per-database, not per-server.** A server with PostGIS installed still needs
`CREATE EXTENSION postgis;` in the specific database you name. Images such as `postgis/postgis` seed
`template1`, so databases created afterwards inherit it; a plain PostgreSQL server with the extension
merely available does not.

**Point it at a database that does not already contain GeoDeploy** — unless you mean to reconnect to
one, below.

### Reconnecting to an existing GeoDeploy database

Pointing the wizard at a database that already holds an installation is supported, and is how you
rebuild a lost server without a backup: the database holds your accounts, layers, portals *and* the
instance's own settings.

The wizard recognises it, restores those settings into `.env`, and offers two choices:

- **Sign in** — the installation is intact and there is nothing to set up.
- **Create a new database** — name one and GeoDeploy creates it on the same server, with PostGIS
  enabled, then continues the fresh install against it.

!!! warning "Carry `GEODEPLOY_SECRET_KEY` across, or lose three settings"

    The SMTP password, the OIDC client secret and the storage secret key are encrypted at rest with
    the key in `.env` — which is deliberately **not** in the database and **not** in any backup, so
    that a stolen backup cannot hand over your credentials.

    Reconnect with a *different* key and everything is recovered except those three, which must be
    re-entered. Copy the old `GEODEPLOY_SECRET_KEY` into the new `.env` before running the wizard and
    nothing is lost. See [Backups and restore](backups.md).

## Upload your first dataset

1. Go to **My Data** and choose **Upload vector** or **Upload raster**.
2. Drop the file in — Shapefile (`.zip`), GeoPackage, GeoJSON, CSV or GeoParquet for vectors,
   GeoTIFF for rasters.
3. GeoDeploy validates it, reads its coordinate system, and stores it — in PostGIS or as GeoParquet
   for vectors, as a Cloud-Optimized GeoTIFF for rasters.
4. The row shows **Ready** when it can be added to a portal.

!!! info "Your coordinate system is kept"
    Data is stored in **its own CRS**, not flattened to EPSG:4326 on the way in. Portal maps draw in
    Web Mercator like every web map, but a download can give you the original projection back —
    nothing is silently reprojected underneath you.

How long step 3 takes depends entirely on the file: a small GeoJSON is near-instant, while a large
dataset is converted and tiled in the background and takes as long as it takes. You can leave the
page — processing continues, and the row updates when it is done.

## Publish your first portal

1. Go to **Portals** → **New portal**
2. Give it a title and click **Create**
3. In the editor: click **+ Add** to add your layers
4. Choose a template
5. Click **Publish** — your portal is live at `http://your-server/portals/your-portal-name/`

## Keeping it up to date

Update from the dashboard — **Settings → Infrastructure** shows the version you are running and
whether a newer one exists, and updates in place, database schema included. That is the intended way;
you should not need a terminal for it.

[How updating works](updating.md){ .md-button }
