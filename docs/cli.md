# The command line

**`geodeploy` — upload data, build and publish portals, and operate an instance, without a browser.**

The CLI talks to the same API the dashboard uses, so anything you can do in the app you can script:
a nightly upload from a field database, a portal rebuilt in CI, a hundred GeoTIFFs pushed in one
command. It is also the Python client the **QGIS plugin** is built on, so what you learn here
carries over.

```bash
pip install geodeploy
geodeploy login https://geodeploy.example.org
geodeploy upload roads.gpkg sites.csv --wait
geodeploy portals create "Field sites 2026" --publish
```

!!! info "No dependencies, Python 3.9+"
    The package installs nothing else — every request goes through the standard library. That is
    deliberate: it means the QGIS plugin can ship the same client without asking you to pip-install
    anything into QGIS.

---

## Installing

=== "pip"

    ```bash
    pip install geodeploy
    ```

=== "pipx (isolated)"

    ```bash
    pipx install geodeploy
    ```

=== "From the repository"

    ```bash
    git clone https://github.com/bravemaster3/GeoDeploy
    pip install -e GeoDeploy/cli
    ```

Check it: `geodeploy --version`.

## Signing in

Create a token in the dashboard — **Settings → API tokens → Create token** — pick its scopes and an
expiry, and copy the `gdp_…` secret. It is shown once.

```bash
geodeploy login https://geodeploy.example.org --token gdp_xxxxxxxxxxxx
```

That stores two things: the instance URL in a config file, and the token in your **operating
system's keyring** if you have one, otherwise in a `0600` file next to it. The token never goes into
the config file, so you can share that file when asking for help.

