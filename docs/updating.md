# Updating

GeoDeploy checks for new releases and can update itself from the dashboard. Everything lives under
**Settings ▸ Infrastructure**.

## Update from the dashboard

The Infrastructure panel shows the version you are running and whether a newer one exists. **Update
now** pulls the new code, rebuilds what changed, restarts the affected services and applies any
database schema changes.

!!! note "What survives an update"
    Your database, uploaded files, published portals and settings are all outside the application
    containers, so an update replaces the software and leaves your data alone.

Updates are additive to the schema: new columns are added if missing, nothing is dropped or
renamed automatically.

## The Infrastructure panel

Pick a service on the left, then work with it through the tabs:

| Tab | What it gives you |
| --- | --- |
| **Logs** | Live output from that service, with adjustable history |
| **Terminal** | A shell inside the container, for the owner only |
| **Deployments** | History of updates, with what changed and whether it succeeded |

You can also start, stop and restart individual services from here.

!!! warning "The terminal is a real shell"
    It runs as root inside the container and is restricted to the owner account. Use it for
    inspection; prefer the dashboard for anything it can already do.

## Services

| Service | Role |
| --- | --- |
| **api** | The application: dashboard backend, uploads, publishing |
| **celery** | Background work: ingestion, tiling, exports, backups |
| **postgres** | The spatial database, and GeoDeploy's own state |
| **minio** | Object storage for files, rasters and portal assets |
| **martin** | Vector tiles from the database |
| **titiler** | Raster tiles from Cloud-Optimized GeoTIFFs |
| **redis** | The task queue |
| **nginx** | Routing and TLS |
| **ui** | The dashboard itself |

A red service is worth investigating before anything else — most "my layer will not draw" reports
turn out to be a tile service that is not running.

## Updating from the command line

If the dashboard is unreachable, the same update runs from the server:

```bash
cd ~/geodeploy
git pull
docker compose build
docker compose up -d
```

!!! danger "Run it from the install directory"
    Run these from the directory GeoDeploy was installed into (`~/geodeploy` by default). Running
    Compose from elsewhere can recreate containers against the wrong paths and detach them from your
    data.

## Before a big change

Take a backup first — it is a button, and restoring is also a button. See
[Backups and restore](backups.md).
