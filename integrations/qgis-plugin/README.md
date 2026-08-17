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
  - `export.py` — what to actually upload for a given layer.
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
- Uploading writes the layer out first (`export.py`) rather than reading `layer.source()` as a
  path: a FILTERED layer's file holds more than the layer does, and a memory or PostGIS layer has no
  file at all. A plain unfiltered file is sent as-is, so nothing is re-encoded needlessly. A remote
  layer is refused with a reason, and so is one with unsaved edits.
- A RASTER still needs a local file: re-encoding one here would mean choosing compression and
  resampling on the user's behalf, and ingest converts to COG anyway.

## Last updated
2026-08-17f (**the two portal paths were not the same path, and every 0.1.5 fix covered only one.**
`PortalOut.LayerConfig` is `{layer_id, layer_type, visible, opacity, style, popup_fields}` — no
`source`, no `geometry_type`, no `name`. Everything read from the published style.json therefore
applied to the ANONYMOUS path alone: with a token, a portal's rasters still opened in the layer's
default colours (the complaint "fixed" in 0.1.3), geometry still depended on a `_row_for` lookup, and
a layer that failed was reported by its numeric id ("1 could not be opened (9)"). New
`portals.enrich_from_published` merges `source`/`geometry_type`/`name` in from style.json for a
PUBLISHED portal, adding keys only — the API's style, visibility and opacity are authoritative and
untouched — and degrading to the API document when style.json cannot be read. An unpublished portal
has nothing to merge, which is honest: its rasters are not served under any styling yet.
`place` now prefers the portal's source for EVERY layer type, not just rasters: a 3D point layer is
drawn from a `pillars` function whose tiles hold polygons, and nothing in the layer's own listing
entry points there. Also fixed a latent `AttributeError` — `portals.py` called `_log`, which it does
not define (`_note` now does, lazily). Tests: the authenticated-parity block in
`scripts/test_published_style.py`.)
2026-08-17e (**polygons drew as a dot per vertex, or not at all.** A `QgsVectorTileBasicRendererStyle`
is bound to ONE geometry type and QGIS honours that literally: a marker symbol over line data draws
a marker at every vertex (a road network as a carpet of dots — the reported screenshot), a fill
symbol over point data draws nothing. `apply_to_vector_tiles` defaulted the unknown case to POINT,
and the unknown case was the common one: `_row_for` matches on `id`, but the public index puts the
UID there while portal configs carry numeric ids, so `layer_row` is **always None** on the anonymous
path. Now `configs_from_published_style` carries `geometry_type`, taken from the **MapLibre layer
type** (`_geometry_of`) rather than `geodeploy:geometry` — the source of a 3D point layer says
"point" while its `pillars` tiles hold polygons — and `place` passes it through even when the row is
missing. Genuinely unknown geometry gets a style per type rather than a guess.
Also: `_vector_tiles(prefer_uri=True)` for portal sources, so a portal drawing from `pillars` is not
silently redirected to the layer's own TileJSON; `_baked_style` reads `fill-extrusion-color`;
`fetch_text` is cached like `fetch_json`. Verified across all six live portals: 25 layers, every one
resolving geometry, source and colour.)
2026-08-17c (**a portal's raster was drawn in the layer's DEFAULT style.** A raster is coloured by
the server, and a portal bakes its colormap/stretch/band/hillshade into its OWN tile URL — proven on
the live instance, where one `Degfert_DEM_restr.tif` appears as `&colormap_name=terrain` in one
portal and a bare `&rescale=264.9,298.33` in another, and `Degfert_DEM.tif` as
`&algorithm=hillshade&expression=b1*5.0` in a third. `open_portal_as_group` now prefers
`_layer_from_portal_source` for rasters, and that path keeps the PORTAL's template (an earlier edit
had it reach for the layer's TileJSON, which would have reintroduced the default style); bounds come
separately from `_raster_bounds`. **Opacity travels IN now too** — `_set_opacity`, applied from the
portal's `layer_configs[].opacity` and from a layer's stored `default_style.opacity`. The push side
already sent it, so a portal opened solid and then reported a change nobody made.
**Push semantics, confirmed unchanged and correct:** `portals.push` writes `layer_configs` only, so
restyling inside a group changes that PORTAL; "Save styling to GeoDeploy" writes the layer's DEFAULT
style. **Units:** symbols are measured in POINTS — device-independent and constant across zoom, the
same behaviour as the browser's CSS pixels. Nothing uses map units, which is the only thing that
would resize on zoom; the "too small when zoomed in" report was the 0.1.2 size bug, not the unit.)
2026-08-17b (**points were drawn at a third of their size, under a dark outline.** `_use_points`
switches a symbol's unit to points but left QGIS's default marker NUMBER (2.0, meant as mm) standing,
so a style that names a colour and no radius — which most do; `/legend` returns `{"color": "#3b82f6"}`
— came out at 0.7 mm, under QGIS's default dark-grey outline, which at that size covers the fill.
Tiny black dots where the browser drew visible blue ones, and only POINT layers were affected, which
is why some layers looked styled and some did not. `_symbol_of` now ALWAYS sets the size, defaulting
to the portal's own `circle-radius: 5`, with the white 1 px stroke the map draws
(`DEFAULT_POINT_RADIUS`/`DEFAULT_POINT_STROKE`, kept in step with mapStyle.js / portal_generator /
portal.js). Verified against the live instance: the anonymous `/legend` path resolves a style for
every public layer, so it was never the styles that were missing.)
2026-08-17 (**speed and symbology, both measured.** `sources.describe` now sends EVERY vector layer
down one viewport-driven path — a **TileJSON**, for PostGIS (Martin) and tiled GeoParquet (the new
per-tile endpoint) alike — and `plugin._vector_tiles` READS it for the tile template, the real zoom
range, the bounds and the name of the layer inside the tiles. The bare-template URI's `zmax=22` was
the slowness: QGIS believed tiles existed at every zoom, kept requesting them past the depth the
server has data, got empties back, retried each three times and drew nothing — "vanishes when I zoom
in", "loading forever", and the retry storms in the log, all from one wrong number. Told the real
range it over-zooms the deepest real tile instead. Opening a PMTiles archive through GDAL is now the
LAST resort, not the first: the driver has no viewport (a five-feature layer = 2.17M tile entries).
`sources.fallback` + `plugin._open_best` walk tiles → archive → OAPIF so an older instance still opens.
**`symbology.apply_to_vector_tiles` now classifies** — one `QgsVectorTileBasicRendererStyle` per class
with a filter expression, which is the same shape as the map's step/match expressions — through the
SAME `_symbol_of` the feature path uses, so sizes/dashes/markers cannot drift. New **`symbology.apply`**
is the single entry point: it picks the renderer from the layer's TYPE, which is what the portal-group
path got wrong (it handed tile layers to `apply_to_qgis`, which silently did nothing). Tile layers get
their real extent, so "zoom to layer" lands on the layer. Portal tree items keep the **whole portal
row** — dropping everything but the slug is why QGIS group names showed ids while the dock showed
titles. Tests: `scripts/test_tile_symbology.py`, `scripts/test_sources.py`, both in CI.)
2026-08-14 (created — browse, add, upload, and styling in both directions)
