# ui/src/views/

## Purpose
Page-level route components. All except SetupWizard/Login render inside `Layout.vue`.

## Contents
- `Layout.vue` — authenticated shell: dark sidebar nav (Data / Portals / Templates / Settings), logout, `<RouterView>` for the page body.
- `Login.vue` — email/password → `auth.loginUser`.
- `SetupWizard.vue` — multi-step first-run wizard (Database → Storage → Admin). Posts to `/api/setup/*`. Shown when setup is incomplete.
- `DataManager.vue` — lists vector + raster layers + **external sources** (WMS/XYZ/WFS) from the data store; opens upload modals and the "Connect source" modal (`AddSourceModal`). Polls happen via the store.
- `PortalBuilder.vue` — portal grid; create/publish/unpublish/delete via the portals store and `PortalCard`.
- `PortalEditor.vue` — the big one: left panel (drag-reorderable layer list via `LayerPanel`, template, access) + live MapLibre preview. `buildPreviewStyle()` constructs the style by hand from `dataStore` layers + `layerConfigs` and returns the **merged** bbox of all visible layers. Save/publish call the portals store. **Prefixes tile URLs with `location.origin`** so MapLibre's worker can fetch them. **Camera:** the rebuild watcher only moves the camera on the FIRST build (restore `portal.initial_view`, else fit all layers); later style edits keep the current view (`setStyle` preserves the camera) — this is what stopped band/colour tweaks from yanking the map. **`save()` persists the current center/zoom as `initial_view`** (baked into the published portal as `geodeploy.view`); a **"Zoom to all"** preview button fits the merged extent. **Z-order:** `layerConfigs[0]` = top of list = top of map (`addLayer` `unshift`s; the build loop iterates `[...].reverse()`; hidden layers `cfg.visible===false` are skipped in preview). **Points** render as `symbol` layers with a canvas marker icon generated on `styleimagemissing` (icon id encodes shape/colour/size); `markerImage` here duplicates the helper in `templates/shared/portal.js`. **Raster band selection:** `rasterTilesUrl()` appends `&bidx=` per band from `cfg.style.bidx` (and drops the colormap for a 3-band RGB composite); `addLayer` copies `default_style.bidx` onto new raster layers. **External sources** (`layer_type: 'external'`) appear in `availableLayers` and render in `buildPreviewStyle` (raster tiles for wms/xyz; geojson via the same-origin proxy for wfs).
- `TemplateGallery.vue` — lists `/api/templates` with preview images.
- `Settings.vue` — infrastructure health + per-service **start/stop/restart** controls (Coolify-style, `POST /admin/services/{name}/{action}`), storage stats, account, and the **"Reload Martin config"** button (`POST /admin/reload-martin`) for empty tile catalogs.
- `icons.js` — shared inline SVG icon components (imported across views/components).

## Dependencies / relationships
- Read/write state through `../stores/` (`data`, `portals`, `auth`, `system`).
- Call the backend only through `../api`.
- `PortalEditor.vue` mirrors `api/geodeploy/services/portal_generator.py`'s style logic — change both together.
- `useMaplibre` composable backs the preview maps.

## Current status & known issues
- **Three parallel MapLibre-style surfaces, keep in sync:** `PortalEditor.buildPreviewStyle()` (editor preview), `portal_generator.generate_style()` (published style), and `templates/shared/portal.js` (the live portal runtime — layer list, symbology popover, marker icons). Divergence = "works in preview, broken in published portal" (or vice-versa).
- Raster layer `bbox` from the API is in source CRS (not lon/lat) — using it directly for `fitToBbox` can throw "Invalid LngLat" (see tasks/raster notes). Prefer zooming via vector bounds or TiTiler TileJSON.

## Last updated
2026-06-04
