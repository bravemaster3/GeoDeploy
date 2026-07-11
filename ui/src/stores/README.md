# ui/src/stores/

## Purpose
Pinia stores — client-side state, all using the composition (setup) style.

## Contents
- `auth.js` — `user`, `fetchMe`, `loginUser` (stores JWT in `localStorage` as `geodeploy_token`), `logout`.
- `data.js` — `vectorLayers`, `rasterLayers`, `externalSources` (WMS/XYZ/WFS connections); `refresh()` loads all three; `pollJob(jobId, type, layerId)` polls every 2s until `ready`/`error` and refreshes (**tolerates transient failures — up to 8 consecutive before giving up — so a multi-minute convert/prep job's progress doesn't freeze on one blip**); **`watchProcessing()` (auto-run at the end of every `refresh()`) re-fetches the whole list every 3s while any layer is `processing`/`queued`** — the safety net that advances the UI processing→ready without a manual page refresh, and resumes after a reload (independent of the per-job poll); `removeVector`/`removeRaster`/`removeExternal`/`addExternal`.
- `portals.js` — `portals`; `refresh`, `create`, `update`, `publish`, `unpublish`, `remove`.
- `system.js` — `health`, `stats`; `refreshHealth`, `refreshStats` (admin endpoints).

## Dependencies / relationships
- All call the backend exclusively through `../api`.
- `data.js` is consumed by `DataManager.vue`, `PortalEditor.vue`, `LayerPanel.vue`, and `useUpload.js`.
- `portals.js` is consumed by `PortalBuilder.vue` and `PortalEditor.vue`.
- `system.js` is consumed by `Settings.vue`.

## Current status & known issues
- `pollJob` uses a 2s `setInterval`; it resolves on `ready` (triggering a full `refresh`) or rejects after 8 consecutive fetch failures. `watchProcessing` (3s) is the list-level backstop and self-stops when nothing is busy.
- The 401 redirect lives in `../api` (axios interceptor), not in `auth.js`.
- **UI bundle caching (see `ui/nginx.conf`):** content-hashed `/assets/*` are cached `immutable`; `index.html` is served `Cache-Control: no-cache` so a rebuild's new bundle loads immediately (a cached `index.html` used to keep the browser on a stale bundle — the page looked "stuck", live search felt non-live — until a manual hard-refresh).

## Last updated
2026-07-11 (resilient pollJob + watchProcessing live-update backstop; nginx no-cache index.html)
