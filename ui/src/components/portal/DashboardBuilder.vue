<script setup>
/**
 * V-16 Dashboard builder — the editor half of the dashboard archetype.
 *
 * THE ONE RULE THIS COMPONENT EXISTS TO ENFORCE: a template is a STARTING POINT, never a fixed
 * layout. Whether the author began from a blank grid or from one of the four presets, every widget
 * in front of them can be removed, replaced, re-bound to a different layer or field, re-wired to
 * filter different widgets, and moved or resized. A preset only decides what is on screen the first
 * time. So there is no "preset mode" here: a preset is loaded into exactly the same editable
 * structure a hand-built dashboard uses, and after loading, the two are indistinguishable.
 *
 * SHAPE. `modelValue` is the portal's `dashboard` object — `{grid, refresh, widgets:[…]}` — the same
 * JSON the server normalises (`services/dashboard.resolve_dashboard`) and the published runtime
 * renders (`templates/shared/dashboard.js`). This component never invents a field those two do not
 * know; adding one means editing all three, which is the documented cost of a new widget type.
 *
 * LAYOUT EDITING is a 12-column grid with pointer drag to move and a corner handle to resize,
 * snapping to whole columns and whole rows. It is deliberately the same geometry the runtime uses
 * (12 columns, `layout.rowHeight` per row) rather than a free-pixel canvas, because a free canvas
 * would need a second, different mapping to the published grid — and the two would drift.
 *
 * The live PREVIEW is the real published portal in an iframe, exactly as it is for every other
 * archetype: PortalEditor debounces a `POST /portals/{id}/preview` on every change here. So there is
 * no second widget renderer in the editor to keep in sync with the runtime — the thing on screen IS
 * the runtime. That is why this component draws configuration, not widgets.
 */
import { computed, ref, watch } from 'vue'
import InfoHint from '../shared/InfoHint.vue'
import { getFieldStats } from '@/api'

const props = defineProps({
  modelValue: { type: Object, default: null },   // {grid, refresh, widgets:[]}
  /** Layers the portal can bind: [{id, type:'vector'|'raster', name, columns, band_count, …}] */
  layers: { type: Array, default: () => [] },
  /** The template's dashboard preset, when the chosen template ships one. */
  preset: { type: Object, default: null },
})
const emit = defineEmits(['update:modelValue'])

/**
 * The widget catalogue. The `type` strings, the `source`/`target` flags and the `needs` binding
 * kind are a MIRROR of `WIDGET_TYPES` in api/geodeploy/services/dashboard.py — the parity contract
 * for this archetype, in the same spirit as the three-way `resolveLayout` mirror. Change one, change
 * all three (here, the resolver, and templates/shared/dashboard.js).
 */
const WIDGET_TYPES = [
  { type: 'map', name: 'Map', needs: 'map', source: true, target: true,
    desc: 'The anchor. Draws every portal layer; click, polygon-draw or box-draw to select.' },
  { type: 'indicator', name: 'Indicator', needs: 'vector', source: true, target: true,
    desc: 'One computed number, optionally against a target.' },
  { type: 'gauge', name: 'Gauge', needs: 'vector', source: true, target: true,
    desc: 'The same number on a dial, with threshold bands.' },
  { type: 'chart', name: 'Chart', needs: 'vector', source: true, target: true,
    desc: 'Bar, line or pie over a grouping field. Clicking a segment filters.' },
  { type: 'table', name: 'List / table', needs: 'vector', source: true, target: true,
    desc: 'Attribute rows, sortable, click-to-zoom on the map.' },
  { type: 'scatter', name: 'Scatter (Y ~ X)', needs: 'vector', source: false, target: true,
    desc: 'One dot per feature, two numeric columns against each other. Randomly sampled.' },
  { type: 'search', name: 'Search box', needs: 'vector', source: true, target: false,
    desc: 'Find a feature by name or address, fly to it and filter to it. A filter source only.' },
  { type: 'profile', name: 'Column profile', needs: 'vector', source: false, target: true,
    desc: 'What is in the selected data, column by column: range, completeness, commonest values.' },
  { type: 'selector', name: 'Selector', needs: 'vector', source: true, target: false,
    desc: 'A category, range or date-range control. A filter source only.' },
  { type: 'legend', name: 'Legend', needs: 'none', source: false, target: false,
    desc: 'What the colours on the map mean. Describes every layer; no binding needed.' },
  { type: 'details', name: 'Details panel', needs: 'none', source: false, target: true,
    desc: 'Full attributes of whatever is selected across the dashboard.' },
  { type: 'rasterstats', name: 'Raster statistics', needs: 'raster', source: false, target: true,
    desc: 'Zonal statistics for the selected area. Target only.' },
]
const TYPE_BY_ID = Object.fromEntries(WIDGET_TYPES.map(t => [t.type, t]))

const AGG_OPS = [
  { id: 'count', name: 'Count' }, { id: 'sum', name: 'Sum' }, { id: 'avg', name: 'Average' },
  { id: 'min', name: 'Minimum' }, { id: 'max', name: 'Maximum' },
]
//: How an aggregation reads in a widget TITLE (as opposed to AGG_OPS, which is how it reads in the
//: dropdown that chooses it).
const OP_TITLE = { count: 'Count of', sum: 'Total', avg: 'Mean', min: 'Lowest', max: 'Highest' }
const CHART_KINDS = [
  { id: 'bar', name: 'Bars' }, { id: 'hbar', name: 'Bars (horizontal)' },
  { id: 'line', name: 'Line' }, { id: 'area', name: 'Area' },
  { id: 'pie', name: 'Pie' }, { id: 'donut', name: 'Donut' },
]
const TIME_BUCKETS = [
  { id: '', name: 'No time bucket' }, { id: 'hour', name: 'Hour' }, { id: 'day', name: 'Day' },
  { id: 'week', name: 'Week' }, { id: 'month', name: 'Month' },
  { id: 'quarter', name: 'Quarter' }, { id: 'year', name: 'Year' },
]
const SELECTOR_KINDS = [
  { id: 'category', name: 'Categories' }, { id: 'range', name: 'Numeric range' },
  { id: 'date', name: 'Date range' },
]
const RASTER_STATS = ['min', 'max', 'mean', 'sum', 'std', 'median', 'count', 'histogram']
const MAP_TOOLS = [
  { id: 'click', name: 'Click a feature' },
  { id: 'polygon', name: 'Draw a polygon' },
  { id: 'bbox', name: 'Drag a box' },
  // A switch rather than a gesture: while it is on, panning or zooming the map republishes the
  // viewport as the geometry filter, so the widgets beside the map describe what is on screen. Off
  // by default — see DEFAULT_MAP_TOOLS in services/dashboard.py.
  { id: 'extent', name: 'Filter by map extent' },
]
// Click hit radius in SCREEN PIXELS, converted to degrees at the click's zoom+latitude by
// dashboard.js. Degrees were the wrong unit for an author to reason in and the wrong unit for the
// runtime to use: 0 (the old default) can only ever hit a polygon, so clicking a point or line
// layer never resolved to a feature at any zoom.
const DEFAULT_TOL_PX = 6

//: Mirrors `LINKED_FILTER_CAPS` / `DEFAULT_LINKED_FILTER_CAP` in `services/dashboard.py` and
//: `LINKED_KEY_CAPS` in `templates/shared/dashboard.js`. The server normalises and the runtime
//: enforces; this copy only offers the choice. A list rather than a number field because too large
//: a value fails as a slow map and a fat response, never as an error — not somewhere an author
//: should be able to wander by typing.
const LINKED_KEY_CAPS = [1000, 5000, 10000, 20000]
const LINKED_KEY_CAP = 5000
const GRID_COLS = 12

// ── the model ───────────────────────────────────────────────────────────────
const dash = computed(() => props.modelValue || { grid: { rowHeight: 90, gap: 10 }, refresh: 0, widgets: [] })
const widgets = computed(() => dash.value.widgets || [])
const selectedId = ref(null)
const selected = computed(() => widgets.value.find(w => w.id === selectedId.value) || null)

function commit(next) { emit('update:modelValue', next) }
function patchDash(patch) { commit({ ...dash.value, ...patch }) }

//: Has anyone touched this grid, or is it still just a template sitting there?
//:
//: The distinction is the whole of the template-switching behaviour below. Loading a preset over
//: widgets an author has arranged destroys work and must be asked for; loading one over widgets
//: another preset put there five seconds ago destroys nothing, and asking is a button press for no
//: reason. Anything already on the grid when this editor opens counts as the author's — it came out
//: of a saved portal, and this component has no way to know how it got there.
const presetTouched = ref(widgets.value.length > 0)
//: Set while `applyPreset` commits, so its own write does not count as a touch.
let applyingPreset = false
//: The preset whose widgets are on the grid, or null if they came from anywhere else — a saved
//: portal, or hand-built. Auto-replacing is only ever safe when THIS component put them there:
//: `presetTouched` alone is not enough, because a portal loading into the editor after mount
//: arrives without going through `setWidgets` and would look untouched.
let appliedPreset = null
//: The preset watcher runs immediately, which is how an empty grid gets its template. That first
//: run is the editor OPENING, not the author choosing — and an offer to replace their layout the
//: moment they open a saved portal would be alarming.
let presetWatchOpened = false
//: A template was chosen whose layout differs from work the author has done. Not applied, ASKED —
//: see the banner in the template.
const presetOffered = ref(false)

function setWidgets(list) {
  if (!applyingPreset) presetTouched.value = true
  patchDash({ widgets: list })
}
function patchWidget(id, patch) {
  setWidgets(widgets.value.map(w => (w.id === id ? deepMerge(w, patch) : w)))
}
function deepMerge(base, patch) {
  const out = { ...base }
  for (const k of Object.keys(patch)) {
    const v = patch[k]
    if (v && typeof v === 'object' && !Array.isArray(v) && out[k] && typeof out[k] === 'object' && !Array.isArray(out[k])) {
      out[k] = { ...out[k], ...v }
    } else {
      out[k] = v
    }
  }
  return out
}
// A widget id lands in DOM ids and CSS selectors in the published page, so it is generated from a
// safe alphabet rather than from the title. The resolver re-checks it — this is the courtesy.
let seq = 0
function newId() {
  seq += 1
  const taken = new Set(widgets.value.map(w => w.id))
  let id = `w${widgets.value.length + seq}`
  while (taken.has(id)) { seq += 1; id = `w${widgets.value.length + seq}` }
  return id
}

// ── layer + field helpers ───────────────────────────────────────────────────
const vectorLayers = computed(() => props.layers.filter(l => l.type === 'vector'))
const rasterLayers = computed(() => props.layers.filter(l => l.type === 'raster'))
function layerOf(w) {
  const ds = w && w.dataSource
  if (!ds || ds.layerId == null) return null
  return props.layers.find(l => l.id === ds.layerId && l.type === ds.layerType) || null
}
function fieldsOf(w) {
  const layer = layerOf(w)
  return (layer && layer.columns) ? layer.columns.filter(c => c && c.name) : []
}
function numericFields(w) { return fieldsOf(w).filter(c => isNumeric(c.type)) }
function isNumeric(type) {
  return /int|numeric|decimal|double|real|float|serial|bigint|smallint/i.test(String(type || ''))
}
function isDate(type) { return /date|timestamp|time/i.test(String(type || '')) }
function dateFields(w) { return fieldsOf(w).filter(c => isDate(c.type)) }

