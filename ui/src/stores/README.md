# ui/src/stores/

## Purpose
Pinia stores — client-side state, all using the composition (setup) style.

## Contents
- `auth.js` — `user`, `fetchMe`, `loginUser` (stores JWT in `localStorage` as `geodeploy_token`), `logout`.
- `data.js` — `vectorLayers`, `rasterLayers`, `externalSources` (WMS/XYZ/WFS connections); `refresh()` loads all three; `pollJob(jobId, type, layerId)` polls every 2s until `ready`/`error` and refreshes; `removeVector`/`removeRaster`/`removeExternal`/`addExternal`.
- `portals.js` — `portals`; `refresh`, `create`, `update`, `publish`, `unpublish`, `remove`.
- `system.js` — `health`, `stats`; `refreshHealth`, `refreshStats` (admin endpoints).

## Dependencies / relationships
- All call the backend exclusively through `../api`.
- `data.js` is consumed by `DataManager.vue`, `PortalEditor.vue`, `LayerPanel.vue`, and `useUpload.js`.
- `portals.js` is consumed by `PortalBuilder.vue` and `PortalEditor.vue`.
- `system.js` is consumed by `Settings.vue`.

## Current status & known issues
- `pollJob` uses a 2s `setInterval`; it resolves on `ready` (triggering a full `refresh`) or rejects on `error`. No max-attempt cap.
- The 401 redirect lives in `../api` (axios interceptor), not in `auth.js`.

## Last updated
2026-06-04
