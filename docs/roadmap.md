---
description: >-
  What GeoDeploy has shipped, release by release, and what is being built next.
---

# Roadmap

GeoDeploy is at **v1.6.1**. Everything under *In v1.0* and in the releases after it is built and
running in production; the groups at the end are what comes next.

<div class="gd-legend" markdown>
:material-check-circle:{ .gd-ok } **Shipped** ·
:material-progress-clock:{ .gd-wip } **Finishing** ·
:material-circle-outline: **Planned**
</div>

---

## In v1.0

<div class="gd-rel done" markdown>
### Install and operate
<span class="gd-when">Shipped</span>

<p class="gd-goal">One command on a bare VPS, then everything an operator needs from inside the
app — because "exec into the container" is not an answer for whoever has to keep this running.</p>

- [x] One-line installer; the whole stack up behind nginx
- [x] Setup wizard: **let GeoDeploy install PostGIS, or connect one you already run**
- [x] Same choice for storage: **managed MinIO on this server, or any S3-compatible provider**
- [x] Infrastructure panel — per-service logs, terminal, deployments, start / stop / restart
- [x] **Scheduled backups** of database, files and state to a separate destination
- [x] **In-app restore**, backup history, and a guarded manage-and-delete section
- [x] One-button update, with a preflight that refuses to run over work in progress
- [x] Owner-editable environment variables, allow-listed and applied per service
- [x] Connection details for the managed PostGIS and MinIO — masked, revealed, copyable
- [x] Demo mode — a public sandbox, wiped hourly, behind one flag ([live](https://geodeploy-demo.kndev.org))

</div>

<div class="gd-rel done" markdown>
### Data
<span class="gd-when">Shipped</span>

<p class="gd-goal">Upload what you have, at the size you actually have it. Twenty million features
should pan and zoom on a small server, without a database in the hot path.</p>

- [x] Shapefile, GeoPackage, GeoJSON, KML, CSV with WKT, GeoParquet, GeoTIFF
- [x] Two vector backends — **PostGIS** served as vector tiles, and **GeoParquet** with no table at all
- [x] Rasters converted to **Cloud-Optimized GeoTIFF**, served by TiTiler
- [x] Automatic **PMTiles** tiling; heavy layers stream by HTTP range request
- [x] deck.gl + DuckDB viewport rendering, with a density-grid overview while detail loads
- [x] Faithful tiling — display simplification off by default, so small features are not dropped
- [x] Direct-to-storage uploads for large files, bypassing the API entirely
- [x] In-browser click-to-identify and **draw-a-box export**, at near-zero server cost
- [x] Register data already in PostGIS or S3 **without re-uploading it**
- [x] Native CRS preserved; reprojection for display rather than on ingest

</div>

<div class="gd-rel done" markdown>
### Portals
<span class="gd-when">Shipped</span>

<p class="gd-goal">A published map that documents itself, in three shapes — because a catalog and a
narrative are not maps with extra panels.</p>

- [x] **Three experiences**: web map, story map, catalog
- [x] **Story maps** — ordered sections of rich text, each with a captured camera and layer state
- [x] **Catalog portals** — facet rail (folder, type, keywords, licence), result cards, side map
- [x] Editor with live preview, kept in parity with the published runtime
- [x] Symbology: colour, opacity, dashes, marker shapes and sizes — one style per layer
- [x] Layer icons and canvas markers, legend swatches, per-layer legends
- [x] Layer folders — nestable, ordered, collapsible
- [x] **About pages** — a WYSIWYG editor with pasted images, per-layer metadata and data links
- [x] Template gallery, per-portal branding, basemap chosen separately from theme
- [x] External services — XYZ, WMS, remote tiles — alongside hosted data
- [x] 3D globe as a saved start view; navigation history on every map
- [x] Responsive layouts across the dashboard and the published portals

</div>

<div class="gd-rel done" markdown>
### People and access
<span class="gd-when">Shipped</span>

<p class="gd-goal">From a single admin to an organisation, with ownership, permissions, and a record
of who did what.</p>

- [x] Role ladder — viewer → editor → admin → a single transferable owner
- [x] **Invitations**: emailed when SMTP is configured, otherwise a copy-able link
- [x] **Single sign-on (OIDC)**, optional, alongside password login
- [x] Per-resource visibility — private ⊂ organization ⊂ public
- [x] Four access tiers on published portals, enforced server-side
- [x] **Scoped API tokens** — shown once, stored hashed, for scripts and desktop tools
- [x] Secrets encrypted at rest; sessions revoked on password change
- [x] **Audit log** with a paginated activity view
- [x] Optional SMTP for notifications — any relay, no vendor lock-in

</div>

<div class="gd-rel done" markdown>
### Open formats
<span class="gd-when">Shipped</span>

<p class="gd-goal">Nothing published here should need GeoDeploy to read it.</p>

- [x] **STAC 1.0.0 API** — collections, items, ready-to-use assets
- [x] **OGC API - Features** — landing, conformance, collections, items
- [x] TileJSON for vector and raster layers — one URL per source
- [x] COG, PMTiles and GeoParquet read directly by QGIS, DuckDB, Python and R
- [x] CORS on public data and catalog endpoints, so browser clients work
- [x] Share links, with a panel in My Data

</div>

<div class="gd-rel done" markdown>
### Finishing v1.0
<span class="gd-when">Shipped</span>

<p class="gd-goal">The last of it: verifying on real hardware what the tests can only check in
parts, then the release notes and the tag.</p>

- [x] Apache 2.0 licence, contribution guide, documentation site
- [x] **Backup → restore round trip proven end to end** on a live instance
- [x] **Scheduled wipe-and-restore proven** (demo mode runs the same restore path hourly)
- [x] Release notes
- [x] **A clean install verified from scratch** on fresh machines — all four combinations of
      managed/external PostGIS and managed/external object storage
- [x] **An upgrade exercised on a live instance** — v1.1 installed from the in-app updater's
      release channel. (Strictly this was branch → v1.1 rather than v1.0 → v1.1; it drives the same
      machinery — fetch tags, resolve the target, reset, rebuild, verify the new code is running.)

</div>

<div class="gd-rel done" markdown>
### v1.1 — maps that show what the data says
<span class="gd-when">Shipped · 2026-08-07</span>

<p class="gd-goal">A portal could show <em>where</em> things are. This is the release where it can
show <em>what they are</em> — and where choosing a version to run stops being a matter of SSH.</p>

- [x] **Data-driven symbology** — colour from a field: categorized for text values, graduated for
      numbers (quantile, equal-interval, natural breaks), with a legend that matches because the
      legend *is* the class list
- [x] **Classified points keep their marker shape** — a star stays a star when it is coloured by a
      field
- [x] **Outlines that can be none**, and point outlines with a thickness — which is how a ring is drawn
- [x] **3D**: polygons extruded by a field, points as bars, a tilt control, and a starfield behind
      the globe
- [x] **Portals load as one piece** instead of assembling themselves in front of the visitor
- [x] **Choose which version to install** — main, the latest release, a specific release, or any
      branch; and the updater verifies the new code is actually running
- [x] Backup history can be tidied; large uploads survive a restore
- [x] Installed from the release channel on a live instance
- [x] **Fixes found by using it**: a bar near the antimeridian striped the whole planet; a shapefile
      declaring "Unknown" geometry took the point path; rasters requested tiles across the whole
      world; download-by-area found nothing on the globe

</div>

<div class="gd-rel done" markdown>
### v1.2 — from an upload to a map anyone can open
<span class="gd-when">Shipped · 2026-08-07</span>

<p class="gd-goal">Rasters that used to fail silently, and layers another tool can actually open.
Every raster fault fixed here reported itself as healthy — the layer said <em>ready</em>, its
TileJSON was valid, and only the tile server's log knew otherwise.</p>

- [x] **Big and awkward rasters work** — a 3 GB upload no longer dies writing overviews (BigTIFF),
      and a 4-band multispectral image renders instead of failing every tile at the PNG encoder
- [x] **Hillshade works from the editor**, not only from the published legend
- [x] **A world layer no longer breaks every tile** — geographic data is clipped to the Web Mercator
      band at import, so a dataset reaching Antarctica stops making the projection refuse
- [x] **One oversized tile no longer hangs a portal** — a 200 m drone plot was still being requested
      at zoom 3, and the timeout left a catalog portal loading forever
- [x] **WMTS for QGIS** — paste one URL and *Zoom to Layer* goes to the data, which an XYZ link can
      never do because it has nowhere to carry an extent
- [x] **Tiles that miss a raster come back empty, not missing** — no more 404 storms in someone
      else's console
- [x] **Share links say which tool each one is for**
- [x] **Portals**: *Start tilted* as an authoring choice, real symbology in the catalog's on-map
      list, a phone layout where the results are not a sliver, SVG logos that take the theme colour,
      and a globe thumbnail that keeps the space behind the earth

</div>

<div class="gd-rel done" markdown>
### v1.3 — the CLI, and getting data back out
<span class="gd-when">Shipped · 2026-08-14</span>

<p class="gd-goal">v1.2 made the data readable by other tools. This one is about reaching an instance
without a browser, and about being able to take a copy with you.</p>

- [x] **A real CLI**, not an example script — every argument the API takes, the v1.1 symbology
      included, with its own section in the docs and tests so it is verified without anyone
      checking by hand. Also the Python client the QGIS plugin is built on: zero dependencies,
      Python 3.9+, so a plugin can vendor it. On PyPI: `pip install geodeploy`
- [x] **Download any layer, whole** — a PostGIS table built to GeoPackage/CSV/GeoJSON, a GeoParquet
      layer straight from its own partition files (uncapped, lossless, no worker), a raster as its
      COG. A built export that hits the row cap now SAYS so, in the archive, in the job status and
      in the CLI's exit code
- [x] **An anonymous index of what an instance publishes** (`/api/public`) — public portals and
      public layers by kind, so a plugin can start from a URL alone
- [x] **A legend anyone can read** (`/api/data/{kind}/{ref}/legend`) — the swatches and labels the
      portal draws, served rather than re-derived by each renderer
- [x] Collapsible legend entries in the layer list, with a collapse-all ([#9](https://github.com/bravemaster3/GeoDeploy/issues/9))
- [x] Class count stops snapping back, and the ceiling agrees with the server ([#10](https://github.com/bravemaster3/GeoDeploy/issues/10))
- [x] **Invert a colour ramp** ([#11](https://github.com/bravemaster3/GeoDeploy/issues/11))
- [x] **A raster's zoom floor read from the file** — measured from its overview pyramid at ingest
      instead of guessed from its extent ([#17](https://github.com/bravemaster3/GeoDeploy/issues/17))
- [x] **Size from a field** — bigger markers for bigger values, thicker lines for busier roads ([#21](https://github.com/bravemaster3/GeoDeploy/issues/21))
- [x] **Style a layer in My Data**, not only inside a portal — the same panel, reused ([#23](https://github.com/bravemaster3/GeoDeploy/issues/23))
- [x] **Story maps that work on a phone** — portrait stacks the map above a sideways-scrolling
      narrative; the control cluster stops running off a landscape screen ([#27](https://github.com/bravemaster3/GeoDeploy/issues/27))
- [x] **Fixes found by using it**: a CSV with an `id` column could not be imported at all; the
      legend 404'd for the owner of their own layer; the share links sent QGIS to a URL it cannot
      open; and one failing schema migration silently disabled every migration after it

</div>

<div class="gd-rel done" markdown>
### v1.4 — into the tools you already use
<span class="gd-when">Shipped · 2026-08-17</span>

<p class="gd-goal">The CLI made an instance reachable without a browser. This one puts it inside the
desktop GIS people already have open.</p>

- [x] **A QGIS plugin** — browse the catalog, add a layer, style it, publish back. Built on the
      packaged client, which is why that client has no dependencies and runs on Python 3.9.
      *Built; not yet run in anger inside QGIS, and `experimental` until it has been*
- [x] **Styling that travels** — portal and layer style interchange with GeoLibre and QGIS, for
      every layer type. *Both directions now for vectors and rasters, including a polygon's outline
      width. 3D extrusion is carried safely but is not yet DRAWN by QGIS — see "Every symbol QGIS
      can draw" below*
- [ ] **Download a backup**, and **restore from disk** — state-only (small, covers a bad restore or
      a botched update) separately from the full copy including objects. *Slipped: designed, not
      built*
- [ ] **Labels** — the other half of data-driven symbology v1.1 did not ship, and unlike size this
      one does not exist anywhere yet. *Slipped; carried forward*

</div>

<div class="gd-rel done" markdown>
### v1.5 — dashboards
<span class="gd-when">Shipped · 29 Aug 2026</span>

<p class="gd-goal">A fourth experience, where the map is one widget among charts, numbers and
filters — for the questions a reader asks of the DATA rather than of the geography.</p>

- [x] **The archetype itself** — `dashboard` alongside web map / story map / catalog: a 12-column
      grid of widgets, with the map placed into a cell rather than the page placed around it
- [x] **Widgets** — indicator, gauge, chart, list/table (spreadsheet *or* cards), column profile,
      scatter, selector, search box, details panel, legend, raster statistics, and the map
- [x] **Cross-filtering** on three channels — an attribute predicate, a geometry, and a selection —
      combining with AND, each clearable from a filter bar. Without it this is a page of pictures
- [x] **Filter by the map's extent**, opt-in, with every widget it narrows saying **· in view**
      for as long as the tool is on
- [x] **Aggregates computed server-side**, in each layer's own storage — SQL for PostGIS, DuckDB
      in place for GeoParquet. Sending a million features to the browser to count them was the
      failure mode to design against
- [x] **Linked layers** — declare that two layers share a column and an attribute filter travels
      between them, pushed into the engine as a subquery rather than passed around as a list of ids
- [x] **Charts that carry their weight** — several measures on one axis, colour per category or a
      shaded ramp for an ordered key, printed values, and a scatter that samples honestly
- [x] **Widgets pinned to the map** — eight anchors, or docked into the map's own control cluster,
      collapsible to a single icon
- [x] **Six starting templates**, each a working layout rather than a blank grid
- [x] **Published like any other portal** — one URL, the four access tiers, embeddable, and it
      stacks on a phone
- [x] **The map narrows every layer it draws**, each by its own filters — and a linked filter can
      narrow it too, behind a per-map opt-in with a chosen key limit and an on-map notice when it
      passes that limit rather than a map that looks narrowed and is not

</div>

<div class="gd-rel done" markdown>
### v1.5.1 — a dashboard you can actually author
<span class="gd-when">Shipped · 31 Aug 2026</span>

<p class="gd-goal">v1.5 shipped the dashboard; this is a fortnight of building real ones with it.
Almost every item here is something that could not be said, sized or read until someone tried —
which is the only way this list could have been written.</p>

- [x] **Wide data plots** — let the COLUMNS be the X axis, for data stored one column per year,
      with the tick labels trimmed and shifted (`gdp1` + 1959 reads as `1960`) and an overall line
      across the current selection
- [x] **Words the author chooses** — axis titles, widget subtitles, and a card heading that follows
      them. A column name is what the data is called, not what it measures
- [x] **Charts that fit their card** — the legend takes its room before the plot does, on the
      multi-series line, the grouped bars *and* the pie, with **Plot size** to overrule the split
- [x] **Readable axes** — gridlines and ticks at intervals on line and scatter plots, a gauge that
      no longer clips its own arc, and a histogram that names the range it covers
- [x] **A scatter that says which feature a dot is** — hover labels from columns you nominate, and
      a point size that suits twelve features or five thousand
- [x] **Several table rows at once** — <kbd>Ctrl</kbd> and <kbd>Shift</kbd> as every desktop list
      uses them, with the map fitting the whole selection and the selection surviving a page turn
- [x] **A new selection replaces the last** rather than compounding with it — drawing a box after
      clicking a feature was asking for the features that are both, and getting none
- [x] **Fill the screen**, per dashboard: the rows share the window instead of each being the row
      height, stretching where there is room and scrolling where there is not
- [x] **Zoom to what a chart selected**, so the filter and the view agree
- [x] **Basemaps that are free without an account** — CARTO out, OpenStreetMap first
- [x] **A card thumbnail of the whole dashboard**, not of the map cell inside it
- [x] **On the map itself** — draw tools you can drag clear of whatever they overlap, a coordinate
      readout that names its CRS and shares one line with the scale bar and the attribution, and
      both legible in dark mode

</div>

<div class="gd-rel done" markdown>
### v1.5.2 — a restore that leaves spatial queries working
<span class="gd-when">Shipped · 2 Sep 2026</span>

<p class="gd-goal">A backup you cannot restore is a guess — and so is one that restores while
quietly breaking every spatial query until somebody restarts the service.</p>

- [x] **The restore no longer replaces the PostGIS extension.** `--clean` dropped it successfully
      (tables go first, so nothing depends on it by then) and the rebuilt `geometry` type came back
      with new OIDs, stranding every connection open across the restore. Spatial predicates failed;
      `COUNT(*)` did not, which is what made it look like a dashboard bug
- [x] **The worker recycles its connections after a restore**, beside the schema and Martin repairs
      it already did
- [x] **A 502 logs the exception it was raised for** — the message naming the cause used to reach
      only the response body, which neither the service log nor the dashboard shows

</div>

<div class="gd-rel done" markdown>
### v1.5.3 — the restore fix, finished
<span class="gd-when">Shipped · 2 Sep 2026</span>

<p class="gd-goal">v1.5.2 kept the PostGIS extensions installed and then could not restore at all on
a database that has <code>postgis_topology</code> or <code>postgis_tiger_geocoder</code> — which the
standard image installs. A failed restore is worse than the bug it was fixing.</p>

- [x] **A restore no longer fails on the schemas its extensions own** — `topology` and `tiger` can
      be neither dropped nor recreated once the extension stays, and that is the intended outcome,
      not an error
- [x] **A missing `psql` degrades to a warning** rather than taking the restore down with it
- [x] **An end-to-end restore test** — dump a real spatial table, restore it through the real code
      path, assert the rows come back and the `geometry` type keeps the same OID. It is what caught
      the v1.5.2 regression, which the pure-function tests could not see

</div>

<div class="gd-rel done" markdown>
### v1.5.4 — a restore that does not lie about the database
<span class="gd-when">Shipped · 2 Sep 2026</span>

<p class="gd-goal">An in-place restore is DDL against a live system. Everything it drops is missing
for a moment that something else can act on — and one of those moments left an instance insisting it
had no database.</p>

- [x] **`setup_config` survives a restore as one row with its primary key.** The API inserts a blank
      default row into the window where `--clean` has emptied the table, the snapshot's row lands on
      top, and the primary key can no longer be created — leaving two rows, no key, and an instance
      that reports "cannot reach its database" while the database answers normally
- [x] Repaired before `_restore_own_db_settings()`, which updates that row and would otherwise fix
      one copy while the API read the other

</div>

<div class="gd-rel done" markdown>
### v1.6 — every symbol QGIS can draw
<span class="gd-when">Shipped · 10 Sep 2026</span>

<p class="gd-goal">v1.4 made styling travel both ways for the symbology GeoDeploy itself has. This
is the rest of what QGIS draws — and an honest account of where a web map cannot follow. Measured
against real PyQGIS on both QGIS 3.44 LTR and QGIS 4.2, in CI, on every commit.</p>

- [x] **Rule-based rendering**, both ways — a rule's filter, its symbol and its own scale range
- [x] **Multi-layer symbols** — a casing under a road, a dashed overlay on a solid line — including
      inside a rule, which was the half that lasted longest
- [x] **Markers along a line** with their spacing, **line offsets**, **hatch and pattern fills**,
      **gradient fills**, **2.5D**, **centroid fills**, **heatmaps**, **arrow lines**, **blend modes**
- [x] **A class carries its own symbol**, not only its colour
- [x] **Anything GeoDeploy has no words for travels as a picture** — an SVG or font marker rendered
      by QGIS, at the size QGIS drew it, so the portal shows the thing you drew
- [x] **Labels as symbology**: rule trees, the text expression, halo, letter spacing,
      capitalisation, offset, priority, per-label scale ranges, and where the text sits — at a
      point or bent along the line, on it or above it
- [x] **One label per feature.** A big polygon carried its name once per tile it touched; labels for
      polygons are drawn from a point inside the shape, with "label every part" as an option
- [x] **A fidelity account before you push** — the push dialog names what will be uploaded,
      registered, restyled and left out, and `coverage_report.py` classifies every symbol QGIS
      offers as exact, approximated, carried or not yet done

<p class="gd-goal">And the other half of the same release: a portal is rarely only your data, and a
public portal should not be a lesser portal for the person reading it without an account.</p>

- [x] **Seven kinds of external source** — XYZ, WMS, WMTS, WFS, OGC API - Features, third-party
      vector tiles and remote PMTiles — from the web UI, the CLI and the QGIS plugin alike
- [x] **Validated when added**: a WFS and an OGC API collection are fetched, a TileJSON is read, a
      PMTiles header is parsed, so a wrong name fails then rather than as an empty published layer
- [x] **Fetched through your instance where a browser would be blocked** by the provider's CORS
      policy — GeoJSON, vector tiles, PMTiles ranges — so a source works for every visitor
- [x] **WCS refused by name, with the way forward.** It is not a display service; most WCS servers
      publish the same data over WMS
- [x] **A public portal opens in QGIS exactly as it does with a token** — one code path now, and
      `GET /api/public/portals/{slug}` serves the authored styling to anybody
- [x] **Delete several layers at once** in My Data, with the portals they are used in named first
- [x] **No basemap** as a choice, on a white ground; **zoom to layer works on a phone**; a control
      flyout fits the screen it opens on

</div>

<div class="gd-rel done" markdown>
### v1.6.1 — "no basemap" actually removes the basemap
<span class="gd-when">Shipped · 11 Sep 2026</span>

<p class="gd-goal">Choosing it in the editor appeared to do nothing, and the editor was innocent:
its preview is a REAL published bundle, so what the preview shows is decided at publish.</p>

- [x] **The bundle honours `__none__`.** It is deliberately not a catalog entry — there is no
      service behind it — so the lookup found nothing and the template's own basemap stayed baked
      in. The runtime switcher could still hide it afterwards, which is why it looked as though
      only the published portal honoured the setting
- [x] The basemap is taken OUT at publish rather than hidden on load, on a white ground, so a
      portal opens as it was authored with no flash of a map nobody asked for

</div>

---

## Next up

<div class="gd-rel" markdown>
### Into the tools you already use
<span class="gd-when">Planned</span>

<p class="gd-goal">Your data can already leave GeoDeploy in open formats. This is the return
trip — edit in the tool you prefer, publish back.</p>

<p class="gd-goal">The QGIS plugin and style interchange shipped in v1.4 and were finished in
v1.6 above — including external services in both directions. What is left here is the rest of the
round trip, not yet scheduled.</p>

- [x] **A `.geolibre.json` importer** — `POST /api/interop/geolibre/preview` (a dry run that
      says what would be imported) and `/publish` (creates a layer per source and builds the
      portal). Shipped in v1.0
- [ ] **Push from GeoLibre** — the other end: a "Publish to GeoDeploy" plugin inside
      GeoLibre, so a project goes across without exporting a file first
- [ ] Write-back: expose a layer as editable GeoJSON and re-ingest the edit
- [x] Catalog **search** — STAC `GET`/`POST /api/stac/search` by bbox, collection, datetime
      and id (what QGIS's STAC browser and pystac-client send), and the catalog experience's
      own search box over names, keywords and abstracts. Shipped in v1.0 and v1.4
- [ ] **Free-text search across the whole instance** — one box that finds a layer, a portal
      or a dataset by anything written about it, rather than a filter per surface

</div>

<div class="gd-rel" markdown>
### Finishing what v1.5 started
<span class="gd-when">Planned · next</span>

<p class="gd-goal">Two items scoped into the dashboard release that it shipped without. Named here
rather than ticked there, because a dashboard nobody has measured on ten million features is not the
same as one that has been.</p>

- [ ] **Linked or detached, chosen per dashboard.** A *linked* dashboard reads its layers live, so
      the numbers move when the data does — that is what is built today. A *detached* dashboard is
      built on saved queries and keeps reporting what it reported, which is what a published figure
      sometimes has to do. The distinction has to be stated in the editor in those terms, because
      "layer or saved query" is a storage detail and "does this update itself?" is the actual
      question being asked
- [ ] **Benchmarks on real data** — the spatial filter path is the one that decides whether a
      dashboard over ten million features is pleasant or merely possible

</div>

<div class="gd-rel" markdown>
### What still cannot travel from QGIS
<span class="gd-when">Planned</span>

<p class="gd-goal">v1.6 closed most of this. What is left is the part where a web renderer genuinely
cannot follow, plus one thing QGIS itself cannot draw.</p>

- [ ] **Symbols a web map cannot draw at all** — a shapeburst fill, an SVG marker from the user's
      disk that fails to rasterise, a geometry generator. Today the closest picture is sent, which
      is right for the portal and lossy for QGIS ⇄ QGIS. The answer is to carry the layer's **QML**
      alongside the friendly style, so a round trip through GeoDeploy returns exactly what went in
      while the portal keeps drawing its approximation. GeoDeploy already does this for GeoLibre
      imports, where raw MapLibre paint rides along in `style.maplibre`
- [ ] **Inverted polygons** — a mask, the world minus the layer, which is how you dim everything
      outside a study area
- [ ] **3D drawn in QGIS from a portal group.** GeoDeploy renders extrusion and the plugin carries
      it safely, and a layer opened from its DATA gets a real 3D renderer. A layer opened as the
      fast preview cannot: QGIS has no 3D renderer for vector TILES in either 3.44 or 4.2, so the
      group says so and points at the editable source rather than drawing it flat in silence

<p class="gd-goal">The constraint that shapes all of it: GeoDeploy renders with MapLibre and
TiTiler, not with QGIS. A symbol travels exactly when the web renderer can express it, and the
useful question for each one is not "can we support it" but "does it survive a round trip
unchanged, and if not, does the author find out before they publish?"</p>

</div>


<div class="gd-rel" markdown>
### Install somewhere that is not empty
<span class="gd-when">Planned</span>

<p class="gd-goal">Every install path so far assumes a bare VPS that GeoDeploy owns — ports 80 and
443 free, fixed container names available, nothing else on the box wanting any of it. That is the
right default and stays the default. It also rules out a lab server, a shared research machine, or
a VM that already serves something on 443.</p>

- [ ] **Serve behind an existing reverse proxy** — publish to a configurable port, or to no host
      port at all, with the `proxy_pass` an operator already running nginx or Caddy needs. The
      single biggest unlock, and the first step
- [ ] **A base path.** A portal lives at `/portals/<slug>/` today; on a shared host it may need to
      live under `/geodeploy/…`. Every absolute URL the app emits — vector tiles, `pmtiles://`, the
      parquet range proxy, published portal assets — has to be built from a configured prefix
      rather than assumed to sit at the root. This is the part that will leak bugs and it deserves
      its own audit
- [ ] **A namespace for containers, networks and volumes**, so two instances can coexist and
      neither collides with unrelated software
- [ ] **A rootless path**, or at minimum a preflight that states the privileges required and fails
      early and legibly without them
- [ ] **A preflight that reports every conflict at once** — ports in use, names taken, network
      present — before it writes anything

<p class="gd-goal">Half-doing the base path produces an instance that mostly works and breaks on the
paths nobody clicked while testing, which is the worst failure available to a self-hosted product:
the operator cannot tell whether they misconfigured it or it is broken. Hence a release rather than
a flag. <a href="https://github.com/bravemaster3/GeoDeploy/issues/79">#79</a></p>

</div>

<div class="gd-rel" markdown>
### A page for a layer
<span class="gd-when">Mostly shipped · v1.4</span>

<p class="gd-goal">A layer is a thing in its own right, not only a row in a list — so it has an
address, and a page at it. <code>/layers/&lt;kind&gt;/&lt;id&gt;</code> is public for a shared
layer and the same page appears inside the app; a private one asks for a sign-in.</p>

- [x] **Click a layer, get its page.** A map of just that layer, at its own extent, drawn by the
      same style builder the portal editor uses — so the page shows what a portal would
- [x] **What it is**: geometry type, feature count, CRS, extent, size on disk, when it was added
- [x] **How it is served**: whether it is tiled and how (PostGIS tiles, a PMTiles archive, a COG),
      and whether a GeoParquet layer has been tiled yet
- [x] **Its symbology, edited and saved here** — the same panel My Data uses, writing the layer's
      default style
- [x] **Everything that already exists about a layer, in one place**: its share links, its legend,
      its sharing state, and the actions that apply to it
- [ ] **The attribute table**, paged, with a click-through from a feature on the map. The one part
      of this page still missing — the data is reachable today only through the API or by
      downloading the layer

</div>

<div class="gd-rel" markdown>
### Cartography and portal tools
<span class="gd-when">Planned</span>

<p class="gd-goal">What regular use keeps asking for.</p>

- [ ] **Portal tools framework** — a toolbar the admin enables per portal
- [ ] Measure distance and area; **print composer** to PDF with legend, scale bar, attribution
- [ ] Swipe compare, and permalinks that restore view and layer state
- [ ] Draw a box to *filter* a catalog, not only to download
- [x] **Data-driven symbology** — shipped in v1.1. Size-from-a-field and labels are scheduled in
      v1.2, above
- [x] **Rule-based and expression symbology** — shipped in v1.6, both directions through the
      QGIS plugin and authorable in the portal editor
- [ ] A wider template gallery (13 today: webmap, story, catalog and six dashboards)
- [x] **Heatmap and cluster renderers** — heatmaps shipped in v1.6 (authorable per layer, with
      a ramp), low-zoom point clustering in v1.5 (applied by tippecanoe when a GeoParquet
      layer is tiled)
- [x] **Multi-file and archive uploads** — pick several files at once in the browser, and a
      `.zip` holding a shapefile set, a GeoPackage, a FileGDB or any other supported dataset
      is unpacked and read (v1.6)
- [ ] `.tar.gz` alongside `.zip`
- [ ] **Choose what a restore replaces** — files, portal assets and database as separate choices,
      instead of all-or-nothing. (Restoring layers *without* users is a different, harder thing:
      `user_id` is a NOT NULL foreign key on layers, portals and tokens, so it needs id remapping,
      not a checkbox.)
- [ ] **Storage credentials in Settings** — there is currently NO screen for them after setup:
      the wizard is the only place they can be entered, and it refuses once an account exists.
      So rotating an S3 key, or supplying one after reconnecting to an existing database, means
      editing `.env` by hand.
- [ ] **Rotate the encryption key from the app** — decrypt with the old key and re-encrypt with the
      new one, so the key can be changed, or an old instance's key adopted before restoring its
      backup, without a shell. Today `GEODEPLOY_SECRET_KEY` is edited in `.env` only, and is
      deliberately absent from the environment editor: setting it in place would leave every
      already-encrypted setting unreadable, with no error at the moment of the change.
- [x] Choose a version when updating — hold back, or step down after a bad one ([four targets](updating.md#choosing-a-version): development, latest release, a specific release, or a branch)
- [ ] Unattended install from environment variables, so provisioning can be scripted

</div>

<div class="gd-rel" markdown>
### Depth
<span class="gd-when">Exploring</span>

<p class="gd-goal">Capabilities that change what a portal can be.</p>

- [ ] **Temporal layers** with a time slider
- [x] **3D terrain** — raise the map by a DEM, per raster layer, in the globe view (v1.6)
- [ ] **3D tiles** in the globe view — somebody else's 3D tileset, drawn beside your data
- [ ] Live connectors — scheduled re-sync, so published maps stay current
- [ ] Photo features — bulk-import geotagged images into a field-story layer
- [ ] In-browser analysis console — SQL against hosted GeoParquet
- [ ] A small geoprocessing toolkit, run server-light
- [ ] Translation of the dashboard and the published portals

</div>

---

## Suggesting something

Open an issue on [GitHub](https://github.com/bravemaster3/GeoDeploy/issues). A concrete description of
what you were trying to do is the most useful kind — a good share of the list above came from exactly
that.
