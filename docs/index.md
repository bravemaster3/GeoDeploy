---
hide:
  - navigation
  - toc
---

<div class="gd-hero" markdown>

# Your data. Your server. Your map.

<p class="gd-sub" markdown>
GeoDeploy is a self-hosted spatial data platform and geoportal builder. Upload your data, style it,
and publish a map anyone can use — with your own domain, your own storage, and no per-seat pricing.
</p>

[Get started](getting-started.md){ .md-button .md-button--primary }
[Access your data](data-access.md){ .md-button }

<div class="gd-install" markdown>
```bash
curl -fsSL https://raw.githubusercontent.com/bravemaster3/geodeploy/main/installer/install.sh | bash
```
</div>

</div>

One command installs the whole stack on a single Linux server — spatial database, object storage,
vector and raster tile services, a web dashboard, and the portals you publish from it. A setup wizard
handles the rest, and you are uploading data a couple of minutes later.

## What you can do with it

<div class="grid cards" markdown>

-   :material-upload:{ .lg } **Bring your data in**

    ---

    Shapefile, GeoPackage, GeoJSON, CSV, GeoParquet and GeoTIFF. Large files upload straight to
    object storage, so a multi-gigabyte dataset is not a special case.

    [:octicons-arrow-right-24: Uploading data](uploading.md)

-   :material-map:{ .lg } **Publish a portal**

    ---

    Pick an experience — a web map, a scrollytelling story, or a searchable catalog — arrange your
    layers, choose who can see it, publish. Each portal gets its own URL.

    [:octicons-arrow-right-24: Portals and experiences](portals.md)

-   :material-share-variant:{ .lg } **Share data properly**

    ---

    Layers you make public are readable by standard clients over open standards, so QGIS, Python and
    R consume them directly — no export step, no format conversion.

    [:octicons-arrow-right-24: Access from other tools](data-access.md)

-   :material-account-group:{ .lg } **Work as a team**

    ---

    Roles from viewer to owner, per-layer visibility, invitation links, scoped API tokens, and an
    audit log of who changed what.

    [:octicons-arrow-right-24: Users, roles and sharing](users-and-sharing.md)

</div>

## How data flows

<div class="gd-flow" markdown>
```
upload ──▶ PostGIS or GeoParquet ──▶ tiles ──▶ portal editor ──▶ published portal
                    │
                    └──▶ OGC API - Features · STAC · COG · PMTiles ──▶ QGIS · Python · R
```
</div>

Vector data lands in **PostGIS** or as **GeoParquet**, depending on its size and how you will use it.
Rasters become **Cloud-Optimized GeoTIFFs**. Everything is then reachable two ways: through the
portals you publish, and through open standards other tools already speak.

## Three kinds of portal

<div class="grid cards" markdown>

-   **Web map**

    ---

    Map-first, with a layer list beside it. The right choice when the map itself is the point.

-   **Story map**

    ---

    Scrollytelling. Each section is pinned to a map position and a set of layers; the map animates
    as the reader scrolls.

-   **Catalog**

    ---

    A browsing surface for when you have more datasets than one map should show. Search and facets
    on the left, results in the middle, map beside them.

</div>

## Runs on a small server

The reference setup is a modest VPS — 2 vCPU, 8 GB RAM, 80 GB disk — which comfortably runs a real
geoportal with real data on it. Everything is Docker Compose behind nginx: one thing to start, one
thing to update.

!!! tip "Updating is a button"
    GeoDeploy checks for new versions and updates itself from the dashboard, database schema
    included. See [Updating](updating.md).

## Where to go next

| If you want to… | Read |
| --- | --- |
| Install it and publish something | [Getting started](getting-started.md) |
| Understand the portal types | [Portals and experiences](portals.md) |
| Get your data into QGIS or DuckDB | [Access from other tools](data-access.md) |
| Script against it | [API reference](api-reference.md) |
| Set up backups | [Backups and restore](backups.md) |
| See what is planned | [Roadmap](roadmap.md) |
