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
  - `rules.py` — **rule-based rendering ⇄ `style.rules`**. A QGIS rule tree flattens to a list of
    render layers, one per leaf, each carrying the AND of the filters above it and the narrowest
    scale range on its path; that list is `style.rules`, and the server draws one MapLibre layer per
    entry. Each rule stores BOTH its MapLibre `filter` (what the web renders) and the QGIS
    `expression` it came from (what QGIS gets back — a round trip should hand somebody the text they
    typed). An ELSE rule becomes NOT(its siblings); a filter outside the expression subset is
    dropped with a note rather than widened, because a rule that draws everything is a different
    map, not a degraded one. `rules[0]` draws FIRST — QGIS's order, and the opposite of
    `layer_configs`.
  - `qgis25d.py` — **QGIS's 2.5D renderer ⇄ GeoDeploy's extrusion**. 2.5D is not 3D: it draws a
    flat map with a pseudo-perspective block, built from a shadow fill and two GEOMETRY GENERATORS
    that extrude the outline. Two consequences shape the whole module: the height and angle are
    **project variables** (`@qgis_25d_height` / `@qgis_25d_angle`), which is why `Qgs25DRenderer`
    exposes colours and no `height()`; and the geometry generators cannot travel, so what does is
    the thing they imitate — a real `fill-extrusion`. That is *better* than 2.5D and not the same
    picture, so the roof colour becomes the extrusion colour and the angle, wall and shadow ride
    along in `extrusion.qgis25d`, which is what makes a round trip come back as 2.5D rather than as
    a plain extrusion somebody has to rebuild.
  - `labels.py` — **QGIS labelling ⇄ `style.labels`**. GeoDeploy had no labels at all before this;
    they were added to the platform and the plugin together. Text (a field, or an expression put
    through the translator), font, size, colour, halo (QGIS's buffer), offset, rotation, wrap,
    transform, letter spacing, overlap, priority and the label's own scale range all travel.
    **Two sharp edges:** `QgsPalLayerSettings` mixes plain ATTRIBUTES (`fieldName`, `xOffset`,
    `priority`, `scaleVisibility`) with METHODS (`format()`), and calling an attribute raises a
    `TypeError` that a blanket handler turns into "this layer has no labels" — silently; `_value`
    exists for that. And a FONT is not carried verbatim: MapLibre draws **nothing at all** for a
    fontstack its glyphs lack, so a family is mapped onto the stacks the instance can serve, keeping
    weight and slant. Shadows, background shapes and callouts are named as not carried.
  - `vendor/geodeploy/` — the published client, checked in (see below).
- `scripts/test_real_qgis.py` — **the round trip against a real PyQGIS**, and the only test here
  that is not stubbed. Every other `scripts/test_*.py` replaces the QGIS classes, and the stubs
  used to share a base handing each symbol layer a `setWidth`/`width` pair that only
  `QgsSimpleLineSymbolLayer` has: the plugin called `setWidth` on a FILL, CI went green, and in
  real QGIS every polygon layer raised `AttributeError` — QGIS drew it in its own default colour,
  and pushing it back uploaded no styling at all. Shipped, and a user found it. This file audits
  that every QGIS name the plugin calls exists on that QGIS, then applies and reads back a style
  per geometry per mode, for feature layers and for the vector-tile renderer. Run it locally with
  Docker, on both ends of the supported range:

      docker run --rm -v "$PWD/integrations/qgis-plugin":/src -w /src -e QT_QPA_PLATFORM=offscreen qgis/qgis:ltr python3 scripts/test_real_qgis.py
      # ...and again with qgis/qgis:4.2 for the Qt6 build. CI runs both (job `qgis-real`).

  **When you add a stub method anywhere in `scripts/`, put it on the narrowest class that really
  has it and add the matching assertion here.** A stub more generous than the API it stands for
  does not test the code; it tests itself.
- `scripts/coverage_report.py` — **the symbology coverage matrix, read out of QGIS's own
  registries** rather than remembered. Joins `symbolLayerRegistry()`, `rendererRegistry()` and the
  data-defined property definitions against the verdicts declared in the script itself, and **fails
  when this QGIS offers something the table does not classify** — so a new QGIS version forces a
  decision instead of quietly widening a gap nobody wrote down. Four verdicts, each a promise about
  the round trip: `EXACT` (MapLibre draws it natively), `APPROX` (something close, deliberately
  chosen), `CARRIED` (stored and handed back, never drawn), `TODO` (not carried yet — the note says
  what it would take). On QGIS 4.2: 30 symbol-layer types, 12 renderers, 74 symbol properties and
  125 label properties, currently `EXACT: 7  APPROX: 9  CARRIED: 4  TODO: 43`. Runs in CI beside
  `test_real_qgis.py`.
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

## User documentation
`docs/qgis.md` — install (including the **Show also experimental plugins** setting, without which
the plugin is invisible in the Plugin Manager), which QGIS version, connecting, the Source picker,
portals, styling both ways, and the known limitations. That page is the one users read; this file is
for whoever changes the code.

## Releasing
plugins.qgis.org takes a **zip**, not a repository: the plugin may live in a subdirectory of a
monorepo as long as `metadata.txt` links to publicly browsable code. Requirements that bite:
mandatory `metadata.txt` / `__init__.py` / `LICENSE`, working homepage-repository-tracker links, and
**no binaries** (irrelevant here — everything is pure Python).

```bash
python integrations/qgis-plugin/scripts/vendor.py          # refresh the client
python integrations/qgis-plugin/scripts/vendor.py --check  # what CI runs
python integrations/qgis-plugin/scripts/build.py           # the installable zip, into dist/
```

### Uploading to plugins.qgis.org

**There is no API for this.** The upload is a web form behind an OSGeo login, so it cannot be done
from CI without storing those credentials — which is why it is a deliberate manual step rather than
an oversight. (`qgis-plugin-ci` exists and can publish from a workflow with `OSGEO_USER` /
`OSGEO_PASSWORD` secrets, if that trade is ever worth making.)

1. Sign in at <https://plugins.qgis.org/> with **GitHub, GitLab, Google or an OSGeo ID** — any of
   them works. Only the OSGeo route has a condition attached: the address in `metadata.txt` (or on
   your profile) must match the address you correspond from.
2. **Plugins ▸ Upload a plugin**, choose `integrations/qgis-plugin/dist/geodeploy_qgis-<version>.zip`.
3. The form reads everything else out of `metadata.txt`. It rejects the archive outright if the zip
   holds anything but exactly one top-level directory named for the package.
4. Two automated emails follow: one confirming the upload and that a **security scan** is queued
   (the version is not downloadable yet), and one with the scan result — code quality, secrets
   detection, suspicious files. Critical findings **block** the version, and the only way past that
   is to fix them and upload a NEW version number. Results are under the **Security** tab on the
   version page.
5. Then a human approves it. Volunteers, published daily on weekdays — a Friday upload is usually a
   Monday approval.

**What the reviewers actually check** — from <https://plugins.qgis.org/docs/approval/>, and the
first one is the easy way to be rejected:

- **`homepage` must be a page describing what the plugin DOES.** Their words: "Any other links will
  result in the plugin being rejected." A project's front page does not qualify — ours points at
  `docs-geodeploy.kndev.org/qgis/`, which is the plugin's own page. The repository README is an
  acceptable substitute if no such page exists.
- **`tracker`** must be the issue tracker, **`repository`** the browsable code — not a zip, and
  publicly readable.
- **No binaries.** Pure Python here, and `build.py` excludes caches, so this is free.
- They run the plugin on **Windows and Unix** to check it does not crash QGIS.
- For an UPDATE they also check the changelog names what changed, and re-test the links.

**While `experimental=True`, users must tick Settings ▸ "Show also experimental plugins" in the
Plugin Manager or they will not see it at all** — searching finds nothing, which looks exactly like
the plugin never having been published. `docs/qgis.md` says so on the user's side; remember to
remove that paragraph in the same change that drops the flag.

Every version must be higher than the last: plugins.qgis.org will not accept a re-upload of a
version it already has, and there is no way down. That is why the plugin has its own version line
rather than following the platform's — see the note in `CHANGELOG.md`.

## Current status & known issues
- **Written but never run inside QGIS.** Every module parses and the vendored client imports, but
  the Qt/QGIS API calls — renderer construction above all — have not been exercised. Treat the first
  QGIS session as the real test.
- `experimental=True` in `metadata.txt` until that happens.
- No icon yet (`icon.png` is referenced by `metadata.txt` and must exist before upload).
- Styling covers single symbol, graduated and categorized for vectors, and colormap / stretch /
  band / colour-per-value / hillshade / contours for rasters — **both directions**.
  Size-from-a-field, marker shape, stroke colour and width, and a polygon's outline width too.
- **3D EXTRUSION IS NOT DRAWN BY QGIS, and the code that tries is unverified.** `apply_3d` builds a
  `QgsVectorLayer3DRenderer` and the round-trip tests pass against stubs, but in a real QGIS session
  an extruded portal layer still renders FLAT in a 3D map view — reported after opening a 3D portal
  editable, zooming in and adding the view. Do not describe this as working. What IS verified: the
  extrusion is never LOST — `extrusion_from_qgis` returns None for a tile layer and the recorded
  spec survives a push, so a round trip cannot delete a portal's 3D (proven against every extrusion
  on the live instance). Candidate causes for whoever picks this up are in
  `notes_temp/notes_for_future.md`; the feature is on the roadmap under "Every symbol QGIS can draw".
