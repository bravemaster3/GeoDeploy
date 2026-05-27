# Getting Started

## Requirements

- A Linux VPS (Hetzner CX31 or equivalent: 2 vCPU, 8GB RAM, 80GB SSD)
- Docker and Docker Compose installed
- A domain name (optional, but recommended)

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

1. Go to **My Data** → **Upload vector**
2. Drag a Shapefile (.zip), GeoJSON, or GeoPackage
3. GeoDeploy validates the geometry, reprojects to EPSG:4326, and loads it into PostGIS
4. Status shows **Ready** when done (usually 10–60 seconds)

## Publish your first portal

1. Go to **Portals** → **New portal**
2. Give it a title and click **Create**
3. In the editor: click **+ Add** to add your layers
4. Choose a template
5. Click **Publish** — your portal is live at `http://your-server/portals/your-portal-name/`

## Update

```bash
cd ~/geodeploy && bash installer/update.sh
```
