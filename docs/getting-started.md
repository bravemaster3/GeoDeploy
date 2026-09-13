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
| **Domain** | Optional, but recommended — a stable portal URL, and HTTPS once you put a reverse proxy in front ([how](behind-a-proxy.md)). GeoDeploy does not yet obtain its own certificate. |

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
2. Checks the machine — Docker, swap, which ports are free, what else is already serving
3. **Asks how GeoDeploy should be published** (see below)
4. Generates a `.env` file with a random secret key
5. Starts the core Docker services
6. Opens the setup wizard, and prints the address

### The one question the installer asks

GeoDeploy never takes ports 80 and 443 without being told it may — not even on an empty server — and
it never stops or reconfigures anything already running.

- **Take over port 80.** GeoDeploy becomes the machine's web server, and you reach it at
  `http://your-server-ip`. The right answer on a VPS you bought for this, and the one the installer
  recommends when nothing else is serving.
- **Behind the web server you already run.** GeoDeploy listens on `127.0.0.1:8080`, reachable only
  from the server itself, and an nginx, Caddy or Apache you already run publishes it on a domain.
  The right answer on a lab server, a shared machine, or anything already hosting a site — and the
  installer recommends it when it finds port 80 in use.

Either way you can change your mind later with one command
(`sudo bash installer/set-port.sh --dedicated`, or a port number). The full story — the SSH tunnel
that gets you to the dashboard before you have a domain, the reverse-proxy configuration the
dashboard writes for you, and what breaks if it is wrong — is in
[Installing alongside other software](behind-a-proxy.md).

If the installer has no terminal to ask with (cloud-init, Ansible, CI), set `GEODEPLOY_DEPLOY_MODE`
to `dedicated` or `behind-proxy` in the environment. With neither a terminal nor a setting, it
installs behind-proxy on a free local port: the only choice that takes nothing from the machine.

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

## Giving it a domain name

After the wizard you have a working GeoDeploy at an IP address or a local port. Turning that into
`https://maps.example.org` takes three steps, and **the dashboard walks you through all three** —
Settings → Infrastructure → **Deployment**. What follows is the same thing in prose, so you know what
you are agreeing to before you start.

You do **not** need to buy a new domain. If you already own `example.org`, a subdomain such as
`maps.example.org` costs nothing and is created in the same control panel.

### 1 · Point the domain at your server

In whoever manages your domain — Cloudflare, Namecheap, GoDaddy, your registrar — add a single
**A record**:

| Field | Value |
| --- | --- |
| **Type** | `A` |
| **Name** | `maps` — the subdomain **only**. The panel appends `example.org` itself. |
| **Value** / *Points to* | your server's public IP address, e.g. `203.0.113.10` |
| **TTL** | Auto, or 300 |
| **Proxy status** *(Cloudflare only)* | **DNS only** — the grey cloud, to start with |

The dashboard shows your server's public address with a copy button, so you do not have to go and
find it. It also checks the record for you and tells the difference between *not propagated yet*
(wait; usually minutes) and *pointing at a different machine* (fix the record) — those need opposite
reactions, which is why it is a step of its own.

!!! tip "The two mistakes almost everyone makes here"
    **Typing the full name in the Name field.** Most panels append the zone, so entering
    `maps.example.org` gives you `maps.example.org.example.org`. Enter `maps`.

    **Leaving Cloudflare's orange cloud on too early.** Proxied means Cloudflare answers DNS with its
    own addresses and terminates HTTPS itself. That is a fine end state, but while you are still
    getting a certificate with certbot's HTTP challenge it will fail — turn the proxy off (grey
    cloud) until the certificate is issued, then turn it back on and set SSL/TLS mode to
    **Full (strict)**.

### 2 · Put a reverse proxy in front

