# Uploading data

**My Data** holds everything you have brought in. Vector layers and rasters are listed separately,
each row showing its status, size and how it is stored.

## What you can upload

=== "Vector"

    | Format | Notes |
    | --- | --- |
    | Shapefile | Upload as a `.zip` containing `.shp`, `.dbf`, `.shx`, `.prj` |
    | GeoPackage | `.gpkg` |
    | GeoJSON | `.geojson` / `.json` |
    | CSV | With X/Y columns or a WKT geometry column — you choose at upload |
    | GeoParquet | `.parquet`, or an existing partitioned dataset already in your bucket |

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

## After upload

Each layer is inspected, reprojected as needed and given a default style. The row shows **Ready**
when it can be added to a portal — usually seconds, longer for very large data.

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
