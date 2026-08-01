# Roadmap

Where GeoDeploy is going, as **releases** rather than a wish list — so you can tell what a given
version will contain, and when something you need is likely to land.

!!! info "GeoDeploy has not been tagged yet"
    Everything under [Already working](#already-working) is in `main` and running in production
    today, but there has never been a numbered release. **v0.1 draws that line** — its job is not new
    features, it is making what already exists installable, documented and trustworthy for someone
    who is not the author.

    Until then, `main` is the release, and the installer tracks it.

---

## Releases

<div class="gd-rel now" markdown>
### v0.1 — Draw the line
<span class="gd-when">Next · mostly hardening</span>

<p class="gd-goal">A stranger can install GeoDeploy, publish a portal, and trust it with their data.
No new capability — the point is proving what is already built.</p>

- [x] Apache 2.0 licence, `NOTICE`, contribution guide with DCO
- [x] Documentation site with real screenshots
- [x] Backups to a separate destination, and in-app restore
- [x] Owner-editable environment variables, no terminal required
- [ ] **A clean install verified from scratch** on a fresh machine, following only the docs
- [ ] **A backup → restore round trip proven** end to end, not merely tested in parts
- [ ] Upgrade path exercised between two tagged versions
- [ ] Known issues published honestly, rather than discovered by users

</div>

<div class="gd-rel" markdown>
### v0.2 — Round-trip
<span class="gd-when">After v0.1</span>

<p class="gd-goal">GeoDeploy stops being somewhere data goes to get stuck. What you publish opens
natively elsewhere — and work done elsewhere comes back.</p>

- [x] OGC API - Features, STAC, COG, PMTiles and GeoParquet served today
- [ ] Push a styled layer **back** from QGIS or GeoLibre into an instance
- [ ] A QGIS plugin that browses the catalog and adds a layer in one click
- [ ] A packaged `geodeploy` CLI, grown from the worked example script
- [ ] Catalog *search*, so a client can discover a dataset rather than fetch a known URL

</div>

<div class="gd-rel" markdown>
### v0.3 — Everyday edges
<span class="gd-when">Later</span>

<p class="gd-goal">What makes daily use pleasant rather than merely possible. Most of these came from
real use rather than a plan.</p>

- [ ] Multi-file and archive uploads (`.tar.gz` alongside `.zip`)
- [ ] Choose a version when updating — hold back, or step down after a bad one
- [ ] Unattended install from environment variables, so provisioning can be scripted
- [ ] Portal tools: measure, print to PDF, swipe compare, permalinks
- [ ] Draw a box in a catalog portal to filter *and* download the selection
- [ ] Responsive polish across the dashboard and published portals

</div>

<div class="gd-rel" markdown>
### v0.4 — Depth
<span class="gd-when">Later</span>

<p class="gd-goal">Capabilities that change what a portal can be, rather than how comfortable it is to
run.</p>

- [ ] Dashboard experience — charts and indicators bound to layer attributes
- [ ] Temporal layers with a time slider
- [ ] 3D terrain and 3D tilesets in the globe view
- [ ] Live connectors — scheduled re-sync so hosted layers stay current
- [ ] Translation of the dashboard and published portals

</div>

<div class="gd-rel" markdown>
### v1.0 — Multi-tenant, and Cloud
<span class="gd-when">When the above is solid</span>

<p class="gd-goal">One instance safely serving separate organisations — the prerequisite for a hosted
GeoDeploy Cloud. Deliberately last: it is the largest change, and the one where a mistake means other
people's data.</p>

- [ ] Workspaces — a tenant boundary above users
- [ ] Tenant isolation: storage prefix, database scope, tile namespace
- [ ] Quotas and fair use, so one heavy job cannot starve its neighbours
- [ ] Metering and billing
- [ ] Self-serve provisioning
- [ ] Managed upgrades and observability

!!! note "Cloud changes nothing here"
    If GeoDeploy Cloud happens it is **hosting only** — this same open-source code, run for people who
    would rather not run a server. No feature is held back for it.

</div>

---

## Why this order

**Trust before features.** A project nobody can install confidently gains nothing from more
capability. v0.1 adds no features and is still the most valuable release on this page.

**Interoperability before depth.** The strongest reason to choose a self-hosted platform is that it
does not trap you. Round-tripping proves that, and it is worth more than another portal type.

**Multi-tenancy last.** It touches every query in the codebase, and its failure mode is one
organisation seeing another's data. It belongs on a foundation that has stopped moving.

---

## Already working

In `main`, and in production use today.

<div class="grid cards" markdown>

-   :material-database:{ .lg } **Data**

    ---

    Shapefile, GeoPackage, GeoJSON, CSV, GeoParquet, GeoTIFF. PostGIS and GeoParquet backends,
    automatic PMTiles tiling, direct-to-storage uploads for large files, native CRS preserved.

-   :material-map:{ .lg } **Portals**

    ---

    Web map, story map and catalog experiences. Folders, symbology, per-portal branding, four access
    tiers enforced server-side, draw-a-box downloads, and the 3D globe as a saved start view.

-   :material-share-variant:{ .lg } **Interoperability**

    ---

    OGC API - Features, STAC, Cloud-Optimized GeoTIFF, PMTiles, TileJSON and GeoParquet — read
    directly by QGIS, GeoLibre, DuckDB, Python and R.

-   :material-account-group:{ .lg } **People**

    ---

    Roles from viewer to owner, per-layer visibility, invitation links, scoped API tokens, an audit
    log, and optional single sign-on.

-   :material-server:{ .lg } **Operations**

    ---

    One-command install, setup wizard, in-app updates, per-service logs and terminal, scheduled
    backups to a separate destination, in-app restore, owner-editable environment.

-   :material-flask:{ .lg } **Try it**

    ---

    Demo mode — a public sandbox anyone joins with a name, wiped hourly. The same code every instance
    runs, behind one flag.

</div>

---

## Suggesting something

Open an issue on [GitHub](https://github.com/bravemaster3/GeoDeploy/issues). A concrete description of
what you were trying to do is the most useful kind — several items above came from exactly that.