- **What is NOT carried yet, and where it is tracked:** QGIS draws far more than GeoDeploy's
  vocabulary — inverted polygons, 2.5D, hatch and gradient fills, line offsets, markers along a
  line, multi-layer symbols, rule-based rendering, labels. Those are simplified on the way in and
  lost on the way out today. Planned as *"Every symbol QGIS can draw"* in `docs/roadmap.md`, where
  the split that matters is written down: symbols a web renderer CAN express (real round trips,
  each to be wired up) versus symbols it cannot (carry the QML alongside the friendly style, so
  QGIS ⇄ QGIS stays lossless while the portal approximates), plus a fidelity report so an author
  learns which of the two they are in BEFORE they push.
- **A polygon's outline WIDTH round-trips now too** — it used to be the one thing that could not,
  because a MapLibre fill's edge is a fixed hairline. GeoDeploy draws it as a `line` layer beside
  the fill, so the number is real and travels. Note the key means two things: `outline_width` is a
  RATIO of the radius on a point and a WIDTH IN PIXELS on a polygon, which is why
  `comparable_style` takes the geometry — without it a polygon read back at its 1 px default
  compares against a marker's 0.28 and every polygon reports as restyled.
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

## Qt6 / QGIS 4
QGIS 4 is Qt6, where an enum member is only reachable through its scope — `Qt.PenStyle.NoPen`, not
`Qt.NoPen`. The plugin has to run on both, since its floor is QGIS 3.28 (Qt5), so `compat.enum`
asks for the scoped name and falls back to the flat one. Every enum read goes through it;
`scripts/test_qt6_compat.py` walks the AST of every module and fails on a flat one, because the
migration was mechanical across 94 sites and a single one typed later would be invisible until a
user on QGIS 4 hit that path.

