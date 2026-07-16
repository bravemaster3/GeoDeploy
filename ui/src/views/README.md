# ui/src/views/

## Purpose
Page-level route components. All except SetupWizard/Login render inside `Layout.vue`.

## Contents
- `Layout.vue` — authenticated shell: dark sidebar nav (Data / Portals / Templates / [Users] /
  Settings — the Users entry appears only for admin/owner via the `nav` computed), logout,
  `<RouterView>` for the page body.
- `Login.vue` — email/password → `auth.loginUser`; offers **"Forgot password?"** (inline email form
  → `POST /auth/forgot-password`, always the same "if it exists, a link was sent" outcome) ONLY when
  `/setup/status` reports `email_enabled` (C-08a).
- `Users.vue` — **admin Users screen (RBAC A-01, route `/users`, `meta.requiresAdmin`)**: Members +
  Pending invitations section cards; invite modal. Components in `components/users/` (see its README).
- `AcceptInvite.vue` / `ResetPassword.vue` — PUBLIC token-link pages (`/accept-invite?token=`,
  `/reset-password?token=`), Login-style shell: validate the token via `GET /auth/invitations/{token}`
  (friendly 410/404 states), then accept (name+password → auto-login) or set the new password.
- `SetupWizard.vue` — multi-step first-run wizard (Database → Storage → Admin). Posts to `/api/setup/*`. Shown when setup is incomplete.
- `DataManager.vue` — card-based layout (max-w-6xl): one section card per data type (vector / raster / external), each with an icon chip, count badge, and a contextual action button in its header (Upload / Connect), plus a global "Import existing" (`DiscoverModal`). Lists vector + raster layers + external sources from the data store; opens upload/connect modals. Polls happen via the store.
- `PortalBuilder.vue` — portal grid; create/publish/unpublish/delete via the portals store and `PortalCard`.
- `PortalEditor.vue` — the big one: left panel (drag-reorderable layer list via `LayerPanel`, template, access) + live MapLibre preview. `buildPreviewStyle()` constructs the style by hand from `dataStore` layers + `layerConfigs` and returns the **merged** bbox of all visible layers. Save/publish call the portals store. **Prefixes tile URLs with `location.origin`** so MapLibre's worker can fetch them. **Camera:** the rebuild watcher only moves the camera on the FIRST build (restore `portal.initial_view`, else fit all layers); later style edits keep the current view (`setStyle` preserves the camera) — this is what stopped band/colour tweaks from yanking the map. **`save()` persists the current center/zoom as `initial_view`** (baked into the published portal as `geodeploy.view`); a **"Zoom to all"** preview button fits the merged extent. **Z-order:** `layerConfigs[0]` = top of list = top of map (`addLayer` `unshift`s; the build loop iterates `[...].reverse()`; hidden layers `cfg.visible===false` are skipped in preview). **Points** render as `symbol` layers with a canvas marker icon generated on `styleimagemissing` (icon id encodes shape/colour/size); `markerImage` here duplicates the helper in `templates/shared/portal.js`. **Raster band selection:** `rasterTilesUrl()` appends `&bidx=` per band from `cfg.style.bidx` (and drops the colormap for a 3-band RGB composite); `addLayer` copies `default_style.bidx` onto new raster layers. **External sources** (`layer_type: 'external'`) appear in `availableLayers` and render in `buildPreviewStyle` (raster tiles for wms/xyz; geojson via the same-origin proxy for wfs). **GeoParquet (file-backed) vector layers** (`storage_backend === 'geoparquet'`) render PRIMARILY via a **deck.gl `MapboxOverlay`** (added as a control → survives `setStyle`) fed by the authed `/features` viewport query — heavy prepped layers show a **density-grid overview** from the layer manifest at large scale and swap to real features when the viewport qualifies (`deckViewportLoad` gates on files/rows, twin of portal.js — keep in sync). A layer explicitly tiled (ready `pmtiles_key`) instead uses the `pmtiles://` vector source (protocol registered in `useMaplibre.js`) and the normal MapLibre paint. **Preview identify popup (2026-07-11):** clicking the preview opens a maplibre Popup with feature attributes — MVT (PostGIS) layers via `queryRenderedFeatures`, GeoParquet deck layers via the public `/identify` endpoint (`identifyVectorFeatures`; skipped while a layer shows the density overview); mirrors the portal.js popup, keep in sync. **Rename (2026-07-11):** clicking the portal title in the top bar turns it into an input; committing calls `portalsStore.update(id, {title})` — the server regenerates the URL **slug** and (if published) re-publishes under it, so the live `/portals/{slug}/` link updates instantly. The editor's own route is by portal **id**, so this page's URL doesn't change. **Viewport-fetch aborting (2026-07-11):** deck.gl `/features` fetches carry an `AbortController` signal; a newer view (or hitting Save/Publish) aborts the previous in-flight fetches, so rapid pans over a heavy GeoParquet can't pile up and saturate the browser's ~6-connection limit — which used to starve the Save/Publish request and make it "never save" (the extent then not persisting was a symptom of that starved save).
- `TemplateGallery.vue` — lists `/api/templates` with preview images.
- `Settings.vue` — card-based layout (max-w-4xl): Infrastructure (health rows with status pills + per-service **start/stop/restart**, `POST /admin/services/{name}/{action}`, + **"Reload Martin"** `POST /admin/reload-martin`) and Storage (bar + stat tiles) are **admin-only** (`v-if auth.isAdmin`, and their admin-only API calls don't fire for lower roles); Account (avatar + role badge, **change-password form** → `PUT /auth/password`, sign-out) shows for everyone.
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
2026-07-16 (C-08a: Login forgot-password; Settings → Email section — optional generic SMTP with
provider recipes + test-send; email is additive, copy links always remain)
2026-07-16 (RBAC A-01: Users/AcceptInvite/ResetPassword views; role-aware nav + router guards
(`requiresAdmin` on /users, `requiresEditor` on the portal editor); `v-if auth.canEdit` sweep on
mutating controls in DataManager/PortalBuilder; created-by chips + client-side creator filter;
Settings admin gating + password form)
2026-07-11 (PortalEditor: preview identify popup; corrected stale PMTiles-primary text to deck.gl-primary; portal rename → slug/URL change + republish; viewport-fetch aborting so heavy GeoParquet doesn't starve Save/Publish; dark-mode MapLibre controls + themed popup + popup z-index above deck in style.css; overview→detail switch is rows-only so dense city cells aren't locked in overview; incremental viewport loading — buffered fetch + skip refetch while the viewport stays in the loaded region at the same zoom, so panning/returning doesn't reload on-screen data)
