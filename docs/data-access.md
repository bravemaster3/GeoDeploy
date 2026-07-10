# Accessing GeoDeploy data from outside (QGIS, DuckDB, scripts)

GeoDeploy is cloud-native: instead of running heavy OGC servers (WMS/WFS/WCS à la GeoServer),
it shares data through formats that clients read **directly over HTTP** — Cloud-Optimized
GeoTIFF, XYZ tiles, and GeoParquet — discovered through a built-in **STAC catalog**.

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

## Consuming the assets

### Rasters (Cloud-Optimized GeoTIFF)

- **QGIS / GDAL — full pixel access, no download:** add a raster layer with the URL
  `/vsicurl/https://YOUR-HOST/api/data/raster/{id}/cog`. Range requests fetch only the tiles
  and overviews you look at (this is the modern replacement for WCS).
- **Download:** the same URL fetched normally returns the whole GeoTIFF.
- **XYZ tiles (display only):** the item's `tiles` asset is a TiTiler tile template — paste it
  into a QGIS *XYZ Tiles* connection or any web map.

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

## Comparison with GeoNode, honestly

| | GeoNode | GeoDeploy |
|---|---|---|
| Catalog/discovery | GeoNode catalog + CSW | STAC API (this page) |
| Raster data access | WCS (GeoServer) | COG over HTTP Range (`/vsicurl/`) |
| Raster display | WMS/WMTS | TiTiler XYZ |
| Vector display | WMS | Martin XYZ vector tiles / PMTiles |
| Vector data access | WFS | GeoParquet over HTTP Range + GeoJSON/GeoArrow viewport queries |
| Server weight | GeoServer (~1–2 GB JVM) | zero additional services |

Legacy OGC endpoints (WMS/WFS/CSW) are deliberately **not** provided — clients that require
them (rather than the cloud-native equivalents above) are out of scope for GeoDeploy's
cheap-VPS design. See `notes_temp/notes_for_future.md` §0 / §0h for the full reasoning.

## Not yet implemented

- Private catalog access via API token (shared layers are public; unshared layers are simply
  not listed).
- A GeoNode-style QGIS plugin (browse + one-click add). The STAC connection covers most of it.
- Single-file GeoParquet download of a partitioned dataset (merge-on-demand).
