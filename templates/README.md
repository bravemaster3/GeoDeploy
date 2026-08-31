# templates/

## Purpose
Portal templates — the visual skin applied when a portal is published. **The portal runtime
(all behaviour + base styling) is shared across every template**; a template only supplies theming,
a basemap, and metadata. This is what makes templates cheap to add and features cheap to update.

## Architecture (read this before touching templates)
- **`shared/`** — the runtime, edited ONCE, inherited by every template:
  - **Template EXPERIENCES / region-driven layout (V-11; redesign R1, 2026-07-22):** a portal has an
    **archetype** (`webmap` · `storymap` · `catalog` — `webmap+catalog` is still unbuilt and
    **aliases to webmap**; `catalog` did too until V-14, which is why choosing it silently rendered a
    plain web map) + **layout manifest** `{archetype, regions, panels}`. `regions` =
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
    `controls.side`. An **on-map layer-list toggle** (`#gd-list-toggle`, `setupListToggle`) is added into
    the **CTRL_POS cluster** (same corner as the zoom/basemap/tools controls, first → TOP of the stack) so
    MapLibre keeps it pixel-aligned with its siblings (it used to sit alone in the list's corner and drift);
    pinned to a 29×29 box in CSS. Hides/shows a docked OR floating list. The
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
    base template, many looks. **`storyBg` (2026-07-25):** an optional `Portal.theme.storyBg` hex →
    `--story-bg`, the storymap narrative-column colour (editor "Story panel colour", storymap only;
    defaults to `--bg`). `applyThemeLive` applies it for the live preview.
  - **Download flyout + storymap layer list (2026-07-25):** the Tools "Download by area" flyout's
    **Draw a box** tab now starts drawing on the FIRST click (no separate "Draw on the map" button);
    **Coordinates** reveals the N/W/E/S cross (the confusing `#` glyphs replaced — a crosshair on the tab,
    a dashed extent box in the centre). The "Download this area" button no longer wraps: MapLibre's
    `.maplibregl-ctrl-group button { width:29px }` was clobbering it (higher specificity than
    `.gd-coords-go`), fixed with `.gd-tools-menu .gd-coords-go { width:100% }`. **Story maps now expose the
    layer list** — floating + collapsed by default (`storymap` archetype defaults changed to
    `layerCatalog:true`, `layerList.mode:floating`, `collapsed:true`), reachable from the toggle at the top
    of the controls; the floating list docks to the right (`right:52px`) so it clears the narrative column,
    and only a DOCKED list is hidden in a story map.
  - **R4 story pictures (V-11 redesign, 2026-07-22):** story sections gained an optional `image`
    (same-origin URL via `uploadPortalAsset`); `renderStoryHtml` emits `<img class="story-img">`
    (URL escaped). Editor: **+ Add image / Change / remove** per section.
  - **Previous / next extent (2026-07-30):** `NavHistoryControl` — the navigation history every
    desktop GIS has, added to the control cluster for EVERY archetype (it belongs to the map, not to
    one experience). `moveend` records the camera; `histSuppress` stops our own `easeTo` from being
    recorded as a new step, and `sameView()` tolerances stop sub-pixel drift and animation tails from
    filling the history with identical entries. A new move discards the forward branch (browser
    semantics); the stack is capped at 60. Buttons stay VISIBLE but disabled at the ends — a control
    that vanishes and returns makes the whole cluster jump.
  - **Catalog Folder facet (2026-07-30):** `portal_generator._folder_by_ref` maps each layer to the
    V-13 folder it sits in, baked into the catalog records. Root-level layers report no folder, so the
    facet narrows without ever becoming a required choice; nested folders report the INNERMOST name.
    Keyed by `(kind, layer_id)` so an external source and a layer sharing an id stay distinct.
    `regions.catalog.mapWidth` (author choice, clamped 30–60%) is applied as the `--cat-map-w` custom
    property at parse time. On phones the split turns VERTICAL — filters on top, results beneath, map
    below at 40vh — instead of the List/Map toggle used at tablet widths.
  - **Globe in the pinned start view (2026-07-30):** `initial_view` already carried
    center/zoom/bearing/**pitch**; the MapLibre v5 **projection** (globe vs mercator) was the one part
    of the camera that was lost, so a portal arranged on the 3D globe opened flat. `currentViewObj()`
    now reports `projection` and `applyProjection()` restores it — on load, from the Home control, when
    the editor pushes a view, and per **storymap section** (a section pinned on the globe returns to
    it). Doubly guarded: a cached MapLibre v4 bundle has no `get/setProjection`, and every portal saved
    before this has no `projection` key — both fall through to the map default (mercator), i.e. the
    previous behaviour. The editor spreads the reported camera WHOLE (`{ ...lastView }`), so the shape
    is owned by `portal.js::currentViewObj` alone. **Tilt joined it (2026-08-07):** `setupEditMode`
    takes a `tilt` message (`map.easeTo` between 0 and `TILT_PITCH`), the editor's "Start tilted"
    checkbox. Unlike `projection`, no explicit `post` is needed — `easeTo` fires `moveend`, which
    already reports the camera, and `syncTilt` keeps the on-map tilt button in step.
  - **Catalog runtime (V-14, 2026-07-30):** when the archetype is `catalog`, `setupCatalog()` fills the
    hidden `#catalog-panel` (a sibling of `#map-wrap` in `shared/layout.html`, mirroring how
    `#story-panel` stays `display:none` for other archetypes) with a search box, a **facet rail**
    (Type · Keywords · Licence, each value carrying a count computed with its OWN group excluded so the
    number says how many results picking it would give), **result cards** (badges, 3-line abstract
    clamp, keyword chips, Show-on-map / Zoom-to / Access-and-download) and a **pager**. The map keeps
    its own element and is never re-parented — `#layout` just becomes a two-column flex with
    `#map-wrap` at `flex: 0 0 40%`, and `map.resize()` runs once the panel is in place or the canvas
    keeps its full-width size. Hovering a card flashes its bbox via the `gd-cat-hl` source (chosen over
    per-card thumbnails, which would cost a tile request each). Records come from
    `style.geodeploy.catalog`, i.e. the SAME `layers_info` the About page renders. The facet rail is
    suppressed below 6 datasets; below 1024px the list and map become two views via `#cat-viewtoggle`;
    below 640px the rail is dropped entirely. `regions.catalog.scope = "public"` additionally fetches
    `/api/portals/{slug}/catalog` and appends instance-wide public datasets — those are marked
    `_offmap` so they show no "Show on map" button (there is no layer on this portal's map to toggle).
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
  - **V-16 DASHBOARD archetype (2026-08-24) — `dashboard.js` + `dashboard.css`, NOT more portal.js.**
    A fourth archetype: a single-screen grid of widgets over the portal's own layers, wired to
    CROSS-FILTER each other. The runtime is a SEPARATE shared file because it is a different kind of
    surface (a widget grid, a filter bus, a query client) that needs the map as one of its widgets —
    portal.js is 4.7k lines already, and this way each can be read and `node --check`ed alone.
    `layout.html` loads `{{DASHBOARD_JS}}` BEFORE `{{PORTAL_JS}}` (it defines `window.GD_DASHBOARD`;
    a script after portal.js would not exist when the load handler calls it) and `{{DASHBOARD_CSS}}`
    after `{{PORTAL_CSS}}`, before `{{THEME_CSS}}` — so a template theme still wins. Both are
    substituted for EVERY archetype (a template shipping its own layout.html would otherwise render
    the literal placeholder) and are inert unless `style.geodeploy.dashboard` is present.
    * **The MAP IS A WIDGET, and is never re-parented.** `#layout` becomes a CSS grid,
      `#dashboard-panel` is `display: contents` so the widget cards become direct grid items, and
      `#map-wrap` — a sibling — takes the map widget's cell by `grid-column`/`grid-row`. Same rule
      as the catalog: a moved MapLibre container loses its measured size. BOTH axes are written
      explicitly by `placeAll`, because `#map-wrap` is always the LAST grid item in document order
      and auto row placement would push the map to the bottom of every dashboard.
    * **Responsive by rewriting placement, not by media query:** 12 cols ≥1100px, 6 cols ≥720px
      (spans halved, columns re-flowed), 1 col below. Mapping 12 onto 6 is arithmetic.
    * **The filter bus** is one state per dashboard with three channels — `attr` (a field/value
      predicate, scoped to targets on the SAME layer), `geom` (a geometry, applied to every target
      regardless of layer — that is what lets a drawn polygon drive raster statistics) and `select`
      (one feature's attributes, for the details panel). Combined with AND. A source publishes only
      to the widgets its `actions.filters` names. Clicked/polygon/bbox selections all normalise to
      ONE geometry (a bbox IS a rectangular polygon). Clicked geometry comes from
      `POST /api/data/vector/{id}/pick`, never from `queryRenderedFeatures` (tile-clipped geometry
      would give zonal statistics for the visible fragment of a parcel). One in-flight request per
      widget, aborted on the next.
    * **The active-filter bar is fixed to the window, not a grid row** — a row for it would be
      `grid-auto-rows` tall and would push every widget down the moment a filter went live.
    * Charts are inline SVG against portal.css's variables — no charting library, so a template
      `theme.css` and a per-portal accent restyle a dashboard like every other archetype.
    * Registering a new widget type = a `RENDERERS` entry here + `services/dashboard.WIDGET_TYPES`
      + a `DashboardBuilder.vue` case. Nothing else branches on type. Validation lives ONLY in
      `resolve_dashboard` (publish-time); this file assumes its invariants.
    * Full design note: `notes_temp/DASHBOARD_ARCHETYPE.md`.
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
  `webmap+catalog` archetype (V-11 — still aliased to webmap).
- `official/story/` — warm serif narrative theme; presets the `storymap` archetype (V-11) — a
  scrollytelling portal whose sections are authored in the editor's Experience panel.
- `official/dashboard-monitoring|dashboard-regional|dashboard-assets|dashboard-zonal/` — the four
  V-16 dashboard starters (operational board · regional snapshot · asset tracker · zonal analysis).
  Each declares `"archetype": "dashboard"` and ships a **`dashboard` preset** in `template.json`:
  a starting widget set + cross-filter wiring with **no layer ids** (they cannot exist when the
  template is written). `DashboardBuilder.applyPreset` binds them to the portal's own layers on
  first load — first suitable layer, first suitable field, successive rasters for successive raster
  widgets — and every part of that guess stays editable. **A preset seeds an EMPTY grid only** and
  never overwrites work; the overwrite path is a button the author presses.
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
2026-08-31 (`shared/portal.css`: the map's bottom chrome — scale bar, **bottom-centre** coordinate
readout, attribution — sits on ONE line, on every archetype. `.maplibregl-ctrl-bottom-left` had
carried `bottom: 24px` since the first shared-runtime refactor while `-bottom-right` kept MapLibre's
0, so the scale bar floated above the credit everywhere. All three now hang off `--map-chrome-b`
(the readout adds MapLibre's own 10px control margin, having none of its own), so the phone gutter
and the storymap's lift over its narrative strip move the whole line instead of one corner each.)

2026-08-31 (**`grid.fit: "screen"` fills the viewport.** `placeAll` counts the rows the layout
actually reaches (per breakpoint — the phone cursor and the desktop max are different numbers) and
`applyRowTemplate` writes `grid-template-rows: repeat(N, minmax(var(--dash-row), 1fr))`. The author's
row height becomes a FLOOR rather than the height, so the board stretches where there is room and
scrolls where there is not, with no media query having to guess the boundary; uniform rows mean a
widget spanning four still gets four times one, so proportions survive. Default is `"rows"`, the
existing behaviour, so no published board changes. `body[data-dash-fit]` publishes the mode.)

2026-08-31 (**every plot shares its card with its key the same way.** `.gd-chart` is `height: 100%`
and the key is its sibling, so the plot claimed the whole body and pushed the key into the
scrollbar — and making the widget taller grew the plot by exactly as much. `plotHeight` /
`capToRoom` / `fixPlotHeight` in `shared/dashboard.js` now serve the multi-series line, the grouped
bars AND the pie, which all had the bug for the same reason. AUTO (`plotSize` unset or 100) measures
the key and gives the plot the rest; a share below 100 hands the plot a fixed fraction and lets a
long key scroll in the remainder. The key is capped either way, so a key taller than the whole card
scrolls itself instead of pushing the plot out. `chartBox()` also reports the CONTENT box (it was
returning `clientHeight`, 20px of padding included), because subtracting a key's height from a
figure that is already too big still overflows.)

2026-08-31 (**the camera follows the whole selection.** `shared/dashboard.js`'s table/card widget
holds `picked` as a Map of key -> bbox rather than a set of keys, and fits the map to the UNION on
every selection change — ctrl-clicking a second row widens the view to hold both, removing one
narrows it back. The bbox is remembered at pick time because a row scrolls off as soon as the
visitor turns the page, so a set spanning two pages would otherwise fit only the last.)

2026-08-31 (**a dashboard is photographed whole.** `shared/portal.js::snapshotDashboard()` renders
the live page into a `<foreignObject>` and rasterises it, so a dashboard's card thumbnail shows the
charts and tables rather than only the map cell — the map alone made two dashboards over the same
layer indistinguishable. The SVG is loaded in the restricted mode images use and fetches NOTHING, so
`collectCss()` inlines every readable rule (rewriting each selector's leading `html` / `body` /
`:root` onto a wrapper that stands in for both, or every `body[data-archetype="dashboard"]` rule
would miss), the WebGL canvas is swapped for a WebP still, and same-origin `<img>` are inlined. Any
failure falls back to the map-only shot. Also: `shared/dashboard.js` scatter dots default to
r=3.5 and honour `style.pointSize` — 2px read as dust on a small card.)

2026-08-24 (**dashboard first-use round**, `shared/dashboard.{js,css}` + the four presets.
`drawLine` now takes `selected` + `onPick`, so line and area charts are filter sources like bars and
pies — with a transparent 9px hit circle, because a 2.6px dot on a 30-point series is unaimable. The
map click resolves its tolerance from `tolPx` by unprojecting two pixels at the click (zoom- and
latitude-correct) and falls through EVERY vector layer in the style, top-down, first hit wins, capped
at 8 — the map draws all the portal's layers, so a click that does nothing over a visible feature
read as "my second layer is not in the dashboard". New `extent` tool: `moveend` republishes the
viewport on the geom channel with `soft: true`, which an explicit selection pins over. `placeAll`
scales grid EDGES rather than origin-and-width separately (independent rounding made four 3-wide
widgets OVERLAP at the 6-column breakpoint), scales row height per breakpoint, caps widget height on
a phone, and re-places from a ResizeObserver on `#layout` rather than only `window.resize`. The
Asset-tracker preset gained the chart it never had; every preset now offers the `extent` button.)

2026-08-24 (**V-16 dashboard archetype** — a fourth archetype and the first with its own runtime
file. `shared/dashboard.js` + `shared/dashboard.css` hold the widget grid, the cross-filter bus and
the query client; portal.js hands them the map on load. The MAP IS A WIDGET: `#layout` becomes a CSS
grid, `#dashboard-panel` is `display:contents`, and `#map-wrap` takes the map widget's cell by
grid-area — never re-parented, the same rule the catalog follows. Placement is written explicitly in
BOTH axes because `#map-wrap` is always the last grid item in document order, so auto row placement
would sink the map to the bottom of every dashboard. Four starter templates ship dashboard PRESETS
whose data bindings are empty by construction and are bound to the portal's layers by the builder.)
2026-08-07g (**the catalog's "On map" list shows real symbology, and the phone layout stops starving
the results.** The list is a catalog portal's ONLY legend (the layer switcher is a separate panel),
and each row carried a 7px dot coloured by KIND — so three point layers on screen were three
identical blue dots. `activeSwatch(ref, rec)` now resolves the row through the same `legendSwatch`
the layer list uses, handling all three cases: MapLibre layers (colour/geometry/dash/marker from the
style), **deck/GeoParquet layers checked FIRST** (they have no style layer to find, only a
`DECK_LAYERS` entry), and rasters. **Rasters deliberately do NOT use `legendSwatch`** — its raster
branch is `geomIcon('raster')`, a grid, which at 18px in a 200px panel was the largest thing in the
row and identical for every raster. They get a colour-RAMP chip from `LEGEND_GRADIENTS` instead,
which is what actually tells two rasters apart; hillshade reads grey, which it is. CSS: `.cat-active-sw`
(fixed 18px box so names stay aligned) + `.cat-active-ramp`, replacing `.cat-active-dot`.
**Phone catalog (portal.css ≤640px):** filters now sit BESIDE the results (rail 42%) instead of a
22vh strip above them — the old stack split the one scarce axis three ways and the result list, the
point of the page, got the smallest share. Tablet/desktop rules untouched.)
2026-08-07f (**the raster popover now OPENS showing what is on the map.** New `effectiveHillshade` /
`effectiveZfactor` / `effectiveColormap` / `effectiveRescale` beside `effectiveBidx`: viewer session
state first, else the params baked into the tile URL. Only `bidx` did this, so a portal published
with hillshade opened the popover UNCHECKED with Z 1, contradicting the map behind it. Second, worse
fault from the same gap: `applyRaster` rebuilt the URL from `rasterState` ALONE, so touching one
control discarded every baked param the viewer had not touched — pick a palette, lose the author's
stretch. `applyRaster` and `rasterLegendHtml` now read through the same helpers, so popover, map and
legend cannot disagree. Stretch inputs render disabled under hillshade (mirrors `LayerPanel`).
`undefined` means "untouched" and is deliberately distinct from a viewer's empty value, which must
NOT resurrect the baked one.)
2026-08-07e (**`applyRaster` no longer stretches a hillshade.** TiTiler applies `rescale` AFTER the
algorithm and a hillshade is already 0–255, so a data-range stretch flattens it to one colour. This
path was only accidentally right — it rebuilds the URL from `baseFull.split('&')[0]`, dropping the
layer's baked rescale, which is why ticking Hillshade in the published legend worked while the editor
sidebar produced a blank layer. A viewer who pressed **Auto** and then ticked Hillshade hit the bug
here too. Mirrors `services/titiler.py::get_tile_url`.)
2026-08-07d (**"Start tilted" is now an authoring choice, not a right-drag.** `setupEditMode` gained a
`tilt` message that eases the preview between 0 and `TILT_PITCH` (60°); the editor checkbox mirrors
the on-map `TiltControl`, and the resulting pitch is pinned with the rest of the camera. Published
portals needed no change — `initial_view.pitch` was already honoured on load.)
2026-08-07c (**Download-by-area hit-tested the SCREEN, so it found nothing on the globe.**
`openDownloadDialog` decided which layers to offer with `queryRenderedFeatures(pixBox, …)` —
`pixBox` being an axis-aligned SCREEN rectangle between the two projected drag corners. On a globe
the region between those corners is a curved quadrilateral, so the query looked somewhere else and
reported "No layers intersect the selected area" over an area full of features. It was also quietly
wrong in 2D: it asked what is RENDERED, so a layer whose tiles had not arrived was left out of the
download. Now one `bboxOverlaps()` over the baked `geodeploy:bbox`, shared by vector, raster and
GeoParquet (they each had their own copy of the comparison, and the vector one was the odd one out).
Coarser — an overlapping extent with no features inside exports nothing — but that is how rasters
already behaved, and the SERVER does the real clip. Offering an empty download beats hiding a real
one. `queryRenderedFeatures` survives only as the fallback for a layer with no baked bbox.)
2026-08-07b (`.layer-actions-row` reads as a TOOLBAR, not floating buttons. With no folders the
`.la-left` group is empty, so `justify-content: space-between` pushed Reset-styling and About to the
far right with nothing anchoring them — they hovered over the list rather than belonging to it. A
bottom rule ties them to the list they act on; `min-height` keeps that rule in the same place whether
or not the folder expand/collapse buttons are present.)
2026-08-06d (**first-paint loader · tilt · brighter space · the list scrolls**. `#gd-loading` lives in
`shared/layout.html`, not in portal.js — its job is to cover the window from the FIRST paint, and an
element created once the runtime has parsed appears after the thing it hides. portal.js's `loading`
clears it on READINESS: gates registered synchronously up front (`map`, `render`, plus `catalog` /
`story` per archetype) and cleared by the piece that owns each — `map` at the end of the load
handler, `render` on the map's first `idle`. Two rules make it safe: register every gate before any
can clear, and clear OUTSIDE the try/catch, so one broken panel cannot make a portal that never
appears. The 15 s timeout is a backstop for a piece that never calls back, not the mechanism. Deck
(GeoParquet) data is deliberately NOT gated — it streams and has its own indicator.
**Tilt:** `NavigationControl` gains `visualizePitch` (drag the compass to pitch) and a `TiltControl`
button toggles 0 ↔ 60°; `maxPitch` raised 60 → 75. Nothing on the page previously advertised
right-drag, so a 3D portal looked flat and unfixable. The button reflects `pitchend`, so it can never
contradict the map. **Space** is brighter: 8 star layers with halos on the bright ones, a diagonal
Milky Way, three nebulae, and a 4-minute drift (dropped under `prefers-reduced-motion`); the sky's
horizon limb went `#7fb2ff` → `#a8d4ff`.
**Catalog "On map" legend (`#cat-active`)** — the box listing what a visitor has switched on — now
COLLAPSES and opens closed, with the count still on the header, and its rows scroll rather than the
whole panel. It sits on the map, and on a catalog the map is already the smaller half of the page,
so a list that grew with every dataset ate the view it described. State is per-visit and re-applied
on each render (the panel is rebuilt from `onMap` on every change, so it cannot live on the DOM).
**Layer list:** `#layer-list` is now the scroll container (needs `min-height:0` down the flex chain)
so the search box and action row stay put while a long list scrolls. Separately, the catalog
archetype no longer hides `#sidebar` in CSS — portal.js hides it when `panels.layerCatalog` is off,
so a catalog author who turns it ON now gets a list (floating, on the map's side, collapsed) instead
of one built and then hidden by a rule that could not see the choice.
**Pointer cursor over features:** the hover handler covered MapLibre layers only, so GeoParquet
layers — which emit no MapLibre layer for `queryRenderedFeatures` to find — showed the pan cursor
over every feature. Deck DETAIL layers are now `pickable` and hit-tested with `deckOverlay.pickObject`
(one pick per animation frame; a pick is a render pass and mousemove far outruns the screen). The
density OVERVIEW stays unpickable — a grid cell is not a feature. `setCursor` is the single writer
and defers to the draw-box / area-select modes, which own the cursor while active. The MapLibre
query now also drops ids the live style lacks: `queryRenderedFeatures` rejects the WHOLE call on one
unknown id rather than skipping that layer.)
2026-08-06c (`markerImage` takes an OUTLINE (colour + width) — the white stroke was hard-coded. Width
is a RATIO of the radius so it stays proportional when a layer is resized (0.28 = the old value, so
an unstyled marker is pixel-identical); a thick one hides the fill, which is how a RING is drawn.
`null` means draw none, `undefined` means unspecified → the old white. The marker image ID carries
both, because they change the pixels — otherwise two differently-ringed markers collide on one image;
`parseMarkerImageId` treats the pair as OPTIONAL so ids baked into portals published before this
still parse and draw as they did. Also: `GeoArrowPolygonLayer` extrudes — that is the transport an
UNTILED GeoParquet layer uses, so extruding only in the GeoJSON branch meant 3D did nothing for
exactly the layers it was added for. A GeoArrow accessor is an Arrow COLUMN, so getElevation takes
the vector and the × multiplier rides on `elevationScale`.)
2026-08-06b (**`map.on('load')` is a guarded sequence — keep it that way**. Every step in it is
wrapped in its own try/catch with a console.warn, because anything escaping aborts the REST of the
handler — which is where `setupBasemaps()` adds the control cluster and the interaction wiring
happens. A map in that state loads, paints and does not respond. `ensurePointImages()` was its first
line and the only unguarded one; it became far riskier when a classified layer started registering
one icon PER CLASS, and `markerImage()` sat outside the try inside `setMarkerImage`. Now guarded at
both levels, as is the `applySpace()` call in `applyProjection` (also reached from that handler).
Also: deck-rendered POLYGON layers extrude via GeoJsonLayer `extruded`/`getElevation` — GeoParquet
layers emit no MapLibre layer, so `fill-extrusion` never reaches them; outline is disabled when
extruded, and a non-numeric height becomes 0 rather than NaN, which would drop the whole mesh.)
2026-08-06 (**data-driven symbology + 3D + space**: `portal.js::vectorLegendHtml` renders the
`geodeploy:legend` baked into the style — a RENDERER only, it never rebuilds labels from
classes/categories, so the published legend cannot drift from the published map. `applySpace()`
gives globe mode a MapLibre sky (atmospheric limb, faded out by zoom) plus a CSS starfield on the
container: the sky draws the atmosphere and everything beyond it is TRANSPARENT, which is why the
globe used to sit in a flat dark panel; stars are not in the MapLibre style spec so CSS is the only
source. A portal containing a `fill-extrusion` layer opens at 45° pitch — straight down, an extruded
polygon and a plain fill are the same shape — but only when the author never pinned a pitch, since
an explicit 0 chosen while looking at a 3D layer is a decision.)
2026-07-30 (V-11 polish, 2nd pass: story-map wheel now ZOOMS the map over the open map and only scrolls
the narrative over the story column [`setupStory` hit-tests the pointer against the panel's opaque band in
a capture-phase wheel listener; `scrollZoom` re-enabled]; the up/down "more" chevrons are bigger [42px,
filled accent] and centred on the visible card column)
2026-07-25 (V-11 polish: download flyout — Draw-a-box acts on first click, `#` glyphs replaced, download
button no longer wraps [MapLibre `button{width:29px}` specificity trap]; on-map list toggle MOVED into the
CTRL_POS cluster so it aligns with the other controls; story maps now expose a floating/collapsed layer
list + toggle; `theme.storyBg`/`--story-bg` narrative-column colour with an editor picker)
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
2026-08-17 (**contours in the published portal**, and two parity bugs fixed with it. `portal.js`'s
raster popover swaps its Hillshade checkbox for a Terrain choice — TiTiler takes ONE algorithm, so
hillshade and contours are mutually exclusive — with Interval/Line-width inputs, and the legend
draws contours as TiTiler's own **terrain** ramp plus an "every N" line, because the algorithm
ignores the layer's colormap entirely. *Parity:* `applyRaster` rebuilt the tile URL from scratch and
dropped a baked `colormap=` JSON mapping, so touching any control on a CLASSIFIED raster silently
fell back to grayscale mid-session; it is now preserved. Under contours the stretch stays ENABLED —
it is the range the relief behind the lines is coloured over — where hillshade disables it.)