The icon is the project's own logo (`ui/public/logo.png`, resized to 128px) rather than a drawn
stand-in — `metadata.txt` requires one and a placeholder is what a reviewer notices first.

**Do not apply the plugins.qgis.org Qt6 report verbatim.** It reports
`QLineEdit.Password` as *"add 'EchoMode' before 'Password'"*, which reads as `Qt.EchoMode.Password`
— a name that does not exist, because `EchoMode` belongs to `QLineEdit`. Every scope in the map was
resolved against the real class instead. And not everything capitalised is an enum:
`QgsVectorFileWriter.SaveVectorOptions` and `QgsColorRampShader.ColorRampItem` are classes, and
scoping them would be a silent `AttributeError`.

The first pass still missed six: `QDialogButtonBox.Ok`/`Cancel`, because the owner map in the fixer
said `QMessageBox`. The AST guard did not catch them either — it only looks for the owners it has
been told about, so an owner absent from that map is an owner unprotected. `QDialogButtonBox` is in
it now, which is the actual lesson: the guard is only as wide as its list.

## The security scan
Every upload to plugins.qgis.org is scanned (Bandit, detect-secrets, Flake8) and **a critical
finding blocks that version until a NEW version number is uploaded** — a burnt version, not a retry.
CI runs the same scan so it fails here instead.

