# ui/src/components/

## Purpose
Reusable presentational/interactive widgets used by the views, grouped by feature area.

## Contents
- `data/VectorRow.vue` — one row in the Data Manager vector list (name, status badge, metadata, delete). Shows a violet **GeoParquet** tag when `storage_backend === 'geoparquet'` (file-backed, not PostGIS). A **"tiling…/tiling failed"** badge shows ONLY when `tile_status` is `'tiling'` or `'error'` — `'none'`/null means "displayed via deck.gl, not PMTiles" (the normal case) and must NOT read as in-progress (was a bug: any truthy non-`ready` tile_status rendered "tiling…" forever). A **Restart** button (refresh icon, hover-reveal while `processing`, always-shown amber on `error`) appears for GeoParquet layers and calls `dataStore.reprocessVector(id)` — re-runs the stalled convert/prep with no re-upload. A **Tile** button (grid icon, hover-reveal; shown for `ready` GeoParquet layers) calls `dataStore.tileVector(id)` → `POST /{id}/tile` to (re)generate the PMTiles archive for fast seamless display of heavy layers; the store flips `tile_status` to `'tiling'` and polls `refresh()` until it settles (tiling has no JobStatus). Re-runnable so the admin can re-tile after a workflow improvement. A sky **Tiled** tag shows when `tile_status === 'ready'` so tiled layers (rendered via static PMTiles vector tiles, not the deck.gl/DuckDB path) are distinguishable from untiled ones.
- `data/RasterRow.vue` — raster equivalent.
- `data/ShareLinksModal.vue` (2026-07-29) — the **Share links** panel: opened by the link icon on any
  *ready* Vector/Raster row (**every role**, not just editors — consuming data elsewhere is not
  editing). Renders `GET /data/{vector,raster}/{id}/links` — a server-built list of tool-labelled
  URLs with copy + open buttons, "Recommended" on the primary one (**OGC API - Features**), and a
  per-link hint naming the exact menu path in QGIS/GeoLibre/GDAL. The UI holds NO knowledge of which
  artifact suits which backend — that lives in `services/share_links.py`. When the response says
  `public: false` it shows an amber notice that the URLs 404 until the layer is shared Public
  (`SharingModal` is the fix, one row over).
  **PARITY:** a published portal's About page shows the same list, rendered as static HTML by
  `services/portal_generator.py::_share_block`. Both read `services/share_links.py`, so only the
  PRESENTATION is duplicated — keep the row anatomy (label · Recommended · format · tools · URL ·
  copy · hint) and the ordering in step when you change either.
