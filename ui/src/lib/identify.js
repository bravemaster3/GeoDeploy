// What is under the pointer — shared by every map in the app (the portal editor's preview and the
// layer page). The published portal does the same in `templates/shared/portal.js`, which is plain
// browser JS outside this bundle and so keeps its own copy: change the two together.

//: The cursor over something that answers a click. An ARROW, against MapLibre's open hand for
//: panning — the change of shape is the signal that a click here does something different from a
//: drag. Same value as portal.js `FEATURE_CURSOR`.
export const FEATURE_CURSOR = 'default'

/** GeoDeploy's own `__gd_*` columns (geometry measures, issue #133) — for the style, never shown. */
export const isInternalField = (k) => String(k).startsWith('__gd_')

export const escHtml = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
  .replace(/>/g, '&gt;').replace(/"/g, '&quot;')

/** The ids of the vector layers the LIVE style has — `queryRenderedFeatures` rejects the whole call
 *  when one id is unknown, so a stale id would silently disable identify for every layer. */
export function vectorLayerIds(map) {
  if (!map) return []
  try {
    return (map.getStyle()?.layers || []).map(l => l.id)
      .filter(id => id.startsWith('vector_') && map.getLayer(id))
  } catch { return [] }      // style mid-rebuild
}

/** The first vector feature within `pad` pixels of `point`, or null. */
export function vectorFeatureAt(map, point, pad = 5) {
  const ids = vectorLayerIds(map)
  if (!ids.length) return null
  const box = pad
    ? [[point.x - pad, point.y - pad], [point.x + pad, point.y + pad]]
    : point
  try { return map.queryRenderedFeatures(box, { layers: ids })[0] || null } catch { return null }
}

/**
 * One layer's attributes as popup HTML.
 *
 * `fields` is the portal's chosen popup fields; without them every non-empty attribute is shown,
 * up to `limit` (the editor keeps the portal's 8; the layer page passes `Infinity`, because
 * there the popup IS the way to read a feature). `action` adds a button the caller wires up.
 */
export function attrTableHtml(title, props, fields, { limit = 8, action = null } = {}) {
  const keys = fields && fields.length
    ? fields.filter(k => props[k] != null)
    : Object.keys(props).filter(k => props[k] != null && !isInternalField(k)).slice(0, limit)
  const body = keys.length
    ? '<div style="max-height:260px;overflow-y:auto"><table style="font-size:12px;border-collapse:collapse">'
      + keys.map(k => `<tr><th style="text-align:left;vertical-align:top;padding:2px 8px 2px 0;color:#6b7280;font-weight:500">${escHtml(k)}</th>`
        + `<td style="padding:2px 0;overflow-wrap:anywhere">${escHtml(props[k])}</td></tr>`).join('')
      + '</table></div>'
    : '<div style="font-size:12px;color:#6b7280">No attributes</div>'
  const button = action
    ? `<div style="margin-top:6px;text-align:right"><button type="button" data-gd-action="${escHtml(action.id)}" `
      + 'style="font-size:11px;color:#2563eb;background:none;border:0;padding:0;cursor:pointer">'
      + `${escHtml(action.label)}</button></div>`
    : ''
  return `<div style="font-weight:600;font-size:12px;margin-bottom:4px">${escHtml(title)}</div>${body}${button}`
}

/** Band values of a raster at a point, as popup HTML (null → nothing to say there). */
export function rasterValuesHtml(title, result) {
  if (!result || !result.values || !result.values.length) return null
  const rows = result.values.map((v, i) => {
    const band = (result.bands && result.bands[i]) || `Band ${i + 1}`
    const val = typeof v === 'number' ? Math.round(v * 10000) / 10000 : v
    return `<tr><th style="text-align:left;padding:2px 8px 2px 0;color:#6b7280;font-weight:500">${escHtml(band)}</th>`
      + `<td style="padding:2px 0">${escHtml(val)}</td></tr>`
  }).join('')
  return `<div style="font-weight:600;font-size:12px;margin-bottom:4px">${escHtml(title)}</div>`
    + `<table style="font-size:12px;border-collapse:collapse">${rows}</table>`
}

/**
 * TiTiler's point query for the raster drawn from `tileUrl`: the same COG and band/expression
 * parameters, `/cog/tiles/{tms}/{z}/{x}/{y}` swapped for `/cog/point/{lng},{lat}`. Only the FIRST
 * query parameter is kept, as portal.js does — that is the COG `url`; the styling ones (colormap,
 * rescale) mean nothing to a point query and some of them make TiTiler reject it.
 */
export function rasterPointUrl(tileUrl, lngLat) {
  const base = String(tileUrl || '').split('&')[0]
  const q = base.indexOf('?')
  if (q < 0) return null
  const path = base.slice(0, q).replace(/\/cog\/tiles\/[^/]+\/\{z\}\/\{x\}\/\{y\}(?:\.[a-z0-9]+)?/,
    `/cog/point/${lngLat.lng},${lngLat.lat}`)
  return path.includes('/cog/point/') ? path + base.slice(q) : null
}

export async function fetchRasterPoint(tileUrl, lngLat) {
  const url = rasterPointUrl(tileUrl, lngLat)
  if (!url) return null
  try {
    const r = await fetch(url)
    if (!r.ok) return null
    const j = await r.json()
    return { values: j.values || [], bands: j.band_names || [] }
  } catch { return null }
}

/**
 * The arrow over anything clickable, the pan hand everywhere else.
 *
 * `hitAt(point)` answers synchronously for MapLibre layers. `deckPick`, when given, is deck.gl's
 * picking — the only thing that knows where a GeoParquet feature is — and it runs once the pointer
 * SETTLES rather than on every move: a pick is a render pass, and ~60 a second over a big GeoArrow
 * layer is most of the cost people notice (the same reasoning as portal.js). Returns a detach.
 */
export function hoverCursor(map, hitAt, deckPick = null, settleMs = 70) {
  let timer = null
  let shown = false
  const set = (on) => {
    if (on === shown) return
    shown = on
    map.getCanvas().style.cursor = on ? FEATURE_CURSOR : ''
  }
  const onMove = (e) => {
    if (timer) { clearTimeout(timer); timer = null }
    if (hitAt(e.point)) { set(true); return }
    set(false)
    if (!deckPick) return
    timer = setTimeout(() => {
      timer = null
      let hit = false
      try { hit = !!deckPick(e.point) } catch { hit = false }
      set(hit)
    }, settleMs)
  }
  const onOut = () => { if (timer) { clearTimeout(timer); timer = null } set(false) }
  map.on('mousemove', onMove)
  map.on('mouseout', onOut)
  return () => { map.off('mousemove', onMove); map.off('mouseout', onOut); onOut() }
}

/** [minx, miny, maxx, maxy] of a GeoJSON geometry, or null. */
export function geometryBbox(geom) {
  if (!geom) return null
  let b = null
  const walk = (c) => {
    if (typeof c[0] === 'number') {
      if (!b) b = [c[0], c[1], c[0], c[1]]
      else { b[0] = Math.min(b[0], c[0]); b[1] = Math.min(b[1], c[1]); b[2] = Math.max(b[2], c[0]); b[3] = Math.max(b[3], c[1]) }
      return
    }
    c.forEach(walk)
  }
  if (geom.type === 'GeometryCollection') (geom.geometries || []).forEach(g => g.coordinates && walk(g.coordinates))
  else if (geom.coordinates) walk(geom.coordinates)
  return b
}
