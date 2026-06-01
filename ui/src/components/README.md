# ui/src/components/

## Purpose
Reusable presentational/interactive widgets used by the views, grouped by feature area.

## Contents
- `data/VectorRow.vue` — one row in the Data Manager vector list (name, status badge, metadata, delete).
- `data/RasterRow.vue` — raster equivalent.
- `data/UploadModal.vue` — drag/drop upload dialog; uses `useUpload` for progress + optimistic insert + background polling. `type` prop = `vector | raster`.
- `portal/CreatePortalModal.vue` — new-portal dialog (title, description, access); creates via the portals store then routes to the editor.
- `portal/LayerPanel.vue` — per-layer controls in the editor (opacity, vector color/fill/outline/width/radius by geometry type, popup field picker, save/use default style). Raster controls: **color palette + hillshade** (single-band), and **stretch/rescale** (all rasters, "min,max"). Header shows a **geometry icon** (point/line/polygon/raster) and the name/row is **click-to-expand**. Accepts `initialExpanded` so the editor can auto-open newly added layers. Emits `update`/`remove`/`zoom`.
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

## Last updated
2026-06-01
