# ui/src/components/

## Purpose
Reusable presentational/interactive widgets used by the views, grouped by feature area.

## Contents
- `data/VectorRow.vue` — one row in the Data Manager vector list (name, status badge, metadata, delete). Shows a violet **GeoParquet** tag when `storage_backend === 'geoparquet'` (file-backed, not PostGIS). A **"tiling…/tiling failed"** badge shows ONLY when `tile_status` is `'tiling'` or `'error'` — `'none'`/null means "displayed via deck.gl, not PMTiles" (the normal case) and must NOT read as in-progress (was a bug: any truthy non-`ready` tile_status rendered "tiling…" forever). A **Restart** button (refresh icon, hover-reveal while `processing`, always-shown amber on `error`) appears for GeoParquet layers and calls `dataStore.reprocessVector(id)` — re-runs the stalled convert/prep with no re-upload. A **Tile** button (grid icon, hover-reveal; shown for `ready` GeoParquet layers) calls `dataStore.tileVector(id)` → `POST /{id}/tile` to (re)generate the PMTiles archive for fast seamless display of heavy layers; the store flips `tile_status` to `'tiling'` and polls `refresh()` until it settles (tiling has no JobStatus). Re-runnable so the admin can re-tile after a workflow improvement. A sky **Tiled** tag shows when `tile_status === 'ready'` so tiled layers (rendered via static PMTiles vector tiles, not the deck.gl/DuckDB path) are distinguishable from untiled ones.
- `data/RasterRow.vue` — raster equivalent.
- `data/UploadModal.vue` — drag/drop upload dialog; uses `useUpload` for progress + optimistic insert + background polling. `type` prop = `vector | raster`. **CSV** (vector): on selecting a `.csv` it parses the header client-side (with the chosen **delimiter** — comma/semicolon/tab/pipe) and shows a **Geometry mode picker (2026-07-11): X/Y point columns OR a WKT geometry column** (any geometry type — a column named wkt/geometry/geom/the_geom auto-selects WKT mode) + EPSG, then posts to `/data/vector/upload-csv` (background job) instead of the normal ingest. **GeoParquet** (vector, `.parquet`/`.geoparquet`): uploads **direct to storage** via `useUpload.uploadGeoParquet` (presign → PUT to `/s3/` → complete) — never through the API; 10 GB client-side cap. **Large files (≥ 2 GB, any vector format — CSV/GeoJSON/GPKG/zip, 2026-07-11):** `handleFile`/`importCsv` route them to `useUpload.uploadLargeVector` (presign → PUT → `/large/complete`) so they upload direct-to-storage and convert to GeoParquet in the background instead of hitting the API's 2 GB 413 (`LARGE_UPLOAD_THRESHOLD`). The CSV header is still parsed from the first 64 KB, so the X/Y/WKT pickers work at any file size.
- `data/AddSourceModal.vue` — connect an **external source** (XYZ/WMTS · WMS · WFS): type picker, URL, layer name (WMS/WFS), attribution; POSTs to `/data/sources` (WFS validated server-side) and inserts via `dataStore.addExternal`.
- `data/SourceRow.vue` — one external-source row (type badge, kind/geometry/layer, URL, delete).
- `data/DiscoverModal.vue` — **import existing data**: two tabs (PostGIS tables / storage files) from `/data/discover/*`, checkbox-select with an editable per-row **name** (default = table/file name). Storage lists GeoTIFFs (raster) + **GeoParquet files (violet chip, 2026-07-11 — imported as file-backed layers via a background inspect+prep job whose `jobs` the modal polls)** + CSVs; selecting a CSV fetches its header (with a chosen **delimiter**) and shows a **geometry-mode picker (X/Y points or a WKT column, 2026-07-11)** + EPSG (CSV loads into PostGIS, the rest register catalog rows with no copy). Refreshes the store after import.
- `portal/CreatePortalModal.vue` — new-portal dialog (title, description, access); creates via the portals store then routes to the editor.
- `portal/LayerPanel.vue` (resolves vector/raster/**external** layers from the data store; external sources get an opacity-only popover, plus a colour picker for WFS vector) — **thin row** mirroring the published portal: drag handle (reorder is wired in `PortalEditor.vue`) · eye/eye-off (`update {visible}`) · **symbol swatch** that opens a **teleported symbology popover** · name · zoom · remove. The popover holds: opacity; vector colour/fill/outline/width; **line type** (solid/dashed/dotted); **point marker shape** (circle/square/triangle/diamond/star/cross) + size; popup-field picker; **raster band selection** (multiband → RGB composite with R/G/B band pickers, or single band) + palette/hillshade/Z (single-band output) and stretch/rescale (all); save/use default. Band selection stores `style.bidx` (`[n]` single, `[r,g,b]` RGB). The list swatch (`geomSvg`/`markerSvg`) draws the actual symbol (colour, dash, marker shape). Emits `update`/`remove`/`zoom`.
- `portal/PortalCard.vue` — portal tile in the builder grid (edit/publish/view/unpublish/delete).
- `shared/StatusBadge.vue` — colored processing/ready/error pill.
- `shared/StorageBar.vue` — used/total storage bar (Settings).

## Dependencies / relationships
- Read/write through `../../stores/` (mostly `data` and `portals`) and call the backend via `../../api`.
- `LayerPanel.vue` reads layer metadata (`columns`, `geometry_type`, `band_count`, `default_style`) from the data store; its style fields must stay consistent with the paint logic in `views/PortalEditor.vue` and the backend `portal_generator.py`.
- Icons from `../../views/icons.js`.

## Modals
All dialogs (`UploadModal`, `AddSourceModal`, `DiscoverModal`, `portal/CreatePortalModal`) wrap their overlay in `<Teleport to="body">` so the backdrop covers the full viewport (they used to render inside the scrollable `<main>`, which left an un-dimmed strip). Overlay style: `bg-gray-900/50 backdrop-blur-sm`, card `shadow-2xl`.

## Current status & known issues
- `LayerPanel` colormap/hillshade controls show for single-band output: a single-band raster
  (`band_count === 1`) or a multiband raster in **Single band** mode. Multiband rasters also get a
  band-mode picker (RGB composite ↔ single band). Colormap is cleared when switching to RGB (it is
  meaningless for a 3-band composite).
- Default-style save/use round-trips through `/api/data/{vector,raster}/{id}/default-style`.
- Point markers: `LayerPanel` carries a duplicate `markerImage` SVG helper that mirrors the canvas
  icon logic in `views/PortalEditor.vue` + `templates/shared/portal.js` — change all three together.

## Last updated
2026-07-11 (VectorRow: manual Tile button → `dataStore.tileVector` → `POST /{id}/tile`, re-runnable PMTiles tiling for heavy GeoParquet)
