# templates/

## Purpose
Portal templates — the visual skin applied when a portal is published. **The portal runtime
(all behaviour + base styling) is shared across every template**; a template only supplies theming,
a basemap, and metadata. This is what makes templates cheap to add and features cheap to update.

## Architecture (read this before touching templates)
- **`shared/`** — the runtime, edited ONCE, inherited by every template:
  - `portal.js` — all portal behaviour (access gate, map init, **thin layer list**: drag-to-reorder ·
    eye/eye-off visibility · symbol swatch that opens a **symbology popover** (opacity, colour, line
    type, size; **point marker shape** circle/square/triangle/diamond/star/cross; raster:
    palette/hillshade/Z/stretch + legend bar) · zoom; popup + attribute table,
    **raster pixel identify**, basemap switcher, coordinate readout, reset styling, **Tools control:
    select-area-and-download** (`POST /api/portals/{slug}/export-bundle`)). It reads its data from a
    `window.GEODEPLOY` object (`title`, `slug`, `style`, `popupConfig`, `accessType`, `passwordSha256`) and
    operates on a fixed set of element IDs (`#map`, `#sidebar`, `#layer-list`, `#attr-panel`,
    `#coords`, `#access-gate`, …). **Add/҂fix a portal feature here and every template gets it.**
  - `portal.css` — all structural CSS, written against CSS variables (`--accent`, `--bg`, …).
  - `layout.html` — the default thin skeleton (the body structure with the required element IDs +
    placeholders). Templates that don't ship their own `layout.html` fall back to this.
- **A template** (`official/<name>/`) just needs:
  - `template.json` — metadata (name, author, description, tags, language, basemap, version, license).
  - `theme.css` — CSS-variable overrides (colours, fonts) + small touches. This is the whole "look".
  - `style.json` — the MapLibre basemap (raster or vector, basemap layers only — no data layers).
  - `preview.png` — 800×500 (optional; only used as the gallery thumbnail).
  - `layout.html` — OPTIONAL. Only add one to change the HTML structure (e.g. logo, sidebar side,
    tabs). Otherwise the shared skeleton is used.

### Placeholders substituted at publish time (`services/portal_generator.py`)
`{{PORTAL_CSS}}`, `{{PORTAL_JS}}` (shared runtime), `{{THEME_CSS}}` (the template theme, injected
AFTER portal.css so it overrides), `{{STYLE_JSON}}`, `{{POPUP_CONFIG}}`, `{{ACCESS_TYPE}}`,
`{{PASSWORD_SHA256}}`, `{{TITLE}}`. Output is a single self-contained `index.html` per portal.

## Contents
- `shared/` — `portal.js`, `portal.css`, `layout.html` (see above).
- `official/minimal/` — light blue theme, CARTO Positron basemap. Complete.
- `official/research/` — teal academic theme, CARTO Voyager basemap. Complete (theme.css + style.json
  + template.json; reuses the shared skeleton via its own copy of `layout.html`).
- `official/west-africa-fr/`, `official/humanitarian/` — metadata-only stubs (not listed until they
  get a theme + are completed).
- `community/` — user submissions + `CONTRIBUTING.md` (CI-validated format).

## Dependencies / relationships
- Bind-mounted read-only at `/templates` in the api + celery containers.
- `services/portal_generator.py` assembles `shared/portal.{css,js}` + the template's
  `layout.html`/`theme.css`/`style.json` + the live data into the published `index.html`.
- `routers/templates.py` lists a template if it has `template.json` + `layout.html` (a template
  without its own `layout.html` still publishes fine via the shared skeleton, but add one — or relax
  the router — if you want it listed).
- **Parity:** `ui/src/views/PortalEditor.vue::buildPreviewStyle()` re-implements the same MapLibre
  style/raster-URL logic for the editor preview — keep it in sync with `shared/portal.js`.

## Current status & known issues
- `shared/portal.js` is large; it's the single source of truth for portal behaviour. Editing it
  reflects in every template on the next publish (no rebuild needed — `/templates` is a bind mount).
- The non-minimal/research official templates are intentionally hidden until completed.
- Adding template-level **colour personalization** later = exposing a few `--accent`/etc. overrides
  per portal (the theming is already variable-based, so this is now straightforward).

## Last updated
2026-06-02
