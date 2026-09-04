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

/**
 * `count` colours sampled evenly from a named ramp. Twin of `symbology.ramp_colors`.
 *
 * INTERPOLATED between the anchor stops, not snapped to the nearest one. Snapping was the earlier
 * behaviour and it had a ceiling nobody had written down: the ramps are seven stops, so eight
 * classes produced seven colours and twelve produced seven — two classes drawn identically in a
 * legend that says they differ. That is what the old 12-class cap was really working around.
 */
export function rampColors(name, count, reverse = false) {
  const stops = RAMPS[name] || RAMPS.viridis
  if (count <= 0) return []
  if (count === 1) return [stops[Math.floor(stops.length / 2)]]
  const out = []
  for (let i = 0; i < count; i++) {
    const pos = (i * (stops.length - 1)) / (count - 1)
    const lo = Math.floor(pos)
    const hi = Math.min(lo + 1, stops.length - 1)
    out.push(blend(stops[lo], stops[hi], pos - lo))
  }
  // Reverse the sampled OUTPUT, not the stop list — the twin of symbology.ramp_colors, which must
  // produce the identical array or the editor preview and the published portal disagree about which
  // class is which colour.
  return reverse ? out.reverse() : out
}

/**
 * `a` and `b` mixed in RGB, `t` from 0 to 1. Twin of `symbology._blend`.
 *
 * `Math.floor(x + 0.5)`, not Math.round(): Python's round() is half-to-EVEN and JavaScript's is
 * half-UP, so the two would land on a different byte wherever a channel came out on .5. Half-up
 * written this way is identical in both languages.
 */
function blend(a, b, t) {
  if (t <= 0) return a
  if (t >= 1) return b
  const ch = (s, i) => parseInt(s.slice(1 + i * 2, 3 + i * 2), 16)
  const hex = (n) => Math.floor(n + 0.5).toString(16).padStart(2, '0')
  return '#' + [0, 1, 2].map(i => hex(ch(a, i) + (ch(b, i) - ch(a, i)) * t)).join('')
}

//: The reciprocal golden ratio — see `symbology._GOLDEN`.
const GOLDEN = 0.6180339887498949

/**
 * The colour for the `index`-th category, for as many categories as a layer has.
 * Twin of `symbology.category_color`.
 *
 * The twelve hand-picked colours first; past those the hue wheel takes over rather than cycling,
 * which would draw two categories identically. Deterministic, so a category keeps its colour when
 * the data gains a value.
 */
export function categoryColor(index) {
  if (index < CATEGORY_COLORS.length) return CATEGORY_COLORS[index]
  const n = index - CATEGORY_COLORS.length
  const hue = (n * GOLDEN) % 1.0
  const sat = 0.58 + 0.16 * (n % 2)
  const light = 0.42 + 0.14 * (Math.floor(n / 2) % 3)
  return hslHex(hue, sat, light)
}

