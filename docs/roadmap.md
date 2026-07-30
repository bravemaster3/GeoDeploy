# Roadmap

There is an interactive board with every tracked item, its status and its dependencies:

[Open the roadmap board](roadmap.html){ .md-button .md-button--primary }

This page is the summary. The board is the source of truth.

## Where things stand

| Phase | Status |
| --- | --- |
| **Foundation** — upload, tiles, portals, publishing | Shipped |
| **Multi-user and access** — roles, sharing, tokens, audit log | Shipped |
| **Portal experiences** — web map, story map, catalog | In progress |
| **Interoperability** — open standards in and out | In progress |
| **Hosted offering** | Planned |

## What is being worked on

### Portal experiences

The catalog experience is the current focus: a browsing surface with facets, result cards and a map
beside them, for portals with more datasets than one map should carry. Still to come there: drawing
a box to filter *and* download the selection, and organising results by folder.

A dashboard experience — charts and indicators alongside the map — is planned after that.

### Round-tripping with the tools you already use

The goal is that GeoDeploy is not a place your data goes to be stuck. Data you publish should open
natively elsewhere, and work done elsewhere should come back.

**QGIS.** Shared layers already open through OGC API - Features, vector tiles and Cloud-Optimized
GeoTIFF, with no export step. Planned: a plugin so you can browse the catalog and add a layer
without copying URLs, and push a styled layer back up as a new dataset.

**GeoLibre.** Planned two-way interoperability: import a GeoLibre project's layers and styling into
a portal, and push a portal's layers and symbology back out as a project. The shared ground is the
MapLibre style specification, which both sides already speak, so most of the work is mapping the
pieces either side has that the other does not — folder structure, 3D extrusion and elevation.

**Anything speaking open standards.** OGC API - Features, STAC, TileJSON, PMTiles, GeoParquet and
COG are all served today; see [Access from other tools](data-access.md). Planned additions are
richer catalog metadata profiles and catalog search, so a dataset can be *found* by a client, not
just fetched once you know its URL.

### Elsewhere

- **Responsive and mobile** — published portals and the dashboard adapt to phones and tablets;
  continuing to refine.
- **Portal tools** — an admin-selectable toolbox: measure, print to PDF, swipe compare, permalinks.
- **Temporal data** — time-aware layers with a slider.
- **3D** — terrain and 3D tiles in the globe view.
- **Live connectors** — scheduled re-sync from a source so hosted layers stay current.
- **Translation** — dashboard and published portals in more languages.

## Suggesting something

Open an issue on [GitHub](https://github.com/bravemaster3/GeoDeploy). Concrete descriptions of what
you were trying to do are the most useful kind.
