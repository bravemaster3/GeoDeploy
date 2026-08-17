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
    attributes, `/vsicurl/…/cog` for rasters. `alternatives()` returns every surface a layer
    offers, each with a `label` and an `is_data` flag — that is what the dock's **Source** picker
    shows. `raster_style_from_tile_url` reads a portal's baked raster styling back OUT of its tile
    template, which is the only place a portal records how it colours a raster.
  - `export.py` — what to actually upload for a given layer.
  - `symbology.py` — GeoDeploy style ⇄ QGIS renderer, **both directions, vector and raster**.
    Classification is never recomputed here: breaks are read from the style or from the renderer,
    and new breaks come from the instance's `/field-stats`, exactly as the CLI does. The raster half
    is `raster_to_qgis` / `raster_from_qgis` (colormap, stretch, band, colour-per-value, hillshade),
    and `comparable_style` folds both shapes so a round trip reports only real edits.
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
- Styling covers single symbol, graduated and categorized for vectors, colormap / stretch / band /
  colour-per-value / hillshade for rasters, and **3D extrusion for polygons and points** — all
  **both directions**. Size-from-a-field round-trips too.
- **What cannot round-trip, and why:** a POLYGON's outline WIDTH. GeoDeploy draws a fill's outline
  with MapLibre's `fill-outline-color`, which is always 1 px — there is no width to carry, so
  writing one from QGIS would promise something no portal can draw. A LINE's width and a point
  marker's stroke width (a ratio of the radius) both do round-trip.
- **3D units are not converted.** GeoDeploy's extrusion heights and pillar radii are metres; QGIS 3D
  measures in the project's map units. Those agree exactly in a projected CRS in metres and do not
  in a geographic one. The number travels unchanged rather than being transformed, because
  converting would need the project CRS and would make the number a user typed differ from the one
  that comes back.
- **Which renderer QGIS offers is decided by the SOURCE, not by us.** Server-rendered raster tiles
  arrive as one band of RGBA ("Singleband color data" — nothing to classify), and vector tiles get
  `QgsVectorTileBasicRenderer`, which has no categorized or graduated mode. The **Source** picker
  and **"Restyle this layer…"** are the two ways to get onto a surface that can be restyled; the
  default is still the fast one, so nothing is slower unless it is asked for.
- Uploading writes the layer out first (`export.py`) rather than reading `layer.source()` as a
  path: a FILTERED layer's file holds more than the layer does, and a memory or PostGIS layer has no
  file at all. A plain unfiltered file is sent as-is, so nothing is re-encoded needlessly. A remote
  layer is refused with a reason, and so is one with unsaved edits.
- A RASTER still needs a local file: re-encoding one here would mean choosing compression and
  resampling on the user's behalf, and ingest converts to COG anyway.