**Declarations are inline `# nosec`, NOT a `.bandit` file, and that is not a style preference.**
plugins.qgis.org invokes bandit with `-t <selected tests>` on the command line. A `.bandit` that
`skips` any test in that set makes bandit abort with:

```
[main] ERROR  Non-exclusive include/exclude test sets: {'B310', 'B110', …}
```

It then scans nothing, reports nothing, and the version is marked as scanned anyway — with a
"Validated (configured)" badge asking an administrator to review suppressions that never applied.
We shipped exactly that in 0.3.1 and their scan report said so. Inline `# nosec` has no such
conflict and is honoured whatever flags they pass.

**Fix first, declare second.** The two MEDIUM findings were real and were fixed:
`connection.http_url` rejects any scheme but http/https before an open (the plugin follows links
that come BACK from an instance, so a hostile one could otherwise have aimed it at `file://`), and a
WMTS capabilities document declaring a `DOCTYPE`/`ENTITY` is refused before parsing, which removes
the entity-expansion class rather than mitigating it. `defusedxml` is not an option: a plugin cannot
pip-install, the same constraint that makes the client vendored.

The rest are the deliberate try/except/pass pattern this code needs to survive Qt API drift, each
carrying its reason next to the `# nosec`.

```bash
cd integrations/qgis-plugin/geodeploy_qgis
python -m bandit -q -r .                                    # plain
python -m bandit -r . -t B101,B110,B112,B310,B314,B405,B605 # the way THEY run it
```

Findings in `vendor/` are fixed in `cli/geodeploy` and re-vendored — never edited in the copy, or
`vendor.py --check` fails.

## Last updated
2026-09-03e (**markers along a line.** QGIS's marker line and hashed line repeat a symbol down a
line; `_line_decoration_symbol` reads it across ALL symbol layers — a decorated line is nearly always
a plain stroke with the markers stacked on top, and reading only `symbolLayer(0)` is what made a road
with ticks arrive as a plain road. The repeated symbol ships as a picture and the server draws it at
`symbol-placement: line`. 202 checks green on QGIS 3.44 and 4.2.)

2026-09-03d (**markers that are pictures, labels, fonts.** A marker symbol GeoDeploy has no words
for — SVG, raster, font, ellipse, filled, or several layers stacked — is now RENDERED by QGIS and
shipped as a PNG data URI (`style.marker_image`, `symbology._marker_picture`), instead of arriving
as a coloured dot. MapLibre does not need to understand an icon, only to have its pixels, so one
branch covers every marker kind including future ones; the id is content-addressed (FNV-1a, twinned
in `ui/src/lib/symbology.js`) so two layers with the same icon share one image. Capped at 96 KB,
since a style rides in every published portal's style.json. Labels and their fonts landed in the
same round — see `labels.py`. 197 checks green on QGIS 3.44 and 4.2.)

2026-09-03c (**rule-based rendering, 2.5D, and the class cap.** Rules travel as `style.rules` — one
entry per leaf of the QGIS rule tree, each with its filter translated by the new
`geodeploy.expressions`, its own symbol and its own zoom range; ELSE becomes NOT(siblings), nesting
ANDs, and a filter outside the expression subset is DROPPED with a note rather than widened.
**2.5D** becomes a real `fill-extrusion`, with the angle/wall/shadow carried in `extrusion.qgis25d`
so the round trip returns 2.5D — note its height and angle are PROJECT variables, not renderer
properties. **Ramps now interpolate**: they used to snap to one of seven anchor stops, so 8 classes
gave 7 colours and 12 gave 7 — which is what the old 12-class cap was working around. Classes are
now 2–100 and categories past twelve get a generated hue wheel instead of a cycled palette. 144
checks green on 3.44 and 4.2.)