/** HSL (0-1) to `#rrggbb`. Twin of `symbology._hsl_hex` — the same arithmetic, deliberately. */
function hslHex(h, s, l) {
  const channel = (p, q, t0) => {
    let t = t0 % 1.0
    if (t < 0) t += 1.0
    if (t < 1 / 6) return p + (q - p) * 6 * t
    if (t < 1 / 2) return q
    if (t < 2 / 3) return p + (q - p) * (2 / 3 - t) * 6
    return p
  }
  const q = l < 0.5 ? l * (1 + s) : l + s - l * s
  const p = 2 * l - q
  const hex = (n) => Math.floor(n * 255 + 0.5).toString(16).padStart(2, '0')
  return '#' + hex(channel(p, q, h + 1 / 3)) + hex(channel(p, q, h)) + hex(channel(p, q, h - 1 / 3))
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

/** A POLYGON's outline width in CSS px. Twin of services/symbology.POLYGON_OUTLINE_WIDTH.
 *
 * 1 is what a MapLibre `fill` already draws: a fill strokes its own edge at a fixed hairline and
 * `fill-outline-color` has no width, so every polygon published before this had a 1 px outline.
 * Keeping the default there means those keep rendering exactly as they did.
 *
 * NOTE the key does double duty: on a POINT `outline_width` is a RATIO of the marker radius (see
 * markerOutline), on a POLYGON it is pixels. The two are never read by the same code path. */
export const POLYGON_OUTLINE_WIDTH = 1

export function polygonOutlineWidth(style = {}) {
  const w = Number(style.outline_width)
  if (!Number.isFinite(w)) return POLYGON_OUTLINE_WIDTH
  return Math.min(Math.max(w, 0), 40)
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
/**
 * The id for a marker PICTURE. Twin of `symbology.picture_id` — FNV-1a, because an id computed
 * differently here would have the preview asking for an image the portal never registers.
 */
export function pictureId (dataUri) {
  let h = 0x811c9dc5
  const s = String(dataUri || '')
  for (let i = 0; i < s.length; i++) {
    h = Math.imul(h ^ (s.charCodeAt(i) & 0xff), 0x01000193) >>> 0
  }
  return 'gd-img-' + h.toString(16).padStart(8, '0')
}

/** The marker bitmap this style carries, or null. Twin of `symbology.marker_picture`. */
export function markerPicture (style = {}) {
  const uri = style.marker_image
  return (typeof uri === 'string' && uri.startsWith('data:image/')) ? uri : null
}

export function iconImageExpression(style = {}) {
  // A PICTURE WINS OVER A SHAPE, and it is ONE image for every feature: a raster icon cannot be
  // recoloured per class the way a generated shape can. Mirrors symbology.icon_image_expression.
  const picture = markerPicture(style)
  if (picture) return pictureId(picture)

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
  // A FILL TILE is an image this style needs, so it belongs here — this list is the runtime's one
  // channel for "create these images". A fill layer is not a symbol layer, so the tile is created
  // through `styleimagemissing`, which can only find it if the id is in a layer's specs.
  // Mirrors symbology.marker_images.
  const tile = fillPattern(style)
  if (Object.keys(tile).length) return [{ id: pictureId(tile.image), image: tile.image }]

  const picture = markerPicture(style)
  // One entry, carrying the PIXELS — the runtime registers it from the data URI instead of drawing
  // a shape. Mirrors symbology.marker_images.
  if (picture) return [{ id: pictureId(picture), image: picture }]

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
// Per-ENTRY symbology a swatch can draw. A classified layer varies only by colour, so a coloured
// square sufficed — but a rule-based one varies by everything at once (one rule dashed, another
// with a marker, a third hatched), and a row of squares reports none of it. Mirrors
// services/symbology.py::_LEGEND_SYMBOL_KEYS.
const LEGEND_SYMBOL_KEYS = ['dash', 'shape', 'marker_image', 'fill_pattern', 'line_width',
  'outline_color', 'outline_width', 'fill_opacity']

function legendSymbol(style = {}) {
  const out = {}
  for (const key of LEGEND_SYMBOL_KEYS) {
    const value = style[key]
    if (value === null || value === undefined || value === '') continue
    if (key === 'fill_pattern') {
      const image = value && typeof value === 'object' ? value.image : null
      if (typeof image === 'string' && image.startsWith('data:')) out.fill_pattern = image
      continue
    }
    if (key === 'marker_image' && !String(value).startsWith('data:')) continue
    out[key] = value
  }
  return out
}

export function legendEntries(style = {}) {
  // A HEATMAP IS NOT A SET OF CLASSES — it has a ramp, and density runs 0-1 whatever the data
  // holds. Returning [] here made a heatmap layer show the classes of the symbology it replaced,
  // which is a legend for a map nobody is looking at.
  const heat = style.heatmap || {}
  if (heat.enabled) {
    const ramp = (heat.ramp || []).filter(c => typeof c === 'string')
    return [{ color: ramp[ramp.length - 1], label: 'Density', ramp, heatmap: true }]
  }
  // RULES BEFORE CLASSES, the same precedence the renderers use: a rule-based style's `color_mode`
  // is only a fallback for viewers that know nothing about rules.
  if (Array.isArray(style.rules) && style.rules.length) {
    return style.rules.filter(r => r && typeof r === 'object').map((rule, i) => {
      const rstyle = (rule.style && typeof rule.style === 'object') ? rule.style : {}
      return {
        color: rstyle.color || style.color || DEFAULT_COLOR,
        label: String(rule.label || rule.expression || `Rule ${i + 1}`),
        rule: true,
        ...legendSymbol({ ...style, ...rstyle }),
      }
    })
  }
  const mode = style.color_mode || 'single'
  if (mode === 'graduated') {
    const shared = legendSymbol(style)
    return (style.classes || []).map((c) => {
      const lo = c.min, hi = c.max
      let label
      if ((lo === null || lo === undefined) && (hi === null || hi === undefined)) label = 'all'
      else if (lo === null || lo === undefined) label = `< ${fmtNum(hi)}`
      else if (hi === null || hi === undefined) label = `≥ ${fmtNum(lo)}`
      else label = `${fmtNum(lo)} – ${fmtNum(hi)}`
      return { color: c.color, label, ...shared }
    })
  }
  if (mode === 'categorized') {
    const shared = legendSymbol(style)
    const out = (style.categories || []).map((c) => ({
      color: c.color, label: String(c.value), ...shared }))
    // The `match` expression has a fallback colour, so the legend must explain it — otherwise every
    // unlisted value is drawn in a colour the legend does not mention.
    if (out.length) {
      out.push({ color: style.other_color || DEFAULT_OTHER_COLOR, label: 'Other', ...shared })
    }
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

// ── Line decoration, marker placement, and layer scope ───────────────────────────────────────────
// Twins of `services/symbology.py`'s block of the same name (2026-09-03). Every one of these is
// something MapLibre draws natively and GeoDeploy simply had no word for, so each is an EXACT round
// trip from QGIS rather than an approximation.

/** `lineType` as a dash array, in MULTIPLES OF THE LINE WIDTH — MapLibre's own unit. */
export const LINE_TYPE_DASHES = { dashed: [2, 1.5], dotted: [0.4, 1.8] }

const LINE_CAPS = ['butt', 'round', 'square']
const LINE_JOINS = ['bevel', 'round', 'miter']

/**
 * The `line-dasharray` for a style, or null for a solid line. Twin of `symbology.dash_array`.
 *
 * An explicit `dash_pattern` wins over `lineType`: the named types are the two presets a web map
 * always had, and a pattern read out of QGIS is the real thing the author drew. Stored in line-width
 * multiples, not pixels — a pattern in absolute units would change shape with the width.
 */
export function dashArray(style = {}) {
  const pattern = style.dash_pattern
  if (Array.isArray(pattern) && pattern.length >= 2) {
    const out = []
    for (const v of pattern) {
      const n = Number(v)
      if (!Number.isFinite(n)) return null
      out.push(Math.round(n * 10000) / 10000)
    }
    // MapLibre wants pairs; an odd-length pattern repeats inverted, which is not what QGIS drew.
    return out.length % 2 === 0 ? out : out.concat([out[out.length - 1]])
  }
  return LINE_TYPE_DASHES[style.lineType] || null
}

/** `line-cap` / `line-join` — LAYOUT properties. Twin of `symbology.line_layout`. */
export function lineLayout(style = {}) {
  const out = {}
  const cap = String(style.line_cap || '').toLowerCase()
  const join = String(style.line_join || '').toLowerCase()
  if (LINE_CAPS.includes(cap)) out['line-cap'] = cap
  if (LINE_JOINS.includes(join)) out['line-join'] = join
  return out
}

/** `line-offset` in pixels, or null. Twin of `symbology.line_offset`. */
export function lineOffset(style = {}) {
  const n = Number(style.line_offset)
  if (!Number.isFinite(n) || !n) return null
  return Math.round(n * 1000) / 1000
}

/** `icon-rotate` and `icon-offset`. Twin of `symbology.marker_layout`. */
export function markerLayout(style = {}) {
  const out = {}
  const rot = Number(style.marker_rotation)
  if (Number.isFinite(rot) && rot) out['icon-rotate'] = Math.round(((rot % 360) + 360) % 360 * 1000) / 1000
  const off = style.marker_offset
  if (Array.isArray(off) && off.length === 2) {
    const x = Number(off[0]); const y = Number(off[1])
    if (Number.isFinite(x) && Number.isFinite(y)) {
      out['icon-offset'] = [Math.round(x * 1000) / 1000, Math.round(y * 1000) / 1000]
    }
  }
  return out
}

/** The layer's opacity times the marker's own. Twin of `symbology.marker_opacity`. */
export function markerOpacity(style = {}, opacity = 1) {
  const own = Number(style.marker_opacity)
  if (!Number.isFinite(own)) return opacity
  return Math.round(opacity * Math.max(0, Math.min(1, own)) * 10000) / 10000
}

/**
 * `minzoom` / `maxzoom` / `filter` that belong to the LAYER rather than to one render layer.
 * Twin of `symbology.layer_scope`.
 *
 * Clamped to MapLibre's 0-24: QGIS stores scale thresholds far outside it, and one out-of-range
 * number makes MapLibre reject the WHOLE style rather than ignore it.
 */
export function layerScope(style = {}) {
  const out = {}
  for (const key of ['minzoom', 'maxzoom']) {
    const raw = Number(style[key])
    if (!Number.isFinite(raw)) continue
    const z = Math.max(0, Math.min(24, raw))
    if ((key === 'minzoom' && z > 0) || (key === 'maxzoom' && z < 24)) out[key] = Math.round(z * 1000) / 1000
  }
  if (style.filter != null) out.filter = style.filter
  return out
}

/** Two MapLibre filters ANDed, flattened. Twin of `symbology.combined_filter`. */
export function combinedFilter(a, b) {
  if (a == null) return b
  if (b == null) return a
  const parts = []
  for (const node of [a, b]) {
    if (Array.isArray(node) && node[0] === 'all') parts.push(...node.slice(1))
    else parts.push(node)
  }
  return ['all', ...parts]
}

/** QGIS's "No symbols" renderer: listed and legible, draws nothing. Twin of `draws_nothing`. */
export function drawsNothing(style = {}) {
  return Boolean(style.no_symbol)
}

// ── Labels ───────────────────────────────────────────────────────────────────────────────────────
// Twins of `services/symbology.py`'s label block. A label is a second thing drawn for the same
// feature — its own colour, size and zoom range — so it becomes its own MapLibre `symbol` layer.

/**
 * The faces SHIPPED with GeoDeploy — not a limit. `templates/shared/fonts/` is a drop-in directory
 * and `/api/fonts` lists whatever is installed, so a UI that wants the real list should ask.
 */
export const LABEL_FONTS = ['Noto Sans Regular', 'Noto Sans Bold', 'Noto Sans Italic']

/**
 * A `text-font` STACK — the face asked for, then the one always shipped. Twin of
 * `symbology.font_stack`.
 *
 * MapLibre reads `text-font` as a preference list and uses the first face its glyphs have, so this
 * draws an installed face when there is one and still draws the label when there is not. Rewriting
 * an unknown face to the fallback here instead would discard a font the operator had installed —
 * only the server knows what is there.
 */
export function fontStack (font) {
  const name = String(font || '').trim()
  if (!name || name === DEFAULT_LABEL_FONT) return [DEFAULT_LABEL_FONT]
  return [name, DEFAULT_LABEL_FONT]
}
export const DEFAULT_LABEL_FONT = 'Noto Sans Regular'
export const DEFAULT_LABEL_SIZE = 12
export const DEFAULT_LABEL_COLOR = '#333333'
export const DEFAULT_LABEL_HALO = '#ffffff'

const LABEL_ANCHORS = ['center', 'left', 'right', 'top', 'bottom',
  'top-left', 'top-right', 'bottom-left', 'bottom-right']
const LABEL_TRANSFORMS = ['none', 'uppercase', 'lowercase']

const lnum = (v, d) => { const n = Number(v); return Number.isFinite(n) ? n : d }

/** The label block when a layer is labelled, else `{}`. Twin of `symbology.labels_of`. */
export function labelsOf(style = {}) {
  const l = style.labels
  if (!l || typeof l !== 'object' || !l.enabled) return {}
  return (l.field || l.expression != null) ? l : {}
}

/** The `text-field`. An expression wins over a field name. Twin of `symbology.label_text`. */
export function labelText(labels = {}) {
  if (labels.expression != null) return labels.expression
  return ['to-string', ['get', String(labels.field)]]
}

/** The `layout` half of a label layer. Twin of `symbology.label_layout`. */
export function labelLayout(labels = {}) {
  const size = lnum(labels.size, DEFAULT_LABEL_SIZE)
  const out = {
    'text-field': labelText(labels),
    'text-font': fontStack(labels.font),
    'text-size': size,
    'text-allow-overlap': Boolean(labels.allow_overlap),
  }
  if (String(labels.placement || 'point').toLowerCase() === 'line') out['symbol-placement'] = 'line'
  const anchor = String(labels.anchor || '').toLowerCase()
  if (LABEL_ANCHORS.includes(anchor)) out['text-anchor'] = anchor
  const off = labels.offset
  if (Array.isArray(off) && off.length === 2 && size) {
    const x = Number(off[0]); const y = Number(off[1])
    // `text-offset` is in EMS and GeoDeploy states offsets in pixels — divide by the text size.
    if (Number.isFinite(x) && Number.isFinite(y)) {
      out['text-offset'] = [Math.round(x / size * 1000) / 1000, Math.round(y / size * 1000) / 1000]
    }
  }
  const rot = lnum(labels.rotation, null)
  if (rot) out['text-rotate'] = Math.round(((rot % 360) + 360) % 360 * 1000) / 1000
  const width = lnum(labels.max_width, null)
  if (width && width > 0) out['text-max-width'] = Math.round(width * 100) / 100
  const transform = String(labels.transform || '').toLowerCase()
  if (LABEL_TRANSFORMS.includes(transform) && transform !== 'none') out['text-transform'] = transform
  const spacing = lnum(labels.letter_spacing, null)
  if (spacing) out['text-letter-spacing'] = Math.round(spacing * 1000) / 1000
  const priority = lnum(labels.priority, null)
  // QGIS priority runs 0-10, higher = more important; MapLibre places LOWER sort keys first, and
  // what is placed first wins the space. So the scale is inverted.
  if (priority != null) out['symbol-sort-key'] = Math.round(-priority * 1000) / 1000
  return out
}

/** The `paint` half. Twin of `symbology.label_paint`. */
export function labelPaint(labels = {}, opacity = 1) {
  const out = { 'text-color': labels.color || DEFAULT_LABEL_COLOR, 'text-opacity': opacity }
  const halo = lnum(labels.halo_width, 0)
  if (halo && halo > 0) {
    out['text-halo-color'] = labels.halo_color || DEFAULT_LABEL_HALO
    out['text-halo-width'] = Math.round(halo * 100) / 100
  }
  return out
}

/** A label's OWN zoom range, which QGIS keeps separately. Twin of `symbology.label_scope`. */
export function labelScope(labels = {}) {
  const out = {}
  for (const key of ['minzoom', 'maxzoom']) {
    const raw = lnum(labels[key], null)
    if (raw == null) continue
    const z = Math.max(0, Math.min(24, raw))
    if ((key === 'minzoom' && z > 0) || (key === 'maxzoom' && z < 24)) out[key] = Math.round(z * 1000) / 1000
  }
  return out
}


// ── Markers along a line ─────────────────────────────────────────────────────────────────────────
// Twins of `symbology.line_marker` / `line_marker_layout`. QGIS repeats a symbol down a line —
// arrows on a river, ticks on a boundary — and MapLibre draws exactly that with a symbol layer at
// `symbol-placement: line`, so it is a real translation rather than an approximation.

export const DEFAULT_LINE_MARKER_SPACING = 40

/** `style.line_marker` when a line carries markers along it, else `{}`. */
export function lineMarker (style = {}) {
  const block = style.line_marker
  if (!block || typeof block !== 'object') return {}
  // The picture lives under `image` here, not `marker_image`: a line's decoration and a point's
  // marker are different things that share a mechanism.
  const uri = block.image
  return (typeof uri === 'string' && uri.startsWith('data:image/')) ? block : {}
}

/** The `layout` for the symbol layer that repeats a marker along a line. */
export function lineMarkerLayout (block = {}) {
  const raw = Number(block.spacing)
  const spacing = Number.isFinite(raw) ? raw : DEFAULT_LINE_MARKER_SPACING
  return {
    'icon-image': pictureId(block.image),
    // ALONG the line and rotated WITH it — `icon-rotation-alignment: map` is what makes an arrow
    // point downstream rather than always up the screen.
    'symbol-placement': 'line',
    'symbol-spacing': Math.max(1, Math.round(spacing * 100) / 100),
    'icon-rotation-alignment': 'map',
    'icon-allow-overlap': true,
    'icon-ignore-placement': true,
  }
}


/** `style.fill_pattern` when a polygon is patterned rather than flat. Twin of `fill_pattern`. */
export function fillPattern (style = {}) {
  const block = style.fill_pattern
  if (!block || typeof block !== 'object') return {}
  const uri = block.image
  return (typeof uri === 'string' && uri.startsWith('data:image/')) ? block : {}
}


// ── Centroid markers and heatmaps ────────────────────────────────────────────────────────────────
// Twins of `symbology.centroid_marker` / `heatmap` / `heatmap_paint`.

/** `style.centroid_marker` — a symbol at each polygon's centre, QGIS's centroid fill. */
export function centroidMarker (style = {}) {
  const block = style.centroid_marker
  if (!block || typeof block !== 'object') return {}
  const uri = block.image
  return (typeof uri === 'string' && uri.startsWith('data:image/')) ? block : {}
}

/** What a heatmap fades between when the style names no ramp. Transparent at the low end. */
export const DEFAULT_HEATMAP_RAMP = ['rgba(0,0,255,0)', '#3b82f6', '#22c55e', '#eab308', '#ef4444']

/** `style.heatmap` when a layer is drawn as density, else `{}`. */
export function heatmap (style = {}) {
  const block = style.heatmap
  return (block && typeof block === 'object' && block.enabled) ? block : {}
}

/**
 * The paint for a `heatmap` layer. Twin of `symbology.heatmap_paint`.
 *
 * The FIRST stop must be transparent or the whole viewport is painted at density zero — the single
 * mistake that makes a heatmap look broken.
 */
export function heatmapPaint (block = {}, opacity = 1) {
  const ramp = (block.ramp || []).filter(c => typeof c === 'string')
  const stops = ramp.length ? ramp : DEFAULT_HEATMAP_RAMP
  const color = ['interpolate', ['linear'], ['heatmap-density']]
  stops.forEach((stop, i) => {
    const position = stops.length > 1 ? i / (stops.length - 1) : 0
    color.push(Math.round(position * 10000) / 10000,
      (i === 0 && !String(stop).startsWith('rgba')) ? 'rgba(0,0,255,0)' : stop)
  })
  const radius = Number(block.radius)
  const paint = {
    'heatmap-color': color,
    'heatmap-radius': Number.isFinite(radius) ? radius : 20,
    'heatmap-opacity': opacity,
  }
  const field = String(block.weight_field || '').trim()
  const top = Number(block.weight_max)
  if (field && Number.isFinite(top) && top > 0) {
    paint['heatmap-weight'] = ['interpolate', ['linear'], ['to-number', ['get', field], 0], 0, 0, top, 1]
  }
  return paint
}
