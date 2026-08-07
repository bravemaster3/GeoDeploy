# Roadmap

GeoDeploy is at **v1.2**. Everything under *In v1.0* and in the releases after it is built and
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

<div class="gd-rel now tail" markdown>
### v1.3 — the CLI, and getting data back out
<span class="gd-when">Next</span>

<p class="gd-goal">v1.2 made the data readable by other tools. This one is about reaching an instance
without a browser, and about being able to take a copy with you.</p>

- [ ] **A real CLI**, not an example script — every argument the API takes, the v1.1 symbology
      included, with its own section in the docs and tests so it is verified without anyone
      checking by hand
- [ ] **Styling that travels** — portal and layer style interchange with GeoLibre and QGIS, for
      every layer type. The CLI is the natural surface for it, so the two are designed together
- [ ] **Download a backup**, and **restore from disk** — state-only (small, covers a bad restore or
      a botched update) separately from the full copy including objects. Restore uploads to a
      staging prefix and reuses the existing restore path rather than growing a second one
- [ ] Collapsible legend entries in the layer list, with a collapse-all ([#9](https://github.com/bravemaster3/GeoDeploy/issues/9))
- [ ] Class count stops snapping back, and the 9-vs-12 ceiling agrees with the server ([#10](https://github.com/bravemaster3/GeoDeploy/issues/10))
- [ ] **Invert a colour ramp** ([#11](https://github.com/bravemaster3/GeoDeploy/issues/11))
- [ ] **A raster's zoom range read from the file, not guessed from its extent** — today a small
      high-resolution layer stops drawing below a computed floor, with nothing saying why
      ([#17](https://github.com/bravemaster3/GeoDeploy/issues/17))
- [ ] **Size from a field**, and **labels** — the half of data-driven symbology v1.1 did not ship

</div>

---

## Next up

<div class="gd-rel" markdown>
### Into the tools you already use
<span class="gd-when">Planned</span>

<p class="gd-goal">Your data can already leave GeoDeploy in open formats. This is the return
trip — edit in the tool you prefer, publish back.</p>

- [ ] **QGIS plugin** — browse the catalog, add a layer, publish back
- [ ] **Push from GeoLibre** — a "Publish to GeoDeploy" plugin and a `.geolibre.json` importer
- [ ] Style import from QGIS and GeoLibre, so nobody restyles from scratch
- [ ] Style interchange — adopt an external MapLibre style, and emit one
- [ ] Write-back: expose a layer as editable GeoJSON and re-ingest the edit
- [ ] A packaged `geodeploy` CLI, for scripted uploads and publishing
- [ ] Catalog **search**, so a client can discover a dataset rather than fetch a known URL

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
- [ ] Rule-based and expression symbology; a wider template gallery
- [ ] **Heatmap and cluster renderers** — the other renderers a data-driven style makes possible
- [ ] Multi-file and archive uploads (`.tar.gz` alongside `.zip`)
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
- [ ] Choose a version when updating — hold back, or step down after a bad one
- [ ] Unattended install from environment variables, so provisioning can be scripted

</div>

<div class="gd-rel" markdown>
### Depth
<span class="gd-when">Exploring</span>

<p class="gd-goal">Capabilities that change what a portal can be.</p>

- [ ] **Dashboard experience** — charts, stats and filters bound to layer attributes
- [ ] **Temporal layers** with a time slider
- [ ] **3D terrain and 3D tiles** in the globe view
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
