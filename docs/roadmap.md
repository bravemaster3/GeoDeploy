# Roadmap

Where GeoDeploy is, and where it is going. **51 tracked items**, of which 17 have shipped.

<div class="gd-legend" markdown>
:material-check-circle:{ .gd-ok } **Shipped** · :material-progress-wrench:{ .gd-wip } **Building
now** · :material-calendar-check:{ .gd-plan } **Planned** · :material-lightbulb-outline:{ .gd-idea }
**Later**
</div>

[Open the interactive board](roadmap.html){ .md-button .md-button--primary }

---

## Building now

<div class="grid cards" markdown>

-   :material-view-grid:{ .lg } **Catalog portals**

    ---

    A browsing experience for portals with more datasets than one map should carry: search, facets by
    folder, type, keywords and licence, result cards, and a map beside them. **Shipped and in use;**
    still to come are drawing a box to filter *and* download the selection.

-   :material-view-dashboard-outline:{ .lg } **Portal experiences**

    ---

    Choosing a template changes the *shape* of the page, not just its colours — web map, story map and
    catalog all ship. A dashboard experience with charts and indicators comes after.

-   :material-swap-horizontal:{ .lg } **Round-trip with GeoLibre**

    ---

    Publish here, open there — already works over open standards. Next is two-way: import a GeoLibre
    project's layers and styling into a portal, and push a portal's symbology back out.

-   :material-api:{ .lg } **OGC API - Features**

    ---

    Serving Core + GeoJSON today, so QGIS, ArcGIS Pro, FME and GDAL read your layers natively.
    Extending to CQL2 filtering and CRS negotiation.

-   :material-email-fast-outline:{ .lg } **Email notifications**

    ---

    Invitations and password resets by mail once SMTP is configured, plus a note when a long export
    or backup finishes.

-   :material-test-tube:{ .lg } **Tests & CI**

    ---

    Over 300 backend tests run on every change. Growing toward integration tests that exercise the
    full Docker stack, which unit tests cannot cover — the failures that have actually hurt here were
    all integration-shaped.

</div>

## Next up

Concrete things queued behind the above, several of which came from real use:

| | |
| --- | --- |
| **Environment variables in the UI** | Change settings and apply them per service without a terminal — the goal is that a mature GeoDeploy never asks you to SSH in |
| **Unattended install** | Configure an install from environment variables instead of the wizard, so provisioning can be scripted |
| **Choose a version when updating** | Hold back, or step back down after a bad update, instead of only ever moving to latest |
| **Multi-file and archive uploads** | Several files at once, and `.tar.gz` alongside `.zip` |
| **A packaged `geodeploy` CLI** | Turning the worked example script into something you install |
| **Responsive polish** | Small-screen behaviour across the dashboard and published portals |
| **Portal tools** | An admin-selectable toolbox: measure, print to PDF, swipe compare, permalinks |

## Interoperability

The principle: **GeoDeploy must not be a place your data gets stuck.** What you publish should open
natively elsewhere, and work done elsewhere should be able to come back.

=== "Working today"

    - **OGC API - Features** — the standard QGIS, ArcGIS Pro, FME and GDAL connect to
    - **STAC** catalog of layers and their assets
    - **Cloud-Optimized GeoTIFF** over HTTP range requests
    - **PMTiles** and **TileJSON** vector tiles
    - **GeoParquet**, readable by DuckDB and GDAL — and now a download format for a drawn area

=== "Planned"

    - **Push back from GeoLibre and QGIS** — style a layer in the tool you already use, publish the
      result, no export-and-re-upload round trip
    - **A QGIS plugin** that browses your catalog and adds a layer in one click
    - **Richer catalog metadata** and catalog *search*, so a dataset can be found by a client rather
      than only fetched once you know its URL

## Further out

<div class="grid cards" markdown>

-   :material-chart-box-outline: **Dashboards** — charts and indicators bound to layer attributes,
    laid out beside the map.

-   :material-clock-outline: **Temporal data** — time-aware layers with a slider for change over time.

-   :material-video-3d: **3D** — terrain and 3D tilesets in the globe view.

-   :material-sync: **Live connectors** — scheduled re-sync from a source so hosted layers stay
    current.

-   :material-translate: **Translation** — dashboard and published portals in more languages.

-   :material-cloud-outline: **GeoDeploy Cloud** — hosted, for people who would rather not run a
    server, or who need only a few layers and would rather share the cost. The project stays open
    source and self-hosting stays the full product.

</div>

## Already shipped

The foundation is done and in daily use:

- **Data** — Shapefile, GeoPackage, GeoJSON, CSV, GeoParquet and GeoTIFF; PostGIS and GeoParquet
  backends; automatic PMTiles tiling; direct-to-storage uploads for large files
- **Portals** — the editor, folders, symbology, three experiences, per-portal branding, four access
  tiers enforced server-side, and area downloads
- **People** — roles, per-layer visibility, invitation links, scoped API tokens, an audit log, and
  optional single sign-on
- **Operations** — one-command install, a setup wizard, in-app updates, service logs, scheduled
  backups to a separate destination, and an in-app restore

## Suggesting something

Open an issue on [GitHub](https://github.com/bravemaster3/GeoDeploy/issues). A concrete description of
what you were trying to do is the most useful kind — several items above came from exactly that.
