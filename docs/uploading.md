---
description: >-
  Upload Shapefile, GeoPackage, GeoJSON, KML, CSV, GeoParquet and GeoTIFF data into GeoDeploy, including multi-gigabyte files that go straight to object storage.
---

# Uploading data

**My Data** holds everything you have brought in. Vector layers and rasters are listed separately,
each row showing its status, size and how it is stored.

## What you can upload

=== "Vector"

    | Format | Notes |
    | --- | --- |
    | Shapefile | Upload as a `.zip` containing `.shp`, `.dbf`, `.shx`, `.prj` — a folder inside the zip is fine |
    | GeoPackage | `.gpkg`, including one holding several layers: each becomes a layer |
    | GeoJSON | `.geojson` / `.json` |
    | CSV | With X/Y columns or a WKT geometry column — you choose at upload |
    | GeoParquet | `.parquet`, or an existing partitioned dataset already in your bucket |

    A `.zip` is searched all the way down, so a shapefile inside a folder — which is what most
    "download this dataset" buttons produce — is found. Any other single-file dataset the server can
    read (a GeoPackage, a GeoJSON, a FlatGeobuf, a KML, a GML) is also read from inside a zip, which
    is how to upload a format that is not in this table. If an archive holds more than one dataset
    the shapefile wins; upload the others separately.

=== "Raster"

    | Format | Notes |
    | --- | --- |
    | GeoTIFF | `.tif` / `.tiff`, any CRS |

    Rasters are converted to **Cloud-Optimized GeoTIFF** so they can be read by range request
    instead of being downloaded whole.

## Two vector backends

Vector data lands in one of two places, and the choice is about how you will use it:

| | PostGIS | GeoParquet |
| --- | --- | --- |
| Stored as | A table in the spatial database | Files in object storage |
| Best for | Frequently queried or edited data | Large, mostly-read datasets |
| Drawn via | Vector tiles | Direct reads, or pre-built tiles |
| Attribute queries | Full SQL | Columnar scans |

Each row in **My Data** shows which backend it uses. Very large uploads are converted to GeoParquet
automatically, because that is the shape that stays fast at size.

## Large files

Files above about 48 MB do not go through the API at all — the browser uploads them **directly to
object storage** in parts, then GeoDeploy ingests them from there.

!!! info "Why this matters"
    Many hosting setups cap how large a single request may be — often at 100 MB — and the request is
    rejected before it reaches the application, which looks like an unexplained network error.
    Uploading in parts sidesteps that entirely, so multi-gigabyte files work even behind a proxy or
    CDN with a small body limit.

Progress is shown per file, and processing continues in the background: you can leave the page and
come back.

## What happens to your file

Worth knowing, because it explains why things behave as they do — and every piece is open source and
replaceable.

=== "A vector layer"

    ```mermaid
    flowchart LR
      F[Your file] --> I[Inspect: geometry,<br/>CRS, attributes]
      I --> P[(PostGIS)]
      I --> G[GeoParquet<br/>in object storage]
      P --> M[Martin<br/>vector tiles]
      G --> T[PMTiles<br/>tippecanoe]
      M --> V[Portal map]
      T --> V
      G --> D[DuckDB<br/>queries + downloads]
    ```

    Small and frequently-queried data goes into **PostGIS**, and **Martin** serves it as vector tiles.
    Large data becomes **GeoParquet** in object storage, and is tiled to **PMTiles** with
    **tippecanoe** in the background; **DuckDB** answers queries, clips and downloads against the file
    itself. Either way the map gets tiles and you get your data back in full resolution.

=== "A raster"

    ```mermaid
    flowchart LR
      F[Your GeoTIFF] --> C[Converted to<br/>Cloud-Optimized GeoTIFF]
      C --> S[Object storage<br/>MinIO or your S3]
      S --> T[TiTiler]
      T --> V[Portal map]
      S --> D["Direct read<br/>/vsicurl/, rasterio"]
    ```

    Rasters become **Cloud-Optimized GeoTIFFs**, which can be read by range request instead of being
    downloaded whole. **TiTiler** renders map tiles from them on demand, and the same file is readable
    directly by QGIS, GDAL and rasterio.

**Your coordinate system is preserved.** Data is stored in its own CRS rather than being flattened on
the way in; portal maps draw in Web Mercator as every web map does, and downloads can return the
original projection.

!!! note "On the roadmap"
    - **Uploading several files at once**, instead of one at a time.
    - **Archive uploads** — `.tar.gz` and friends, not just `.zip` for Shapefiles.

    See the [roadmap](roadmap.md).

## After upload

Each layer is inspected and given a default style. The row shows **Ready** when it can be added to a
portal — how long that takes depends on the file, from near-instant for a small GeoJSON to a while for
a large dataset being converted and tiled.

!!! note "Styling in the file is not read — send it from QGIS instead"

    The style a layer arrives with is **generated**, not taken from your file. A GeoPackage saved by
    QGIS's *Package Layers*, or by *Save Style ▸ In database*, carries a `layer_styles` table with
    your symbology in it; a `.qml` sitting beside a shapefile is the same idea. GeoDeploy reads
    neither. Your data arrives intact and your styling does not.

    The [QGIS plugin](qgis.md) is the way to keep it. **Upload selected layer(s)…** sends a layer
    *with* its symbology, and it carries more than the file could: a symbol GeoDeploy has no words
    for — an SVG or font marker, a hatch fill — is rendered by QGIS and sent as a picture, where a
    style embedded in a file only points at an SVG on the machine that made it.

    If the layer is already here, style it in GeoDeploy, or open it in QGIS through the plugin and
    press **Save styling to GeoDeploy**.

### Add metadata

Fill in an abstract, keywords, licence and attribution from the layer's row. This is worth doing:
it drives search and filtering in a catalog portal, appears on the portal's About page, and is what
other people see when they find your data.

### Heavy layers

A layer big enough to be slow to draw can be **tiled** from its row, producing a pre-generalized
pyramid so the portal stays responsive at every zoom. See
[Performance tuning](performance-tuning.md).

## Existing data in your bucket

If your GeoParquet is already in object storage, you can attach it in place instead of re-uploading —
GeoDeploy reads the metadata and registers the layer without copying the data.
