# Getting Started

## What you need

| | |
| --- | --- |
| **RAM** | **4 GB recommended.** A running instance is comfortable there, including tiling. Less may well work — it simply has not been measured yet. |
| **CPU** | 2 cores recommended; 1 is enough to get started. Tiling and raster conversion are the only CPU-heavy steps, and they run in the background. |
| **Disk** | Depends entirely on your data, not on GeoDeploy. The software itself is small; layers are what grow. |
| **Domain** | Optional, but recommended — you get HTTPS and a stable portal URL. |

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

**Step 1 — Database**: Choose "Set up PostGIS on this server" (recommended). GeoDeploy installs and manages PostgreSQL + PostGIS for you. Or connect an existing PostGIS database.

**Step 2 — File storage**: Choose "Use local storage on this server" (recommended). GeoDeploy installs and manages MinIO (S3-compatible) for you. Or connect your own S3-compatible bucket.

**Step 3 — Admin account**: Create your admin login.

After setup you land on the main dashboard. You never return to the wizard.

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
