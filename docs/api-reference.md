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

A worked end-to-end client lives in [`examples/geodeploy_cli.py`](https://github.com/bravemaster3/GeoDeploy/blob/main/examples/geodeploy_cli.py) —
whoami, upload, portal get/set, add/remove layer, set sharing, publish.

## Public read surfaces (no authentication)

Data you have shared publicly is readable by standard clients without a token, and these are the
URLs to hand to someone who just wants the data:

| Path | Standard |
| --- | --- |
| `/api/ogc` | **OGC API - Features** — the landing page QGIS, ArcGIS Pro, FME and GDAL connect to |
| `/api/stac` | STAC 1.0.0 catalog of layers and their assets |
| `/api/data/vector/{uid}/…` | PMTiles, TileJSON, GeoJSON and GeoParquet artifacts |
| `/api/data/raster/{uid}/cog` | Cloud-Optimized GeoTIFF, readable via `/vsicurl/` |

These send permissive CORS headers so browser-based clients can read them cross-origin.

See **[Accessing GeoDeploy data from outside](data-access.md)** for which format to hand to which
tool, with copy-paste examples for QGIS, DuckDB and Python.

## Last updated

2026-07-30 (created — the root README linked here before the page existed)
