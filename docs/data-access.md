# Accessing your data from other tools

**QGIS · GeoLibre · DuckDB · Python · R · anything that speaks HTTP.**

GeoDeploy is cloud-native: rather than running heavy XML-era OGC services, it shares data through
formats and APIs that clients read **directly over HTTP** — Cloud-Optimized
GeoTIFF, XYZ tiles, and GeoParquet — discovered through a built-in **STAC catalog**, plus a
standards-based **OGC API - Features** service (the WFS successor) so any GIS can read a layer
with no GeoDeploy-specific knowledge.

Three surfaces, three jobs:

| Surface | For | Start at |
|---|---|---|
| **OGC API - Features** | reading features in any GIS (QGIS, ArcGIS, FME, GDAL) | `/api/ogc` |
| **STAC** | discovering what an instance holds, and where each asset lives | `/api/stac` |
| **Tiles** (XYZ · TileJSON · PMTiles · COG) | drawing big layers fast | per-layer, see below |

In the app, the **Share links** panel (link icon on any ready layer in *My Data*) hands you the
right URL for each of these, labelled with the exact menu path in each tool.

**About the identifiers in these URLs.** A layer is addressed by a short opaque id such as
`vector-a7f3c91b04e2` — deliberately not a row number. Row numbers get reused when a layer is
deleted, which would make an old link quietly return someone else's data; the opaque id is stable
for the life of the layer and 404s honestly once it is gone. Links that used numbers still work.

## Sharing a layer (admin)

Nothing is shared by default. In **My Data**, click the globe icon on a ready layer to list it
in the public catalog (a "Public data" badge appears). Optional catalog metadata — abstract,
keywords, license, attribution — can be set via the API:

```bash
curl -X PUT https://YOUR-HOST/api/data/vector/5/sharing \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"is_public": true, "abstract": "Land cover 2018", "license": "CC-BY-4.0",
       "keywords": "landcover, france", "attribution": "© IGN"}'
```

