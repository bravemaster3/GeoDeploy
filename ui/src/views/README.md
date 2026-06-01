# ui/src/views/

## Purpose
Page-level route components. All except SetupWizard/Login render inside `Layout.vue`.

## Contents
- `Layout.vue` — authenticated shell: dark sidebar nav (Data / Portals / Templates / Settings), logout, `<RouterView>` for the page body.
- `Login.vue` — email/password → `auth.loginUser`.
- `SetupWizard.vue` — multi-step first-run wizard (Database → Storage → Admin). Posts to `/api/setup/*`. Shown when setup is incomplete.
- `DataManager.vue` — lists vector + raster layers (from the data store), opens upload modals. Polls happen via the store.
- `PortalBuilder.vue` — portal grid; create/publish/unpublish/delete via the portals store and `PortalCard`.
- `PortalEditor.vue` — the big one: left panel (layers, template, access) + live MapLibre preview. `buildPreviewStyle()` constructs the style by hand from `dataStore` layers + `layerConfigs`. Save/publish call the portals store. **Prefixes tile URLs with `location.origin`** so MapLibre's worker can fetch them.
- `TemplateGallery.vue` — lists `/api/templates` with preview images.
- `Settings.vue` — infrastructure health + per-service **start/stop/restart** controls (Coolify-style, `POST /admin/services/{name}/{action}`), storage stats, account, and the **"Reload Martin config"** button (`POST /admin/reload-martin`) for empty tile catalogs.
- `icons.js` — shared inline SVG icon components (imported across views/components).

## Dependencies / relationships
- Read/write state through `../stores/` (`data`, `portals`, `auth`, `system`).
- Call the backend only through `../api`.
- `PortalEditor.vue` mirrors `api/geodeploy/services/portal_generator.py`'s style logic — change both together.
- `useMaplibre` composable backs the preview maps.

## Current status & known issues
- `PortalEditor.buildPreviewStyle()` and `portal_generator.generate_style()` are parallel implementations of the same MapLibre style; divergence = "works in preview, broken in published portal" (or vice-versa).
- Raster layer `bbox` from the API is in source CRS (not lon/lat) — using it directly for `fitToBbox` can throw "Invalid LngLat" (see tasks/raster notes). Prefer zooming via vector bounds or TiTiler TileJSON.

## Last updated
2026-06-01
