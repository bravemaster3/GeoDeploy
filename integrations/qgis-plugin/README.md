# integrations/qgis-plugin/

## Purpose
The **GeoDeploy plugin for QGIS** (roadmap v1.4): browse an instance, add its layers using the
fastest source it offers, and upload a QGIS layer back — with its styling. Sits beside
`integrations/geolibre-plugin/` because both are "GeoDeploy inside somebody else's tool".

## Contents
- `geodeploy_qgis/` — **the folder that becomes the uploaded zip.** Its name is the plugin package
  name, and plugins.qgis.org requires the archive to contain exactly one such directory.
  - `metadata.txt` — what the plugin website reads. `qgisMinimumVersion=3.28`, links to homepage /
    repository / tracker, `experimental=True` until it has been used in anger.
  - `__init__.py` — `classFactory()` only. Anything heavier imported here would disable the plugin
    with a traceback the user cannot act on.
  - `plugin.py` — the dock: connect, list, add, upload. Every call runs in a `QgsTask`, because a
    plugin that freezes QGIS during a 2 GB upload is one people uninstall.
  - `connection.py` — the client wrapper. **Anonymous is the default path**: with no token it reads
    `GET /api/public`, so pasting a URL shows what the instance publishes. It also reads the CLI's
    stored profiles, so `geodeploy login` at a shell is already a login here.
  - `sources.py` — which URL to hand QGIS. PMTiles for a tiled layer (fastest to draw, needs
    GDAL ≥ 3.8, checked at runtime), OGC API - Features otherwise or when the user asks for
    attributes, `/vsicurl/…/cog` for rasters.
  - `symbology.py` — GeoDeploy style ⇄ QGIS renderer, **both directions**. Classification is never
    recomputed here: breaks are read from the style or from the renderer, and new breaks come from
    the instance's `/field-stats`, exactly as the CLI does.
  - `vendor/geodeploy/` — the published client, checked in (see below).
- `scripts/vendor.py` — refresh the vendored copy; `--check` in CI.

## Dependencies / relationships
- **The client is vendored, not installed.** A plugin cannot pip-install into someone's QGIS, which
  is why `cli/geodeploy` has zero dependencies and a Python 3.9 floor. The copy is **checked in**
  rather than built: plugins.qgis.org expects the zip to correspond to browsable repository code,
  and a copy that only exists in CI is a copy nobody reviews.
- `scripts/vendor.py --check` runs in CI so the copy cannot drift from `cli/geodeploy`. That drift
  is not hypothetical: the Python and JavaScript symbology twins disagreed for months because
  nothing compared them.
- Consumes the API's public surface — `/api/public`, `/api/ogc`, `/pmtiles`, `/cog`, `/legend`,
  `/field-stats` — all of which exist because of this plugin. See `api/geodeploy/routers/README.md`.

## Releasing
plugins.qgis.org takes a **zip**, not a repository: the plugin may live in a subdirectory of a
monorepo as long as `metadata.txt` links to publicly browsable code. Requirements that bite:
mandatory `metadata.txt` / `__init__.py` / `LICENSE`, working homepage-repository-tracker links, and
**no binaries** (irrelevant here — everything is pure Python).

```bash
python integrations/qgis-plugin/scripts/vendor.py          # refresh the client
python integrations/qgis-plugin/scripts/vendor.py --check  # what CI runs
```

## Current status & known issues
- **Written but never run inside QGIS.** Every module parses and the vendored client imports, but
  the Qt/QGIS API calls — renderer construction above all — have not been exercised. Treat the first
  QGIS session as the real test.
- `experimental=True` in `metadata.txt` until that happens.
- No icon yet (`icon.png` is referenced by `metadata.txt` and must exist before upload).
- Styling covers single symbol, graduated and categorized. **Size-from-a-field is not translated
  yet** in either direction, though the instance and the CLI both support it.
- Uploading requires a local file source; a layer already streaming from a remote URL is refused
  rather than round-tripped.

## Last updated
2026-08-14 (created — browse, add, upload, and styling in both directions)