- `data/UploadModal.vue` — drag/drop upload dialog; uses `useUpload` for progress + optimistic insert + background polling. `type` prop = `vector | raster`. **CSV** (vector): on selecting a `.csv` it parses the header client-side (with the chosen **delimiter** — comma/semicolon/tab/pipe) and shows a **Geometry mode picker (2026-07-11): X/Y point columns OR a WKT geometry column** (any geometry type — a column named wkt/geometry/geom/the_geom auto-selects WKT mode) + EPSG, then posts to `/data/vector/upload-csv` (background job) instead of the normal ingest. **GeoParquet** (vector, `.parquet`/`.geoparquet`): uploads **direct to storage** via `useUpload.uploadGeoParquet` (presign → PUT to `/s3/` → complete) — never through the API; 10 GB client-side cap. **Large files (≥ 2 GB, any vector format — CSV/GeoJSON/GPKG/zip, 2026-07-11):** `handleFile`/`importCsv` route them to `useUpload.uploadLargeVector` (presign → PUT → `/large/complete`) so they upload direct-to-storage and convert to GeoParquet in the background instead of hitting the API's 2 GB 413 (`LARGE_UPLOAD_THRESHOLD`). The CSV header is still parsed from the first 64 KB, so the X/Y/WKT pickers work at any file size.
- `data/AddSourceModal.vue` — connect an **external source** (XYZ/WMTS · WMS · WFS): type picker, URL, layer name (WMS/WFS), attribution; POSTs to `/data/sources` (WFS validated server-side) and inserts via `dataStore.addExternal`.
- `data/SourceRow.vue` — one external-source row (type badge, kind/geometry/layer, URL, delete).
- `data/DiscoverModal.vue` — **import existing data**: two tabs (PostGIS tables / storage files) from `/data/discover/*`, checkbox-select with an editable per-row **name** (default = table/file name). Storage lists GeoTIFFs (raster) + **GeoParquet files (violet chip, 2026-07-11 — imported as file-backed layers via a background inspect+prep job whose `jobs` the modal polls)** + CSVs; selecting a CSV fetches its header (with a chosen **delimiter**) and shows a **geometry-mode picker (X/Y points or a WKT column, 2026-07-11)** + EPSG (CSV loads into PostGIS, the rest register catalog rows with no copy). Refreshes the store after import.
- `infra/InfrastructurePanel.vue` (2026-07-30) — the consolidated Settings → Infrastructure view,
  Coolify-shaped: a SERVICE RAIL on the left, and everything about the selected service as tabs on
  the right (**Logs** with line count / timestamps / stream, **Terminal** = the gated one-command
  runner, **Deployments** = update history), with Restart/Stop/Start — plus martin's *Reload config*
  — in the top-right action row. It replaces three separate cards (health list, logs card,
  danger-zone terminal) that each made you re-pick the service you were already looking at.
  `TERMINAL_ALLOWED` here mirrors `admin.py`; the SERVER enforces it, so drift is cosmetic.
  Streaming polls only while the box is ticked AND the Logs tab is open — an unattended tail would
  otherwise hit the Docker socket forever.
  **Fixed heights (2026-08-06):** Deployments scrolls inside `h-96` like Logs, and Environment
  scrolls only its VARIABLE LIST — the notice above and the Save/Apply row below stay put, because
  scrolling the whole tab would push the buttons (the reason you are on that tab) out of reach.
  These lists only grow; an unbounded panel pushed the rest of the Settings page off the screen.
- `infra/ConnectionDetails.vue` — owner-only "here are your PostGIS and MinIO credentials" card.
  Fetches nothing until **Show** is pressed (the request is audited, so an automatic load would fill
  the log with views nobody performed); secrets are masked with a reveal + copy per field. The
  precedence behind the values is `routers/admin.merge_credentials` (`.env` first). The response
  carries a `source` per group for diagnostics; it is deliberately **not rendered** — the panel
  answers "what are my credentials", and where they were read from is our plumbing, not the
  operator's question (user call, 2026-08-06).
