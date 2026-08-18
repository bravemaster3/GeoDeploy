# API reference

Every GeoDeploy instance serves its **own** interactive, always-current API reference — generated
from the running code, so it can never drift from what your version actually does:

| Path | What it is |
| --- | --- |
| `/api/docs` | Swagger UI — browse and **try** every endpoint from the browser |
| `/api/redoc` | ReDoc — the same spec, laid out for reading |
| `/api/openapi.json` | The raw OpenAPI schema, for code generators and API clients |

Replace the host with your own instance, e.g. `https://geodeploy.example.org/api/docs`.

This page is deliberately a pointer rather than a copy of the endpoint list: a hand-written endpoint
table is out of date the day after it is written.

## Which interface should I use?

Four ways in, all talking to the same API and enforcing the same permissions. Pick by what you are
doing, not by what is most powerful:

| | Reach for it when | Where |
| --- | --- | --- |
| **Dashboard** | you are looking at data, building a portal, or administering the instance | your instance in a browser |
| **[QGIS plugin](qgis.md)** | the work is cartography — restyle a layer or a whole portal with QGIS's own tools, then publish back | Plugins ▸ Install from ZIP |
| **[CLI](cli.md)** — `pip install geodeploy` | it should happen repeatedly or unattended: a nightly upload, a portal rebuilt in CI, a hundred files at once | any shell |
| **Python client** — the same package | you are writing a script and want objects rather than parsing `--json` | `from geodeploy import Client` |
| **HTTP API** | you are in another language, or building something GeoDeploy does not do | `/api/docs` |

They are layers of one thing rather than alternatives: the QGIS plugin **vendors** the Python
client, the CLI **is** that client with an argument parser, and the client is a thin wrapper over
the HTTP API. Anything the plugin can do is therefore scriptable, and anything you can script is
reachable from another language.

## Authenticating

Two ways in:

- **Session cookie** — what the dashboard uses after you sign in. Nothing to configure.
- **API token** — for scripts, CI, QGIS plugins and anything headless. Create one in
  **Settings → API tokens**. The raw `gdp_…` token is shown **once** at creation and only its
  sha256 hash is stored, so copy it then; if you lose it, revoke and make a new one.

Send it as a bearer token:

```bash
curl -H "Authorization: Bearer gdp_your_token_here" \
     https://geodeploy.example.org/api/data/vector
```

A token is capped at the scopes you grant it, and never exceeds its owner's live role — demote the
user and their tokens weaken with them.

| Scope | Minimum role | Covers |
| --- | --- | --- |
| `data:read` | viewer | list and read layers |
| `data:write` | editor | upload, rename, delete, share layers |
| `portal:read` | viewer | read portal configuration |
| `portal:write` | editor | create and edit portals |
| `portal:publish` | editor | publish and unpublish portals |
| `users:admin` | admin | user and role management |

## Command line

There is a packaged client — **[`geodeploy`](cli.md)** — that covers this API rather than
demonstrating it: upload (any format, any size, many files at once), layers, styling including
data-driven symbology, portals, publishing, the public catalog, and instance administration.

```bash
pip install geodeploy
geodeploy login https://geodeploy.example.org --token gdp_…
```

It is also an importable Python client with no dependencies, which is what the QGIS plugin is built
on. See **[The command line](cli.md)**.

!!! note "Where this is going"
    The API exists so GeoDeploy is not a place your work gets stuck. Planned next:

    - **Edit in QGIS or GeoLibre, push back to your instance** — style a layer in the tool you already
      use and publish the result, without exporting and re-uploading.
    - **A QGIS plugin** that browses your catalog and adds a layer in one click, instead of copying
      URLs by hand.

    See the [roadmap](roadmap.md).

## Public read surfaces (no authentication)

Data you have shared publicly is readable by standard clients without a token, and these are the
URLs to hand to someone who just wants the data:

| Path | Standard |
| --- | --- |
| `/api/public` | This instance's own index: published public portals, and public layers by kind |
| `/api/ogc` | **OGC API - Features** — the landing page QGIS, ArcGIS Pro, FME and GDAL connect to |
| `/api/stac` | STAC 1.0.0 catalog of layers and their assets |
| `/api/data/vector/{uid}/…` | PMTiles, TileJSON, GeoJSON and GeoParquet artifacts |
| `/api/data/raster/{uid}/cog` | Cloud-Optimized GeoTIFF, readable via `/vsicurl/` |

These send permissive CORS headers so browser-based clients can read them cross-origin.

See **[Accessing GeoDeploy data from outside](data-access.md)** for which format to hand to which
tool, with copy-paste examples for QGIS, DuckDB and Python.

## Last updated

2026-07-30 (created — the root README linked here before the page existed)