2026-09-03b (**four more reader defects, all found by running `from_qgis` over a REAL 16-layer QGIS 4
project rather than over memory layers** — the synthetic harness proves the loop closes, not that it
survives symbology a person authored. (1) `renderer.symbols(None)` raises `TypeError` on QGIS 4, so
every rule-based layer uploaded nothing at all; `_symbols_of` now tries a real `QgsRenderContext`
first and walks the rule tree, cloning what it finds. (2) The graduated and categorized branches
returned the classes and NO shape — the cause of "correct categories but no dashed lines"; both now
merge the first class symbol's shape, minus its colour. (3) Non-simple symbol layers (SVG, raster,
font markers; marker lines) returned colour only; size is now read from the SYMBOL, which has it
whatever sits inside. (4) `QgsRendererRange.symbol()` and `QgsRendererCategory.symbol()` are
BORROWED from temporaries — holding one past the loop segfaults QGIS with no traceback, and piped
stdout is lost, so rerun with `python3 -u` when a run dies silently. Result on that project: 16 of 16
layers carry real symbology, where 9 did before. Also: an upload now LINKS the QGIS layer it came
from to the new GeoDeploy layer, so Save styling and Restyle work without reopening; a **Refresh**
button beside Connect; and an expired token is explained rather than shown as HTTP 401.)

2026-09-03 (**the polygon symbology bug, found by a user and confirmed against real QGIS.**
`QgsSimpleFillSymbolLayer` has `setStrokeWidth`/`strokeWidth`, never `setWidth`/`width` — the plugin
called the line setter on a fill in BOTH directions, so every polygon layer raised `AttributeError`:
`apply_to_vector_tiles` swallowed it into "Could not style the vector tiles" and QGIS drew its own
default colour, and `from_qgis` swallowed it into `{}` so a polygon uploaded unstyled. Fixed via
`_set_stroke_width` / `_stroke_width_of`, with `setStrokeWidthUnit` added to `_use_points` (a fill's
outline was otherwise measured in millimetres against a CSS-pixel number). Three fixes came with it:
the tile branch of `from_qgis` caught only `ImportError`, so anything the tile reader raised escaped
into `save_style` with no handler; `apply_to_vector_tiles` let one geometry's failure abort the
whole renderer, which is what left GeoParquet layers with no `geometry_type` completely unstyled;
and `other_color` was missing from `_STYLE_DEFAULTS`, so a categorized layer opened and pushed
straight back read as edited. `scripts/test_real_qgis.py` and the CI `qgis-real` matrix are the
answer to how this survived a green suite — 105 checks, 0 failed on 3.44 and 4.2.)