- `portal/CreatePortalModal.vue` — new-portal dialog (title, description, access); creates via the portals store then routes to the editor.
- `portal/LayerPanel.vue` (resolves vector/raster/**external** layers from the data store; external sources get an opacity-only popover, plus a colour picker for WFS vector) — **thin row** mirroring the published portal: drag handle (reorder is wired in `PortalEditor.vue`) · eye/eye-off (`update {visible}`) · **symbol swatch** that opens a **teleported symbology popover** · name · zoom · remove. The popover holds: opacity; vector colour/fill/outline/width; **line type** (solid/dashed/dotted); **point marker shape** (circle/square/triangle/diamond/star/cross) + size; popup-field picker; **raster band selection** (multiband → RGB composite with R/G/B band pickers, or single band) + palette/hillshade/Z (single-band output) and stretch/rescale (all); save/use default. Band selection stores `style.bidx` (`[n]` single, `[r,g,b]` RGB). The list swatch (`geomSvg`/`markerSvg`) draws the actual symbol (colour, dash, marker shape). Emits `update`/`remove`/`zoom`.
  **Two hosts, one control** (issue #23): `standalone` renders the symbology body ALONE — no row, no
  popover chrome, no default-style actions — via `<Teleport :disabled>`, which is what lets
  `data/StyleModal.vue` reuse it in My Data instead of a second styling UI growing there. The panel
  stays host-agnostic: it takes a `config` and emits patches; the HOST decides where they are saved
  (portal `layer_configs`, or `PUT /data/{kind}/{id}/default-style`).
  Also holds **size-by-a-field** for points and lines (`size_mode`/`size_field`/`size_stops`, issue
  #21 — the instance had drawn it since v1.1 with no way to set it) and the ramp **Reverse**
  checkbox, which flips the stored class colours locally: a reversed ramp is the same colours
  backwards, so no request is needed and hand-edited colours survive.
- `portal/DashboardBuilder.vue` (V-16, 2026-08-24) — the editor half of the **dashboard**
  archetype, mounted in `PortalEditor`'s Experience panel. **The one rule it exists to enforce: a
  template is a STARTING POINT, never a fixed layout.** Whether the author began blank or from a
  preset, every widget is removable, replaceable (the type dropdown REPLACES in place, keeping the
  cell and title — carrying a chart's `groupBy` onto a gauge would leave an unusable field in the
  config), re-bindable, re-wirable and re-sizable; the widget picker always offers all eight types.
  `applyPreset` binds a template's unbound preset to this portal's layers (first suitable layer,
  first suitable field of the right type, successive rasters for successive raster widgets) and only
  ever seeds an EMPTY grid — the overwrite path is a button. Layout editing is a 12-column grid with
  pointer drag + a corner resize handle and arrow-key nudges, deliberately the SAME geometry the
  runtime uses (a free-pixel canvas would need a second mapping to the published grid, and the two
  would drift). The wiring UI shows both directions: what this widget filters, and who filters it.
  **There is no widget renderer here** — the live preview is the real published portal in an iframe,
  as it is for every other archetype, so this component draws configuration only. Its `WIDGET_TYPES`
  table mirrors `services/dashboard.WIDGET_TYPES` and `templates/shared/dashboard.js`'s `RENDERERS`
  — three surfaces, change together. Changing a widget's LAYER clears every field on it (a column of
  the old table is almost never a column of the new one, and the query would 400).
- `portal/PortalCard.vue` — portal tile in the builder grid (edit/publish/view/unpublish/delete).
- `shared/InfoHint.vue` — an ⓘ next to a label that opens its explanation on click. Use it for the
  two-or-three-sentence *why* behind a control; keep STATE inline and visible ("Nothing filters this
  yet", "Add a raster layer first"), because a reader will not click an ⓘ to discover a blocking
  condition. Not `title=""` (what the older rows use): a native tooltip cannot be opened by touch,
  is unstyled, and truncates multi-sentence text. TELEPORTED to `<body>` and positioned `fixed`, so
  a narrow scrolling inspector cannot clip it — the bug the symbology popover had.
- `shared/StatusBadge.vue` — colored processing/ready/error pill.
- `shared/StorageBar.vue` — used/total storage bar (Settings).
- `users/` — admin Users screen components (RBAC A-01): UserRow / InviteRow / InviteModal /
  CopyLink. See `users/README.md`.

## Dependencies / relationships
- Read/write through `../../stores/` (mostly `data` and `portals`) and call the backend via `../../api`.
- `LayerPanel.vue` reads layer metadata (`columns`, `geometry_type`, `band_count`, `default_style`) from the data store; its style fields must stay consistent with the paint logic in `views/PortalEditor.vue` and the backend `portal_generator.py`.
- Icons from `../../views/icons.js`.

## Modals
All dialogs (`UploadModal`, `AddSourceModal`, `DiscoverModal`, `portal/CreatePortalModal`) wrap their overlay in `<Teleport to="body">` so the backdrop covers the full viewport (they used to render inside the scrollable `<main>`, which left an un-dimmed strip). Overlay style: `bg-gray-900/50 backdrop-blur-sm`, card `shadow-2xl`.

## Current status & known issues
- `LayerPanel` colormap/hillshade controls show for single-band output: a single-band raster
  (`band_count === 1`) or a multiband raster in **Single band** mode. Multiband rasters also get a
  band-mode picker (RGB composite ↔ single band). Colormap is cleared when switching to RGB (it is
  meaningless for a 3-band composite).
- Default-style save/use round-trips through `/api/data/{vector,raster}/{id}/default-style`.
- Point markers: `LayerPanel` carries a duplicate `markerImage` SVG helper that mirrors the canvas
  icon logic in `views/PortalEditor.vue` + `templates/shared/portal.js` — change all three together.

## Last updated
2026-08-29 (`shared/InfoHint.vue` added; DashboardBuilder's ten explanatory paragraphs moved into
hints on the controls they describe — the map inspector had become a wall of prose. Its map editor
also gained the `linkedFilter` checkbox.)

2026-08-24 (**dashboard first-use round**, `portal/DashboardBuilder.vue`. `addWidget` shipped every
widget with `actions.filters: []`, so a hand-built dashboard cross-filtered NOTHING — the map
published a geometry to nobody and the raster-stats and details panels sat on their placeholders for
ever, with no error anywhere. Cross-filtering is the archetype, so `autowire()` now connects a new
widget in BOTH directions and disconnecting is the deliberate act; duplicate wires incoming only. A
map widget binds the first vector layer and a `tolPx` click radius by default (a map with no
`layerId` returned early on every click) and exposes both as controls. `applyPreset` retitles an
auto-guessed aggregate after the field it actually reads — "Mean condition" over an arbitrary numeric
column named a quantity the card was not showing — and `autoRangeGauge` reads `/field-stats` to
rescale the dial and its threshold bands off the column's real range instead of a hardcoded 0–100.)

2026-08-24 (**V-16 `portal/DashboardBuilder.vue`** — the widget picker, the 12-column drag/resize
grid, per-widget data binding and the source→target filter wiring. It draws configuration only: the
live preview is the real published portal in an iframe, so there is no second widget renderer to
keep in sync with `templates/shared/dashboard.js`. A template preset seeds an empty grid once and is
fully editable afterwards — that is the rule the component exists to enforce.)
2026-08-07 (`portal/LayerPanel.vue`: the **Stretch (min/max)** inputs and ⚡ Auto are **disabled while
Hillshade is on**, with the hint swapped to say why. The algorithm returns a finished 0–255 relief
image and TiTiler applies `rescale` after it, so a stretch there silently flattened the shading —
the control looked available and did the opposite of nothing. Backend rule lives in
`services/titiler.py::get_tile_url`; see `api/geodeploy/services/README.md` 2026-08-07b.)
2026-08-06c (`portal/LayerPanel.vue`: outline controls. Polygons AND points can now set an outline
colour or **None** — previously a polygon always had a blue one and a point a hard-coded white one.
Points also get a thickness, expressed as a PROPORTION of the marker so it survives a resize; past
~60% the fill is hidden and the marker reads as a RING, which the panel says rather than leaving you
to find out. `NO_OUTLINE` is the sentinel string `"none"`, never `""` — an uninitialised colour input
yields `""`, and treating that as "no outline" would silently strip outlines from layers nobody
styled. The popover widened 230→288px; it had grown a mode picker, field, class controls, an
editable legend, marker and outline controls and a 3D block, and the labelled rows were wrapping.)
2026-08-06b (`portal/LayerPanel.vue`: **data-driven symbology**. The popover gains Single /
Graduated / Categories, a field picker (numeric-only for graduated; the geometry column never
offered), class count + method + ramp, and an EDITABLE legend whose swatches are the colours the map
will actually use. Class breaks are requested from `GET /data/vector/{ref}/field-stats` and never
computed here — the classifier reads the whole column, and a second implementation in the browser
would be two versions of one decision. `refreshClasses(over)` takes the control that just changed,
because the style prop has not updated yet when it runs (reading it back classifies with the
previous value: the classic one-step-lag bug). Polygons also gain a 3D "extrude by a field" control,
hidden when the layer has no numeric column. The row swatch now shows the MIDDLE class via
`lib/symbology.representativeColor` — the flat `color` is unused under a classification, and a
swatch showing a colour that appears nowhere on the map is a small lie told constantly.)
2026-08-06 (`infra/ConnectionDetails.vue` documented + it now shows which source each credential
group came from — issue #2)
2026-07-29 (new `data/ShareLinksModal.vue` + a link button on VectorRow/RasterRow — the per-layer
"use this data elsewhere" panel, led by OGC API - Features)
2026-07-16 (RBAC A-01: new `users/` folder; VectorRow/RasterRow/SourceRow/PortalCard mutating
actions hidden for viewers via `v-if auth.canEdit` — hide, don't disable, backend enforces; rows
show a "by {creator}" chip from the new `created_by` field)
2026-07-11 (VectorRow: manual Tile button → `dataStore.tileVector` → `POST /{id}/tile`, re-runnable PMTiles tiling for heavy GeoParquet)