| Where | What | Path |
| --- | --- | --- |
| Config | instance URLs, profile names, the account each was logged in as | `~/.config/geodeploy/config.json` (Windows: `%APPDATA%\GeoDeploy\`, macOS: `~/Library/Application Support/geodeploy/`) |
| Credentials | tokens and sessions | OS keyring, else `credentials.json` alongside, mode `0600` |

### Several instances

Every login is a **profile**, named after the host unless you say otherwise:

```bash
geodeploy login https://geodeploy.example.org --token gdp_aaa --name prod
geodeploy login https://staging.example.org  --token gdp_bbb --name staging

geodeploy profile list          # which ones exist, and which is active
geodeploy profile use prod      # switch
geodeploy layers list -p staging   # or override for one command
```

### In CI, or without logging in

Two environment variables are enough, and nothing is written to disk:

```bash
export GEODEPLOY_URL=https://geodeploy.example.org
export GEODEPLOY_TOKEN=gdp_xxxxxxxxxxxx
geodeploy layers list
```

!!! tip "Use `127.0.0.1`, not `localhost`, against a local instance"
    On Windows with WSL2, `localhost` resolves to IPv6 first while the published port is IPv4 only,
    so every request waits for that attempt to fail. `http://127.0.0.1` skips it.

### Scopes, and what a token cannot do

A token acts as its owner, limited to its scopes and never above their live role. If a command comes
back with `Token missing scope: data:write`, mint one that has it.

| Scope | Lets you |
| --- | --- |
| `data:read` | list and inspect layers |
| `data:write` | upload, rename, restyle, share, delete layers |
| `portal:read` | read portal configuration |
| `portal:write` | create and edit portals |
| `portal:publish` | publish and unpublish |
| `users:admin` | manage members and invitations |

**Administration is deliberately closed to tokens.** `geodeploy admin …` (health, services, updates,
backups, credentials) and `geodeploy token create` need a *password session*, so that a leaked token
cannot restart your database or mint more tokens:

```bash
geodeploy login https://geodeploy.example.org --password --email you@example.org
```

The password is prompted for (never a flag value, so it stays out of shell history), sent once, and
never stored — what is kept is the same 7-day session the browser gets, alongside your token. In CI,
`--password-stdin` reads it from a pipe. `geodeploy logout --session-only` drops it again.

---

## Uploading

One command handles every format and any number of files:

```bash
geodeploy upload roads.gpkg                    # one file
geodeploy upload data/*.gpkg data/*.tif --wait # a directory's worth, waiting for ingest
geodeploy upload sites.csv --x lon --y lat     # CSV points
geodeploy upload plots.csv --wkt geometry      # CSV with WKT geometry of any type
geodeploy upload parcels.parquet --name Parcels
```

`--wait` follows each ingest job to completion and **fails the command if ingest fails** — which is
what a script needs, since a queued job says nothing about whether the data was readable.

### Which route a file takes

You do not choose; the CLI does, and `--dry-run` shows you what it decided before anything moves:

```bash
$ geodeploy upload big.gpkg sites.csv dem.tif --dry-run
PATH        LAYER TYPE  ROUTE         NAME    SIZE H   CHUNKED  REASON
big.gpkg    vector      large-vector  big     220.4 MB yes      over the 48 MB direct-upload threshold…
sites.csv   vector      csv-api       sites   1.2 KB   no
dem.tif     raster      raster-api    dem     18.0 MB  no
```

| Route | When | Result |
| --- | --- | --- |
| `vector-api` | `.gpkg` `.geojson` `.json` `.zip` under 48 MB | a PostGIS layer |
| `csv-api` | a small `.csv` with geometry columns | a PostGIS layer |
| `large-vector` | any of those at or over 48 MB | uploaded direct to storage, converted to GeoParquet |
| `geoparquet` | `.parquet` / `.geoparquet`, any size | registered in place, no conversion |
| `raster-api` / `raster-large` | `.tif` / `.tiff`, small / large | Cloud-Optimized GeoTIFF |

Files over 48 MB never pass through the API: they go straight to object storage in 48 MB presigned
parts, four at a time, each part retried on its own. This is why a multi-gigabyte upload works
behind a proxy or CDN that caps request bodies at 100 MB — see
[Uploading data](uploading.md#large-files).

### CSV geometry

Give the columns explicitly, or let the CLI read the header and offer a guess:

```bash
$ geodeploy upload sites.csv
warning: sites.csv: guessed geometry from the header (x=Longitude, y=Latitude). Pass --x/--y or --wkt to be explicit.
```

It never guesses silently — a wrong x/y column puts your layer in the Gulf of Guinea and nothing
reports an error. `--no-guess` refuses instead of guessing; `--srid` sets the CRS of the coordinates
(default 4326) and `--delimiter` overrides the sniffed separator.

---

## Layers

```bash
geodeploy layers list                      # everything, vector and raster
geodeploy layers list --type vector --status ready
geodeploy layers list --query roads        # match name, abstract or keywords

geodeploy layers show roads                # one layer, in full
geodeploy layers fields roads              # its attribute columns
geodeploy layers stats roads --field pop   # the distribution of one attribute
geodeploy layers usage roads               # which portals use it
```

**You can name a layer any way you have it**: its id (`7`), its stable public id
(`a7f3c91b04e2`), a `vector-7` reference, or its name — a unique part of the name is enough. An
ambiguous name is an error, never a guess.

```bash
geodeploy layers rename roads "Main roads"
geodeploy layers share roads --visibility public --license CC-BY-4.0 --attribution "SLU"
geodeploy layers links roads               # the URL for each tool, labelled
geodeploy layers download dem -o dem.tif   # the COG itself
geodeploy layers delete roads --yes
```

`--visibility public` is the opt-in that puts a layer in the [STAC catalog and OGC API -
Features](data-access.md). Nothing is public by default. `layers links` then prints the URL to hand
to each tool — WMTS for QGIS, TileJSON for MapLibre, and so on.

### Fixing a stuck layer

```bash
geodeploy layers reprocess "field sites" --wait   # restart processing, no re-upload
geodeploy layers tile roads --wait                # (re)build the PMTiles archive
geodeploy layers prepare parcels                  # re-run GeoParquet preparation
```

---

## Styling

The same styling flags work in three places: on a **layer's default style**, when **adding a layer
to a portal**, and when **restyling a layer on a portal**. Only the flags you pass change; the rest
of the style is left alone.

```bash
geodeploy layers style roads --color '#e11d48' --line-width 2 --line-type dashed
geodeploy layers style sites --marker star --radius 6 --outline-color none
geodeploy layers style dem   --colormap terrain --rescale 0,2400
```

| Flag | For |
| --- | --- |
| `--color` | polygon fill, line colour, or point colour |
| `--fill-opacity`, `--opacity` | fill opacity; whole-layer opacity |
| `--outline-color`, `--outline-width` | outline colour (`none` for no outline) and, on points, its width as a fraction of the radius — a wide one is how a ring is drawn |
| `--line-width`, `--line-type` | width in px; `solid` `dashed` `dotted` |
| `--radius`, `--marker` | point size; `circle` `square` `triangle` `diamond` `star` `cross` |
| `--colormap`, `--rescale`, `--algorithm`, `--zfactor`, `--bidx` | raster: colour ramp, stretch, `hillshade`, exaggeration, band selection |

### Colour by a field

```bash
# graduated: numeric classes computed from the data
geodeploy portals style 3 parcels --color-field population --classify quantile --classes 5 --ramp magma

# categorized: one colour per distinct value
geodeploy portals style 3 landcover --color-field type --classify

# or state the classes yourself
geodeploy portals style 3 parcels --color-field pop --class-breaks '*-100:#fee,100-500:#f88,500-*:#f00'
geodeploy portals style 3 landcover --color-field type --categories 'forest:#2c7,water:#39f'
```

`--classify` asks the **instance** to compute the breaks (`quantile`, `equal`, or `jenks` natural
breaks) with the same code the portal editor and the published map use, so a CLI-styled layer lands
in exactly the classes the editor would show. Ramps: `viridis` `magma` `blues` `reds` `greens`
`oranges` `rdbu` `brbg` `spectral`.

Size and 3D work the same way:

```bash
geodeploy portals style 3 towns --size-field population --size-stops '0:3,1000000:20'
geodeploy portals style 3 buildings --extrude --extrude-field height --extrude-scale 1.5
geodeploy portals style 3 stations --extrude --extrude-field depth --extrude-radius 250
```

Anything the flags do not cover goes through `--style-json '{"…":…}'` or `--style-json @style.json`.

---

## Portals

```bash
geodeploy portals list
geodeploy portals create "Field sites 2026"
geodeploy portals create "Catalogue" --experience catalog --access organization
```

`--experience` picks the archetype — `webmap` (default), `storymap` or `catalog`. `--access` sets
who may view the published portal: `public`, `password` (with `--password`), `organization` (any
signed-in member) or `owner`.

### Arranging layers

```bash
geodeploy portals add-layer 3 roads --color '#e11d48'
geodeploy portals add-layer 3 dem --colormap terrain --bottom
geodeploy portals layers 3                 # what is on it, top of the list first
geodeploy portals move-layer 3 roads top
geodeploy portals remove-layer 3 dem
```

**Index 0 is the top of the layer list and draws on top**, so `add-layer` puts a layer at the top
unless you pass `--bottom`. With no styling flags, a layer arrives with the default style it has in
*My Data*.

### Publishing

```bash
geodeploy portals set-description 3 @about.md    # the About page, in Markdown
geodeploy portals asset 3 logo.svg
geodeploy portals publish 3
geodeploy portals url 3
```

!!! warning "Editing changes a draft"
    The live portal keeps serving its previous version until you publish. Every command that
    changes something says so, and most accept `--publish` to do both in one step.

### Editing the whole configuration

```bash
geodeploy portals export 3 portal3.json     # everything editable, as JSON
#   …edit layers, symbology, folders, story sections, theme…
geodeploy portals import 3 portal3.json
geodeploy portals publish 3
```

This round trip is how the plugins work, and it is the one to reach for when you want a portal under
version control. The file is written as UTF-8 without a BOM, and read back the same way, so it
survives PowerShell.

### Downloading an area

```bash
geodeploy portals download-area 3 "11.8,57.6,12.1,57.8" -o gothenburg.zip --format gpkg
```

---

## Everything else

=== "External services"

    ```bash
    geodeploy sources add "Orthophoto" https://wms.example.org/wms --type wms --layer-name ortho_2025
    geodeploy sources add "OSM" 'https://tile.openstreetmap.org/{z}/{x}/{y}.png' --type xyz
    geodeploy sources list
    ```

    A WFS is probed when registered, so a wrong `typeName` fails immediately rather than as an
    empty layer on a published map.

=== "Data already on the server"

    ```bash
    geodeploy import db-list                       # spatial tables not yet registered
    geodeploy import db-add public.roads
    geodeploy import storage-list --kind geoparquet
    geodeploy import storage-add vectors/parcels.parquet --wait
    geodeploy import csv uploads/sites.csv --x lon --y lat
    ```

    Nothing is copied: tables and objects are registered where they already are.

=== "The public catalog"

    ```bash
    geodeploy catalog collections        # OGC API - Features, one per public vector layer
    geodeploy catalog items vector-a7f3c91b04e2 --bbox 11,55,24,69 --limit 5
    geodeploy catalog stac
    geodeploy catalog search --bbox 11,55,24,69
    geodeploy catalog templates
    ```

    These need no credentials — which makes them the honest check on what you have actually
    published. If a layer is not listed here, nobody outside your organisation can see it either.

=== "Operating an instance"

    ```bash
    geodeploy admin health
    geodeploy admin logs celery -n 200
    geodeploy admin service celery restart
    geodeploy admin storage
    geodeploy admin updates --refresh
    geodeploy admin update v1.3.0 --watch
    geodeploy admin backups --run
    geodeploy admin audit --action portal --since 2026-08-01T00:00:00Z
    ```

    All of these need a password session (see above). `geodeploy users …` does not — it is
    scope-gated, so a token with `users:admin` can manage members.

=== "Jobs"

    ```bash
    geodeploy jobs show <job-id>
    geodeploy jobs watch <job-id>
    ```

---

## Scripting it

Every command takes `--json`, and in that mode **stdout is exactly one JSON document** — progress,
warnings and hints all go to stderr, so a pipe stays clean.

```bash
geodeploy layers list --json | jq -r '.[] | select(.status=="ready") | .name'

# publish every draft portal
for id in $(geodeploy portals list --draft --json | jq -r '.[].id'); do
  geodeploy portals publish "$id"
done
```

Errors are JSON too — `{"ok": false, "error": "…"}` — so a script can read the failure instead of
scraping it.

### Exit codes

| Code | Meaning |
| --- | --- |
| `0` | success |
| `1` | the operation failed (the instance said no, or a job failed) |
| `2` | the command line was wrong |
| `3` | authentication: no credential, expired token, missing scope, role too low |
| `4` | the instance could not be reached |
| `5` | the instance returned a server error |

Separating these is what lets a nightly job alert on "the token expired" without alerting on "the
instance was restarting".

### A worked example

```bash
#!/usr/bin/env bash
set -euo pipefail
export GEODEPLOY_URL=https://geodeploy.example.org
export GEODEPLOY_TOKEN="$CI_GEODEPLOY_TOKEN"

geodeploy upload exports/*.gpkg --wait --json > uploaded.json
portal=$(geodeploy portals list --query "Monitoring" --json | jq -r '.[0].id')

for name in $(jq -r '.uploaded[].name' uploaded.json); do
  geodeploy portals add-layer "$portal" "$name" --replace \
      --color-field status --classify quantile --classes 5
done

geodeploy portals publish "$portal"
```

---

## From Python

The CLI is a thin shell over a client you can import — the same one the QGIS plugin uses:

```python
from geodeploy import Client

gd = Client("https://geodeploy.example.org", token="gdp_…")

result = gd.uploads.upload("roads.gpkg", wait=True)
portal = gd.portals.create("Roads")
gd.portals.add_layer(portal["id"], result.layer_id, "vector", {"color": "#e11d48"})
gd.portals.publish(portal["id"])
```

Failures raise typed exceptions — `AuthError`, `PermissionError_`, `NotFoundError`,
`ValidationError`, `ServerError`, `TransportError`, `JobFailed` — so calling code can tell "your
token is wrong" from "that layer is gone" from "the network blipped" without reading status codes.

Uploads report progress and can be cancelled:

```python
gd.uploads.upload("huge.parquet",
                  on_progress=lambda done, total: print(done, "/", total),
                  cancel=lambda: stop_requested)
```

And the transport is swappable, which is how a desktop plugin inherits the host application's proxy
and certificate settings:

```python
gd = Client(url, token=token, transport=MyQgisTransport())   # any .send(Request) -> Response
```

---

## Troubleshooting

**`No instance configured.`** — Run `geodeploy login <url>`, or set `GEODEPLOY_URL`. `geodeploy
profile show` prints which instance and credential a command would use, and where each came from.

**`Token missing scope: …`** — The token is valid but was minted without that scope. Create a new
one in Settings → API tokens.

**`… administration is session-only by design`** — You are using an API token on a route that only
accepts a browser-style session. Run `geodeploy login --password`.

**An upload stalls or fails with a network error** — Almost always a proxy body limit. Files over
48 MB already avoid it; if a smaller one fails, force the direct route by uploading it as
GeoParquet, or check what sits in front of the instance.

**A layer sits at *processing* forever** — The worker was probably recreated mid-conversion.
`geodeploy layers reprocess <layer>` restarts it without re-uploading.

**Self-signed certificate** — `--insecure` skips TLS verification. Only for a lab instance you
control.

## Last updated

2026-08-12 (the packaged CLI ships in v1.3, replacing `examples/geodeploy_cli.py`)
