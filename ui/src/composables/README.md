# `ui/src/composables/`

## Purpose

Reusable stateful logic pulled out of components — the things two or more views need to do
identically, where a copy in each would drift.

## Contents

- `useMaplibre.js` — map instance lifecycle for the views that host a MapLibre map directly.
- `useUpload.js` — the upload pipeline shared by the data views (chunking, direct-to-storage
  hand-off, progress).
- `portalThumbnail.js` (2026-08-02) — `capturePortalThumbnail(portalId)`: mounts the portal preview
  in an **off-screen iframe**, asks the runtime for one snapshot, uploads it as the card image.

  Exists because the capture used to live only in `PortalEditor.vue`, which photographs its own
  live preview iframe. The Portals **list** also publishes, had no preview, and so produced portals
  with no thumbnail at all — which read as the feature being broken rather than as a path that
  never had it.

  Three requirements that each yield a silent failure if missed:
  - **Build the preview bundle first.** `/portals/_preview/{id}/` is not a live route; it serves a
    bundle `POST /portals/{id}/preview` writes to disk, and only the editor ever called that. From
    the Portals list the directory frequently did not exist, so the iframe loaded a 404 and nothing
    answered. Rebuilding also re-bakes the bundle from the CURRENT `templates/shared/portal.js`
    (`portal_generator` inlines the runtime at publish time), so an old portal is not photographed
    by an old runtime.
  - **Wait for the runtime's `ready` message, do not post on `iframe.onload`.** `portal.js` installs
    its listener inside `setupEditMode()`, called from `map.on('load')` — seconds after the document
    loads. A request sent at onload arrives before anything is listening and is dropped silently.
    The request is also repeated every 2s, which is the only way to reach a bundle old enough not to
    send `ready` at all.
  - **`?edit=1`** — `templates/shared/portal.js` sets `preserveDrawingBuffer` only in edit mode (it
    costs performance, so a published portal does not pay for it). Without it the WebGL canvas is
    already cleared by the time `toDataURL` runs.
  - **Off-screen, not hidden** — `display:none` or a 0×0 iframe gives MapLibre a zero-sized canvas.
    Position it at `-10000px` at a real size (1200×800) instead.

## Dependencies / relationships

- `portalThumbnail.js` speaks the same `postMessage` protocol as `PortalEditor.vue` and
  `templates/shared/portal.js` (`{ gd: 1, type: 'snapshot', requestId }` → reply with `dataUrl`,
  always sent, `null` on failure). A change to that protocol touches all three.
- It calls `syncSession()` first: the `/portals/_preview/…` route is behind an nginx session gate,
  and without the cookie the iframe loads the login page and photographs that.
- Uploads via `uploadPortalThumbnail` → `POST /api/portals/{id}/thumbnail`, which mints a unique
  `thumbnail-{hex}.webp` name per capture (a fixed name was cached for a day by the asset headers,
  which is why thumbnails once appeared to take hours).

## Current status & known issues

- The editor keeps its own copy of the capture flow rather than using `portalThumbnail.js`: it
  already has a warm, visible iframe, so mounting a second one would be slower and would photograph
  a colder map. The duplication is deliberate but real — a protocol change must update both.
- Capture is best-effort everywhere. A failed or blank capture leaves the previous image, or the
  gradient placeholder; publishing never fails over a picture.

## Last updated

2026-08-02