// ── add / remove / duplicate ────────────────────────────────────────────────
const pickerOpen = ref(false)

/** A new widget's opening size. Chosen per type so the picker drops something usable rather than a
 *  uniform box that every author immediately resizes. */
const DEFAULT_SIZE = {
  map: { w: 7, h: 6 }, indicator: { w: 3, h: 2 }, gauge: { w: 4, h: 4 },
  chart: { w: 5, h: 3 }, table: { w: 6, h: 4 }, selector: { w: 3, h: 2 },
  details: { w: 4, h: 4 }, rasterstats: { w: 4, h: 2 },
}
/**
 * Wire a widget into the bus the moment it exists, in BOTH directions.
 *
 * Cross-filtering is what makes a dashboard a dashboard rather than a page of charts, so an
 * unwired widget is a broken one, not a neutral starting point. Shipping every new widget with
 * `filters: []` meant a hand-built dashboard cross-filtered NOTHING: the map published a geometry
 * to nobody, so a Raster Stats panel sat on "draw a box on the map" for ever and the details panel
 * on "select a feature" — with no error anywhere, because from the runtime's point of view that is
 * simply a dashboard whose author wired nothing.
 *
 * So the default is "connected", and disconnecting is the deliberate act (the wiring panel, which
 * already shows both directions). `incomingOnly` is for DUPLICATE: a copy must be DRIVEN like its
 * original, but must not re-publish the original's filter — two sources publishing the identical
 * predicate to the same targets is never what duplicating means.
 */
function autowire(list, freshId, incomingOnly) {
  const fresh = list.find(w => w.id === freshId)
  if (!fresh) return list
  const canListen = TYPE_BY_ID[fresh.type]?.target && fresh.actions?.listens !== false
  return list.map((w) => {
    if (w.id === freshId) {
      if (incomingOnly || !TYPE_BY_ID[w.type]?.source) return w
      const targets = list
        .filter(t => t.id !== freshId && TYPE_BY_ID[t.type]?.target && t.actions?.listens !== false)
        .map(t => t.id)
      return { ...w, actions: { ...w.actions, filters: targets } }
    }
    // Every existing SOURCE gains the newcomer as a target, so adding a chart to a dashboard that
    // already has a map does not leave the map silently not filtering it.
    if (!canListen || !TYPE_BY_ID[w.type]?.source) return w
    const cur = w.actions?.filters || []
    if (cur.includes(freshId)) return w
    return { ...w, actions: { ...w.actions, filters: [...cur, freshId] } }
  })
}

function addWidget(type) {
  const size = DEFAULT_SIZE[type] || { w: 4, h: 3 }
  const id = newId()
  const w = {
    id,
    type,
    title: '',
    layout: { x: 0, y: nextRow(), w: size.w, h: size.h },
    dataSource: defaultSource(type),
    style: defaultStyle(type),
    actions: { filters: [], listens: TYPE_BY_ID[type].target },
  }
  setWidgets(autowire([...widgets.value, w], id, false))
  selectedId.value = id
  pickerOpen.value = false
}
function nextRow() {
  return widgets.value.reduce((m, w) => Math.max(m, (w.layout?.y || 0) + (w.layout?.h || 2)), 0)
}
function defaultSource(type) {
  if (type === 'map') {
    // A selection layer by DEFAULT. Without one `dashboard.js` returns early on every click
    // (`ds.layerId == null`), so the map's click tool did nothing at all on a hand-built dashboard
    // — the tool highlighted, the cursor changed, and no request was ever made.
    const l = vectorLayers.value[0]
    const base = { tools: ['click', 'polygon', 'bbox'], tolPx: DEFAULT_TOL_PX }
    return l ? { ...base, layerType: 'vector', layerId: l.id } : base
  }
  if (type === 'none' || type === 'details') return null
  if (type === 'rasterstats') {
    const l = rasterLayers.value[0]
    return l ? { layerType: 'raster', layerId: l.id, stats: ['min', 'max', 'mean'], band: 1 }
             : { layerType: 'raster', stats: ['min', 'max', 'mean'], band: 1 }
  }
  const l = vectorLayers.value[0]
  const base = l ? { layerType: 'vector', layerId: l.id } : { layerType: 'vector' }
  if (type === 'chart') return { ...base, op: 'count', limit: 12, sort: 'value_desc' }
  if (type === 'table') return { ...base, fields: [], pageSize: 50 }
  if (type === 'profile') return { ...base, fields: [], topN: 5 }
  if (type === 'search') return { ...base, fields: [], searchMode: 'contains', limit: 8 }
  if (type === 'scatter') return { ...base, xField: null, yField: null, limit: 1500 }
  if (type === 'selector') return { ...base, kind: 'category', multi: true }
  return { ...base, op: 'count' }
}
function defaultStyle(type) {
  if (type === 'chart') return { chart: 'bar', legend: true }
  if (type === 'gauge') {
    return { min: 0, max: 100, decimals: 1, bands: [
      { from: 0, color: '#dc2626', label: 'Critical' },
      { from: 40, color: '#d97706', label: 'Warning' },
      { from: 70, color: '#16a34a', label: 'Good' },
    ] }
  }
  if (type === 'indicator') return { format: 'auto', decimals: 1, compareMode: 'delta', goodDirection: 'up' }
  return { format: 'auto', decimals: 1 }
}
function removeWidget(id) {
  // Also unwire it everywhere: a dangling target is dropped by the resolver anyway, but leaving one
  // in the saved config means the builder shows a checkbox for a widget that no longer exists.
  const next = widgets.value
    .filter(w => w.id !== id)
    .map(w => ({ ...w, actions: { ...w.actions, filters: (w.actions?.filters || []).filter(t => t !== id) } }))
  setWidgets(next)
  if (selectedId.value === id) selectedId.value = null
}
function duplicateWidget(id) {
  const src = widgets.value.find(w => w.id === id)
  if (!src) return
  const copy = JSON.parse(JSON.stringify(src))
  copy.id = newId()
  copy.layout = { ...copy.layout, y: nextRow() }
  // Deliberately NOT copying the wiring: two widgets publishing the identical filter to the same
  // targets is never what duplicating means, and it is invisible until something double-filters.
  copy.actions = { filters: [], listens: copy.actions?.listens !== false }
  // Incoming wiring only: the copy IS driven by whatever drives the original (otherwise it sits
  // there never updating), but publishes nothing of its own — see `autowire`.
  setWidgets(autowire([...widgets.value, copy], copy.id, true))
  selectedId.value = copy.id
}
function replaceType(id, type) {
  // A REPLACEMENT, not a mutation: the new type keeps the old one's place on the grid and its
  // title, and gets defaults for everything else. Carrying a chart's groupBy onto a gauge would
  // leave a field the gauge cannot use silently sitting in the config.
  const src = widgets.value.find(w => w.id === id)
  if (!src || src.type === type) return
  patchWidget(id, {
    type,
    dataSource: defaultSource(type),
    style: defaultStyle(type),
    actions: { filters: TYPE_BY_ID[type].source ? (src.actions?.filters || []) : [],
               listens: TYPE_BY_ID[type].target },
  })
}

// ── the source→target wiring (the "actions" model) ──────────────────────────
function canTarget(w) { return TYPE_BY_ID[w.type]?.target }
function canSource(w) { return TYPE_BY_ID[w.type]?.source }
function targetsFor(w) {
  return widgets.value.filter(t => t.id !== w.id && canTarget(t) && t.actions?.listens !== false)
}
function isWired(w, targetId) { return (w.actions?.filters || []).includes(targetId) }
function toggleWire(w, targetId) {
  const cur = w.actions?.filters || []
  const next = cur.includes(targetId) ? cur.filter(t => t !== targetId) : [...cur, targetId]
  patchWidget(w.id, { actions: { filters: next } })
}
function wireAll(w) {
  patchWidget(w.id, { actions: { filters: targetsFor(w).map(t => t.id) } })
}
function wireNone(w) { patchWidget(w.id, { actions: { filters: [] } }) }
function widgetLabel(w) {
  return w.title || TYPE_BY_ID[w.type]?.name || w.type
}
/** Who currently filters this widget — shown on a TARGET so the wiring is legible from both ends. */
function sourcesOf(w) { return widgets.value.filter(s => isWired(s, w.id)) }

// ── the layout grid: drag to move, corner handle to resize ──────────────────
const ROW_PX = 26          // the editor's own row height; the published one comes from grid.rowHeight
const gridEl = ref(null)
const drag = ref(null)     // {id, mode:'move'|'resize', startX, startY, orig}

//: The canvas draws only what the GRID places. An overlay widget is pinned to the map instead, so
//: it takes no cell — and showing it in one would claim space that is actually free for its
//: neighbours, which is a layout the author never gets.
//: RELATIONS — how a filter travels from one layer to another. Without one, an attribute filter
//: cannot leave its own layer: a predicate on `canton` means nothing against a table with no such
//: column, so the runtime drops it rather than silently returning zero rows. A relation is the
//: author saying "these two layers describe the same things, and this pair of columns proves it".
const relations = computed({
  get: () => dash.value.relations || [],
  set: (v) => patchDash({ relations: v }),
})
//: Every vector layer a widget actually binds to. A relation between layers nobody displays can
//: never fire, so offering one would be offering a control that does nothing.
const boundLayers = computed(() => {
  const seen = new Map()
  for (const w of widgets.value) {
    const ds = w.dataSource
    if (ds?.layerType === 'vector' && ds.layerId != null && !seen.has(ds.layerId)) {
      const l = vectorLayers.value.find(x => x.id === ds.layerId)
      if (l) seen.set(ds.layerId, l)
    }
  }
  return [...seen.values()]
})
function layerFields(id) {
  const l = vectorLayers.value.find(x => x.id === id)
  return (l?.columns || []).filter(c => c && c.name)
}
function addRelation() {
  const ls = boundLayers.value
  if (ls.length < 2) return
  relations.value = [...relations.value, {
    left: { layerId: ls[0].id, field: null },
    right: { layerId: ls[1].id, field: null },
  }]
}
function patchRelation(i, side, patch) {
  relations.value = relations.value.map((r, n) =>
    (n === i ? { ...r, [side]: { ...r[side], ...patch } } : r))
}
function removeRelation(i) {
  relations.value = relations.value.filter((_, n) => n !== i)
}

const gridWidgets = computed(() => widgets.value.filter(w => !w.layout?.overlay))
const overlayWidgets = computed(() => widgets.value.filter(w => w.layout?.overlay))
const ANCHOR_LABELS = {
  controls: 'with the map buttons',
  'top-left': 'top left', 'top-center': 'top centre', 'top-right': 'top right',
  'left-center': 'left', 'right-center': 'right',
  'bottom-left': 'bottom left', 'bottom-center': 'bottom centre', 'bottom-right': 'bottom right',
}
function anchorLabel(a) { return ANCHOR_LABELS[a] || a || '' }

