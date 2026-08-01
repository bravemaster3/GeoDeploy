# Roadmap

GeoDeploy as **releases** — what each version contained, and what the next ones will.

Development started **27 May 2026**. The eight milestones below are the road since then; everything
green is in `main` and running in production. **v1.0 is the release being cut now** — it adds no
features, because the work left is proving what is already here.

<div class="gd-legend" markdown>
:material-check-circle:{ .gd-ok } **Shipped** · :material-progress-clock:{ .gd-wip } **In flight** ·
:material-circle-outline: **Planned**
</div>

---

## The road to 1.0

<div class="gd-rel done" markdown>
### v0.1 — It installs, and it maps
<span class="gd-when">Late May 2026 · shipped</span>

<p class="gd-goal">One command on a bare VPS, and a working spatial stack — not a compose file to
assemble yourself.</p>

- [x] One-line installer, whole stack up behind nginx
- [x] Setup wizard: **let GeoDeploy install PostGIS, or connect one you already run**
- [x] Same choice for storage: **managed MinIO on this server, or any S3-compatible provider**
- [x] Vector upload — Shapefile, GeoPackage, GeoJSON, KML — into PostGIS, served as vector tiles
- [x] Raster upload converted to **Cloud-Optimized GeoTIFF**, served by TiTiler
- [x] Native CRS preserved; reprojection on the fly for display rather than on ingest
- [x] Basemaps, legends, palettes, hillshade, auto-stretch
- [x] Per-service start / stop / restart from Settings

</div>

<div class="gd-rel done" markdown>
### v0.2 — Data at real size
<span class="gd-when">June 2026 · shipped</span>

<p class="gd-goal">Stop being a toy at 50 000 features. Twenty million should pan and zoom on a
cheap server without a database in the hot path.</p>

- [x] **GeoParquet as a first-class backend** — no PostGIS table required
- [x] Direct-to-storage uploads for large files, bypassing the API entirely
- [x] Automatic **PMTiles** tiling; heavy layers stream by HTTP range request
- [x] deck.gl + DuckDB viewport rendering, with a density-grid overview while detail loads
- [x] Faithful tiling — display simplification off by default, extend-zooms only where features drop
- [x] In-browser click-to-identify and **draw-a-box export**, at near-zero server CPU
- [x] CSV with WKT, existing `.parquet` import, heavy-upload auto-conversion
- [x] Register data already in PostGIS or S3 **without re-uploading it**

</div>

<div class="gd-rel done" markdown>
### v0.3 — Portals worth publishing
<span class="gd-when">June – early July 2026 · shipped</span>

<p class="gd-goal">A published map that documents itself, rather than a link to a viewer.</p>

- [x] Portal editor with live preview, kept in parity with the published runtime
- [x] Symbology: single, graduated and categorized; line dashes, marker shapes, sizes, opacity
- [x] **Layer icons and canvas markers**, legend swatches, per-layer legends
- [x] Layer folders — nestable groups, ordered and collapsible
- [x] **About pages** — a WYSIWYG editor, with pasted images, per-layer metadata and data links
- [x] Portal branding: header, palette, basemap picked separately from theme
- [x] Attach external services — XYZ, WMS, remote tiles — alongside hosted data
- [x] 3D globe as a saved start view

</div>

<div class="gd-rel done" markdown>
### v0.4 — More than one person
<span class="gd-when">Mid-July 2026 · shipped</span>

<p class="gd-goal">From a single admin to an organisation, with ownership, permissions and a record
of who did what.</p>

- [x] Role ladder — viewer → editor → admin → a single transferable owner
- [x] **Invitations**: emailed when SMTP is configured, otherwise a copy-able link
- [x] **Single sign-on (OIDC)** — optional, alongside password login
- [x] Per-resource visibility — private ⊂ organization ⊂ public — on layers and external sources
- [x] Four access tiers on published portals, enforced server-side
- [x] **Scoped API tokens** — shown once, stored hashed, for scripts and desktop tools
- [x] Secrets encrypted at rest; session revocation on password change
- [x] **Audit log** with a paginated activity view
- [x] Optional SMTP for notifications — no vendor lock-in, any relay works

</div>

<div class="gd-rel done" markdown>
### v0.5 — Three kinds of portal
<span class="gd-when">Late July 2026 · shipped</span>

<p class="gd-goal">One runtime, three experiences — because a catalog and a narrative are not maps
with extra panels.</p>

- [x] **Experiences**: web map, story map, catalog — chosen at creation
- [x] **Story maps** — ordered sections of rich text, each with a captured camera and layer state
- [x] **Catalog portals** — facet rail (folder, type, keywords, licence), result cards, side map
- [x] Layout manifests: map width, region grid, panel toggles
- [x] Template gallery with distinct themes over the shared runtime
- [x] Portal thumbnails captured from the real published map
- [x] Add every layer at once when building a portal
- [x] Navigation history control — previous and next extent — on every map

</div>

<div class="gd-rel done" markdown>
### v0.6 — Open by default
<span class="gd-when">Late July 2026 · shipped</span>

