/**
 * Data-driven symbology — the JavaScript twin of `api/geodeploy/services/symbology.py`.
 *
 * A layer's colour used to be one string. It can now be a function of a feature property, and that
 * function must be computed IDENTICALLY in four places (CLAUDE.md's parity rule): the published
 * MapLibre style (Python), the live portal runtime (`templates/shared/portal.js`), the editor
 * preview (`PortalEditor.vue`) and the swatch beside the layer name (`LayerPanel.vue`). The last
 * three are JavaScript and import this file; the first is the Python module, kept line-for-line
 * equivalent and pinned by `api/tests/test_symbology.py`.
 *
 * The failure this exists to prevent is quiet: an editor preview and a published portal disagreeing
 * about which class a feature falls into. Nobody notices until the map is public.
 *
 * WHAT IS DELIBERATELY NOT HERE: `classify()` — the quantile/equal/jenks maths. Class breaks are
 * computed ONLY on the server (`GET /data/vector/{ref}/field-stats`), because the classifier reads
 * the whole column and because two implementations of one decision is exactly the divergence this
 * file is trying to avoid. Changing the class count, method or ramp re-requests the stats; that is
 * one cheap call, and it keeps a single source of truth for the maths.
 */

// Sampled at 7 stops and interpolated to whatever class count is asked for. Perceptually-uniform
// ramps first: equal steps in value look like equal steps in colour, which is what a graduated
// legend claims. MUST match RAMPS in symbology.py.
export const RAMPS = {
  viridis: ['#440154', '#46327e', '#365c8d', '#277f8e', '#1fa187', '#4ac16d', '#fde725'],
  magma: ['#000004', '#3b0f70', '#8c2981', '#de4968', '#fe9f6d', '#fecf92', '#fcfdbf'],
  blues: ['#f7fbff', '#deebf7', '#c6dbef', '#9ecae1', '#6baed6', '#3182bd', '#08519c'],
  reds: ['#fff5f0', '#fee0d2', '#fcbba1', '#fc9272', '#fb6a4a', '#de2d26', '#a50f15'],
  greens: ['#f7fcf5', '#e5f5e0', '#c7e9c0', '#a1d99b', '#74c476', '#31a354', '#006d2c'],
  oranges: ['#fff5eb', '#fee6ce', '#fdd0a2', '#fdae6b', '#fd8d3c', '#e6550d', '#a63603'],
  rdbu: ['#b2182b', '#ef8a62', '#fddbc7', '#f7f7f7', '#d1e5f0', '#67a9cf', '#2166ac'],
  brbg: ['#8c510a', '#d8b365', '#f6e8c3', '#f5f5f5', '#c7eae5', '#5ab4ac', '#01665e'],
  spectral: ['#d53e4f', '#fc8d59', '#fee08b', '#ffffbf', '#e6f598', '#99d594', '#3288bd'],
}

// Ramps with a meaningful midpoint — offered separately in the picker, because using one for data
// that has no midpoint invents a "neutral" value the data does not have.
export const DIVERGING = ['rdbu', 'brbg', 'spectral']

// QUALITATIVE, for categories. A sequential ramp on unordered values implies a ranking that is not
// in the data — the most common misleading map there is.
export const CATEGORY_COLORS = [
  '#3b82f6', '#ef4444', '#22c55e', '#f59e0b', '#a855f7', '#06b6d4',
  '#ec4899', '#84cc16', '#f97316', '#6366f1', '#14b8a6', '#eab308',
]

export const DEFAULT_COLOR = '#3b82f6'
export const DEFAULT_OTHER_COLOR = '#9ca3af'

/** `count` colours sampled evenly from a named ramp. Nearest-stop, matching ramp_colors(). */
export function rampColors(name, count) {
  const stops = RAMPS[name] || RAMPS.viridis
  if (count <= 0) return []
  if (count === 1) return [stops[Math.floor(stops.length / 2)]]
  const out = []
  for (let i = 0; i < count; i++) {
    const pos = (i * (stops.length - 1)) / (count - 1)
    out.push(stops[Math.round(pos)])
  }
  return out
}

/**
 * The MapLibre value for a colour paint property: a string, or a data-driven expression.
 *
 * Falls back to the plain colour whenever the configuration is incomplete. Styling is edited LIVE,
 * so every intermediate state (mode chosen, field not yet picked) is something someone is looking
 * at — it must render the layer, never blank it.
 */