function gridStyle(w) {
  const l = w.layout || { x: 0, y: 0, w: 4, h: 3 }
  return {
    gridColumn: `${(l.x || 0) + 1} / span ${l.w || 4}`,
    gridRow: `${(l.y || 0) + 1} / span ${l.h || 3}`,
  }
}
//: Two grid rectangles share a cell.
function hits(a, b) {
  return a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h
}

//: Resolve overlaps by pushing the widgets the ANCHOR landed on downwards, cascading.
//:
//: CSS Grid does not object to two items in one cell — it stacks them — so a dragged widget simply
//: sat on top of whatever was there, and the dashboard was saved that way. (A portal on the test
//: instance had one table overlapping four other widgets, including the map.) Nothing warned,
//: because nothing was checking.
//:
//: Push rather than block: a drag that refuses to land reads as a broken control, and the author's
//: intent is the position they dragged TO. The anchor keeps exactly what it was given and everything
//: it displaces moves down, which is the convention every grid dashboard uses.
//:
//: `from` is the layout as it stood when the drag STARTED, not the running one. Resolving against
//: the running layout would accumulate — each pointermove pushing neighbours a little further —
//: so dragging a widget across the grid and back would leave everything below it lower than it
//: began. Recomputing from the snapshot makes any single drag reversible.
function resolveOverlaps(list, anchorId, from) {
  const base = from || list
  const byId = {}
  base.forEach((w) => { byId[w.id] = w.layout })
  const anchor = list.find((w) => w.id === anchorId)
  if (!anchor) return list

  const placed = [{ id: anchorId, ...anchor.layout }]
  const rest = list
    .filter((w) => w.id !== anchorId && !w.layout?.overlay)   // overlays are pinned to the map
    .map((w) => ({ w, start: byId[w.id] || w.layout }))
    .sort((a, b) => (a.start.y - b.start.y) || (a.start.x - b.start.x))

  const moved = {}
  for (const { w, start } of rest) {
    const box = { x: start.x, y: start.y, w: start.w, h: start.h }
    // Bounded: the grid has no row limit, so an unbounded loop is a hang rather than a bad layout.
    for (let guard = 0; guard < 400 && placed.some((p) => hits(p, box)); guard++) box.y += 1
    placed.push({ id: w.id, ...box })
    if (box.y !== (w.layout?.y ?? 0)) moved[w.id] = box.y
  }
  if (!Object.keys(moved).length && !from) return list
  return list.map((w) => (
    moved[w.id] !== undefined || (from && byId[w.id] && w.id !== anchorId)
      ? { ...w, layout: { ...w.layout, ...byId[w.id], y: moved[w.id] !== undefined ? moved[w.id] : byId[w.id].y } }
      : w))
}

function onPointerDown(ev, w, mode) {
  if (ev.button !== 0) return
  ev.preventDefault()
  selectedId.value = w.id
  drag.value = {
    id: w.id, mode, startX: ev.clientX, startY: ev.clientY, orig: { ...w.layout },
    // Every widget's layout as it stood when the drag began — what collisions are resolved
    // against, so a drag cannot accumulate displacement across its own pointermoves.
    from: widgets.value.map((x) => ({ id: x.id, layout: { ...x.layout } })),
  }
  window.addEventListener('pointermove', onPointerMove)
  window.addEventListener('pointerup', onPointerUp, { once: true })
}
function onPointerMove(ev) {
  const d = drag.value
  if (!d || !gridEl.value) return
  const colPx = gridEl.value.clientWidth / GRID_COLS
  const dx = Math.round((ev.clientX - d.startX) / colPx)
  const dy = Math.round((ev.clientY - d.startY) / ROW_PX)
  const o = d.orig
  const patch = d.mode === 'move'
    ? { x: Math.max(0, Math.min(GRID_COLS - o.w, o.x + dx)), y: Math.max(0, o.y + dy) }
    : { w: Math.max(2, Math.min(GRID_COLS - o.x, o.w + dx)),
        h: Math.max(2, Math.min(24, o.h + dy)) }
  // Resizing collides exactly as moving does — growing a widget onto its neighbour is the same
  // problem — so both go through the resolver.
  const next = widgets.value.map(w => (w.id === d.id ? deepMerge(w, { layout: patch }) : w))
  setWidgets(resolveOverlaps(next, d.id, d.from))
}
function onPointerUp() {
  drag.value = null
  window.removeEventListener('pointermove', onPointerMove)
}
// Keyboard nudge — a drag is not the only way to place a box, and it is the only way that needs a
// mouse. Arrows move, shift+arrows resize.
function onKey(ev, w) {
  const l = w.layout
  const step = ev.shiftKey ? 'size' : 'move'
  const map = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] }
  const d = map[ev.key]
  if (!d) return
  ev.preventDefault()
  const patch = step === 'move'
    ? { x: Math.max(0, Math.min(GRID_COLS - l.w, l.x + d[0])), y: Math.max(0, l.y + d[1]) }
    : { w: Math.max(2, Math.min(GRID_COLS - l.x, l.w + d[0])),
        h: Math.max(2, Math.min(24, l.h + d[1])) }
  // No snapshot here: each keypress is its own complete move, so resolving against the current
  // layout is right — there is no burst of intermediate states to accumulate.
  const next = widgets.value.map(x => (x.id === w.id ? deepMerge(x, { layout: patch }) : x))
  setWidgets(resolveOverlaps(next, w.id, null))
}

// ── presets ─────────────────────────────────────────────────────────────────
/**
 * Load the chosen template's preset and BIND it to this portal's layers.
 *
 * A preset ships with no layer ids — it cannot have any, since the layers do not exist when the
 * template is written. Binding them here is what makes "start from a template" mean a working
 * dashboard instead of eight placeholder cards. The binding is a guess (first suitable layer, first
 * suitable field) and every part of it stays editable, which is the point: the author corrects a
 * guess rather than filling in a blank.
 */
function applyPreset(overwrite) {
  const preset = props.preset
  if (!preset || !preset.widgets) return
  if (!overwrite && widgets.value.length) return
  const vec = vectorLayers.value[0]
  const ras = rasterLayers.value
  let rasterCursor = 0
  const bound = preset.widgets.map((raw) => {
    const w = JSON.parse(JSON.stringify(raw))
    const spec = TYPE_BY_ID[w.type]
    if (!spec) return w
    w.dataSource = w.dataSource || {}
    if (spec.needs === 'raster') {
      // Successive raster widgets take successive rasters — the Zonal preset ships three, and
      // pointing all of them at the same DEM would look like a bug.
      const layer = ras[Math.min(rasterCursor, Math.max(0, ras.length - 1))]
      rasterCursor += 1
      if (layer) { w.dataSource.layerType = 'raster'; w.dataSource.layerId = layer.id }
    } else if (spec.needs === 'vector' || spec.needs === 'map') {
      if (vec) { w.dataSource.layerType = 'vector'; w.dataSource.layerId = vec.id }
      if (w.type === 'map' && w.dataSource.tolPx == null) w.dataSource.tolPx = DEFAULT_TOL_PX
      const cols = (vec && vec.columns) || []
      const nums = cols.filter(c => isNumeric(c.type))
      const cats = cols.filter(c => !isNumeric(c.type) && !isDate(c.type))
      const dates = cols.filter(c => isDate(c.type))
      if (w.type === 'selector') {
        const wanted = w.dataSource.kind === 'date' ? dates
          : w.dataSource.kind === 'range' ? nums : cats
        const pick = (wanted[0] || cols[0])
        if (pick) w.dataSource.field = pick.name
      } else if (w.type === 'chart') {
        if (w.dataSource.timeBucket) {
          if (dates[0]) w.dataSource.groupBy = dates[0].name
          else { delete w.dataSource.timeBucket; if (cats[0]) w.dataSource.groupBy = cats[0].name }
        } else if (cats[0]) {
          w.dataSource.groupBy = cats[0].name
        }
        if (w.dataSource.op !== 'count' && nums[0]) w.dataSource.field = nums[0].name
        else if (w.dataSource.op !== 'count') w.dataSource.op = 'count'
      } else if (w.type === 'table') {
        w.dataSource.fields = cols.slice(0, 6).map(c => c.name)
        w.dataSource.keyField = w.dataSource.fields[0] || null
      } else if (w.type === 'scatter') {
        // Two DIFFERENT numeric columns, or the plot is the y = x diagonal and says nothing.
        w.dataSource.xField = nums[0]?.name || null
        w.dataSource.yField = (nums[1] || nums[0])?.name || null
      } else if (w.type === 'search') {
        // Text columns only: searching a numeric id column with a contains-match is a scan that
        // finds nothing anyone typed.
        const text = cats.length ? cats : cols
        w.dataSource.fields = text.slice(0, 3).map(c => c.name)
        w.dataSource.keyField = w.dataSource.fields[0] || null
        w.dataSource.titleField = w.dataSource.fields[0] || null
      } else if (w.type === 'profile') {
        // A mix reads better than the first four columns: a couple of numbers give ranges and a
        // couple of categories give top lists, so the panel shows both of its shapes straight away.
        w.dataSource.fields = [...nums.slice(0, 2), ...cats.slice(0, 2)].map(c => c.name)
        if (!w.dataSource.fields.length) w.dataSource.fields = cols.slice(0, 4).map(c => c.name)
      } else if (w.type === 'map') {
        if (cats[0]) w.dataSource.field = cats[0].name
      } else if (w.dataSource.op && w.dataSource.op !== 'count') {
        if (nums[0]) w.dataSource.field = nums[0].name
        else w.dataSource.op = 'count'
      }
      // A PRESET TITLE DESCRIBES A DATASET THAT DOES NOT EXIST. "Mean condition" is written for a
      // hypothetical asset table; bound to whatever this portal's first numeric column happens to
      // be, it names a quantity the card is not showing. So a widget whose number comes from an
      // auto-guessed field is retitled after the field it actually reads. The author can type
      // anything over it — but the default now describes the data instead of the template.
      if (['indicator', 'gauge'].includes(w.type)
          && w.dataSource.field && (w.dataSource.op || 'count') !== 'count') {
        w.title = `${OP_TITLE[w.dataSource.op] || w.dataSource.op} ${w.dataSource.field}`
      }
    }
    return w
  })
  applyingPreset = true
  commit({
    grid: preset.grid || { rowHeight: 90, gap: 10 },
    refresh: preset.refresh || 0,
    widgets: bound,
  })
  applyingPreset = false
  presetTouched.value = false      // this grid is the template's again, not anyone's work
  appliedPreset = preset
  presetOffered.value = false
  selectedId.value = null
  // After the commit, not during it: `autoRangeGauge` reads the committed widget back and patches
  // it, so it has to run against the model the author can now see.
  bound.forEach((w) => {
    if (w.type === 'gauge' && w.dataSource?.field) autoRangeGauge(w.id, w.dataSource.field)
  })
}
const presetName = computed(() => (props.preset ? 'this template' : null))

