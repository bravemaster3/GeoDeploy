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
    **band selection** (multiband → RGB composite or single band), palette/hillshade/Z/stretch +
    legend bar — the viewer's tweaks preserve the admin's baked `bidx`; **external sources**
    (WMS/XYZ/WFS, flagged `geodeploy:external`) get an opacity-only popover (+colour for WFS) and
    skip the raster stretch path; geojson `data` URLs are absolutified like tile URLs; **GeoParquet** layers render PRIMARILY via a **deck.gl `MapboxOverlay`** (deck.gl loaded via CDN in layout.html), whose data comes from **DuckDB-WASM running in the browser** when the layer descriptor carries `parquet.manifest` (duckdb-wasm 1.29.0 lazy-imported from jsDelivr only then; partitions under the viewport are computed from the manifest grid — same math as the server — registered via `registerFileURL(directIO=true)` → HTTP Range requests through the public `/parquet/{path}` proxy; plain `read_parquet` + covering bbox WHERE, NO spatial extension; WKB→GeoJSON decoded in JS, geometry column only), **falling back to the public `/features.geojson` viewport query** (no manifest / non-4326 CRS / no WebAssembly / any wasm error — sticky per session); zoom-scaled limit on both paths (z<7→10k, z<10→25k, else 50k); descriptors in `STYLE.geodeploy.deckLayers`, refetched on `moveend` with stale responses dropped, basic switcher row = show/hide + zoom, no symbology popover yet; a layer explicitly tiled to PMTiles falls back to a `pmtiles://` vector source — portal.js registers the pmtiles protocol (lib via CDN) and rewrites `pmtiles:///api/...` → `pmtiles://<origin>/api/...`) · zoom; popup + attribute table — **incl. GeoParquet identify (2026-07-11): the click popup also queries the public `/data/vector/{id}/identify` endpoint for visible deck layers showing detail (not the density overview), since the deck transports ship geometry only; honours `POPUP_CONFIG` fields, shows the first feature + a “+N more” note**,
    **raster pixel identify**, basemap switcher, coordinate readout, reset styling, **Tools control:
    select-area-and-download** (`POST /api/portals/{slug}/export-bundle`) — **the download dialog also lists GeoParquet (deck) layers (2026-07-11)**, bbox hit-tested like rasters and exported as `layer_type:'vector'` (the server resolves them to a DuckDB clip)). It reads its data from a
    `window.GEODEPLOY` object (`title`, `slug`, `style`, `popupConfig`, `accessType`, `passwordSha256`) and
    sets the **initial view** from `style.geodeploy.view` (admin-pinned center/zoom → `jumpTo`) when
    present, else `fitBounds` to `style.geodeploy.bounds`; and
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
2026-07-11 (portal.js: GeoParquet identify-on-click popups; download dialog lists GeoParquet layers)