## Last updated
2026-08-17n (**3D extrusion now travels both ways, for polygons AND points — and it was verified
against the live instance rather than only against stubs.** `merge_style` already PRESERVED the
`extrusion` key through a push, which is the floor: it meant a restyle from QGIS did not delete 3D,
but 3D could not be edited there either. Now `apply_3d` writes it (`QgsPolygon3DSymbol` with an
extrusion height, or `QgsPoint3DSymbol` shaped as the CYLINDER `services/pillars` actually builds a
point into) and `extrusion_from_qgis` reads it back.
*The hard part is that QGIS cannot express every GeoDeploy extrusion.* A cylinder has one length, so
a point whose height comes from a COLUMN has no equivalent — and reading the symbol back naively
reports a fixed height, which `merge_style` would then treat as "the user replaced the column" and
DELETE it. So the applied spec is recorded on the layer (`P_EXTRUSION`) beside what QGIS ended up
holding, and returned unchanged while the two still match; only a real edit is read as one. Same
device as `P_COLORMAP`, and the same reason: QGIS is not a lossless container for someone else's
style. `opacity` survives the same way.
*Three answers, not two:* `extrusion_from_qgis` returns None for a layer that cannot hold 3D (a
vector-TILE layer, or a QGIS without 3D) so a push cannot delete a portal's extrusion; `{"enabled":
False}` for a feature layer whose 3D was switched off, which is a real edit; and the block itself
otherwise. `with_3d` is applied to EVERY `from_qgis` return path, not just the single-symbol one —
the live instance has extrusions on categorized and graduated layers, and reading only the branch
the 2D renderer matched would have dropped them.
*Verified live* (geodeploy-lite, read-only): **40 vector styles and 12 raster styles round-trip with
zero phantom changes**, including all 7 real extrusions. Two of those cases no amount of reasoning
produced — `CO` is stored `{"enabled": true}` with NO height (GeoDeploy draws it flat, so QGIS must
too), and a world-scale point layer carries `radius: 10000000`, which is why the plugin's 30 m
fallback footprint must never be written back into a style that named none. Both are now fixtures in
`scripts/test_3d_symbology.py`.
*Also, from the same live read:* the raster `/legend` route reported no `zfactor`, so a PUBLIC
hillshade — whose only styling source is that route — opened flat instead of at its stored
exaggeration. Fixed in `routers/data/raster.py`; **needs an API deploy to take effect**.
*And forward work for contour styling:* `raster_style_of` and `comparable_style` now carry keys this
plugin has never met instead of dropping them, and only a HILLSHADE drops the stretch (any other
algorithm keeps it) — so `algorithm: "contours"` with `increment`/`thickness` round-trips the day it
lands. `_RASTER_KEYS` says what to add there when it does.)
2026-08-17m (**"raster symbology can't be changed, and for polygons I can only change fill colour"
— neither was a symbology limit. It was the SOURCE.** QGIS decides which renderer to offer from the
layer TYPE: server-rendered raster tiles reach it as one band of RGBA, so Symbology shows
"Singleband color data" with no bands and no classes; vector tiles get `QgsVectorTileBasicRenderer`,
a flat list of symbols with no categorized or graduated mode and no attribute statistics to classify
from. Both are the right way to LOOK at a layer and neither can be restyled beyond a colour. Three
changes, and the requirement driving all of them was *faithful both ways*:
*1. `symbology.raster_to_qgis`* — the missing half. `raster_from_qgis` has read QGIS raster
renderers for a while but nothing WROTE them, so `plugin.py` excluded the COG from styling
(`source["kind"] != "cog"`) and "prefer the real data" was a TRADE: values or GeoDeploy's colours,
never both. Now colormap → `QgsSingleBandPseudoColorRenderer` over the named ramp, `color_classes` →
`QgsPalettedRasterRenderer`, `algorithm: hillshade` → `QgsHillshadeRenderer` at GeoDeploy's own
315/45, three bands → `QgsMultiBandColorRenderer`, and a bare stretch → gray — chosen in the SAME
ORDER `services/titiler.get_tile_url` chooses, or QGIS would show a colormap the portal ignores.
**QGIS does not keep a ramp's NAME** (only ColorBrewer/cpt-city ramps answer `schemeName`; viridis
and friends are anonymous gradients), so `P_COLORMAP` records it alongside the ramp's COLOURS and
the name is believed only while those still match — forwards, or exactly reversed, which is how
flipping the ramp in QGIS travels back as `colormap_reverse`. A different ramp means no name, not
the last one we remember. Also new: `raster_style_from_legend` (a raster legend was being read by
the VECTOR reader, which returned `{"color": …}` — a key no raster renderer can use, so a public
raster could never arrive coloured), `raster_style_of` for the flat stored shape, `_qcolor`
(**Qt reads `#rrggbbaa` as `#AARRGGBB`** — GeoDeploy writes alpha LAST, so a transparent "no data"
class became opaque near-black), and raster branches in `comparable_style`/`merge_style`: a raster
read-back is the WHOLE colouring, `bidx: [1]` and no band are the same picture, and a hillshade
ignores the colormap so a stale one is not a difference.
*2. A per-layer Source picker* replaces the global "prefer the real data" checkbox — the right
question in the wrong place: it applied to whatever was added next, named no layer, and listed
neither surface. `sources.alternatives` now labels each with `is_data`. The default is unchanged
(the fast tiles), and a restyle deliberately does NOT make the data surface sticky.
*3. "Restyle this layer…"* reopens the active layer from its data — GeoTIFF, or full features —
carrying the styling it is wearing now, and replaces it IN PLACE (found by layer id, not
`list.index`, whose wrapper comparison would have appended it to the end of a portal group and
changed the portal's drawing order). For a portal raster the current styling is parsed out of the
TILE URL (`sources.raster_style_from_tile_url`), because that is where a portal records how it
colours a raster — reading the layer's default would restyle it to something no portal chose.
*Speed, since the ask was "faithful, not slower":* the default source is untouched; a colormap with
no stretch reuses the range QGIS already computed when it opened the raster rather than asking the
provider for band statistics (range requests over the network on a remote COG — `_default_range`),
and falls back to a bounded 250k-pixel sample only if that is absent. Adding a raster the ordinary
way now also skips a pointless style fetch and no longer logs "saved style could not be applied" for
every one of them — those tiles are already coloured by the server.
*And two fidelity bugs found on the way:* "Save styling to GeoDeploy" sent `opacity: 1.0` and
`popup_fields: []` hardcoded, so saving a colour from QGIS made a half-transparent layer opaque and
DELETED its popup fields along with anything else QGIS cannot draw. It now merges over the stored
style and sends the layer's real opacity, the same rule the portal push path already used.
Tests: `scripts/test_raster_symbology.py` — 9 styles applied and read back through the comparison
`_style_differs` uses, plus alpha, reversal, restretching, a swapped ramp, declined tile layers, the
merge rules, both readers, the tile-URL parser and the no-statistics guarantee.)
2026-08-17l (**a raster could not be restyled at all, and three symbol properties never travelled.**
*The raster blocker was server-side and two layers deep:* the only source of a raster's real band
values is its COG, and `routers/data/raster.py::raster_cog` required `is_public` with NO authenticated
path — so the owner of an unshared raster could not open their own pixels, and "tick Prefer the real
data and restyle the GeoTIFF" was advice that could not be followed. Now readable by any signed-in
user `visible_to` already lets see the layer. **And GDAL needed the token separately:** `/vsicurl/`
does not go through `QgsNetworkAccessManager`, so the Qt request preprocessor never touched it. New
`_install_gdal_auth` sets `GDAL_HTTP_HEADERS` **path-specifically** for the instance's prefix —
never the global option, which would send this instance's bearer token to every `/vsicurl/` URL in
the project. GDAL < 3.6 has only the global option, so it declines and says so.
*Properties:* `outline_width` is a RATIO of the marker radius (`markerImage` draws
`lineWidth = radius * ratio`, default 0.28) and was neither applied nor read — the plugin wrote a flat
1 px, i.e. ratio 0.2, and a width change was invisible. Reported as "stroke color now works well, but
stroke width doesn't seem to be saved". Marker SHAPE and size-BY-FIELD (`_size_from_qgis`, parsing the
`scale_linear` expression `_apply_data_defined_size` writes) now round-trip too.
*And a push stopped deleting things:* `symbology.merge_style` lays the read-back visual keys OVER the
stored style, so 3D extrusion, imported MapLibre paint and popup fields survive a restyle from QGIS
instead of being replaced by the subset QGIS can express. A change of colour or size MODE still clears
the previous mode's leftovers.
`comparable_style` covers the new keys, folds integer-vs-float class bounds, and ignores the derived
`classes_n`. Tests: 7 full styles round-trip with no phantom change, plus every property individually.)
2026-08-17k (**change detection was wrong in both directions, and the user diagnosed it exactly: "when
I only change the symbol fill it detects the change; when I only change the stroke colour it doesn't."**
*Missed edits:* `_style_from_symbol` read the fill colour, the size and the dash — and never the
STROKE, so a stroke-only edit produced an identical dict. It now reads `outline_color` (via
`_stroke_of`, with `Qt.NoPen` → `"none"`) and the marker SHAPE (`_shape_name`, using
`encodeShape`; a shape GeoDeploy cannot express is omitted rather than forced to the nearest match).
*Phantom edits:* the mirror image, reported as "I changed only one style, but it says 3 were
restyled". QGIS has no concept of "unset", so `_symbol_of` fills every gap with the map's default and
a read-back style is always COMPLETE, while a stored one holds only the keys somebody chose. New
`symbology.comparable_style` fills both sides from one table before comparison — and folds the two
geometry-dependent outline defaults (`#ffffff` on a marker, `#1d4ed8` on a fill) into one token, folds
colour case, and DROPS the top-level `color` for a classified layer, whose classes carry the colours.
`portals._style_differs` compares through it. The reader also stopped inventing that colour: it was
taking it from whichever entry came first — the catch-all, or class 0.
One care point: category `value`s are DATA and are no longer case-folded with the colours; folding
them would both hide a real change and mislabel the map.
*Rasters:* unchanged and unchangeable by design — server-rendered tiles are colour, not values. The
`attributes` checkbox label now says "(needed to restyle a raster)", since that checkbox IS the
answer, and the explanation is logged at Info: three identical warnings for three rasters read as
three failures. `_log` gained a level for exactly that.
Tests: ~30 change-detection cases — every edit that must register, and every cosmetic difference that
must not.)
2026-08-17j (**a restyled POINT layer pushed back as no change at all.** QGIS's own vector-tile
symbology editor keeps one UNFILTERED style per geometry type — Polygons, Lines, Points — and
`style_from_vector_tiles` de-duplicated on the FILTER alone, so only the first survived: a user who
changed the point marker had the POLYGON entry read back, a colour they never touched and equal to the
old default. `_style_differs` therefore saw nothing, "Push group to portal" published nothing, and
"Save styling to GeoDeploy" stored the style it already had. Reproduced exactly with a stand-in for
QGIS's editor output before changing anything.
Fixed by recording the geometry ON the layer when the plugin builds it (`P_GEOMETRY`, set in
`_build_layer` and both portal-source paths — a tile layer cannot be asked what it holds) and reading
back only the entries for that geometry. With no geometry recorded and entries that disagree it now
returns `{}` rather than guessing, which `plan_push` turns into "keep what the portal has" — a guess
here would silently publish a colour nobody picked. `_style_from_symbol` also stopped losing a whole
style to one unreadable number: a missing marker size now costs the radius, not the colour.
**The stub was hiding it twice:** `QgsVectorTileBasicRendererStyle` exposed `geometry_type` as an
attribute where QGIS has `geometryType()`, so the new geometry filter matched every entry and the test
passed against the bug. Same lesson as `smoke.py` — a double has to be faithful about TYPES and about
whether something is a method, not merely permissive.)
2026-08-17i (**the public layer page actually works now, and the portal button does something
different from the button next to it.**
*Page:* the public row was missing two things the page needs — a raster's `tile_url` (`lib/mapStyle.js`
skips a raster without one, so the page showed metadata beside an empty map) and the `links`/`catalog`
the Share-links panel shows. `ShareLinksModal` now uses `props.layer.links` when the row carries them
instead of calling the `data:read`-scoped `/links` route, which answered 401 for a signed-out visitor
looking at links that were already loaded. `mapStyle` no longer demands `pmtiles_key` to draw a tiled
GeoParquet layer: the tiling task sets it with `tile_status`, the URL is built from the layer id, and
a public row deliberately carries no storage keys. Every write action on the page is behind
`auth.canEdit`, so signed out there is nothing to press.
*Portal button:* `open_in_browser` opens `/portals/<id>/edit` for a portal — "Add to map" already
offers the published page, so two buttons doing that was one too many. It needs a session and the
numeric id, and an anonymous listing has neither, so it is DISABLED with the reason rather than
falling back to view mode. New `_apply_auth_ui` does the same for push-group / upload / save-styling:
disabled until a token is connected, each keeping its own explanation under the "needs a token" note.
*Test doubles:* `scripts/smoke.py`'s `_Any` returned itself from every method, so `toolTip() + "…"`
and `selectedItems()[0]` failed where real Qt would not. It now returns the right TYPE for named
string and list getters — the stub's job is to be faithful, not merely permissive.)
2026-08-17h (**pushing a restyle from a portal group sent nothing, and for rasters it DELETED the
portal's styling.** Two halves of one mistake: a portal's layers open as the surfaces that draw like
the portal, and neither is what QGIS can read a style back out of.
*Vectors:* `from_qgis` reads FEATURE renderers, and a portal group's vectors are `QgsVectorTileLayer`
— so it returned `{}` and the restyle never left QGIS. New `style_from_vector_tiles` is the inverse of
`apply_to_vector_tiles`: each renderer entry carries a symbol and the FILTER the class was written
with, so `"k" = 'a'` reads back as a category and `"pop" >= 100 AND "pop" < 1000` as a class
(`_CAT_FILTER`/`_parse_range`). A filter this did not write, or two fields in one classification,
degrades to a single symbol rather than being half-parsed into somebody else's classification.
*Rasters:* portal tiles are a picture — QGIS models them as `QgsSingleBandColorDataRenderer`,
"Singleband color data", with no bands to stretch — so there is genuinely nothing to read.
`plan_push` now KEEPS the portal's existing style whenever the QGIS side reads as empty, for vectors
too: pushing `{}` replaced a portal's colormap with nothing, so an attempted restyle silently
destroyed the styling it meant to change. The diff dialog reports these as "Style kept as the portal
has it" with the way to actually restyle a raster (open the GeoTIFF, restyle, Save styling).
Also: the size conversions did not invert — `_symbol_of` writes `radius * 2 * 0.75` while the reader
used `size / 2` and `width * 4` (a leftover from millimetres), so a radius of 5 round-tripped to 3.75
and a line width of 2 to 6. One shared `_style_from_symbol` now divides by the same constant the
writer multiplies by, and `test_tile_symbology` round-trips every mode to keep it honest.)
2026-08-17g (new **"Open in GeoDeploy"** button — `open_in_browser` opens `/layers/<kind>/<uid>`, the
instance's own page for one layer, in the desktop browser; for a portal it opens the portal. The dock
shows a layer's geometry and symbology, and the page shows what it cannot: metadata, field list,
extent, sharing state, and every ready-made link for other tools. The address is PUBLIC — see
`routers/public.py::public_layer` and the new UI route — so a shared layer opens for anyone and a
private one becomes a sign-in prompt on the instance, where the visitor may well have access. That is
why the button needs no token to be useful. `_open_portal` now shares the one `_open_url` helper.)
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
