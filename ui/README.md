# ui/

## Purpose
Vue 3 single-page dashboard — the browser-only control panel for setup, data upload, portal building, and settings. Talks only to `/api/*`.

## Contents
- `src/main.js` — app bootstrap: Pinia, vue-router, vue-i18n (locale from `navigator.language`, en/fr).
- `src/App.vue` — root shell (just `<RouterView>`).
- `src/router/index.js` — routes + global guard: checks `/api/setup/status` first (redirects to `/setup` if incomplete), then auth (`/login`). Authenticated pages are children of `views/Layout.vue`.
- `src/api/index.js` — axios instance (`baseURL: /api`), attaches the JWT from `localStorage`, redirects to `/login` on 401. **Every backend call is a named export here** — the single source of truth for endpoints.
- `src/stores/` — Pinia state. See `stores/README.md`.
- `src/views/` — page-level components. See `views/README.md`.
- `src/components/` — reusable widgets. See `components/README.md`.
- `src/composables/useMaplibre.js` — wraps a MapLibre map instance (`applyStyle`, `fitToBbox`, `jumpTo`, `loaded`).
- `src/composables/useUpload.js` — upload + optimistic store insert + background job polling. `uploadGeoParquet()` does the presigned DIRECT-to-storage flow (presign → raw PUT to `/s3/` → complete), bypassing the API for multi-GB files.
- `src/views/icons.js` — shared inline SVG icon components.
- `src/i18n/en.json`, `fr.json` — UI strings (FR ships at Phase 1).
- `src/style.css`, `tailwind.config.js`, `postcss.config.js` — Tailwind setup (brand colors, `.btn-primary`/`.card`/`.input` utility classes).
- `index.html`, `vite.config.js` — Vite entry + dev server. **Vite dev proxy** forwards `/api`, `/portals`, `/tiles` (→ martin:3000), `/raster` (→ titiler:80) with path rewrites that strip the prefix (mirrors nginx).
- `Dockerfile` (multi-stage; `development` target = `npm run dev`), `nginx.conf` (serves the built SPA inside the `geodeploy-ui` container).

## Dependencies / relationships
- All data comes from `api/` via `src/api/index.js`, proxied by `nginx/` in production and by the Vite proxy in dev.
- The **portal editor preview** (`views/PortalEditor.vue`) builds a MapLibre style by hand that mirrors what `api/.../services/portal_generator.py` produces for the *published* portal — keep the two layer/paint builders consistent (colors, source-layer names, opacity math).
- Tile URLs from the API are root-relative; the editor prefixes `location.origin` before handing them to MapLibre (worker can't resolve relative URLs).

## Current status & known issues
- Phases 0–1 features are present (setup, data manager, portal builder/editor, templates gallery, settings). deck.gl advanced layers and DuckDB filter UI are Phase 2 (not built).
- The preview-vs-published parity is a recurring footgun: a fix in `PortalEditor.vue` often needs a mirror fix in `portal_generator.py` (and `templates/.../layout.html`). See `notes_temp/notes_for_future.md`.
- `vite.config.js` dev proxy must stay aligned with `nginx/nginx.conf` (prefix stripping + titiler port 80).

## Last updated
2026-06-04