export function colorExpression(style = {}) {
  const base = style.color || DEFAULT_COLOR
  const mode = style.color_mode || 'single'
  const field = (style.color_field || '').trim()
  if (mode === 'single' || !field) return base

  if (mode === 'graduated') {
    const classes = (style.classes || []).filter((c) => c && c.color)
    if (!classes.length) return base
    const expr = ['step', ['to-number', ['get', field]], classes[0].color]
    for (const c of classes.slice(1)) {
      if (c.min === null || c.min === undefined) continue
      expr.push(c.min, c.color)
    }
    return expr.length < 5 ? base : expr
  }

  if (mode === 'categorized') {
    const cats = (style.categories || []).filter((c) => c && c.color)
    if (!cats.length) return base
    const expr = ['match', ['to-string', ['get', field]]]
    // String labels: the input is coerced with to-string, so a numeric category would otherwise
    // never equal its own label and every feature would fall to the fallback colour.
    for (const c of cats) expr.push(String(c.value), c.color)
    expr.push(style.other_color || DEFAULT_OTHER_COLOR)
    return expr.length > 3 ? expr : base
  }
  return base
}

/** Radius (points) or width (lines): a number, or an `interpolate` over `size_field`. */
export function sizeExpression(style = {}, fallback) {
  if ((style.size_mode || 'fixed') !== 'proportional') return fallback
  const field = (style.size_field || '').trim()
  const stops = (style.size_stops || []).filter((s) => Array.isArray(s) && s.length === 2)
  if (!field || stops.length < 2) return fallback
  const ordered = [...stops].sort((a, b) => a[0] - b[0])
  const expr = ['interpolate', ['linear'], ['to-number', ['get', field]]]
  let last = null
  for (const [value, size] of ordered) {
    if (last !== null && value <= last) continue   // MapLibre requires ascending stop inputs
    expr.push(value, size)
    last = value
  }
  return expr.length >= 7 ? expr : fallback
}

/** `fill-extrusion` paint for attribute-driven 3D. Mirrors extrusion_paint(). */
export function extrusionPaint(style = {}, opacity = 1) {
  const ex = style.extrusion || {}
  const field = ex.field || ''
  const scale = ex.scale || 1
  // Explicit `0` fallback inside to-number: one row with a null or text height must not make
  // MapLibre discard the expression and flatten the whole layer.
  const height = field ? ['*', ['to-number', ['get', String(field)], 0], scale] : (ex.height || 0)
  const base = ex.base || 0
  return {
    'fill-extrusion-color': ex.color || colorExpression(style),
    'fill-extrusion-height': height,
    'fill-extrusion-base': (typeof base === 'string' && base)
      ? ['*', ['to-number', ['get', String(base)], 0], scale] : base,
    'fill-extrusion-opacity': opacity * (ex.opacity ?? 1),
    // A flat wash of one colour reads as a shapeless blob at any pitch; the vertical gradient is
    // what makes individual volumes legible.
    'fill-extrusion-vertical-gradient': true,
  }
}

export function isExtruded(style = {}) {
  const ex = style.extrusion || {}
  return !!ex.enabled && !!(ex.field || ex.height)
}

/** True when colour or size varies per feature. */
export function isDataDriven(style = {}) {
  const mode = style.color_mode || 'single'
  if (mode === 'graduated' && style.color_field && (style.classes || []).length) return true
  if (mode === 'categorized' && style.color_field && (style.categories || []).length) return true
  return (style.size_mode || 'fixed') === 'proportional'
    && !!style.size_field && (style.size_stops || []).length >= 2
}

// ── Point markers ────────────────────────────────────────────────────────────
// Points draw as a `symbol` layer with a generated canvas icon, which is what gives them SHAPES on
// any basemap. One bitmap cannot be recoloured per feature — but `icon-image` is itself data-driven
// in MapLibre, so a classified layer emits one image PER CLASS and picks between them with the same
// step/match the colour uses. Shapes and per-feature colour, both. (An SDF icon + `icon-color` was
// the alternative: MapLibre reads the alpha channel as a distance field, so a plain shape mask
// renders with mushy edges — worse output for more code.)
//
// The image ID CARRIES its parameters so any renderer can generate a missing one from the id alone,
// which matters now that a layer needs N images rather than one.

export const NO_OUTLINE = 'none'
export const DEFAULT_MARKER_OUTLINE = '#ffffff'

