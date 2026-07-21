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
- `PortalEditor.vue` — the big one: left panel (a **folder tree** of layers via the recursive
  `components/portal/LayerTree.vue`, template, access) + live MapLibre preview. **Layer catalog (V-13,
  2026-07-20):** the flat layer list is now a nested folder tree (`layerTree` ref, built from
  `portal.layer_groups` via `reconcileTree`, mirrored in the flat `layerConfigs` styles). LayerTree
  renders group nodes + `LayerPanel` leaves and does within-level ops (reorder ↑/↓, add/sub-folder,
  rename, collapse, exclusive, description, delete-promotes-children); layer ops + "move to folder"
  bubble to PortalEditor. **Drag & drop (2026-07-21):** a `dnd` controller owned by PortalEditor centralizes
  moves — LayerTree fires start/over/drop (from `LayerPanel`'s grip for layers, a folder grip for groups)
  with a node ref + position (before/after/into), and `_moveNode` relocates that exact node in the single
  `layerTree` (drop into a folder · reorder · drag whole folders; guards against a folder into its own
  descendant). The ↑/↓ arrows + move-to-folder menu remain as an explicit fallback (arrows are additive,
  not removed). **Search (`layerFilter`)** hides non-matching layers + empty folders (name resolved via the
  data store); **Expand all / Collapse all** (`setAllCollapsed`); **zoom-to-folder** (`zoomToGroup` unions
  the folder's layers' bboxes → `fitToBbox` on the preview, `@group-zoom`). `buildPreviewStyle` draws in **flattened-tree order** (parity with
  `generate_style` + `portal.js applyLayerGroups`); `save()` sends `layer_groups`. No tree → flat. **Access picker (2026-07-16):** the published-access tier (public/password/organization/owner, labelled Public/Password/Organization/**Private**) is an inline **icon dropdown** (colored icon + one-line description per tier, teleported to `<body>` + fixed-positioned so the scrolling sidebar can't clip it — same pattern as `components/shared/VisibilitySelect.vue`), replacing the old radio list; the password field shows under it when Password is chosen. The **PortalCard** keeps a read-only badge of the saved tier. `buildPreviewStyle()` constructs the style by hand from `dataStore` layers + `layerConfigs` and returns the **merged** bbox of all visible layers. Save/publish call the portals store. **Prefixes tile URLs with `location.origin`** so MapLibre's worker can fetch them. **Camera:** the rebuild watcher only moves the camera on the FIRST build (restore `portal.initial_view`, else fit all layers); later style edits keep the current view (`setStyle` preserves the camera) — this is what stopped band/colour tweaks from yanking the map. **`save()` persists the current center/zoom as `initial_view`** (baked into the published portal as `geodeploy.view`); a **"Zoom to all"** preview button fits the merged extent. **Z-order:** `layerConfigs[0]` = top of list = top of map (`addLayer` `unshift`s; the build loop iterates `[...].reverse()`; hidden layers `cfg.visible===false` are skipped in preview). **Points** render as `symbol` layers with a canvas marker icon generated on `styleimagemissing` (icon id encodes shape/colour/size); `markerImage` here duplicates the helper in `templates/shared/portal.js`. **Raster band selection:** `rasterTilesUrl()` appends `&bidx=` per band from `cfg.style.bidx` (and drops the colormap for a 3-band RGB composite); `addLayer` copies `default_style.bidx` onto new raster layers. **External sources** (`layer_type: 'external'`) appear in `availableLayers` and render in `buildPreviewStyle` (raster tiles for wms/xyz; geojson via the same-origin proxy for wfs). **GeoParquet (file-backed) vector layers** (`storage_backend === 'geoparquet'`) render PRIMARILY via a **deck.gl `MapboxOverlay`** (added as a control → survives `setStyle`) fed by the authed `/features` viewport query — heavy prepped layers show a **density-grid overview** from the layer manifest at large scale and swap to real features when the viewport qualifies (`deckViewportLoad` gates on files/rows, twin of portal.js — keep in sync). A layer explicitly tiled (ready `pmtiles_key`) instead uses the `pmtiles://` vector source (protocol registered in `useMaplibre.js`) and the normal MapLibre paint. **Preview identify popup (2026-07-11):** clicking the preview opens a maplibre Popup with feature attributes — MVT (PostGIS) layers via `queryRenderedFeatures`, GeoParquet deck layers via the public `/identify` endpoint (`identifyVectorFeatures`; skipped while a layer shows the density overview); mirrors the portal.js popup, keep in sync. **Rename (2026-07-11):** clicking the portal title in the top bar turns it into an input; committing calls `portalsStore.update(id, {title})` — the server regenerates the URL **slug** and (if published) re-publishes under it, so the live `/portals/{slug}/` link updates instantly. The editor's own route is by portal **id**, so this page's URL doesn't change. **No-flash first paint (2026-07-17):** the map mounts BLANK (`useMaplibre(..., {version:8,sources:{},layers:[]})`, not the OSM default) and the style watcher is gated on a `ready` flag flipped only after the portal + data + basemap catalog have all loaded — so the preview paints ONCE on the chosen basemap + layers instead of flashing OSM→light→chosen→catalog-swap (each `applyStyle` is a full `setStyle` repaint). The watcher also **dedupes**: it skips `applyStyle` when the rebuilt style JSON is unchanged (e.g. the `/api/basemaps` catalog resolving to identical tiles), but always runs `refreshDeck` (deck layers live outside the MapLibre style). **Viewport-fetch aborting (2026-07-11):** deck.gl `/features` fetches carry an `AbortController` signal; a newer view (or hitting Save/Publish) aborts the previous in-flight fetches, so rapid pans over a heavy GeoParquet can't pile up and saturate the browser's ~6-connection limit — which used to starve the Save/Publish request and make it "never save" (the extent then not persisting was a symptom of that starved save).
- `TemplateGallery.vue` — lists `/api/templates` with preview images.
- `Login.vue` — email/password + "Forgot password?" (when email configured) + a **"Sign in with …"** SSO
  button (A-04) shown when `GET /auth/oidc/status` reports enabled; reads `?sso_error` to surface a
  refusal. `SsoCallback.vue` (route `/sso-callback`, public) — the OIDC redirect lands here; it pulls the
  JWT from the `gd_session` cookie via `GET /auth/session-token` into localStorage, `fetchMe`, → `/data`.
- `Settings.vue` — **tabbed** (2026-07-17, so it doesn't sprawl): now Account · API tokens (everyone) ·
  Infrastructure · Email · **Authentication** (admin). The **Authentication** tab (A-04) is the OIDC
  provider form (enabled, issuer, client_id, client_secret blank-to-keep, label, auto_provision +
  allowed_domains + default_role) with the read-only redirect URI to register with the provider. The
  Account tab gained a **"Log out other sessions"** button (A-04). Original tab notes: an underline tab bar driven by an `activeTab` ref + a role-filtered `tabs` computed. **Account** + **API tokens** show for everyone; **Infrastructure** (health rows with status pills + per-service start/stop/restart `POST /admin/services/{name}/{action}` + "Reload Martin"; and Storage bar/tiles) and **Email** are admin-only tabs (their admin API calls still don't fire for lower roles). Panels are `v-if`'d on the active tab; the existing cards moved verbatim under their tab. **API tokens tab (A-03):** lists the caller's tokens (name, scope badges, `gdp_…` prefix, last-used/expiry) with two-step inline revoke, and a "Create token" button opening `components/users/TokenModal.vue`; calls `listTokens`/`revokeToken`/`createToken`. Account keeps the change-password form (`PUT /auth/password`) + sign-out.
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
2026-07-21 (V-13 editor catalog: LayerTree drag & drop — reorder · into-folder · drag whole folders —
centralized in a PortalEditor `dnd` controller, arrows kept as fallback; layer search, expand/collapse-all,
zoom-to-folder (`zoomToGroup`). `Activity.vue`: click a "Who" cell → a user-info popup (name/role/email +
layer/portal/source counts) from the users store. `DataManager` rows: inline **rename** a layer via
`components/data/VectorRow`/`RasterRow` pencil → `PUT /data/{vector,raster}/{id}/rename`.)
2026-07-16 (Settings Storage: real per-store breakdown — stacked proportion bar + PostGIS/raster/
GeoParquet/portal-pages tiles from the new storage-stats fields; '—' = unmeasurable, not zero;
StorageBar.vue no longer used here)
2026-07-16 (C-08a: Login forgot-password; Settings → Email section — optional generic SMTP with
provider recipes + test-send; email is additive, copy links always remain)
2026-07-16 (RBAC A-01: Users/AcceptInvite/ResetPassword views; role-aware nav + router guards
(`requiresAdmin` on /users, `requiresEditor` on the portal editor); `v-if auth.canEdit` sweep on
mutating controls in DataManager/PortalBuilder; created-by chips + client-side creator filter;
Settings admin gating + password form)
2026-07-11 (PortalEditor: preview identify popup; corrected stale PMTiles-primary text to deck.gl-primary; portal rename → slug/URL change + republish; viewport-fetch aborting so heavy GeoParquet doesn't starve Save/Publish; dark-mode MapLibre controls + themed popup + popup z-index above deck in style.css; overview→detail switch is rows-only so dense city cells aren't locked in overview; incremental viewport loading — buffered fetch + skip refetch while the viewport stays in the loaded region at the same zoom, so panning/returning doesn't reload on-screen data)
