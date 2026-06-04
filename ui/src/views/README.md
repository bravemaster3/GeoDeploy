# ui/src/views/

## Purpose
Page-level route components. All except SetupWizard/Login render inside `Layout.vue`.

## Contents
- `Layout.vue` — authenticated shell: dark sidebar nav (Data / Portals / Templates / Settings), logout, `<RouterView>` for the page body.
- `Login.vue` — email/password → `auth.loginUser`.
- `SetupWizard.vue` — multi-step first-run wizard (Database → Storage → Admin). Posts to `/api/setup/*`. Shown when setup is incomplete.
- `DataManager.vue` — card-based layout (max-w-6xl): one section card per data type (vector / raster / external), each with an icon chip, count badge, and a contextual action button in its header (Upload / Connect), plus a global "Import existing" (`DiscoverModal`). Lists vector + raster layers + external sources from the data store; opens upload/connect modals. Polls happen via the store.
- `PortalBuilder.vue` — portal grid; create/publish/unpublish/delete via the portals store and `PortalCard`.
- `PortalEditor.vue` — the big one: left panel (drag-reorderable layer list via `LayerPanel`, template, access) + live MapLibre preview. `buildPreviewStyle()` constructs the style by hand from `dataStore` layers + `layerConfigs` and returns the **merged** bbox of all visible layers. Save/publish call the portals store. **Prefixes tile URLs with `location.origin`** so MapLibre's worker can fetch them. **Camera:** the rebuild watcher only moves the camera on the FIRST build (restore `portal.initial_view`, else fit all layers); later style edits keep the current view (`setStyle` preserves the camera) — this is what stopped band/colour tweaks from yanking the map. **`save()` persists the current center/zoom as `initial_view`** (baked into the published portal as `geodeploy.view`); a **"Zoom to all"** preview button fits the merged extent. **Z-order:** `layerConfigs[0]` = top of list = top of map (`addLayer` `unshift`s; the build loop iterates `[...].reverse()`; hidden layers `cfg.visible===false` are skipped in preview). **Points** render as `symbol` layers with a canvas marker icon generated on `styleimagemissing` (icon id encodes shape/colour/size); `markerImage` here duplicates the helper in `templates/shared/portal.js`. **Raster band selection:** `rasterTilesUrl()` appends `&bidx=` per band from `cfg.style.bidx` (and drops the colormap for a 3-band RGB composite); `addLayer` copies `default_style.bidx` onto new raster layers. **External sources** (`layer_type: 'external'`) appear in `availableLayers` and render in `buildPreviewStyle` (raster tiles for wms/xyz; geojson via the same-origin proxy for wfs). **GeoParquet (file-backed) vector layers** (`storage_backend === 'geoparquet'`) render as a **PMTiles vector source** — `buildPreviewStyle` emits `{type:'vector', url:'pmtiles://<origin>/api/data/vector/{id}/pmtiles'}` with `source-layer: 'geodeploy'`, reusing the **same fill/line/symbol paint** as PostGIS vector layers (so symbology/z-order just work). The `pmtiles://` protocol is registered once in `useMaplibre.js`. A geoparquet layer is only drawn once `tile_status === 'ready'` (else just its bbox feeds zoom-to-all). The earlier deck.gl/DuckDB-viewport overlay was removed (it froze on big files + mis-rendered). The **published portal** renders geoparquet the same way (`portal_generator` emits the `pmtiles://` source, `portal.js` registers the protocol + absolutifies the url) — so editor and published are in parity.
- `TemplateGallery.vue` — lists `/api/templates` with preview images.
- `Settings.vue` — card-based layout (max-w-4xl): Infrastructure (health rows with status pills + per-service **start/stop/restart**, `POST /admin/services/{name}/{action}`, + **"Reload Martin"** `POST /admin/reload-martin`), Storage (bar + stat tiles), Account (avatar/sign-out).
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