<p class="gd-goal">Nothing published here should need GeoDeploy to read it. This is the whole
argument for self-hosting, so it had to be real before 1.0.</p>

- [x] **STAC 1.0.0 API** — collections, items, ready-to-use assets
- [x] **OGC API - Features** — landing, conformance, collections, items
- [x] TileJSON for vector and raster layers — one URL per source
- [x] COG, PMTiles and GeoParquet reachable directly by QGIS, DuckDB, Python and R
- [x] CORS on public data and catalog endpoints, so browser clients work
- [x] Share links, with a panel in My Data
- [x] SSRF guard on remote imports

</div>

<div class="gd-rel done" markdown>
### v0.7 — Run it without SSH
<span class="gd-when">Late July – August 2026 · shipped</span>

<p class="gd-goal">Everything an operator needs, in the app — because "just exec into the container"
is not an answer for the person who has to keep this running.</p>

- [x] **Infrastructure panel** — service rail with logs, terminal and deployments
- [x] **Scheduled backups** of PostGIS, files and state to a separate S3 destination
- [x] **In-app restore**, backup history, and a guarded manage/delete section
- [x] One-button update, with a preflight that refuses to run over work in progress
- [x] **Owner-editable environment variables**, allow-listed, applied per service
- [x] Connection details for the managed PostGIS and MinIO — masked, revealed, copyable
- [x] Responsive pass across the dashboard and published portals
- [x] Demo mode — a public sandbox, wiped hourly, behind one flag

</div>

<div class="gd-rel now tail" markdown>
### v1.0 — Prove it
<span class="gd-when">Now · the first tagged release</span>

<p class="gd-goal">No new features. Nothing above has ever carried a version number, and a platform
people trust with their data has to be installable and recoverable by someone who is not the author.</p>

- [x] Apache 2.0 licence, `NOTICE`, contribution guide with DCO sign-off
- [x] Documentation site with real screenshots
- [ ] **A clean install verified from scratch** on a fresh machine, following only the docs
- [ ] **A backup → restore round trip proven** end to end, not merely tested in parts
- [ ] An upgrade exercised between two tagged versions
- [ ] Release notes, and known issues published rather than discovered

</div>

---

## After 1.0

<div class="gd-rel" markdown>
### v1.1 — Into the tools you already use
<span class="gd-when">Next</span>

<p class="gd-goal">v0.6 made the data readable by other software. This makes the round trip
work — edit somewhere else, come back.</p>

- [ ] **QGIS plugin** — browse the catalog, add a layer, publish back
- [ ] **Push from GeoLibre** — a "Publish to GeoDeploy" plugin and a `.geolibre.json` importer
- [ ] Style interchange — adopt an external MapLibre style, and emit one
- [ ] Style import from QGIS and GeoLibre, so nobody restyles from scratch
- [ ] Write-back: expose a layer as editable GeoJSON and re-ingest the edit
- [ ] A packaged `geodeploy` CLI, grown from the worked example script
- [ ] Catalog **search**, so a client can discover a dataset rather than fetch a known URL

</div>

<div class="gd-rel" markdown>
### v1.2 — Cartography and everyday edges
<span class="gd-when">After v1.1</span>

<p class="gd-goal">The things regular use keeps asking for.</p>

- [ ] **Portal tools framework** — a toolbar the admin enables per portal
- [ ] Measure distance and area; **print composer** to PDF with legend, scale bar, attribution
- [ ] Swipe compare, and permalinks that restore view and layer state
- [ ] Draw a box to *filter* a catalog, not only to download
- [ ] Rule-based and expression symbology; a wider template gallery
- [ ] Multi-file and archive uploads (`.tar.gz` alongside `.zip`)
- [ ] Choose a version when updating — hold back, or step down after a bad one
- [ ] **Unattended install from environment variables**, so provisioning can be scripted

</div>

<div class="gd-rel" markdown>
### v1.3 — Depth
<span class="gd-when">Later</span>

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

## Why this order

**Interoperability came before depth, and stays there.** The reason to self-host is that nobody can
trap your data. v0.6 proved it can leave; v1.1 proves it can come back. Both are worth more than
another portal type.

**v1.0 adds nothing.** It is the least exciting release on this page and the most valuable one —
seven milestones of work that no one can currently install by version number.

**Nothing here needs a bigger server.** Every milestone above was built and is running on a small
VPS, and that constraint decides the architecture: tiles and GeoParquet read by the browser, not
features rendered by a server per visitor.

---

## Not on this roadmap

**Multi-tenancy and hosted GeoDeploy Cloud.** If a hosted option ever exists it is *hosting* — this
same code, run for people who would rather not run a server. It is not a feature of the project, so
it is not planned here, and nothing on this page is held back for it.

**Everything in this repository stays open.** Apache 2.0, one codebase, no held-back edition.

---

## Suggesting something

Open an issue on [GitHub](https://github.com/bravemaster3/GeoDeploy/issues). A concrete description of
what you were trying to do is the most useful kind — a good share of v1.2 came from exactly that.