2026-08-18c (**CORRECTION to the entry below: 3D extrusion does not draw in QGIS AT ALL, not merely
on tiles.** Tested for real — a 3D portal opened editable, zoomed to, then View ▸ New 3D Map View —
and the polygons are still FLAT. So the previous entry's implication, that the editable portal mode
shows 3D, is wrong, and every claim that 3D "travels both ways" has been removed from the changelog,
the plugin's published description and the roadmap. What survives scrutiny is narrower and still
worth having: the extrusion is never LOST. `extrusion_from_qgis` returns None for a layer that
cannot hold 3D, the recorded spec is returned unchanged while the symbol matches it, and all six
live extrusions round-trip with no phantom change — so opening a portal and pushing it back cannot
delete somebody's 3D. The DISPLAY half is unbuilt, and is now the first item under "Every symbol
QGIS can draw" in `docs/roadmap.md`. Candidate causes — an unresolved `qgis._3d` import that logs
at Info and returns False, terrain-relative altitude clamping, and above all the CRS units problem
(in an EPSG:4326 project a height in metres is read as DEGREES) — are in
`notes_temp/notes_for_future.md`. The lesson for this file: stub tests prove the code does what it
was written to do, not that QGIS draws anything. Nothing may be described as working in QGIS until
it has been seen working in QGIS.)
2026-08-18b (**"3D extrusions don't show in the QGIS 3D view" — they cannot, on tiles, and the
plugin now says so.** A `QgsVectorLayer3DRenderer` needs a FEATURE layer. A portal opened *as the
portal draws it* hands QGIS vector tiles, `apply` routes those to `apply_to_vector_tiles`, and
`apply_3d` is never reached — so the 3D view shows flat polygons. Nothing was wrong and nothing was
lost (the extrusion is still stored, and a push from a tile layer returns None, so it cannot be
deleted), but silence about it is indistinguishable from "not implemented". Reported on a portal
whose two GeoParquet polygon layers both carry real extrusions.
Now: `apply` logs the reason and the fix at Info when it meets an extruded tile layer, and
`open_portal_as_group` NAMES those layers in its result — "reopen with Source set to Editable to see
and edit it. The 3D itself is unchanged." The editable portal mode already draws them properly,
since it opens each layer from its data. `test_3d_symbology` pins the two halves that matter: a tile
layer is given no 3D renderer, and the one it never had is not cleared either.)
2026-08-18 (**the default source now follows the BACKEND, and a portal can be opened editable.**
*Defaults:* PostGIS holds the layers people classify — the attribute table is the point, and Martin's
tiles carry only what was baked into them — so a PostGIS layer now opens over OGC API - Features,
ready to be styled by a column. Tiled GeoParquet is the large-data backend and keeps its tiles.
`sources.describe` therefore takes a THREE-valued `prefer_attributes`: `None` means "this backend's
default", `True`/`False` are a choice somebody made — two values would not do, because `False` has
to still mean "give me the tiles" for the picker to offer both. `prefers_attributes()` is the one
place that decision lives, and `alternatives()` orders the picker by it. The dock's sticky
preference starts as `None` for the same reason: `False` would have overridden the new default on
every layer.
*Portals:* the Source picker is no longer blank for a portal — it offers **"As the portal draws it"**
and **"Editable — each layer from its data"**. The editable group opens every layer from its own
data (features, or the GeoTIFF) and then paints it with the PORTAL's styling, so all of QGIS's
symbology applies to a portal layer exactly as it does to a single one, and `Push group to portal`
sends it home. A raster's portal colours are parsed back out of its baked tile URL first, since that
is where a portal records them. A layer whose data cannot be reached — not in the listing this token
can see — still opens from the portal's tiles and is NAMED in the result, rather than silently
arriving unrestylable.
*And the contour parameters reached the comparison:* they live in `_RASTER_KEYS` so a change of
algorithm clears them, which also filtered them out of `comparable_style` — so changing a contour
interval from 5 m to 25 m, the most visible edit a contour map has, reported as "unchanged".)
2026-08-17p (**class LABELS round-trip.** Values and colours already did; the reader built
`{value, color}` and dropped the label, so pushing a classified raster back from QGIS replaced its
classes with unlabelled ones and every legend — the layer page, every portal — fell back to bare
numbers. QGIS labels a class with its own value when nothing else is given, so that case folds to
"no label" in `_comparable_raster_class`: a raster whose classes were never named must not report as
edited on every push. The label text is DATA, like a category value, so its case is not folded —
"Water" and "water" are a real difference.)
2026-08-17o (**contour styling round-trips too — and it needed the record-and-verify device a THIRD
time.** GeoDeploy grew `algorithm: "contours"` (with `increment`/`thickness`/`minz`/`maxz`), and QGIS
has no renderer for it: QGIS makes contours with a processing algorithm that outputs a VECTOR layer.
So the raster is drawn here with its stretch alone — honest — but reading THAT back reports a plain
stretch, and `merge_style` treats a raster read-back as the whole colouring, so opening a contour
layer and pushing it back would have turned it grey. `P_RASTER_ALGO` records the untranslatable keys
with a signature of the renderer QGIS was actually given, and `with_algorithm` puts them back on
every `raster_from_qgis` return path while that still matches; picking a palette or classifying the
layer changes the renderer, the signature stops matching, and the replacement travels as the real
edit it is. Hillshade is deliberately NOT recorded — it became a real renderer and reads back on its
own, and shadowing it would restore a stale copy over a genuine change.
`sources.raster_style_from_tile_url` also learned `algorithm_params`, since a portal drawing contours
every 10 m carries that number nowhere else. `raster_style_of` and `comparable_style` now CARRY keys
the plugin has never met rather than dropping them, so the next server-side property survives before
anyone teaches the plugin about it.)
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