/** `[colour, widthRatio]` for a marker outline; colour null means draw none. Twin of marker_outline(). */
export function markerOutline(style = {}) {
  const raw = style.outline_color === undefined ? DEFAULT_MARKER_OUTLINE : style.outline_color
  const color = (raw === NO_OUTLINE || raw === '' || raw === false || raw === null)
    ? null : (raw || DEFAULT_MARKER_OUTLINE)
  let ratio = Number(style.outline_width)
  if (!Number.isFinite(ratio)) ratio = 0.28
  return [color, Math.min(Math.max(ratio, 0), 1)]
}

const _px = (v, d = 5) => {
  let n = Number(v == null ? d : v)
  if (!Number.isFinite(n)) n = d
  return Math.round(n * 100) / 100
}

/** Every parameter that changes the PIXELS is in the id — see the note above. */
export function markerImageId(shape, color, size, outline = DEFAULT_MARKER_OUTLINE, outlineWidth = 0.28) {
  const hexish = String(color || DEFAULT_COLOR).replace(/^#/, '').toLowerCase() || '3b82f6'
  const ohex = !outline ? 'none' : String(outline).replace(/^#/, '').toLowerCase()
  return `gd-pt-${shape || 'circle'}-${hexish}-${_px(size)}-${ohex}-${_px(outlineWidth, 0.28)}`
}

/** Parse an id back into its parameters — how `styleimagemissing` builds one on demand. */
export function parseMarkerImageId(id) {
  // The outline pair is optional: an id baked into a portal published before outlines were
  // configurable still parses, and then draws with the old white stroke — which is how it looked.
  const m = /^gd-pt-([a-z]+)-([0-9a-f]{3,8})-([0-9.]+)(?:-(none|[0-9a-f]{3,8})-([0-9.]+))?$/
    .exec(String(id || ''))
  if (!m) return null
  return {
    shape: m[1], color: '#' + m[2], size: parseFloat(m[3]),
    outline: m[4] === undefined ? undefined : (m[4] === 'none' ? null : '#' + m[4]),
    outlineWidth: m[5] === undefined ? undefined : parseFloat(m[5]),
  }
}

/** `icon-image`: one id, or a data-driven choice. Mirrors colorExpression stop for stop. */
export function iconImageExpression(style = {}) {
  const shape = style.marker || 'circle'
  const size = style.radius ?? 5
  const [ol, ow] = markerOutline(style)
  const base = markerImageId(shape, style.color || DEFAULT_COLOR, size, ol, ow)
  const mode = style.color_mode || 'single'
  const field = (style.color_field || '').trim()
  if (mode === 'single' || !field) return base

  if (mode === 'graduated') {
    const classes = (style.classes || []).filter((c) => c && c.color)
    if (!classes.length) return base
    const expr = ['step', ['to-number', ['get', field]], markerImageId(shape, classes[0].color, size, ol, ow)]
    for (const c of classes.slice(1)) {
      if (c.min === null || c.min === undefined) continue
      expr.push(c.min, markerImageId(shape, c.color, size, ol, ow))
    }
    return expr.length < 5 ? base : expr
  }
  if (mode === 'categorized') {
    const cats = (style.categories || []).filter((c) => c && c.color)
    if (!cats.length) return base
    const expr = ['match', ['to-string', ['get', field]]]
    for (const c of cats) expr.push(String(c.value), markerImageId(shape, c.color, size, ol, ow))
    expr.push(markerImageId(shape, style.other_color || DEFAULT_OTHER_COLOR, size, ol, ow))
    return expr.length > 3 ? expr : base
  }
  return base
}

/** Every marker bitmap this style needs: [{id, shape, color, size}]. */
export function markerImages(style = {}) {
  const shape = style.marker || 'circle'
  const size = style.radius ?? 5
  const [ol, ow] = markerOutline(style)
  const colors = [style.color || DEFAULT_COLOR]
  const mode = style.color_mode || 'single'
  if (mode === 'graduated' && style.color_field) {
    colors.push(...(style.classes || []).filter((c) => c.color).map((c) => c.color))
  } else if (mode === 'categorized' && style.color_field) {
    colors.push(...(style.categories || []).filter((c) => c.color).map((c) => c.color))
    colors.push(style.other_color || DEFAULT_OTHER_COLOR)
  }
  const seen = new Set()
  const out = []
  for (const c of colors) {
    const id = markerImageId(shape, c, size, ol, ow)
    if (seen.has(id)) continue
    seen.add(id)
    out.push({ id, shape, color: c, size, outline: ol, outline_width: ow })
  }
  return out
}

/** `icon-size`: a multiplier of the bitmap's natural size (drawn at the layer's base radius). */
export function iconSizeExpression(style = {}) {
  if ((style.size_mode || 'fixed') !== 'proportional') return 1
  const base = Number(style.radius ?? 5) || 5
  const expr = sizeExpression(style, null)
  return expr === null ? 1 : ['/', expr, base]
}

/** What a legend should show: [{color, label}], or [] for a single symbol. */
export function legendEntries(style = {}) {
  const mode = style.color_mode || 'single'
  if (mode === 'graduated') {
    return (style.classes || []).map((c) => {
      const lo = c.min, hi = c.max
      let label
      if ((lo === null || lo === undefined) && (hi === null || hi === undefined)) label = 'all'
      else if (lo === null || lo === undefined) label = `< ${fmtNum(hi)}`
      else if (hi === null || hi === undefined) label = `≥ ${fmtNum(lo)}`
      else label = `${fmtNum(lo)} – ${fmtNum(hi)}`
      return { color: c.color, label }
    })
  }
  if (mode === 'categorized') {
    const out = (style.categories || []).map((c) => ({ color: c.color, label: String(c.value) }))
    // The `match` expression has a fallback colour, so the legend must explain it — otherwise every
    // unlisted value is drawn in a colour the legend does not mention.
    if (out.length) out.push({ color: style.other_color || DEFAULT_OTHER_COLOR, label: 'Other' })
    return out
  }
  return []
}

/** Legend numbers: trim a float that is really an integer, keep the rest short. Mirrors _num(). */
export function fmtNum(v) {
  const f = Number(v)
  if (!Number.isFinite(f)) return String(v)
  if (Number.isInteger(f) && Math.abs(f) < 1e15) return String(f)
  return f.toLocaleString(undefined, { maximumFractionDigits: 2 })
}

/**
 * The single colour that best REPRESENTS a layer — for the swatch beside its name, where one
 * pixel-sized square has to stand for a whole classification. The middle class rather than the
 * first: with a sequential ramp the first class is nearly white on most ramps, and a white swatch
 * reads as "no style".
 */
export function representativeColor(style = {}) {
  const entries = legendEntries(style)
  if (entries.length) return entries[Math.floor(entries.length / 2)].color
  return style.color || DEFAULT_COLOR
}

// ── 3D bars: defaults that depend on the DATA ────────────────────────────────
// Twin of `symbology.extent_metres` / `pillar_radius` (Python). A point has no size of its own, so
// both the width of a bar and the height of one come entirely from defaults — and a fixed default
// is wrong at every scale but one. 240 country centroids with the old 30 m default drew bars about
// three thousandths of a pixel wide: rendered exactly as asked, and indistinguishable from "3D does
// not work". Deriving from the layer's own extent means ticking the box shows something.

/** Rough diagonal of a lon/lat bbox in metres, or null. Approximate on purpose — it picks a
 *  symbol size, and a geodesic would not change anything a viewer can see. */
export function extentMetres(bbox) {
  let b = bbox
  if (typeof b === 'string') { try { b = JSON.parse(b) } catch { return null } }
  if (!Array.isArray(b) || b.length < 4) return null
  const [w, s, e, n] = b.map(Number)
  // Must actually BE lon/lat — a projected bbox would read as millions of degrees and clamp the
  // symbol to its maximum size. Parity: symbology.extent_metres.
  if (![w, s, e, n].every(Number.isFinite)) return null
  if (!(e > w && n > s && w >= -180 && e <= 180 && s >= -90 && n <= 90)) return null
  const mid = ((n + s) / 2) * Math.PI / 180
  const dx = (e - w) * 111320 * Math.max(Math.cos(mid), 0.05)
  const dy = (n - s) * 110540
  const d = Math.hypot(dx, dy)
  return d > 0 ? d : null
}

export const PILLAR_RADIUS_FRACTION = 400
export const DEFAULT_PILLAR_RADIUS_M = 30

/** The bar footprint radius in metres: the author's if they set one, else from the extent. */
export function pillarRadius(style = {}, bbox = null) {
  const raw = (style.extrusion || {}).radius
  if (raw !== null && raw !== undefined && raw !== '') {
    const r = Number(raw)
    if (Number.isFinite(r)) return Math.min(Math.max(r, 0.5), 100000)
  }
  const d = extentMetres(bbox)
  if (d) return Math.min(Math.max(d / PILLAR_RADIUS_FRACTION, 5), 100000)
  return DEFAULT_PILLAR_RADIUS_M
}