// A dashboard portal with no widgets is a blank page, so the FIRST time the archetype is chosen and
// a preset is available, load it. Only when the grid is genuinely empty — this must never overwrite
// an author's work, which is why the overwrite path is a button they press.
watch(() => props.preset, (p) => {
  const opening = !presetWatchOpened
  presetWatchOpened = true
  if (!p) return
  // Nothing there yet — a dashboard with no widgets is a blank page.
  if (!widgets.value.length) { applyPreset(false); return }
  // Widgets are there, but THIS component put them there from a template and nobody has touched
  // them since. Switching should then just show the new one: someone is trying layouts on, and
  // making them find "Reload template" after every choice is the editor pretending it did not
  // understand. `appliedPreset` as well as `presetTouched`, because a portal that finished loading
  // after mount never passed through `setWidgets` and would otherwise read as untouched.
  if (appliedPreset && !presetTouched.value) { applyPreset(true); return }
  // Real work, or work of unknown origin. Never overwrite it silently — but do not sit in silence
  // either, which is what made choosing a template look like it had done nothing at all. Not on the
  // first run: that is the editor opening, not a choice.
  if (!opening) presetOffered.value = true
}, { immediate: true })

// The common opening order is "new portal → pick Dashboard → add layers", which means the preset
// loads while there is nothing to bind it to and every widget arrives unbound. Re-binding when the
// first layers appear is what makes that order work. Guarded on EVERY widget still being unbound,
// so it can never overwrite a binding the author made — the moment one exists, this stops firing.
watch(() => props.layers.length, (now, before) => {
  if (!now || before) return
  if (!props.preset || !widgets.value.length) return
  const anyBound = widgets.value.some(w => w.dataSource && w.dataSource.layerId != null)
  if (!anyBound) applyPreset(true)
})

// ── per-widget editors: small helpers the template reads ────────────────────
function toggleStat(w, stat) {
  const cur = w.dataSource?.stats || []
  const next = cur.includes(stat) ? cur.filter(s => s !== stat) : [...cur, stat]
  patchWidget(w.id, { dataSource: { stats: next } })
}
function toggleTool(w, tool) {
  const cur = w.dataSource?.tools || []
  const next = cur.includes(tool) ? cur.filter(t => t !== tool) : [...cur, tool]
  // Never all three off: a map widget with no selection mode cannot be a filter source, and the
  // author has no way to tell that from looking at it.
  patchWidget(w.id, { dataSource: { tools: next.length ? next : cur } })
}
function toggleTableField(w, name) {
  const cur = w.dataSource?.fields || []
  const next = cur.includes(name) ? cur.filter(f => f !== name) : [...cur, name]
  patchWidget(w.id, { dataSource: { fields: next, keyField: w.dataSource?.keyField || next[0] || null } })
}
// The profile has no keyField to keep in step — it describes columns, it does not filter by one.
//: MEASURES — several aggregates against one grouping ("mean height AND mean age per district").
//: Absent, a chart has the single op/field pair below it and behaves exactly as it always has; the
//: first measure added SEEDS ITSELF FROM that pair, so turning one chart into two series never
//: silently discards the one the author already configured.
const MAX_SERIES = 4
function seriesOf(w) { return w.dataSource?.series || [] }
function addSeries(w) {
  const cur = seriesOf(w)
  if (cur.length >= MAX_SERIES) return
  const ds = w.dataSource || {}
  const next = cur.length
    ? [...cur, { op: 'count' }]
    // Seed with what the chart is already plotting, then the new one.
    : [{ op: ds.op || 'count', ...(ds.field ? { field: ds.field } : {}) }, { op: 'count' }]
  patchWidget(w.id, { dataSource: { series: next } })
}
function patchSeries(w, i, patch) {
  const next = seriesOf(w).map((m, n) => (n === i ? { ...m, ...patch } : m))
  patchWidget(w.id, { dataSource: { series: next } })
}
function removeSeries(w, i) {
  const next = seriesOf(w).filter((_, n) => n !== i)
  // Back to one measure is back to a plain single-series chart, not a one-item list: the op/field
  // controls below take over again, and they are what an existing chart uses.
  patchWidget(w.id, { dataSource: { series: next.length > 1 ? next : null } })
}

function toggleSearchField(w, name) {
  const cur = w.dataSource?.fields || []
  const next = cur.includes(name) ? cur.filter(f => f !== name) : [...cur, name]
  patchWidget(w.id, {
    dataSource: {
      fields: next,
      keyField: w.dataSource?.keyField || next[0] || null,
      titleField: w.dataSource?.titleField || next[0] || null,
    },
  })
}
function toggleProfileField(w, name) {
  const cur = w.dataSource?.fields || []
  const next = cur.includes(name) ? cur.filter(f => f !== name) : [...cur, name]
  patchWidget(w.id, { dataSource: { fields: next } })
}
function setLayer(w, value) {
  const [type, id] = String(value).split(':')
  // Changing the layer INVALIDATES every field on the widget — a field name from the old table is
  // almost never a column of the new one, and the resolver would keep it and the query would 400.
  patchWidget(w.id, { dataSource: {
    layerType: type, layerId: Number(id),
    field: undefined, groupBy: undefined, keyField: undefined,
    fields: w.type === 'table' ? [] : undefined,
    filterField: undefined, filterValue: undefined,
  } })
}

/**
 * The aggregated FIELD of an indicator / gauge / chart, plus the two things that have to follow it.
 *
 * A gauge is the widget where a wrong scale is a wrong READING rather than an ugly one: the preset
 * and `defaultStyle` both ship 0–100, so a dial bound to an area in m² or an elevation in metres
 * pegged at full and told the visitor nothing. So binding a field also asks the server for that
 * column's actual range and rescales the dial — and the threshold bands with it, at the same
 * FRACTIONS of the arc the author (or the preset) put them at, because a band is "the top third",
 * not "70".
 *
 * One-shot, on the binding change the author just made. There is no stored "auto" flag to go stale:
 * the moment they drag the min/max inputs, nothing here fires again until they rebind the field.
 */
function setAggField(w, field) {
  patchWidget(w.id, { dataSource: { field: field || undefined } })
  if (w.type === 'gauge' && field) autoRangeGauge(w.id, field)
}

async function autoRangeGauge(widgetId, field) {
  const w = widgets.value.find(x => x.id === widgetId)
  if (!w || w.type !== 'gauge' || w.dataSource?.layerId == null) return
  let stats
  try {
    const res = await getFieldStats(w.dataSource.layerId, { field })
    stats = res?.data
  } catch (e) { return }        // a range we could not read is not worth a message; 0–100 stands
  if (!stats || stats.kind !== 'numeric') return
  const lo = Number(stats.min), hi = Number(stats.max)
  if (!Number.isFinite(lo) || !Number.isFinite(hi) || hi <= lo) return
  // The widget may have been retyped, rebound or removed while the request was in flight.
  const cur = widgets.value.find(x => x.id === widgetId)
  if (!cur || cur.type !== 'gauge' || cur.dataSource?.field !== field) return
  const oldMin = Number(cur.style?.min ?? 0)
  const oldMax = Number(cur.style?.max ?? 100)
  const span = (oldMax - oldMin) || 1
  const bands = (cur.style?.bands || []).map(b => ({
    ...b,
    from: tidy(lo + ((Number(b.from) - oldMin) / span) * (hi - lo)),
  }))
  // The ends round OUTWARD. Rounding a max of 1.0210 to 1.02 puts the column's own largest value
  // past the end of the dial, which is the one number a gauge must be able to show.
  patchWidget(widgetId, { style: { min: tidy(lo, 'down'), max: tidy(hi, 'up'), bands } })
}
/** A readable number at a precision that suits its magnitude, optionally rounded outward. */
function tidy(n, dir) {
  if (!Number.isFinite(n)) return n
  const mag = Math.abs(n)
  const dp = mag >= 100 ? 0 : mag >= 1 ? 2 : 4
  const f = 10 ** dp
  if (dir === 'down') return Math.floor(n * f) / f
  if (dir === 'up') return Math.ceil(n * f) / f
  return Number(n.toFixed(dp))
}
function layerValue(w) {
  const ds = w.dataSource
  return ds && ds.layerId != null ? `${ds.layerType}:${ds.layerId}` : ''
}
function bandCount(w) { return layerOf(w)?.band_count || 1 }
</script>

