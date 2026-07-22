# templates/

## Purpose
Portal templates — the visual skin applied when a portal is published. **The portal runtime
(all behaviour + base styling) is shared across every template**; a template only supplies theming,
a basemap, and metadata. This is what makes templates cheap to add and features cheap to update.

## Architecture (read this before touching templates)
- **`shared/`** — the runtime, edited ONCE, inherited by every template:
  - **Template EXPERIENCES / region-driven layout (V-11; redesign R1, 2026-07-22):** a portal has an
    **archetype** (now just `webmap` · `storymap` — the Phase-1 `webmap+catalog`/`catalog` were dropped as
    meaningless and **alias to webmap**) + **layout manifest** `{archetype, regions, panels}`. `regions` =
    `layerList {side:left|right, mode:docked|floating, collapsed, width, x, y}`, `controls {side:left|right}`
    (the whole map-control cluster), `header {style}`. Lives in `Portal.layout_config` (nullable JSON),
    resolved by `portal_generator.resolve_layout` (defaults ⊕ overrides), baked into `style.geodeploy.layout`.
    `portal.js::applyLayoutAttrs` sets `data-*` on `<body>` (`data-archetype`, `data-layerlist-side`,
    `data-layerlist` [docked/floating], `data-controls-side`, `data-header`, `data-collide` [1 when
    list+controls share a side]); the `map.on('load')` handler gates mounts by `panels.*`. **PARITY:
    `resolveLayout` mirrored in THREE places** — `portal_generator` (Python), `portal.js`, `PortalEditor.vue`
    — the archetype-defaults table + alias map must match. **Back-compat: no manifest ⇒ webmap ⇒ pre-V-11
    shell** (every element ID preserved; only classes/placement change). `template.json` may declare
    `"archetype"`/`"layout"` to preset on select (`official/story` → `storymap`).
  - **R1 runtime substrate (V-11 redesign, 2026-07-22):** the map-control cluster
    (basemap/globe/zoom/tools + NEW **HomeControl** [default extent], **ZoomAllControl** [fit all layers],
    **DrawZoomControl** [drag-box zoom, toggles back to pan]) is added at `CTRL_POS` derived from
    `controls.side`. An **on-map layer-list toggle** (`#gd-list-toggle`, `setupListToggle`) is pinned to the
    layer-list side ABOVE the panel — hides/shows a docked OR floating list, never covered by it. The
    **floating list** now collapses (`#sidebar.collapsed` → `display:none`) and is **movable + resizable**
    (`applyFloatingLayout` adds `.gd-float-move`/`.gd-float-resize`; box seeded from `layerList.width/x/y`).
    `setupLayerSearch` always builds a `.layer-actions-row` and **relocates Reset styling + About into it**
    (next to expand/collapse-all). Layer-card accent left-border removed from themes (default transparent =
    the minimal feel). `data-collide="1"` drops the floating list + on-map toggle below the control stack.
  - **R2 faithful iframe preview + click-to-place (V-11 redesign, 2026-07-22):** the editor's preview is now
    a same-origin `<iframe>` of the REAL portal (built by `POST /portals/{id}/preview` into the unlisted,
    logged-in-only `data/portals/_preview/{id}/`, served via nginx `location /portals/_preview/`). `portal.js`
    gained an **edit shim** (`setupEditMode`, active only under `?edit=1`): a same-origin postMessage channel
    that reports the live camera (`view`), runs click-to-place (`place` → left/right slot zones → `placed`),
    and applies `zoomall`/`home`/`fitbbox`/`setview`. So the preview can't drift from the published portal.
  - **R3 colour themes (V-11 redesign, 2026-07-22):** `Portal.theme` `{mode, accent, font}` →
    `portal_generator.build_theme_css` renders CSS-var overrides (`--accent`/`--accent-light` + `body`
    font) appended AFTER the template `theme.css` (so per-portal colours win). **Validated** (hex regex +
    known font key — bad values dropped, never emitted into the `<style>`). `resolve_theme` bakes `.mode`
    into `style.geodeploy.theme`; portal.js uses it as the default light/dark (visitor toggle still wins).
    Editor: a **Theme** section (mode · accent presets/custom · font). Themes layer OVER templates → one
    base template, many looks.
  - **R4 story pictures (V-11 redesign, 2026-07-22):** story sections gained an optional `image`
    (same-origin URL via `uploadPortalAsset`); `renderStoryHtml` emits `<img class="story-img">`
    (URL escaped). Editor: **+ Add image / Change / remove** per section.
  - **Story map runtime (V-11 Phase 2 MVP, 2026-07-21):** when the archetype is `storymap`, `setupStory()`
    fills `#story-panel` (an overlay narrative column, `layout.html`) from `style.geodeploy.story`
    (`{sections:[{title,body,view,layers}]}`). An `IntersectionObserver` (mid-viewport band) drives
    `map.flyTo` to each entering section's camera and applies its per-section layer visibility via
    `setLayerVisByRef` (matches `type:layer_id`, handles MapLibre + deck layers). Section text is
    title+body, XSS-escaped by `renderStoryHtml` (`s.html` reserved for the future rich editor, V-15).
    The editor authors sections (title/body + "Capture current map view") in `PortalEditor`'s Experience
    panel. Full rich-text/media + scroll polish = roadmap `V-15`.
  - **Layer catalog search (V-13, 2026-07-20):** `setupLayerSearch()` (run in `map.on('load')` after
    the switcher + groups are built) inserts a search box above `#layer-list` when there are ≥2 layers
    (or any folder); `filterLayers(q)` matches `.layer-card` `.layer-name` text, hides non-matches and any
    folder left with no visible card, force-expands folders holding a match, and shows a "No matching
    layers" note. Clearing restores the pre-search collapse state (captured on first keystroke). Purely a
    list filter (no map visibility change). When folders exist it also adds **Expand all / Collapse all**
    links (`setAllGroups`). `resetStyling` now re-applies the folder groups (was flattening them) and
    clears the filter. Styled via `.layer-search*` / `.layer-group-actions` in `portal.css`.
  - **Layer catalog drag & drop (V-13, 2026-07-21):** `enableLayerDrag` is now **tree-aware** and
    delegated on `#layer-list` (attach-once via `_treeDragWired`; `markDraggables` sets `draggable` on
    every `.layer-card` + `.layer-group > .layer-group-header`, re-run after group re-org / reset).
    `dropTarget` (via `elementFromPoint`) resolves before/after/into against the card or folder header
    under the cursor; `performDrop` moves the DOM node (a card, or a whole `.layer-group` when its header
    is grabbed) — into a folder body, or before/after a sibling. `applyLayerOrder` then re-reads
    `.layer-card`s in DOM order (recursive) and reapplies map z-order. Guards: can't drop a folder into
    itself/descendant (`dragEl.contains`). Indicators `.dnd-before/.dnd-after/.dnd-into` (in `portal.css`).
    Session-only (not persisted). Wired at the END of the load/reset sequence (needs deck rows + groups).
  - **Zoom to folder (V-13, 2026-07-21):** each folder header has a `.lg-zoom` button; `zoomToGroup(body)`
    unions the `data-bbox` of every descendant `.layer-card` (baked onto both MapLibre + deck cards at
    build) and `fitBounds` to it. Mirrored in the editor preview (`PortalEditor.zoomToGroup`).
  - **Layer catalog / folder groups (V-13, 2026-07-20):** when `STYLE.geodeploy.layerTree` is baked,
    `applyLayerGroups(tree)` (run in `map.on('load')` after `buildLayerSwitcher` + `appendDeckRows`)
    REORGANIZES the flat layer cards into a nested folder tree by `data-ref` (`type:id`, tagged on both
    MapLibre + deck cards) — moving each card keeps its handlers. Group behaviors: collapse/expand,
    toggle-all (clicks descendant `.layer-eye`s), exclusive/radio (showing one hides its siblings),
    per-group description. `portal.css` styles group headers/indentation. No tree → flat list.
  - **Anti-flash on load (2026-07-16):** a deck-only portal used to `fitBounds` the full extent then hard-snap (`duration:0`) to the manifest core extent once it loaded — a visible flash. When the server baked the core extent (`STYLE.geodeploy.coreFitted`, see `portal_generator.read_deck_core_bbox`), portal.js now **skips the refit** (the initial fit already opened on the core). Only unbaked/older bundles still refit, and it now **glides** (`duration:650`) and resolves on `moveend` before arming the `moveend`/first-fetch handlers, so it neither snaps nor double-fetches. **Basemap no-swap (2026-07-17):** publish repoints the builtin base layer to the chosen basemap so the portal OPENS on it; `setupBasemaps` used to then `selectBasemap(DEFAULT_BASEMAP)` on load, hiding that builtin and showing the catalog copy of the SAME basemap — a redundant repaint flash. It now skips that initial swap when `STYLE.geodeploy.baseRepointed` is set (only swaps for a vector template whose base couldn't be repointed, or the `'__default__'` no-op).
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
  - **Incremental viewport loading (`fetchDeck`, 2026-07-11):** each pan used to re-fetch the whole viewport — including the part already on screen — so panning stuttered and returning to a loaded area re-ran "Loading features…". Now `fetchDeck` fetches a **buffered** bbox (`padBbox`, `DECK_FETCH_PAD` = 0.35 each side) and records the covered region on `deckState[id].loaded = {bbox, band}`; a subsequent pan **skips the refetch** while the viewport is still `bboxContains`-ed by that region at the same integer-zoom band. The row limit is scaled by the buffer area (`DECK_PAD_AREA`, cap `DECK_FETCH_MAX` 150k) so on-screen density is preserved. The overview grid records a world-wide region (only a zoom-band change reloads it). The overview/detail decision (`fitsDetail`) and the mid-gesture RAF both evaluate the SAME padded bbox so detail only loads when the area-capped fetch is reasonably complete. Editor twin: `PortalEditor.vue` `refreshDeck` (`deckFetched`, same constants).
  - **Overview→detail switch (`fitsDetail`, 2026-07-11):** the density-overview-vs-per-feature-detail decision now gates on the **frac-weighted ROW estimate under the viewport only**, NOT the partition-file count. Detail is fetched from the server in ONE request (GeoArrow/GeoJSON — the duckdb-wasm serial-read path `WASM_DETAIL_READS` is off), so file count is irrelevant; gating on it locked **dense cells** (split into many partition files *because* they're dense — e.g. city centres) into the overview at EVERY zoom, so you could never see individual buildings in a capital however far you zoomed in. The file gate is kept only behind `WASM_DETAIL_READS`. Editor twin: `PortalEditor.vue` `deckViewportLoad` gate is likewise rows-only.
  - `portal.css` — all structural CSS, written against CSS variables (`--accent`, `--bg`, …). **Popup (2026-07-11): `.maplibregl-popup` gets `z-index:10`** so a clicked feature's attributes render ABOVE the deck.gl overlay canvas (interleaved:false draws over the map), and **`.gd-popup .maplibregl-popup-content` is themed `background/color`** — MapLibre's default white left dark-mode text unreadable on the un-striped rows (only even rows had a dark bg → the "white/navy" striping the user saw). **Basemap switcher (2026-07-11) is an enlarged popover**: `.gd-basemap-menu` 250px with a "Basemap" header, 68×46 `.gd-basemap-thumb` thumbnails, 13px labels, and a selected-row highlight via `.gd-basemap-opt:has(input:checked)` (accent border + check mark). **Dark-mode MapLibre controls**: `html[data-theme="dark"]` recolours `.maplibregl-ctrl-group` to the theme surface and light-inverts the built-in `.maplibregl-ctrl-icon` glyphs (nav zoom/compass/globe) — the custom basemap/tools buttons use `currentColor` so they're untouched. The dashboard editor mirrors this in `ui/src/style.css` (`.dark .maplibregl-ctrl…`).
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
- `official/minimal/` — clean white default (its `theme.css` only sets the body font — the stale
  `#title` selector no longer exists, so it's effectively the shared portal.css look). Complete.
- `official/satellite-dark/` — dark UI (dark `:root` overrides) over Esri satellite imagery; sky accent.
- `official/editorial/` — warm cream + terracotta, serif headings, on CARTO Voyager. Print/story feel.
- `official/humanitarian/` — OCHA-style cerulean header + red rule, high contrast, on OSM. Presets the
  `webmap+catalog` archetype (V-11).
- `official/story/` — warm serif narrative theme; presets the `storymap` archetype (V-11) — a
  scrollytelling portal whose sections are authored in the editor's Experience panel.
- `official/west-africa-fr/` — metadata-only stub (no `style.json` → not listed until completed;
  a French light theme is the intended finish).
- `community/` — user submissions + `CONTRIBUTING.md` (CI-validated format).

## Dependencies / relationships
- Bind-mounted read-only at `/templates` in the api + celery containers.
- `services/portal_generator.py` assembles `shared/portal.{css,js}` + the template's
  `layout.html`/`theme.css`/`style.json` + the live data into the published `index.html`.
- `routers/templates.py` lists a template if it has **`style.json`** (a basemap); `theme.css` and
  `layout.html` are optional (fall back to the shared skeleton). So a metadata-only stub without
  `style.json` is silently hidden — add a `style.json` to make it appear.
- **Parity:** `ui/src/views/PortalEditor.vue::buildPreviewStyle()` re-implements the same MapLibre
  style/raster-URL logic for the editor preview — keep it in sync with `shared/portal.js`.

## Current status & known issues
- `shared/portal.js` is large; it's the single source of truth for portal behaviour. Editing it
  reflects in every template on the next publish (no rebuild needed — `/templates` is a bind mount).
- Listed official templates: **minimal, satellite-dark, editorial, humanitarian** (each has a
  `style.json`). `west-africa-fr` stays a hidden stub until it gets one. The old `research` template
  was removed (2026-07-14).
- Basemap is now chosen independently in the editor/portal (shared basemap catalog), so a template's
  `style.json` basemap is only the DEFAULT; a template's real job is visual identity (`theme.css`).
- Adding template-level **colour personalization** later = exposing a few `--accent`/etc. overrides
  per portal (theming is already variable-based). Tracked as roadmap `V-10` (template gallery & branding).

## Last updated
2026-07-22 (V-11 REDESIGN R1–R4 all built: R1 runtime substrate [archetypes → webmap/storymap; new
controls; on-map list toggle; floating collapse/move/resize; actions-row Reset/About]; R2 faithful iframe
editor preview + click-to-place [+ `POST /portals/{id}/preview`, nginx `/portals/_preview/`, portal.js
edit shim]; R3 colour themes [`Portal.theme` → validated CSS-var overrides]; R4 story pictures. See
Architecture. Follow-ups: remove hidden editor map, persist floating box — notes_for_future.md)
2026-07-21 (V-11 Template Experiences: region-driven layout manifest + archetypes [webmap/webmap+catalog/
catalog/storymap], editor Experience panel, storymap MVP + `official/story` template — see Architecture)
2026-07-21 (V-13 catalog: tree-aware drag & drop — reorder · into-folder · drag whole folders — plus
zoom-to-folder and expand/collapse-all on the published switcher)
2026-07-20 (V-13 layer catalog: grouped folder switcher + layer-list search/filter; `resetStyling` now
re-applies groups)
2026-07-14 (removed the research template; added satellite-dark, editorial, humanitarian; fixed the
listing requirement note to `style.json`; basemap now chosen separately from the template)
