# ui/src/components/

## Purpose
Reusable presentational/interactive widgets used by the views, grouped by feature area.

## Contents
- `data/VectorRow.vue` — one row in the Data Manager vector list (name, status badge, metadata, delete).
- `data/RasterRow.vue` — raster equivalent.
- `data/UploadModal.vue` — drag/drop upload dialog; uses `useUpload` for progress + optimistic insert + background polling. `type` prop = `vector | raster`.
- `portal/CreatePortalModal.vue` — new-portal dialog (title, description, access); creates via the portals store then routes to the editor.
- `portal/LayerPanel.vue` — **thin row** mirroring the published portal: drag handle (reorder is wired in `PortalEditor.vue`) · eye/eye-off (`update {visible}`) · **symbol swatch** that opens a **teleported symbology popover** · name · zoom · remove. The popover holds: opacity; vector colour/fill/outline/width; **line type** (solid/dashed/dotted); **point marker shape** (circle/square/triangle/diamond/star/cross) + size; popup-field picker; raster palette+hillshade+Z (single-band) and stretch/rescale (all); save/use default. The list swatch (`geomSvg`/`markerSvg`) draws the actual symbol (colour, dash, marker shape). Emits `update`/`remove`/`zoom`.
- `portal/PortalCard.vue` — portal tile in the builder grid (edit/publish/view/unpublish/delete).
- `shared/StatusBadge.vue` — colored processing/ready/error pill.
- `shared/StorageBar.vue` — used/total storage bar (Settings).

## Dependencies / relationships
- Read/write through `../../stores/` (mostly `data` and `portals`) and call the backend via `../../api`.
- `LayerPanel.vue` reads layer metadata (`columns`, `geometry_type`, `band_count`, `default_style`) from the data store; its style fields must stay consistent with the paint logic in `views/PortalEditor.vue` and the backend `portal_generator.py`.
- Icons from `../../views/icons.js`.

## Current status & known issues
- `LayerPanel` colormap control only shows for single-band rasters (`band_count === 1`).
- Default-style save/use round-trips through `/api/data/{vector,raster}/{id}/default-style`.
- Point markers: `LayerPanel` carries a duplicate `markerImage` SVG helper that mirrors the canvas
  icon logic in `views/PortalEditor.vue` + `templates/shared/portal.js` — change all three together.

## Last updated
2026-06-03