A reverse proxy is the piece that answers on ports 80/443, holds the HTTPS certificate, and forwards
requests to GeoDeploy. The dashboard writes the configuration for **nginx, Caddy, Apache or Traefik**
and you paste it — GeoDeploy never edits your web server itself, because on a shared machine a bad
reload takes down everybody else's sites too.

If you are choosing one and the machine is yours, **Caddy is three lines and gets the certificate
automatically**:

```
maps.example.org {
    reverse_proxy 127.0.0.1:8080
}
```

For nginx there are four settings that are not optional — without them shared links point at
`127.0.0.1`, and uploads over 1 MB fail with a 413. The generated block includes them, with a comment
on each explaining what breaks; the full version is in
[Installing alongside other software](behind-a-proxy.md).

### 3 · Verify

Press **Verify** in the dashboard. It resolves the domain, reaches it from the server, and confirms
the request lands on *this* instance with the hostname intact — reporting each check separately, so a
failure tells you which part to fix rather than just "it does not work".

That last check matters more than it looks: GeoDeploy builds every link it hands out — shared links,
portal previews, the STAC and OGC catalogues — from the hostname the request arrived on. A proxy that
forgets to pass it produces links pointing at an address nobody else can open, and nothing else
notices for days.

!!! info "If GeoDeploy took port 80 (the dedicated option)"
    It serves plain HTTP on your IP address and does not yet obtain its own certificate. To get
    HTTPS, move it to a local port and put a proxy in front — one command, reversible:

    ```bash
    cd ~/geodeploy && sudo bash installer/set-port.sh 8080
    ```

    Then follow the three steps above. Automatic certificates in dedicated mode are on the roadmap.

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

## If GeoDeploy is already installed here

Running the install command again on a machine that already has GeoDeploy is **safe, and is a
supported way to move to a different version**. It updates the checkout, rebuilds and restarts — and
deliberately changes nothing about where the instance lives:

- **The port is kept.** It is not re-picked, even if the port it originally avoided is now free, and
  even if you pass a different one. Your reverse proxy, your DNS record and everyone's bookmarks all
  point at that number. To move it, use `installer/set-port.sh`.
- **Your data is untouched** — `data/` and `.env` are outside version control.
- **The setup wizard does not run again.**

One thing to know: the installer does a `git reset --hard` onto the version you asked for, so **any
edits you made to tracked files are discarded** — `docker-compose.yml`, `nginx/nginx.conf`, and so
on. Settings belong in `.env`, which is never touched. If you have customised a tracked file, keep
it as a patch or a fork.

To install to a different directory, set `GEODEPLOY_DIR`:

```bash
GEODEPLOY_DIR=/opt/geodeploy curl -fsSL …/install.sh | bash
```

### Can I run two GeoDeploys on one machine?

**Not yet, and the installer will refuse rather than let you try.** It is on the roadmap.

Five containers still have fixed names — `geodeploy-postgres`, `-redis`, `-minio`, `-martin`,
`-titiler` — and every instance joins the same Docker network, where the first one's database answers
to the generic name `postgres`. A second instance would not merely collide: **its API could connect
to the first instance's database.** Rather than risk that, preflight detects an existing installation
anywhere on the machine and stops, naming the directory it found.

Note this is *different* from running GeoDeploy alongside **other software**, which is fully
supported — see [Installing alongside other software](behind-a-proxy.md). It is specifically two
copies of GeoDeploy that cannot coexist.

## Removing it again

One command, and it takes nothing else on the machine with it:

```bash
cd ~/geodeploy && sudo docker compose down    # stop it, keep everything
```

To remove it properly — including the option that deletes your data, and the things it deliberately
leaves behind (Docker, your reverse-proxy config, your DNS record) — see
[Uninstalling](uninstall.md).

## Keeping it up to date

Update from the dashboard — **Settings → Infrastructure** shows the version you are running and
whether a newer one exists, and updates in place, database schema included. That is the intended way;
you should not need a terminal for it.

[How updating works](updating.md){ .md-button }