What the flag controls: **discovery** (the layer's entry in the STAC catalog) and, for rasters,
the **raw COG endpoint**. Portal display endpoints (tiles, viewport features) are always
addressable by id — publishing a portal already exposes its layers' rendering.

## The STAC catalog

Entry point: `https://YOUR-HOST/api/stac`

- `GET /api/stac` — catalog root (STAC 1.0.0, API core + collections + item-search)
- `GET /api/stac/collections` — two collections: `vectors`, `rasters`
- `GET /api/stac/collections/{id}/items` — one STAC Item per shared layer, with ready-to-use
  asset URLs
- `GET /api/stac/search?bbox=minx,miny,maxx,maxy&collections=rasters&limit=50` — item search

Works with **QGIS** (native STAC support in 3.40+, or the STAC API plugin: add
`https://YOUR-HOST/api/stac` as a connection), **stac-browser**, and **pystac-client**:

```python
from pystac_client import Client
cat = Client.open("https://YOUR-HOST/api/stac")
for item in cat.search(bbox=[-5, 42, 9, 51]).items():
    print(item.id, list(item.assets))
```

## OGC API - Features — the one that works everywhere

Entry point: `https://YOUR-HOST/api/ogc`

If you only read one section, read this one. **OGC API - Features** is the standard every modern
GIS speaks natively, so it is the shortest path from a GeoDeploy layer to somebody else's tool.
Every layer you share as **Public** becomes a collection, whatever it is stored as (PostGIS or
GeoParquet) — same URLs, same GeoJSON, EPSG:4326.

- `GET /api/ogc` — landing page · `GET /api/ogc/conformance` — conformance classes
- `GET /api/ogc/collections` — one collection per shared vector layer (`vector-{uid}`)
- `GET /api/ogc/collections/{cid}/items?bbox=minx,miny,maxx,maxy&limit=1000&offset=0`
- `GET /api/ogc/collections/{cid}/items/{featureId}`

Responses carry `numberReturned`, `numberMatched` (when it is known), and `next`/`prev` links —
follow `next` to walk a whole layer.

**QGIS** — *Layer ▸ Add Layer ▸ Add OGC API - Features Layer ▸ New*, URL `https://YOUR-HOST/api/ogc`,
then pick the layer from the list. Attributes, the attribute table, and identify all work; QGIS
requests only your current extent.

**GDAL / ogr2ogr** — the `OAPIF` driver takes the collection URL directly:

```bash
ogr2ogr -f GPKG roads.gpkg \
  "OAPIF:https://YOUR-HOST/api/ogc/collections/vector-a7f3c91b04e2"
```

**Anything else** (Python, R, a browser) — `…/items?bbox=…` is plain GeoJSON over HTTP.

Also works in: **ArcGIS Pro** (OGC API server connection) and **FME**.

What it deliberately does *not* do: no CRS negotiation (everything is CRS84/EPSG:4326), no CQL2
filtering, no transactions, no OGC API - Tiles or Records — and `/api/ogc/conformance` claims
**only** Core + GeoJSON, so a spec-driven client is never misled.

> **Rendering vs. reading.** Use OGC API - Features to *get the data*. For drawing a very large
> layer quickly, use the tile links below (TileJSON / PMTiles) — they are generalized per zoom and
> have no attribute queries. GeoLibre reads TileJSON under *Add data ▸ OGC API - Tiles (vector)*.
> The **Share links** panel in My Data (link icon on any ready layer) gives you every one of these
> URLs for a layer, already labelled with the menu path for each tool.

> **PMTiles: paste the plain URL.** `…/api/data/vector/{uid}/pmtiles` is the archive itself —
> that is what GeoLibre's PMTiles field, a download, and GDAL's `/vsicurl/` all expect. The
> `pmtiles://` prefix you may have seen is a protocol handler the MapLibre GL JS library registers
> in *code*; it belongs inside a style source (GeoDeploy's own portals emit it), never in a UI
> field or an address bar. To load a layer *into QGIS*, use OGC API - Features — PMTiles is a
> rendering format.

## Consuming the assets

### Rasters (Cloud-Optimized GeoTIFF)

- **QGIS / GDAL — full pixel access, no download:** add a raster layer with the URL
  `/vsicurl/https://YOUR-HOST/api/data/raster/{id}/cog`. Range requests fetch only the tiles
  and overviews you look at (this is the modern replacement for WCS).
- **Download:** the same URL fetched normally returns the whole GeoTIFF.
- **XYZ tiles (display only):** the item's `tiles` asset is a TiTiler tile template — paste it
  into a QGIS *XYZ Tiles* connection or any web map.
- **TileJSON (preferred over raw XYZ):** `…/api/data/raster/{id}/tilejson` wraps that template with
  the layer's **bounds** and saved styling, so clients can *zoom to layer* — a bare XYZ URL cannot.

### Vector layers served from PostGIS

- **XYZ vector tiles (display only):** the `vector-tiles` asset
  (`https://YOUR-HOST/tiles/{schema}.{table}/{z}/{x}/{y}`) pastes into a QGIS *Vector Tiles*
  connection. Tiles are generalized per zoom — for full-fidelity data use the portal's
  select-and-download tool, or store the layer as GeoParquet.

### Vector layers stored as GeoParquet

A prepared layer is a **spatially partitioned GeoParquet dataset**: a prefix of
`__cell=N/*.parquet` files plus a `manifest.json` describing the partition grid and per-cell
files. All of it is served with HTTP Range support.

- **Viewport queries (simplest):**
  - `…/api/data/vector/{id}/features.geojson?bbox=minx,miny,maxx,maxy&limit=50000` → GeoJSON
  - `…/api/data/vector/{id}/features.arrow?bbox=…&limit=…` → GeoArrow (Arrow IPC stream)
- **DuckDB — query the dataset in place:**

```sql
-- discover the files
-- curl https://YOUR-HOST/api/data/vector/5/parquet/manifest.json
SELECT count(*)
FROM read_parquet([
  'https://YOUR-HOST/api/data/vector/5/parquet/__cell=137/data_0.parquet',
  'https://YOUR-HOST/api/data/vector/5/parquet/__cell=138/data_0.parquet'
]);
-- every partition file carries a GeoParquet 1.1 bbox covering column: filter on
-- struct_extract("bbox", 'xmin') etc. for row-group pruning, exactly like GeoDeploy does.
```

  A small script can read the manifest, pick the cells overlapping an area of interest
  (`cell = ix*grid + iy` on the manifest's grid), and hand DuckDB just those files.
- **QGIS/GDAL:** single `.parquet` files open via
  `/vsicurl/https://YOUR-HOST/api/data/vector/{id}/parquet/__cell=N/data_0.parquet`
  (GDAL ≥ 3.5 with the Parquet driver).

## GeoLibre

[GeoLibre](https://geolibre.app) reads GeoDeploy layers directly — it is a lightweight, cloud-native
GIS that speaks the same modern formats, so nothing has to be exported or converted.

| In GeoLibre, choose | Paste |
| --- | --- |
| **Add data ▸ OGC API - Features** | the layer's OGC API - Features URL |
| **Add data ▸ OGC API - Tiles (vector)** | the layer's TileJSON URL — fastest for drawing a big layer |
| **PMTiles** | the layer's PMTiles URL, as a plain `https://` address with no prefix |
| **COG** | a raster's Cloud-Optimized GeoTIFF URL |

Every one of those URLs is on the layer's **Share** panel in GeoDeploy, ready to copy.

!!! note "Round-tripping is on the roadmap"
    Today the flow is one-way: publish here, open there. Planned next is two-way — import a GeoLibre
    project's layers and styling into a portal, and push a portal's layers and symbology back out.
    Both sides already speak the MapLibre style specification, which is the shared ground that makes
    it tractable. See the [roadmap](roadmap.md).

## Which standard for which job

Every access path here is a modern, HTTP-native standard. There is one for each kind of job:

| You want to… | Use |
|---|---|
| Read features with their attributes | **OGC API - Features** |
| Draw a large vector layer quickly | **Vector tiles** (TileJSON) or **PMTiles** |
| Read raster pixels, or a subset of them | **Cloud-Optimized GeoTIFF** over HTTP Range |
| Draw a raster quickly | **XYZ raster tiles** |
| Analyse a large table columnar-style | **GeoParquet** |
| Discover what exists and its metadata | **STAC** |

The XML-era OGC services — WMS, WFS, WCS, CSW — are deliberately **not** provided. Each has a
modern replacement in the table above that clients read directly over HTTP, without a heavyweight
service in front of it: OGC API - Features instead of WFS, COG over Range instead of WCS,
XYZ/TileJSON instead of WMS, STAC instead of CSW. Keeping to those is what lets GeoDeploy run on a
small server. Clients that specifically require the older protocols are out of scope.

## Not yet implemented

- Private catalog access via API token (shared layers are public; unshared layers are simply
  not listed).
- A QGIS plugin (browse the catalog and add a layer in one click). The STAC connection already
  covers most of this.
- Single-file GeoParquet download of a partitioned dataset (merge-on-demand).
- OGC API - Features extensions: CRS negotiation (Part 2), CQL2 filtering (Part 3), transactions,
  and property filters/queryables. Core + GeoJSON only, as `/api/ogc/conformance` states.
- OGC API - Tiles and OGC API - Records (tiles are TileJSON/PMTiles; the catalog is STAC).
- Single-feature access (`/items/{featureId}`) for a GeoParquet layer whose dataset has no
  id-like column (`id`/`fid`/`gid`/`objectid`/…) — the collection still pages normally.
