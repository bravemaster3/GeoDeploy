/**
 * Point markers, drawn as canvas images — the browser twin of the same code in
 * `templates/shared/portal.js`.
 *
 * MapLibre cannot draw a star or a cross from paint properties, so a point layer with a SHAPE is a
 * symbol layer whose icon is generated here and registered on demand (`styleimagemissing`). Every
 * map that shows GeoDeploy points therefore needs this, which is why it is a library rather than
 * something the portal editor happens to own.
 */
function starPts(cx, cy, r) {
  const p = []
  for (let i = 0; i < 10; i++) { const a = -Math.PI / 2 + i * Math.PI / 5, rr = (i % 2) ? r * 0.45 : r; p.push([cx + Math.cos(a) * rr, cy + Math.sin(a) * rr]) }
  return p
}

function crossPts(cx, cy, r) {
  const t = r * 0.38
  return [[-t, -r], [t, -r], [t, -t], [r, -t], [r, t], [t, t], [t, r], [-t, r], [-t, t], [-r, t], [-r, -t], [-t, -t]].map(d => [cx + d[0], cy + d[1]])
}

export function markerImage(shape, color, size, outline, outlineWidth) {
  const dpr = 2, r = Math.max(3, Number(size) || 5)
  // Outline width is a RATIO of the radius (see portal.js) so it stays proportional when a layer is
  // resized; 0.28 reproduces the old hard-coded stroke exactly.
  const ow = outlineWidth == null ? 0.28 : Number(outlineWidth)
  const stroke = Math.max(0, r * (isFinite(ow) ? ow : 0.28))
  const dim = 80  // fixed canvas (see portal.js): constant dims let updateImage handle size changes
  const cv = document.createElement('canvas')
  cv.width = dim * dpr; cv.height = dim * dpr
  const ctx = cv.getContext('2d')
  ctx.scale(dpr, dpr); ctx.lineJoin = 'round'
  const cx = dim / 2, cy = dim / 2
  ctx.beginPath()
  if (shape === 'square') ctx.rect(cx - r, cy - r, r * 2, r * 2)
  else if (shape === 'triangle') { ctx.moveTo(cx, cy - r); ctx.lineTo(cx + r * 0.92, cy + r * 0.72); ctx.lineTo(cx - r * 0.92, cy + r * 0.72); ctx.closePath() }
  else if (shape === 'diamond') { ctx.moveTo(cx, cy - r); ctx.lineTo(cx + r, cy); ctx.lineTo(cx, cy + r); ctx.lineTo(cx - r, cy); ctx.closePath() }
  else if (shape === 'star') { starPts(cx, cy, r).forEach((p, i) => i ? ctx.lineTo(p[0], p[1]) : ctx.moveTo(p[0], p[1])); ctx.closePath() }
  else if (shape === 'cross') { crossPts(cx, cy, r).forEach((p, i) => i ? ctx.lineTo(p[0], p[1]) : ctx.moveTo(p[0], p[1])); ctx.closePath() }
  else ctx.arc(cx, cy, r, 0, Math.PI * 2)
  ctx.fillStyle = color || '#3b82f6'; ctx.fill()
  // null = draw no outline; undefined = unspecified, i.e. the white default markers always had.
  const oc = outline === undefined ? '#ffffff' : outline
  if (oc && stroke > 0) { ctx.strokeStyle = oc; ctx.lineWidth = stroke; ctx.stroke() }
  const d = ctx.getImageData(0, 0, dim * dpr, dim * dpr)
  return { width: dim * dpr, height: dim * dpr, data: d.data, pixelRatio: dpr }
}


/**
 * Wire a map up to draw the markers a style asks for.
 *
 * `specs` is what `buildMapStyle` returns: icon id -> {shape, color, size, outline}. MapLibre asks
 * for an image the first time a symbol layer references one it does not have, which is the only
 * moment we can know which are actually needed.
 */
export function registerMarkerImages(map, specs) {
  if (!map || map.__gdMarkerHook) return
  map.__gdMarkerHook = true
  map.on('styleimagemissing', (e) => {
    if (!e.id || !e.id.startsWith('gd-pt-') || map.hasImage(e.id)) return
    const spec = (map.__gdMarkerSpecs || {})[e.id]
    if (!spec) return
    const im = markerImage(spec.shape, spec.color, spec.size, spec.outline, spec.outline_width)
    try { map.addImage(e.id, im, { pixelRatio: im.pixelRatio }) } catch { /* already added */ }
  })
  setMarkerSpecs(map, specs)
}

/** Replace the specs a map draws from — call whenever the style is rebuilt. */
export function setMarkerSpecs(map, specs) {
  if (!map) return
  map.__gdMarkerSpecs = { ...(map.__gdMarkerSpecs || {}), ...(specs || {}) }
}