<template>
  <div>
    <div class="flex items-center justify-between mb-2">
      <span class="text-xs font-semibold text-muted-foreground uppercase tracking-wider">Widgets</span>
      <div class="flex items-center gap-2">
        <button v-if="preset" @click="applyPreset(true)"
          class="text-[11px] text-muted-foreground/80 hover:text-foreground"
          :title="`Replace the current widgets with ${presetName}'s starting set`">↺ Reload template</button>
        <button @click="pickerOpen = !pickerOpen" class="text-xs text-primary hover:text-primary/80 font-medium">+ Add widget</button>
      </div>
    </div>

    <!-- Asked, not done. Choosing a template with work already on the grid used to change nothing
         visible, leaving a quiet "Reload template" link as the only clue that a choice had been
         registered at all. -->
    <div v-if="presetOffered"
      class="mb-3 p-2.5 rounded-lg border border-primary/40 bg-primary/5 flex items-start gap-2">
      <span class="text-[11px] leading-snug flex-1">
        This template has a different starting layout.
        <span class="text-muted-foreground">Loading it replaces the widgets you have now.</span>
      </span>
      <button @click="applyPreset(true)"
        class="text-[11px] font-medium text-primary hover:text-primary/80 shrink-0">Load it</button>
      <button @click="presetOffered = false"
        class="text-[11px] text-muted-foreground hover:text-foreground shrink-0">Keep mine</button>
    </div>

    <!-- The widget picker. Every type, always — a template's set is a starting point, not a menu. -->
    <div v-if="pickerOpen" class="mb-3 p-2 bg-muted/40 rounded-lg border border-border space-y-0.5">
      <button v-for="t in WIDGET_TYPES" :key="t.type" @click="addWidget(t.type)"
        class="w-full text-left p-1.5 rounded hover:bg-card transition-colors">
        <span class="block text-xs font-medium text-foreground/90">{{ t.name }}</span>
        <span class="block text-[10px] text-muted-foreground/70 leading-snug">{{ t.desc }}</span>
      </button>
    </div>

    <p v-if="!widgets.length" class="text-[11px] text-muted-foreground/70 mb-2 leading-snug">
      No widgets yet. Add a <strong>Map</strong> first — it is the anchor every selection comes from —
      then the indicators, charts and panels it should filter.
    </p>

    <!-- ── the layout grid ────────────────────────────────────────────────── -->
    <div v-if="widgets.length" ref="gridEl"
      class="relative mb-3 rounded-lg border border-border bg-muted/30 p-1 select-none"
      style="display:grid; grid-template-columns:repeat(12, 1fr); grid-auto-rows:26px; gap:3px;">
      <div v-for="w in gridWidgets" :key="w.id" :style="gridStyle(w)"
        class="relative rounded border text-[10px] overflow-hidden cursor-move focus:outline-none"
        :class="selectedId === w.id
          ? 'border-primary bg-primary/15 ring-1 ring-primary'
          : 'border-border bg-card hover:border-muted-foreground/50'"
        tabindex="0"
        @pointerdown="onPointerDown($event, w, 'move')"
        @keydown="onKey($event, w)"
        @click.stop="selectedId = w.id">
        <div class="px-1.5 pt-1 font-medium text-foreground/85 truncate">{{ widgetLabel(w) }}</div>
        <div class="px-1.5 text-muted-foreground/70 truncate">{{ TYPE_BY_ID[w.type]?.name }}</div>
        <!-- Resize handle. A corner grip rather than edges: an edge handle on a 26px row is smaller
             than a pointer target should be, and the corner does both axes at once. -->
        <div class="absolute right-0 bottom-0 w-3 h-3 cursor-nwse-resize"
          @pointerdown.stop="onPointerDown($event, w, 'resize')">
          <svg viewBox="0 0 10 10" class="w-full h-full text-muted-foreground/60"><path d="M9 1v8H1" fill="none" stroke="currentColor" stroke-width="1.4"/></svg>
        </div>
      </div>
    </div>
    <p v-if="gridWidgets.length" class="text-[10px] text-muted-foreground/60 -mt-2 mb-3">
      Drag to move, pull the corner to resize. Arrow keys nudge; hold shift to resize.
    </p>

    <!-- ── relations between layers ───────────────────────────────────────── -->
    <div v-if="boundLayers.length > 1" class="mb-3 p-2.5 rounded-lg border border-border bg-card">
      <div class="flex items-center gap-2 mb-1">
        <span class="text-[11px] font-medium">Linked layers</span>
        <InfoHint label="What linked layers do">
          Filters normally stay on their own layer. Link two layers on a shared column and a filter
          on one narrows the other — clicking a canton in a buildings chart then narrows the
          entrances.
        </InfoHint>
        <button @click="addRelation" class="ml-auto text-[11px] text-primary hover:text-primary/80">+ Link</button>
      </div>
      <div v-for="(r, i) in relations" :key="i" class="flex items-center gap-1 mb-1">
        <select :value="r.left.layerId"
          @change="patchRelation(i, 'left', { layerId: Number($event.target.value), field: null })"
          class="min-w-0 flex-1 text-[11px] bg-background border border-border rounded px-1.5 py-1">
          <option v-for="l in boundLayers" :key="l.id" :value="l.id">{{ l.name }}</option>
        </select>
        <select :value="r.left.field || ''"
          @change="patchRelation(i, 'left', { field: $event.target.value || null })"
          class="min-w-0 flex-1 text-[11px] bg-background border border-border rounded px-1.5 py-1">
          <option value="">— column —</option>
          <option v-for="f in layerFields(r.left.layerId)" :key="f.name" :value="f.name">{{ f.name }}</option>
        </select>
        <span class="text-[11px] text-muted-foreground flex-shrink-0">=</span>
        <select :value="r.right.layerId"
          @change="patchRelation(i, 'right', { layerId: Number($event.target.value), field: null })"
          class="min-w-0 flex-1 text-[11px] bg-background border border-border rounded px-1.5 py-1">
          <option v-for="l in boundLayers" :key="l.id" :value="l.id">{{ l.name }}</option>
        </select>
        <select :value="r.right.field || ''"
          @change="patchRelation(i, 'right', { field: $event.target.value || null })"
          class="min-w-0 flex-1 text-[11px] bg-background border border-border rounded px-1.5 py-1">
          <option value="">— column —</option>
          <option v-for="f in layerFields(r.right.layerId)" :key="f.name" :value="f.name">{{ f.name }}</option>
        </select>
        <button @click="removeRelation(i)" title="Remove this link"
          class="text-[11px] text-red-400 hover:text-red-500 px-1 flex-shrink-0">&times;</button>
      </div>
      <p v-if="!relations.length" class="text-[10px] text-muted-foreground/60">
        No links yet — filters stay on their own layer.
      </p>
    </div>

    <!-- Widgets pinned to the map. They are NOT in the canvas above: they take no grid cell, so
         drawing them in one would show a box reserving space that is actually free, and offer a
         resize handle that changes nothing once published. They are sized in pixels instead. -->
    <div v-if="overlayWidgets.length" class="mb-3">
      <span class="text-[10px] text-muted-foreground block mb-1">On the map</span>
      <div class="flex flex-wrap gap-1">
        <button v-for="w in overlayWidgets" :key="w.id" @click="selectedId = w.id"
          class="px-2 py-1 rounded border text-[10px] text-left"
          :class="selectedId === w.id
            ? 'border-primary bg-primary/15 text-primary'
            : 'border-border bg-card text-foreground/75 hover:border-muted-foreground/50'">
          <span class="font-medium">{{ widgetLabel(w) }}</span>
          <span class="text-muted-foreground/70"> · {{ anchorLabel(w.layout?.overlay) }}</span>
        </button>
      </div>
    </div>

    <!-- ── the selected widget's configuration ────────────────────────────── -->
    <div v-if="selected" class="p-2.5 rounded-lg border border-border bg-card space-y-2.5">
      <div class="flex items-center gap-1.5">
        <input :value="selected.title" @input="patchWidget(selected.id, { title: $event.target.value })"
          :placeholder="TYPE_BY_ID[selected.type]?.name"
          class="flex-1 min-w-0 text-xs font-medium bg-transparent border-b border-border/60 focus:outline-none focus:border-primary/60 px-1 py-0.5" />
        <button @click="duplicateWidget(selected.id)" title="Duplicate"
          class="text-[11px] text-muted-foreground/70 hover:text-foreground px-1">⧉</button>
        <button @click="removeWidget(selected.id)" title="Remove this widget"
          class="text-[11px] text-red-400 hover:text-red-500 px-1">✕</button>
      </div>

      <!-- Type. Changing it REPLACES the widget in place, keeping its cell and its title. -->
      <label class="block">
        <span class="text-[10px] text-muted-foreground block mb-0.5">Widget type</span>
        <select :value="selected.type" @change="replaceType(selected.id, $event.target.value)"
          class="w-full text-xs bg-background border border-border rounded px-2 py-1 focus:outline-none focus:border-primary/60">
          <option v-for="t in WIDGET_TYPES" :key="t.type" :value="t.type">{{ t.name }}</option>
        </select>
      </label>

      <!-- Placement. A widget either takes a grid cell or floats in a corner OF THE MAP; the map
           widget itself is the thing being floated over, so it is never offered the choice. -->
      <template v-if="selected.type !== 'map'">
        <div class="grid grid-cols-2 gap-2">
          <label class="block">
            <span class="text-[10px] text-muted-foreground block mb-0.5">Placement</span>
            <select :value="selected.layout?.overlay || ''"
              @change="patchWidget(selected.id, { layout: { overlay: $event.target.value || null } })"
              class="w-full text-xs bg-background border border-border rounded px-2 py-1 focus:outline-none focus:border-primary/60">
              <option value="">In the grid</option>
              <option value="controls">With the map's buttons</option>
              <optgroup label="On the map — top">
                <option value="top-left">Top left</option>
                <option value="top-center">Top centre</option>
                <option value="top-right">Top right</option>
              </optgroup>
              <optgroup label="On the map — sides">
                <option value="left-center">Left</option>
                <option value="right-center">Right</option>
              </optgroup>
              <optgroup label="On the map — bottom">
                <option value="bottom-left">Bottom left</option>
                <option value="bottom-center">Bottom centre</option>
                <option value="bottom-right">Bottom right</option>
              </optgroup>
            </select>
          </label>
          <label class="block" v-if="selected.layout?.overlay">
            <span class="text-[10px] text-muted-foreground block mb-0.5">Width (px)</span>
            <input type="number" min="140" max="520" step="10" :value="selected.layout?.overlayW ?? 260"
              @change="patchWidget(selected.id, { layout: { overlayW: Number($event.target.value) || 260 } })"
              class="w-full text-xs bg-background border border-border rounded px-2 py-1 focus:outline-none focus:border-primary/60" />
          </label>
        </div>
        <label class="block" v-if="selected.layout?.overlay">
          <span class="text-[10px] text-muted-foreground block mb-0.5">Height (px, 0 = fit content)</span>
          <input type="number" min="0" max="800" step="10" :value="selected.layout?.overlayH ?? 0"
            @change="patchWidget(selected.id, { layout: { overlayH: Number($event.target.value) || 0 } })"
            class="w-full text-xs bg-background border border-border rounded px-2 py-1 focus:outline-none focus:border-primary/60" />
        </label>
        <label v-if="selected.layout?.overlay" class="flex items-center gap-1.5 text-[11px] cursor-pointer select-none">
          <input type="checkbox" class="accent-primary"
            :checked="!!selected.layout?.overlayCollapsed"
            @change="patchWidget(selected.id, { layout: { overlayCollapsed: $event.target.checked } })" />
          Start collapsed as an icon
          <InfoHint label="About pinning to the map">
            Pinned to the map and sized in pixels, so it is not in the canvas above and its cell is
            freed for the widgets around it. Leave the height at 0 for a box that fits its content.
          </InfoHint>
        </label>
      </template>

      <!-- Data source -->
      <template v-if="TYPE_BY_ID[selected.type]?.needs !== 'none'">
        <label class="block">
          <span class="text-[10px] text-muted-foreground flex items-center gap-1 mb-0.5">
            {{ selected.type === 'map' ? 'Selection layer (optional)' : 'Layer' }}
            <InfoHint v-if="selected.type === 'map' && vectorLayers.length > 1"
              label="How the selection layer is used">
              A click tries this layer first, then the portal's other vector layers top-down, so
              every layer on the map is selectable. A filter narrows the layer it came from wherever
              that layer is drawn, whichever layer is named here.
            </InfoHint>
          </span>
          <select :value="layerValue(selected)" @change="setLayer(selected, $event.target.value)"
            class="w-full text-xs bg-background border border-border rounded px-2 py-1 focus:outline-none focus:border-primary/60">
            <option value="">— none —</option>
            <option v-for="l in (TYPE_BY_ID[selected.type].needs === 'raster' ? rasterLayers : vectorLayers)"
              :key="l.id" :value="`${l.type}:${l.id}`">{{ l.name }}</option>
          </select>
          <span v-if="TYPE_BY_ID[selected.type].needs === 'raster' && !rasterLayers.length"
            class="text-[10px] text-muted-foreground/70 leading-snug block mt-0.5">
            Add a raster layer to this portal first — zonal statistics read its pixels.
          </span>
        </label>

        <!-- map: which selection modes, and what a click publishes -->
        <template v-if="selected.type === 'map'">
          <div>
            <span class="text-[10px] text-muted-foreground flex items-center gap-1 mb-1">
              Selection tools
              <InfoHint label="About the selection tools">
                All three produce the same kind of filter — a geometry — so raster statistics respond
                to a clicked feature, a drawn polygon and a dragged box alike.
              </InfoHint>
            </span>
            <div class="flex flex-wrap gap-1">
              <button v-for="t in MAP_TOOLS" :key="t.id" @click="toggleTool(selected, t.id)"
                class="px-2 py-0.5 rounded border text-[11px]"
                :class="(selected.dataSource?.tools || []).includes(t.id)
                  ? 'border-primary text-primary bg-primary/10' : 'border-border text-foreground/70'">{{ t.name }}</button>
            </div>
          </div>
          <!-- Click radius. In PIXELS, because that is what the visitor is aiming with; the runtime
               converts it to degrees at the click's own zoom and latitude. A point layer needs a few
               pixels of slack or a click never lands on a feature at all. -->
          <label class="block">
            <span class="text-[10px] text-muted-foreground flex items-center gap-1 mb-0.5">
              Click radius — {{ selected.dataSource?.tolPx ?? DEFAULT_TOL_PX }} px
              <InfoHint label="About the click radius">
                How near a click has to land. Points and lines need a few pixels; polygons work at 0.
              </InfoHint>
            </span>
            <input type="range" min="0" max="24" step="1"
              :value="selected.dataSource?.tolPx ?? DEFAULT_TOL_PX"
              @input="patchWidget(selected.id, { dataSource: { tolPx: Number($event.target.value) } })"
              class="w-full accent-primary" />
          </label>
          <label class="flex items-center gap-1.5 text-[11px] cursor-pointer select-none">
            <input type="checkbox" class="accent-primary"
              :checked="selected.dataSource?.zoomToFilter !== false"
              @change="patchWidget(selected.id, { dataSource: { zoomToFilter: $event.target.checked } })" />
            Zoom to what a chart selects
            <InfoHint label="About zooming to a chart's selection">
              Clicking a bar or a slice frames the features it selected. Charts only — a slider or a
              search box is a control you are working in, and moving the camera under the hand using
              it fights you. Turn it off where comparing categories in a fixed view matters more.
            </InfoHint>
          </label>
          <!-- The map following a LINKED filter. Off by default, and the one setting here whose
               failure mode is a map that LOOKS narrowed and is not — so the bound stays one click
               away rather than being left to the docs. -->
          <label v-if="relations.length"
            class="flex items-center gap-1.5 text-[11px] cursor-pointer select-none">
            <input type="checkbox" class="accent-primary"
              :checked="!!selected.dataSource?.linkedFilter"
              @change="patchWidget(selected.id, { dataSource: { linkedFilter: $event.target.checked } })" />
            Follow linked-layer filters
            <InfoHint label="About following linked-layer filters">
              Lets a filter published on a linked layer narrow this map too, by fetching the matching
              keys. Works for a narrow selection; past the limit below the map is left whole and says
              so on screen, rather than looking narrowed when it is not.
            </InfoHint>
          </label>
          <label v-if="relations.length && selected.dataSource?.linkedFilter" class="block">
            <span class="text-[10px] text-muted-foreground flex items-center gap-1 mb-0.5">
              Give up past
              <InfoHint label="About the key limit">
                How many matching keys the map will fetch before it stops narrowing. Higher reaches
                broader selections and moves more data on every filter change — roughly 40 KB per
                1 000 keys. Past the limit the map draws everything and says so.
              </InfoHint>
            </span>
            <select :value="selected.dataSource?.linkedFilterCap ?? LINKED_KEY_CAP"
              @change="patchWidget(selected.id, { dataSource: { linkedFilterCap: Number($event.target.value) } })"
              class="w-full text-xs bg-background border border-border rounded px-2 py-1 focus:outline-none focus:border-primary/60">
              <option v-for="c in LINKED_KEY_CAPS" :key="c" :value="c">
                {{ c.toLocaleString() }} keys{{ c === LINKED_KEY_CAP ? ' (default)' : '' }}
              </option>
            </select>
          </label>
          <label v-if="selected.dataSource?.layerId != null" class="block">
            <span class="text-[10px] text-muted-foreground block mb-0.5">Also filter by this field on click</span>
            <select :value="selected.dataSource?.field || ''"
              @change="patchWidget(selected.id, { dataSource: { field: $event.target.value || undefined } })"
              class="w-full text-xs bg-background border border-border rounded px-2 py-1 focus:outline-none focus:border-primary/60">
              <option value="">— geometry only —</option>
              <option v-for="f in fieldsOf(selected)" :key="f.name" :value="f.name">{{ f.name }}</option>
            </select>
          </label>
        </template>

        <!-- indicator / gauge / chart: the aggregation -->
        <template v-if="['indicator', 'gauge', 'chart'].includes(selected.type)">
          <div class="grid grid-cols-2 gap-2">
            <label class="block">
              <span class="text-[10px] text-muted-foreground block mb-0.5">Aggregation</span>
              <select :value="selected.dataSource?.op || 'count'"
                @change="patchWidget(selected.id, { dataSource: { op: $event.target.value } })"
                class="w-full text-xs bg-background border border-border rounded px-2 py-1 focus:outline-none focus:border-primary/60">
                <option v-for="o in AGG_OPS" :key="o.id" :value="o.id">{{ o.name }}</option>
              </select>
            </label>
            <label v-if="(selected.dataSource?.op || 'count') !== 'count'" class="block">
              <span class="text-[10px] text-muted-foreground block mb-0.5">Field</span>
              <select :value="selected.dataSource?.field || ''"
                @change="setAggField(selected, $event.target.value)"
                class="w-full text-xs bg-background border border-border rounded px-2 py-1 focus:outline-none focus:border-primary/60">
                <option value="">—</option>
                <option v-for="f in numericFields(selected)" :key="f.name" :value="f.name">{{ f.name }}</option>
              </select>
            </label>
          </div>
        </template>

        <!-- chart: grouping -->
        <template v-if="selected.type === 'chart'">
          <div class="grid grid-cols-2 gap-2">
            <label class="block">
              <span class="text-[10px] text-muted-foreground block mb-0.5">Group by</span>
              <select :value="selected.dataSource?.groupBy || ''"
                @change="patchWidget(selected.id, { dataSource: { groupBy: $event.target.value } })"
                class="w-full text-xs bg-background border border-border rounded px-2 py-1 focus:outline-none focus:border-primary/60">
                <option value="">—</option>
                <option v-for="f in fieldsOf(selected)" :key="f.name" :value="f.name">{{ f.name }}</option>
              </select>
            </label>
            <label class="block">
              <span class="text-[10px] text-muted-foreground block mb-0.5">Time bucket</span>
              <select :value="selected.dataSource?.timeBucket || ''"
                @change="patchWidget(selected.id, { dataSource: { timeBucket: $event.target.value || undefined } })"
                class="w-full text-xs bg-background border border-border rounded px-2 py-1 focus:outline-none focus:border-primary/60">
                <option v-for="b in TIME_BUCKETS" :key="b.id" :value="b.id">{{ b.name }}</option>
              </select>
            </label>
          </div>
          <p v-if="selected.dataSource?.timeBucket && !dateFields(selected).some(f => f.name === selected.dataSource?.groupBy)"
            class="text-[10px] text-amber-500 leading-snug">
            A time bucket needs a date or timestamp field in “Group by”.
          </p>
          <!-- Several measures against the one grouping. Pie and donut are excluded: a pie divides
               ONE quantity into parts, so "mean height and mean age" has no whole to be parts of. -->
          <div v-if="!['pie', 'donut'].includes(selected.style?.chart || 'bar')">
            <div class="flex items-center gap-2 mb-1">
              <span class="text-[10px] text-muted-foreground">Measures plotted</span>
              <button v-if="seriesOf(selected).length < 4" @click="addSeries(selected)"
                class="ml-auto text-[10px] text-primary hover:text-primary/80">+ Measure</button>
            </div>
            <div v-for="(m, i) in seriesOf(selected)" :key="i" class="flex items-center gap-1 mb-1">
              <select :value="m.op"
                @change="patchSeries(selected, i, { op: $event.target.value })"
                class="min-w-0 flex-1 text-[11px] bg-background border border-border rounded px-1.5 py-1">
                <option v-for="o in AGG_OPS" :key="o.id" :value="o.id">{{ o.name }}</option>
              </select>
              <select v-if="m.op !== 'count'" :value="m.field || ''"
                @change="patchSeries(selected, i, { field: $event.target.value || null })"
                class="min-w-0 flex-1 text-[11px] bg-background border border-border rounded px-1.5 py-1">
                <option value="">— column —</option>
                <option v-for="f in numericFields(selected)" :key="f.name" :value="f.name">{{ f.name }}</option>
              </select>
              <input :value="m.label || ''" placeholder="Label"
                @change="patchSeries(selected, i, { label: $event.target.value || null })"
                class="min-w-0 flex-1 text-[11px] bg-background border border-border rounded px-1.5 py-1" />
              <button @click="removeSeries(selected, i)" title="Remove this measure"
                class="text-[11px] text-red-400 hover:text-red-500 px-1 flex-shrink-0">&times;</button>
            </div>
            <!-- Pie/donut only: the plot and its legend share the card, and only these two have a
               legend that competes with the plot for it. On a bar chart the same control would just
               leave empty space. -->
          <label v-if="['pie', 'donut'].includes(selected.style?.chart)" class="block">
            <span class="text-[10px] text-muted-foreground flex items-center gap-1 mb-0.5">
              Plot size — {{ selected.style?.plotSize ?? 100 }}%
              <InfoHint label="About the plot size">
                How much of the card the circle takes, leaving the rest to the legend. Making the
                widget bigger grows both together; this is what shrinks the plot so a long legend
                has room to be read without scrolling.
              </InfoHint>
            </span>
            <input type="range" min="30" max="100" step="5"
              :value="selected.style?.plotSize ?? 100"
              @input="patchWidget(selected.id, { style: { plotSize: Number($event.target.value) } })"
              class="w-full accent-primary" />
          </label>
          <p v-if="!seriesOf(selected).length" class="text-[10px] text-muted-foreground/70 mb-1">
              One measure — the aggregation below. Add another to plot them side by side, with a
              legend; colour then names the measure rather than the category.
            </p>
          </div>

          <div class="grid grid-cols-2 gap-2">
            <label class="block">
              <span class="text-[10px] text-muted-foreground block mb-0.5">Chart</span>
              <select :value="selected.style?.chart || 'bar'"
                @change="patchWidget(selected.id, { style: { chart: $event.target.value } })"
                class="w-full text-xs bg-background border border-border rounded px-2 py-1 focus:outline-none focus:border-primary/60">
                <option v-for="k in CHART_KINDS" :key="k.id" :value="k.id">{{ k.name }}</option>
              </select>
            </label>
            <label class="block">
              <span class="text-[10px] text-muted-foreground block mb-0.5">Bar colours</span>
              <select :value="selected.style?.colorMode || 'single'"
                @change="patchWidget(selected.id, { style: { colorMode: $event.target.value } })"
                class="w-full text-xs bg-background border border-border rounded px-2 py-1 focus:outline-none focus:border-primary/60">
                <option value="single">One colour</option>
                <option value="category">One per category</option>
                <option value="sequential">Shaded (ordered)</option>
              </select>
            </label>
            <label class="block">
              <span class="text-[10px] text-muted-foreground block mb-0.5">Values on bars</span>
              <select :value="selected.style?.valueLabels == null ? '' : (selected.style.valueLabels ? 'on' : 'off')"
                @change="patchWidget(selected.id, { style: { valueLabels: $event.target.value === '' ? null : $event.target.value === 'on' } })"
                class="w-full text-xs bg-background border border-border rounded px-2 py-1 focus:outline-none focus:border-primary/60">
                <option value="">Automatic</option>
                <option value="on">Always show</option>
                <option value="off">Never show</option>
              </select>
            </label>
            <label class="block">
              <span class="text-[10px] text-muted-foreground block mb-0.5">Max groups</span>
              <input type="number" min="2" max="100" :value="selected.dataSource?.limit || 12"
                @input="patchWidget(selected.id, { dataSource: { limit: Number($event.target.value) } })"
                class="w-full text-xs bg-background border border-border rounded px-2 py-1 focus:outline-none focus:border-primary/60" />
            </label>
          </div>
        </template>

        <!-- table: which columns, and which one a row click publishes -->
        <template v-if="selected.type === 'table'">
          <div>
            <span class="text-[10px] text-muted-foreground block mb-1">Columns</span>
            <div class="flex flex-wrap gap-1 max-h-24 overflow-y-auto">
              <button v-for="f in fieldsOf(selected)" :key="f.name" @click="toggleTableField(selected, f.name)"
                class="px-2 py-0.5 rounded border text-[11px]"
                :class="(selected.dataSource?.fields || []).includes(f.name)
                  ? 'border-primary text-primary bg-primary/10' : 'border-border text-foreground/70'">{{ f.name }}</button>
            </div>
            <p v-if="!(selected.dataSource?.fields || []).length" class="text-[10px] text-muted-foreground/70 mt-1">
              None chosen — the published table shows the layer’s first columns.
            </p>
          </div>
          <label class="block">
            <span class="text-[10px] text-muted-foreground block mb-0.5">A row click filters by</span>
            <select :value="selected.dataSource?.keyField || ''"
              @change="patchWidget(selected.id, { dataSource: { keyField: $event.target.value || null } })"
              class="w-full text-xs bg-background border border-border rounded px-2 py-1 focus:outline-none focus:border-primary/60">
              <option value="">— nothing —</option>
              <option v-for="f in fieldsOf(selected)" :key="f.name" :value="f.name">{{ f.name }}</option>
            </select>
          </label>
          <div class="grid grid-cols-2 gap-2">
            <label class="block">
              <span class="text-[10px] text-muted-foreground block mb-0.5">Shape</span>
              <select :value="selected.style?.layout || 'table'"
                @change="patchWidget(selected.id, { style: { layout: $event.target.value } })"
                class="w-full text-xs bg-background border border-border rounded px-2 py-1 focus:outline-none focus:border-primary/60">
                <option value="table">Table</option>
                <option value="cards">Cards (directory)</option>
              </select>
            </label>
            <label class="block" v-if="(selected.style?.layout || 'table') === 'cards'">
              <span class="text-[10px] text-muted-foreground block mb-0.5">Card heading</span>
              <select :value="selected.dataSource?.titleField || ''"
                @change="patchWidget(selected.id, { dataSource: { titleField: $event.target.value || null } })"
                class="w-full text-xs bg-background border border-border rounded px-2 py-1 focus:outline-none focus:border-primary/60">
                <option value="">— same as the filter column —</option>
                <option v-for="f in fieldsOf(selected)" :key="f.name" :value="f.name">{{ f.name }}</option>
              </select>
            </label>
          </div>
        </template>

        <!-- scatter -->
        <template v-if="selected.type === 'scatter'">
          <div class="grid grid-cols-2 gap-2">
            <label class="block">
              <span class="text-[10px] text-muted-foreground block mb-0.5">X axis</span>
              <select :value="selected.dataSource?.xField || ''"
                @change="patchWidget(selected.id, { dataSource: { xField: $event.target.value || null } })"
                class="w-full text-xs bg-background border border-border rounded px-2 py-1 focus:outline-none focus:border-primary/60">
                <option value="">— pick a column —</option>
                <option v-for="f in numericFields(selected)" :key="f.name" :value="f.name">{{ f.name }}</option>
              </select>
            </label>
            <label class="block">
              <span class="text-[10px] text-muted-foreground block mb-0.5">Y axis</span>
              <select :value="selected.dataSource?.yField || ''"
                @change="patchWidget(selected.id, { dataSource: { yField: $event.target.value || null } })"
                class="w-full text-xs bg-background border border-border rounded px-2 py-1 focus:outline-none focus:border-primary/60">
                <option value="">— pick a column —</option>
                <option v-for="f in numericFields(selected)" :key="f.name" :value="f.name">{{ f.name }}</option>
              </select>
            </label>
          </div>
          <label class="block">
            <span class="text-[10px] text-muted-foreground flex items-center gap-1 mb-0.5">
              Points plotted (sampled)
              <InfoHint label="About sampling">
                Drawn from a random sample, so the shape holds even on a very large layer.
              </InfoHint>
            </span>
            <input type="number" min="50" max="3000" step="50" :value="selected.dataSource?.limit ?? 1500"
              @change="patchWidget(selected.id, { dataSource: { limit: Number($event.target.value) || 1500 } })"
              class="w-full text-xs bg-background border border-border rounded px-2 py-1 focus:outline-none focus:border-primary/60" />
          </label>
        </template>

        <!-- search -->
        <template v-if="selected.type === 'search'">
          <div>
            <span class="text-[10px] text-muted-foreground flex items-center gap-1 mb-1">
              Columns to search
              <InfoHint label="About choosing columns">
                Each column adds to the scan. Two or three named ones stay fast on a big layer.
              </InfoHint>
            </span>
            <div class="flex flex-wrap gap-1 max-h-24 overflow-y-auto">
              <button v-for="f in fieldsOf(selected)" :key="f.name" @click="toggleSearchField(selected, f.name)"
                class="px-2 py-0.5 rounded border text-[11px]"
                :class="(selected.dataSource?.fields || []).includes(f.name)
                  ? 'border-primary text-primary bg-primary/10' : 'border-border text-foreground/70'">{{ f.name }}</button>
            </div>
          </div>
          <div class="grid grid-cols-2 gap-2">
            <label class="block">
              <span class="text-[10px] text-muted-foreground block mb-0.5">Match</span>
              <select :value="selected.dataSource?.searchMode || 'contains'"
                @change="patchWidget(selected.id, { dataSource: { searchMode: $event.target.value } })"
                class="w-full text-xs bg-background border border-border rounded px-2 py-1 focus:outline-none focus:border-primary/60">
                <option value="contains">Anywhere in the value</option>
                <option value="prefix">Starts with (faster)</option>
              </select>
            </label>
            <label class="block">
              <span class="text-[10px] text-muted-foreground block mb-0.5">Results shown</span>
              <input type="number" min="3" max="25" :value="selected.dataSource?.limit ?? 8"
                @change="patchWidget(selected.id, { dataSource: { limit: Number($event.target.value) || 8 } })"
                class="w-full text-xs bg-background border border-border rounded px-2 py-1 focus:outline-none focus:border-primary/60" />
            </label>
          </div>
          <label class="block">
            <span class="text-[10px] text-muted-foreground block mb-0.5">A chosen result filters by</span>
            <select :value="selected.dataSource?.keyField || ''"
              @change="patchWidget(selected.id, { dataSource: { keyField: $event.target.value || null } })"
              class="w-full text-xs bg-background border border-border rounded px-2 py-1 focus:outline-none focus:border-primary/60">
              <option value="">— nothing —</option>
              <option v-for="f in fieldsOf(selected)" :key="f.name" :value="f.name">{{ f.name }}</option>
            </select>
          </label>
        </template>

        <!-- profile -->
        <template v-if="selected.type === 'profile'">
          <div>
            <span class="text-[10px] text-muted-foreground block mb-1">Columns to describe</span>
            <div class="flex flex-wrap gap-1 max-h-24 overflow-y-auto">
              <button v-for="f in fieldsOf(selected)" :key="f.name" @click="toggleProfileField(selected, f.name)"
                class="px-2 py-0.5 rounded border text-[11px]"
                :class="(selected.dataSource?.fields || []).includes(f.name)
                  ? 'border-primary text-primary bg-primary/10' : 'border-border text-foreground/70'">{{ f.name }}</button>
            </div>
            <p v-if="!(selected.dataSource?.fields || []).length" class="text-[10px] text-muted-foreground/70 mt-1">
              Pick at least one — the panel describes only the columns you choose.
            </p>
          </div>
          <label class="block">
            <span class="text-[10px] text-muted-foreground block mb-0.5">Values listed per column</span>
            <input type="number" min="3" max="20" :value="selected.dataSource?.topN ?? 5"
              @change="patchWidget(selected.id, { dataSource: { topN: Number($event.target.value) || 5 } })"
              class="w-full text-xs bg-background border border-border rounded px-2 py-1 focus:outline-none focus:border-primary/60" />
          </label>
        </template>

        <!-- selector -->
        <template v-if="selected.type === 'selector'">
          <div class="grid grid-cols-2 gap-2">
            <label class="block">
              <span class="text-[10px] text-muted-foreground block mb-0.5">Control</span>
              <select :value="selected.dataSource?.kind || 'category'"
                @change="patchWidget(selected.id, { dataSource: { kind: $event.target.value } })"
                class="w-full text-xs bg-background border border-border rounded px-2 py-1 focus:outline-none focus:border-primary/60">
                <option v-for="k in SELECTOR_KINDS" :key="k.id" :value="k.id">{{ k.name }}</option>
              </select>
            </label>
            <label class="block">
              <span class="text-[10px] text-muted-foreground block mb-0.5">Field</span>
              <select :value="selected.dataSource?.field || ''"
                @change="patchWidget(selected.id, { dataSource: { field: $event.target.value } })"
                class="w-full text-xs bg-background border border-border rounded px-2 py-1 focus:outline-none focus:border-primary/60">
                <option value="">—</option>
                <option v-for="f in fieldsOf(selected)" :key="f.name" :value="f.name">{{ f.name }}</option>
              </select>
            </label>
          </div>
          <label v-if="(selected.dataSource?.kind || 'category') === 'category'" class="flex items-center justify-between text-xs">
            <span class="text-muted-foreground flex items-center gap-1">
              Allow several at once
              <InfoHint label="About selectors">
                A selector is a filter source only — it never gets filtered by the other widgets, so
                the control cannot move under the hand using it.
              </InfoHint>
            </span>
            <input type="checkbox" :checked="selected.dataSource?.multi !== false"
              @change="patchWidget(selected.id, { dataSource: { multi: $event.target.checked } })" />
          </label>
        </template>

        <!-- raster statistics -->
        <template v-if="selected.type === 'rasterstats'">
          <div>
            <span class="text-[10px] text-muted-foreground flex items-center gap-1 mb-1">
              Statistics
              <InfoHint label="How raster statistics are driven">
                Listens for the active area selection — a clicked feature, a drawn polygon or a
                dragged box — and reports statistics for it. Wire the map to this widget below.
              </InfoHint>
            </span>
            <div class="flex flex-wrap gap-1">
              <button v-for="s in RASTER_STATS" :key="s" @click="toggleStat(selected, s)"
                class="px-2 py-0.5 rounded border text-[11px] capitalize"
                :class="(selected.dataSource?.stats || []).includes(s)
                  ? 'border-primary text-primary bg-primary/10' : 'border-border text-foreground/70'">{{ s }}</button>
            </div>
          </div>
          <label v-if="bandCount(selected) > 1" class="block">
            <span class="text-[10px] text-muted-foreground block mb-0.5">Band</span>
            <select :value="selected.dataSource?.band || 1"
              @change="patchWidget(selected.id, { dataSource: { band: Number($event.target.value) } })"
              class="w-full text-xs bg-background border border-border rounded px-2 py-1 focus:outline-none focus:border-primary/60">
              <option v-for="n in bandCount(selected)" :key="n" :value="n">Band {{ n }}</option>
            </select>
          </label>
        </template>
      </template>

      <!-- gauge bands -->
      <template v-if="selected.type === 'gauge'">
        <div class="grid grid-cols-2 gap-2">
          <label class="block">
            <span class="text-[10px] text-muted-foreground block mb-0.5">Dial minimum</span>
            <input type="number" :value="selected.style?.min ?? 0"
              @input="patchWidget(selected.id, { style: { min: Number($event.target.value) } })"
              class="w-full text-xs bg-background border border-border rounded px-2 py-1 focus:outline-none focus:border-primary/60" />
          </label>
          <label class="block">
            <span class="text-[10px] text-muted-foreground block mb-0.5">Dial maximum</span>
            <input type="number" :value="selected.style?.max ?? 100"
              @input="patchWidget(selected.id, { style: { max: Number($event.target.value) } })"
              class="w-full text-xs bg-background border border-border rounded px-2 py-1 focus:outline-none focus:border-primary/60" />
          </label>
        </div>
        <div>
          <div class="flex items-center justify-between mb-1">
            <span class="text-[10px] text-muted-foreground">Threshold bands</span>
            <button class="text-[10px] text-primary hover:text-primary/80"
              @click="patchWidget(selected.id, { style: { bands: [...(selected.style?.bands || []), { from: selected.style?.max ?? 100, color: '#16a34a', label: '' }] } })">+ Band</button>
          </div>
          <div v-for="(b, i) in (selected.style?.bands || [])" :key="i" class="flex items-center gap-1.5 mb-1">
            <input type="number" :value="b.from" title="Band starts at"
              @input="patchWidget(selected.id, { style: { bands: (selected.style.bands || []).map((x, j) => j === i ? { ...x, from: Number($event.target.value) } : x) } })"
              class="w-16 text-xs bg-background border border-border rounded px-1.5 py-0.5 focus:outline-none focus:border-primary/60" />
            <input type="color" :value="b.color"
              @input="patchWidget(selected.id, { style: { bands: (selected.style.bands || []).map((x, j) => j === i ? { ...x, color: $event.target.value } : x) } })"
              class="w-7 h-6 rounded border border-border bg-background" />
            <input :value="b.label" placeholder="Label"
              @input="patchWidget(selected.id, { style: { bands: (selected.style.bands || []).map((x, j) => j === i ? { ...x, label: $event.target.value } : x) } })"
              class="flex-1 min-w-0 text-xs bg-background border border-border rounded px-1.5 py-0.5 focus:outline-none focus:border-primary/60" />
            <button class="text-[11px] text-red-400 hover:text-red-500 px-0.5"
              @click="patchWidget(selected.id, { style: { bands: (selected.style.bands || []).filter((x, j) => j !== i) } })">✕</button>
          </div>
        </div>
      </template>

      <!-- indicator: format + target -->
      <template v-if="['indicator', 'gauge', 'rasterstats'].includes(selected.type)">
        <div class="grid grid-cols-3 gap-2">
          <label class="block">
            <span class="text-[10px] text-muted-foreground block mb-0.5">Format</span>
            <select :value="selected.style?.format || 'auto'"
              @change="patchWidget(selected.id, { style: { format: $event.target.value } })"
              class="w-full text-xs bg-background border border-border rounded px-2 py-1 focus:outline-none focus:border-primary/60">
              <option value="auto">Auto</option><option value="integer">Whole</option>
              <option value="decimal">Decimal</option><option value="percent">Percent</option>
              <option value="compact">Compact</option>
            </select>
          </label>
          <label class="block">
            <span class="text-[10px] text-muted-foreground block mb-0.5">Decimals</span>
            <input type="number" min="0" max="6" :value="selected.style?.decimals ?? 1"
              @input="patchWidget(selected.id, { style: { decimals: Number($event.target.value) } })"
              class="w-full text-xs bg-background border border-border rounded px-2 py-1 focus:outline-none focus:border-primary/60" />
          </label>
          <label class="block">
            <span class="text-[10px] text-muted-foreground block mb-0.5">Unit</span>
            <input :value="selected.style?.unit || ''" placeholder="m, %, kg"
              @input="patchWidget(selected.id, { style: { unit: $event.target.value } })"
              class="w-full text-xs bg-background border border-border rounded px-2 py-1 focus:outline-none focus:border-primary/60" />
          </label>
        </div>
      </template>
      <template v-if="selected.type === 'indicator'">
        <div class="grid grid-cols-3 gap-2">
          <label class="block">
            <span class="text-[10px] text-muted-foreground block mb-0.5">Compare to</span>
            <input type="number" :value="selected.style?.target ?? ''" placeholder="none"
              @input="patchWidget(selected.id, { style: { target: $event.target.value === '' ? undefined : Number($event.target.value) } })"
              class="w-full text-xs bg-background border border-border rounded px-2 py-1 focus:outline-none focus:border-primary/60" />
          </label>
          <label class="block">
            <span class="text-[10px] text-muted-foreground block mb-0.5">Show as</span>
            <select :value="selected.style?.compareMode || 'delta'"
              @change="patchWidget(selected.id, { style: { compareMode: $event.target.value } })"
              class="w-full text-xs bg-background border border-border rounded px-2 py-1 focus:outline-none focus:border-primary/60">
              <option value="delta">Difference</option><option value="percent">Percent</option>
              <option value="none">Hide</option>
            </select>
          </label>
          <label class="block">
            <span class="text-[10px] text-muted-foreground block mb-0.5">Good is</span>
            <select :value="selected.style?.goodDirection || 'up'"
              @change="patchWidget(selected.id, { style: { goodDirection: $event.target.value } })"
              class="w-full text-xs bg-background border border-border rounded px-2 py-1 focus:outline-none focus:border-primary/60">
              <option value="up">Higher</option><option value="down">Lower</option><option value="none">Neither</option>
            </select>
          </label>
        </div>
      </template>

      <!-- indicator / gauge click-to-filter -->
      <template v-if="['indicator', 'gauge'].includes(selected.type) && selected.dataSource?.layerId != null">
        <div class="grid grid-cols-2 gap-2">
          <label class="block">
            <span class="text-[10px] text-muted-foreground block mb-0.5">Clicking filters by</span>
            <select :value="selected.dataSource?.filterField || ''"
              @change="patchWidget(selected.id, { dataSource: { filterField: $event.target.value || undefined } })"
              class="w-full text-xs bg-background border border-border rounded px-2 py-1 focus:outline-none focus:border-primary/60">
              <option value="">— not clickable —</option>
              <option v-for="f in fieldsOf(selected)" :key="f.name" :value="f.name">{{ f.name }}</option>
            </select>
          </label>
          <label class="block">
            <span class="text-[10px] text-muted-foreground block mb-0.5">equal to</span>
            <input :value="selected.dataSource?.filterValue ?? ''" placeholder="value"
              @input="patchWidget(selected.id, { dataSource: { filterValue: $event.target.value || undefined } })"
              class="w-full text-xs bg-background border border-border rounded px-2 py-1 focus:outline-none focus:border-primary/60" />
          </label>
        </div>
      </template>

      <!-- ── the wiring ───────────────────────────────────────────────────── -->
      <div class="pt-2 border-t border-border/60">
        <template v-if="canSource(selected)">
          <div class="flex items-center justify-between mb-1">
            <span class="text-[10px] text-muted-foreground uppercase tracking-wider">Interacting with this filters</span>
            <span class="flex gap-2">
              <button class="text-[10px] text-primary hover:text-primary/80" @click="wireAll(selected)">All</button>
              <button class="text-[10px] text-muted-foreground/70 hover:text-foreground" @click="wireNone(selected)">None</button>
            </span>
          </div>
          <p v-if="!targetsFor(selected).length" class="text-[10px] text-muted-foreground/70">
            Nothing to filter yet — add another widget that can listen.
          </p>
          <div v-else class="flex flex-wrap gap-1">
            <button v-for="t in targetsFor(selected)" :key="t.id" @click="toggleWire(selected, t.id)"
              class="px-2 py-0.5 rounded border text-[11px] max-w-full truncate"
              :class="isWired(selected, t.id)
                ? 'border-primary text-primary bg-primary/10' : 'border-border text-foreground/70'"
              :title="TYPE_BY_ID[t.type]?.name">{{ widgetLabel(t) }}</button>
          </div>
        </template>
        <p v-else class="text-[10px] text-muted-foreground/70 leading-snug">
          This widget is a filter target only — it responds to selections but publishes none.
        </p>

        <template v-if="canTarget(selected)">
          <label class="flex items-center justify-between text-xs mt-2">
            <span class="text-muted-foreground">Responds to other widgets</span>
            <input type="checkbox" :checked="selected.actions?.listens !== false"
              @change="patchWidget(selected.id, { actions: { listens: $event.target.checked } })" />
          </label>
          <p v-if="selected.actions?.listens !== false && sourcesOf(selected).length"
            class="text-[10px] text-muted-foreground/70 mt-1 leading-snug">
            Filtered by: {{ sourcesOf(selected).map(widgetLabel).join(', ') }}.
          </p>
          <p v-else-if="selected.actions?.listens !== false"
            class="text-[10px] text-muted-foreground/70 mt-1 leading-snug">
            Nothing filters this yet — wire a source to it from that widget’s panel.
          </p>
        </template>
      </div>
    </div>

    <p v-else-if="widgets.length" class="text-[11px] text-muted-foreground/70">
      Select a widget above to bind its data and choose what it filters.
    </p>

    <!-- ── dashboard-wide settings ────────────────────────────────────────── -->
    <div v-if="widgets.length" class="mt-3 pt-3 border-t border-border/60 grid grid-cols-2 gap-2">
      <label class="block">
        <span class="text-[10px] text-muted-foreground block mb-0.5">Row height (px)</span>
        <input type="number" min="40" max="240" :value="dash.grid?.rowHeight ?? 90"
          @input="patchDash({ grid: { ...(dash.grid || {}), rowHeight: Number($event.target.value) } })"
          class="w-full text-xs bg-background border border-border rounded px-2 py-1 focus:outline-none focus:border-primary/60" />
      </label>
      <label class="block">
        <span class="text-[10px] text-muted-foreground block mb-0.5">Auto-refresh (s, 0 = off)</span>
        <input type="number" min="0" max="3600" :value="dash.refresh || 0"
          @input="patchDash({ refresh: Number($event.target.value) })"
          class="w-full text-xs bg-background border border-border rounded px-2 py-1 focus:outline-none focus:border-primary/60" />
      </label>
    </div>
  </div>
</template>
