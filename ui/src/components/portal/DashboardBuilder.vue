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
  { type: 'selector', name: 'Selector', needs: 'vector', source: true, target: false,
    desc: 'A category, range or date-range control. A filter source only.' },
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
const GRID_COLS = 12

// ── the model ───────────────────────────────────────────────────────────────
const dash = computed(() => props.modelValue || { grid: { rowHeight: 90, gap: 10 }, refresh: 0, widgets: [] })
const widgets = computed(() => dash.value.widgets || [])
const selectedId = ref(null)
const selected = computed(() => widgets.value.find(w => w.id === selectedId.value) || null)

function commit(next) { emit('update:modelValue', next) }
function patchDash(patch) { commit({ ...dash.value, ...patch }) }
function setWidgets(list) { patchDash({ widgets: list }) }
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

function gridStyle(w) {
  const l = w.layout || { x: 0, y: 0, w: 4, h: 3 }
  return {
    gridColumn: `${(l.x || 0) + 1} / span ${l.w || 4}`,
    gridRow: `${(l.y || 0) + 1} / span ${l.h || 3}`,
  }
}
function onPointerDown(ev, w, mode) {
  if (ev.button !== 0) return
  ev.preventDefault()
  selectedId.value = w.id
  drag.value = { id: w.id, mode, startX: ev.clientX, startY: ev.clientY, orig: { ...w.layout } }
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
  if (d.mode === 'move') {
    const x = Math.max(0, Math.min(GRID_COLS - o.w, o.x + dx))
    const y = Math.max(0, o.y + dy)
    patchWidget(d.id, { layout: { x, y } })
  } else {
    const w = Math.max(2, Math.min(GRID_COLS - o.x, o.w + dx))
    const h = Math.max(2, Math.min(24, o.h + dy))
    patchWidget(d.id, { layout: { w, h } })
  }
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
  if (step === 'move') {
    patchWidget(w.id, { layout: {
      x: Math.max(0, Math.min(GRID_COLS - l.w, l.x + d[0])), y: Math.max(0, l.y + d[1]) } })
  } else {
    patchWidget(w.id, { layout: {
      w: Math.max(2, Math.min(GRID_COLS - l.x, l.w + d[0])), h: Math.max(2, Math.min(24, l.h + d[1])) } })
  }
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
  commit({
    grid: preset.grid || { rowHeight: 90, gap: 10 },
    refresh: preset.refresh || 0,
    widgets: bound,
  })
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
  if (p && !widgets.value.length) applyPreset(false)
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
      <div v-for="w in widgets" :key="w.id" :style="gridStyle(w)"
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
    <p v-if="widgets.length" class="text-[10px] text-muted-foreground/60 -mt-2 mb-3">
      Drag to move, pull the corner to resize. Arrow keys nudge; hold shift to resize.
    </p>

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

      <!-- Data source -->
      <template v-if="TYPE_BY_ID[selected.type]?.needs !== 'none'">
        <label class="block">
          <span class="text-[10px] text-muted-foreground block mb-0.5">
            {{ selected.type === 'map' ? 'Selection layer (optional)' : 'Layer' }}
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
            <span class="text-[10px] text-muted-foreground block mb-1">Selection tools</span>
            <div class="flex flex-wrap gap-1">
              <button v-for="t in MAP_TOOLS" :key="t.id" @click="toggleTool(selected, t.id)"
                class="px-2 py-0.5 rounded border text-[11px]"
                :class="(selected.dataSource?.tools || []).includes(t.id)
                  ? 'border-primary text-primary bg-primary/10' : 'border-border text-foreground/70'">{{ t.name }}</button>
            </div>
            <p class="text-[10px] text-muted-foreground/70 leading-snug mt-1">
              All three produce the same kind of filter — a geometry — so raster statistics respond to
              a clicked feature, a drawn polygon and a dragged box alike.
            </p>
          </div>
          <!-- Click radius. In PIXELS, because that is what the visitor is aiming with; the runtime
               converts it to degrees at the click's own zoom and latitude. A point layer needs a few
               pixels of slack or a click never lands on a feature at all. -->
          <label class="block">
            <span class="text-[10px] text-muted-foreground block mb-0.5">
              Click radius — {{ selected.dataSource?.tolPx ?? DEFAULT_TOL_PX }} px
            </span>
            <input type="range" min="0" max="24" step="1"
              :value="selected.dataSource?.tolPx ?? DEFAULT_TOL_PX"
              @input="patchWidget(selected.id, { dataSource: { tolPx: Number($event.target.value) } })"
              class="w-full accent-primary" />
            <span class="text-[10px] text-muted-foreground/70 leading-snug block">
              How near a click has to land. Points and lines need a few pixels; polygons work at 0.
            </span>
          </label>
          <p v-if="vectorLayers.length > 1" class="text-[10px] text-muted-foreground/70 leading-snug">
            A click tries this layer first, then the portal's other vector layers top-down, so every
            layer on the map is selectable. Attribute filters stay scoped to the layer they came
            from — a selector over one layer cannot narrow a widget reading another.
          </p>
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
            <span class="text-muted-foreground">Allow several at once</span>
            <input type="checkbox" :checked="selected.dataSource?.multi !== false"
              @change="patchWidget(selected.id, { dataSource: { multi: $event.target.checked } })" />
          </label>
          <p class="text-[10px] text-muted-foreground/70 leading-snug">
            A selector is a filter source only — it never gets filtered by the other widgets, so the
            control cannot move under the hand using it.
          </p>
        </template>

        <!-- raster statistics -->
        <template v-if="selected.type === 'rasterstats'">
          <div>
            <span class="text-[10px] text-muted-foreground block mb-1">Statistics</span>
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
          <p class="text-[10px] text-muted-foreground/70 leading-snug">
            Listens for the active area selection — a clicked feature, a drawn polygon or a dragged
            box — and reports statistics for it. Wire the map to this widget below.
          </p>
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
