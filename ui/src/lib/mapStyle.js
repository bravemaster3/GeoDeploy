/**
 * The MapLibre style for a set of GeoDeploy layers — the ONE implementation.
 *
 * Lifted out of PortalEditor so the portal preview and any other view that has to draw layers use
 * the same code. This function is the browser twin of `services/portal_generator.generate_style`,
 * and nearly every line of it encodes a parity decision that took a bug to learn: which source a
 * GeoParquet layer draws from, why an omitted `fill-outline-color` still draws an outline, why a
 * hillshade must not be rescaled, why points become pillars in 3D. A second copy would drift from
 * the published portal, and the drift would show up as a map that does not match its own legend.
 *
 * Everything it used to reach for in the editor's scope is now an argument, so the caller decides
 * what to draw and nothing here knows about editor state.
 */
import {
  colorExpression as symColorExpression,
  sizeExpression as symSizeExpression,
  extrusionPaint as symExtrusionPaint,
  isExtruded as symIsExtruded,
  iconImageExpression as symIconImageExpression,
  iconSizeExpression as symIconSizeExpression,
  markerImages as symMarkerImages,
  NO_OUTLINE,
} from '@/lib/symbology'

export function buildMapStyle({ configs = [], layers = [], rasters = [], sources = [],
                               basemap }) {
  /**
   * `configs` are layer_configs in PORTAL order (index 0 = top of the list). Draw order is the
   * reverse, because MapLibre paints later layers on top — the same reversal the generator does.
   */
  const bm = basemap
  // Icon id -> spec, for the caller to register on its map. Returned rather than written to an
  // outer variable: this function is pure, and a closure write is exactly what broke when it moved.
  const markerSpecs = {}
  const style = {
    version: 8,
    glyphs: 'https://demotiles.maplibre.org/font/{fontstack}/{range}.pbf',
    sources: {
      basemap: {
        type: 'raster',
        tiles: bm.tiles,
        tileSize: 256,
        attribution: bm.attribution,
      },
    },
    layers: [{ id: 'basemap', type: 'raster', source: 'basemap' }],
  }

  // Merge every visible layer's bbox (skipping non-lon/lat bboxes, e.g. an old
  // projected raster) so "fit"/zoom-to-all covers all layers, not just the last one.
  let bounds = null
  const expandBounds = (b) => {
    if (!lonLatBbox(b)) return
    bounds = bounds
      ? [Math.min(bounds[0], b[0]), Math.min(bounds[1], b[1]), Math.max(bounds[2], b[2]), Math.max(bounds[3], b[3])]
      : b.slice()
  }

  // Draw order follows the folder tree (flattened, top→bottom); top of the list draws on top → reverse.
  // Parity with portal_generator.generate_style. Falls back to config order if a node lacks a config.
  for (const cfg of [...configs].reverse()) {
    if (cfg.visible === false) continue
    if (cfg.layer_type === 'elevation') { expandBounds(cfg.bbox); continue }  // deck-only (see refreshDeck)
    if (cfg.layer_type === 'vector') {
      const layer = layers.find(l => l.id === cfg.layer_id)
      if (!layer || layer.status !== 'ready') continue

      const srcId = `vector_${layer.id}`
      let sourceLayer
      if (layer.storage_backend === 'geoparquet') {
        // File-backed (GeoParquet). PRIMARY display = a deck.gl overlay fed by the viewport query
        // (rendered outside this MapLibre style — see refreshDeck), so EXCLUDE the layer here and
        // just keep its bbox for zoom-to-all. FALLBACK: a layer explicitly tiled (ready PMTiles)
        // renders via the pmtiles:// vector source instead.
        // `tile_status === 'ready'` is the whole test. The tiling task sets it together with
        // `pmtiles_key`, so also requiring the key added nothing — and it meant the PUBLIC layer
        // page could not draw a tiled layer at all, because a public row deliberately carries no
        // storage keys: the URL below is built from the layer's id, and the endpoint resolves the
        // key server-side. Asking for a key we do not need in order to build a URL that does not
        // use it is how a public page ended up with an empty map.
        if (layer.tile_status !== 'ready') { expandBounds(layer.bbox); continue }
        style.sources[srcId] = { type: 'vector', url: `pmtiles://${location.origin}/api/data/vector/${layer.id}/pmtiles` }
        sourceLayer = 'geodeploy'
      } else {
        style.sources[srcId] = {
          type: 'vector',
          tiles: [`${location.origin}/tiles/${layer.schema_name}.${layer.table_name}/{z}/{x}/{y}`],
          minzoom: 0, maxzoom: 22,
        }
        sourceLayer = `${layer.schema_name}.${layer.table_name}`
      }
      // Raw-paint passthrough (GeoLibre-imported layers): emit one MapLibre layer per entry, wired to
      // this layer's tile source/source-layer. Mirrors portal_generator._vector_layers so the editor
      // preview matches the published portal. Distinct ids let fill + outline coexist.
      const rawLayers = cfg.style?.maplibre?.layers
      if (Array.isArray(rawLayers) && rawLayers.length) {
        rawLayers.forEach((entry, i) => {
          const ml = { id: `${srcId}-${entry.suffix || entry.type || i}`, type: entry.type,
                       source: srcId, 'source-layer': sourceLayer }
          if (entry.filter != null) ml.filter = entry.filter
          if (entry.paint) ml.paint = entry.paint
          if (entry.layout) ml.layout = { ...entry.layout }
          style.layers.push(ml)
        })
        expandBounds(layer.bbox)
        continue
      }
      const st = cfg.style || {}
      const opacity = cfg.opacity ?? 1.0
      const geom = (layer.geometry_type || '').toLowerCase()
      // Colour may be a data-driven EXPRESSION (graduated / categorized). `lib/symbology` is the
      // twin of services/symbology.py, so the preview and the published portal classify features
      // identically — see the parity note in that file.
      const color = symColorExpression(st)

      if (geom.includes('polygon')) {
        if (symIsExtruded(st)) {
          // 3D: a different layer TYPE, not a paint variation. Needs pitch to be visible at all —
          // `ensurePitchFor3D` below tilts the preview the first time an extrusion appears.
          style.layers.push({
            id: srcId, type: 'fill-extrusion', source: srcId, 'source-layer': sourceLayer,
            paint: symExtrusionPaint(st, opacity),
          })
        } else {
          // MapLibre has no transparent-outline keyword: you get no outline by omitting the
          // property. Mirrors portal_generator._vector_layer.
          const fillPaint = {
            'fill-color': color,
            'fill-opacity': opacity * (st.fill_opacity ?? 0.45),
          }
          // `fill-antialias: false`, not an omission: an omitted fill-outline-color MATCHES the
          // fill colour (the spec default), which is why "None" drew a visible edge. Mirrors
          // portal_generator._vector_layer.
          if (st.outline_color === NO_OUTLINE) fillPaint['fill-antialias'] = false
          else fillPaint['fill-outline-color'] = st.outline_color || '#1d4ed8'
          style.layers.push({
            id: srcId, type: 'fill', source: srcId, 'source-layer': sourceLayer, paint: fillPaint,
          })
        }
      } else if (geom.includes('line')) {
        const linePaint = {
          'line-color': color,
          'line-width': symSizeExpression(st, st.line_width ?? 2),
          'line-opacity': opacity,
        }
        if (st.lineType === 'dashed') linePaint['line-dasharray'] = [2, 1.5]
        else if (st.lineType === 'dotted') linePaint['line-dasharray'] = [0.4, 1.8]
        style.layers.push({
          id: srcId, type: 'line', source: srcId, 'source-layer': sourceLayer, paint: linePaint,
        })
      } else if (symIsExtruded(st) && layer.storage_backend !== 'geoparquet') {
        // POINTS IN 3D: pillars. MapLibre extrudes fills only, so the geometry has to become a
        // polygon — the shared Martin function buffers the points by a radius in metres and serves
        // them as one. A SECOND source beside the normal one, so toggling 3D off changes nothing
        // else. Mirrors portal_generator._vector_layer / the pillar source it adds.
        const pillarSrc = `${srcId}-pillars`
        const r = Math.min(Math.max(Number(st.extrusion?.radius) || 30, 0.5), 100000)
        const qs = new URLSearchParams({
          schema: layer.schema_name, table: layer.table_name,
          geom: layer.geometry_column || 'geom', radius: String(Math.round(r * 100) / 100),
        })
        style.sources[pillarSrc] = {
          type: 'vector',
          tiles: [`${location.origin}/tiles/point_pillars/{z}/{x}/{y}?${qs}`],
          minzoom: 0, maxzoom: 22,
        }
        style.layers.push({
          id: srcId, type: 'fill-extrusion', source: pillarSrc, 'source-layer': 'pillars',
          paint: symExtrusionPaint(st, opacity),
        })
      } else {
        // Points render as a symbol layer with generated canvas icons, so marker SHAPES work on any
        // basemap. A classified layer keeps its shape: `icon-image` is data-driven, so we register
        // one image per class and let MapLibre pick. Mirrors portal_generator._vector_layer — if
        // these disagree, the preview shows a style the portal will not render.
        symMarkerImages(st).forEach((im) => { markerSpecs[im.id] = im })
        style.layers.push({
          id: srcId, type: 'symbol', source: srcId, 'source-layer': sourceLayer,
          layout: {
            'icon-image': symIconImageExpression(st),
            'icon-size': symIconSizeExpression(st),
            'icon-allow-overlap': true,
            'icon-ignore-placement': true,
          },
          paint: { 'icon-opacity': opacity },
        })
      }

      expandBounds(layer.bbox)

    } else if (cfg.layer_type === 'raster') {
      const layer = rasters.find(l => l.id === cfg.layer_id)
      if (!layer || layer.status !== 'ready' || !layer.tile_url) continue

      const srcId = `raster_${layer.id}`
      const absTileUrl = rasterTilesUrl(layer.tile_url, cfg.style, layer.band_count)
      style.sources[srcId] = { type: 'raster', tiles: [absTileUrl], tileSize: 256 }
      // Where the data actually IS — the published style has carried this for a while, this map
      // never did. Without it MapLibre asks for tiles across the whole viewport at every zoom and
      // the tile server 404s every one that misses the raster. For a drone orthomosaic a few
      // hundred metres wide that is nearly every tile on screen, which is what filled the console.
      const rbounds = lonLatBbox(layer.bbox)
      if (rbounds) {
        style.sources[srcId].bounds = rbounds
        // And how far OUT to bother asking. Mirrors portal_generator._min_zoom_for: `bounds` stops
        // tiles that MISS the raster, not a tile that hits it and spans a continent — and a z3 tile
        // of a drone orthomosaic took long enough that nginx returned 504, which hangs the map.
        const mz = minZoomFor(rbounds)
        if (mz) style.sources[srcId].minzoom = mz
      }
      style.layers.push({
        id: srcId, type: 'raster', source: srcId,
        paint: { 'raster-opacity': cfg.opacity ?? 1.0, ...(cfg.style?.paint || {}) },
      })
      expandBounds(layer.bbox)

    } else if (cfg.layer_type === 'external') {
      const src = sources.find(s => s.id === cfg.layer_id)
      if (!src) continue
      const srcId = `ext_${src.id}`
      const abs = (u) => (u && u.startsWith('/')) ? location.origin + u : u
      const op = cfg.opacity ?? 1.0
      if (src.kind === 'raster') {
        if (!src.tile_url) continue
        style.sources[srcId] = { type: 'raster', tiles: [abs(src.tile_url)], tileSize: 256 }
        if (src.attribution) style.sources[srcId].attribution = src.attribution
        style.layers.push({ id: `external-${src.id}`, type: 'raster', source: srcId, paint: { 'raster-opacity': op } })
      } else {
        if (!src.data_url) continue
        style.sources[srcId] = { type: 'geojson', data: abs(src.data_url) }
        if (src.attribution) style.sources[srcId].attribution = src.attribution
        const geom = src.geometry_type || 'polygon'
        const color = cfg.style?.color || '#3b82f6'
        if (geom === 'polygon') {
          style.layers.push({ id: `external-${src.id}`, type: 'fill', source: srcId,
            paint: { 'fill-color': color, 'fill-opacity': op * (cfg.style?.fill_opacity ?? 0.45), 'fill-outline-color': cfg.style?.outline_color || '#1d4ed8' } })
        } else if (geom === 'line') {
          style.layers.push({ id: `external-${src.id}`, type: 'line', source: srcId,
            paint: { 'line-color': color, 'line-width': cfg.style?.line_width ?? 2, 'line-opacity': op } })
        } else {
          style.layers.push({ id: `external-${src.id}`, type: 'circle', source: srcId,
            paint: { 'circle-color': color, 'circle-radius': cfg.style?.radius ?? 5, 'circle-opacity': op, 'circle-stroke-color': '#fff', 'circle-stroke-width': 1 } })
        }
      }
      expandBounds(src.bbox)
    }
  }

  return { style, bounds, markerSpecs }
}


export function lonLatBbox(b) {
  return (Array.isArray(b) && b.length === 4 &&
    b[0] >= -180 && b[2] <= 180 && b[0] < b[2] &&
    b[1] >= -90 && b[3] <= 90 && b[1] < b[3]) ? b.slice(0, 4) : null
}

// The lowest zoom worth requesting tiles at, from the layer's extent. Mirrors
// services/portal_generator.py::_min_zoom_for — a tile spans 360/2^z degrees, so the layer first
// fits in one tile at log2(360/width); four levels of slack keeps it visible as a speck before we
// stop asking. 0 (falsy) = continent-sized, leave unrestricted.
const MINZOOM_SLACK = 4
function minZoomFor(b) {
  const width = Math.max(b[2] - b[0], b[3] - b[1])
  if (!(width > 0)) return 0
  const fitsAt = width < 360 ? Math.log2(360 / width) : 0
  return Math.max(0, Math.min(Math.trunc(fitsAt) - MINZOOM_SLACK, 18))
}

// Every key of a raster style that changes the picture — the JS twin of services/titiler.STYLE_KEYS.
// `opacity` is deliberately absent: the map applies it, the tile server does not.
export const RASTER_STYLE_KEYS = [
  'colormap', 'colormap_reverse', 'rescale', 'algorithm', 'zfactor', 'bidx',
  'color_classes', 'increment', 'thickness', 'minz', 'maxz',
]

// Pull a raster style out of a layer's default_style or a portal's layer_config, whole.
//
// FOUR PLACES USED TO HAND-LIST THESE and every one of them listed five of the eleven: the panel's
// "save as default", its "use the layer's default", the My Data style modal, and the portal editor's
// add-layer. A raster coloured per pixel VALUE, or with a reversed ramp, lost exactly that on the way
// through — and a dropped key does not fail, it quietly serves a style nobody chose. Same counting
// problem the backend had at seven call sites, and the same fix.
export function rasterStyleOf(source) {
  const out = {}
  RASTER_STYLE_KEYS.forEach((key) => {
    const value = source?.[key]
    if (value !== undefined && value !== null && value !== '') out[key] = value
  })
  return out
}

//: Must match services/titiler.MAX_COLOR_CLASSES — the mapping rides in every tile request's URL.
const MAX_COLOR_CLASSES = 128

// `{"3":[r,g,b,a]}` for a raster coloured per pixel VALUE. Mirrors services/titiler._explicit_colormap,
// including the reversal rule: the colours are re-paired with the values in the opposite order, so
// the values keep their places and the palette runs the other way.
function explicitColormap(classes, reverse) {
  if (!Array.isArray(classes) || !classes.length) return null
  let entries = classes.slice(0, MAX_COLOR_CLASSES).filter(e => e && typeof e === 'object')
  if (reverse) {
    const colours = entries.map(e => e.color).reverse()
    entries = entries.map((e, i) => ({ ...e, color: colours[i] }))
  }
  const mapping = {}
  entries.forEach(e => {
    const text = String(e.color || '').trim().replace(/^#/, '')
    if (text.length !== 6 && text.length !== 8) return
    const key = parseInt(e.value, 10)
    if (!Number.isFinite(key)) return
    const rgba = []
    for (let i = 0; i < text.length; i += 2) rgba.push(parseInt(text.slice(i, i + 2), 16))
    if (rgba.some(n => !Number.isFinite(n))) return
    if (rgba.length === 3) rgba.push(255)
    mapping[String(key)] = rgba
  })
  return Object.keys(mapping).length ? JSON.stringify(mapping) : null
}

// CONTOUR parameters. Mirrors services/titiler._contour_params, and the two traps are the same:
// TiTiler's contours colours the data across minz–maxz with a built-in terrain ramp and draws the
// lines on that, so its planet-sized defaults (−12000..8000) render a survey DEM as one flat band —
// hence borrowing the layer's own stretch. And it types minz/maxz as INT, rejecting the whole tile
// request for a fractional one, so the borrowed range is floored and ceiled to widen rather than clip.
export function contourParams(style) {
  const out = {}
  const increment = Number(style?.increment)
  const thickness = Number(style?.thickness)
  out.increment = Number.isFinite(increment) && increment > 0 ? increment : 35
  out.thickness = Number.isFinite(thickness) && thickness > 0 ? Math.trunc(thickness) : 1
  let lo = Number(style?.minz)
  let hi = Number(style?.maxz)
  if (!(Number.isFinite(lo) && Number.isFinite(hi) && hi > lo)) {
    const parts = String(style?.rescale || '').split(',')
    lo = Number(parts[0]); hi = Number(parts[1])
  }
  if (Number.isFinite(lo) && Number.isFinite(hi) && hi > lo) {
    out.minz = Math.floor(lo)
    out.maxz = Math.ceil(hi)
  }
  return JSON.stringify(out)
}

// Build a raster tile URL from the layer's base URL + the configured raster style.
export function rasterTilesUrl(baseTileUrl, style, bandCount) {
  const base = (baseTileUrl || '').split('&')[0]  // s3 key has no '&', so this keeps ?url=...
  const params = []
  let bands = Array.isArray(style?.bidx) ? style.bidx.filter(b => b != null) : []
  // Mirrors services/titiler.py::get_tile_url. A PNG holds at most four channels and TiTiler adds
  // the mask as alpha, so a 4-band multispectral raster asks the driver for five and every tile
  // 500s. With no band selection TiTiler reads them all — hence an explicit default.
  if (!bands.length && bandCount > 3) bands = [1, 2, 3]
  bands.forEach(b => params.push(`bidx=${b}`))
  // Mirrors services/titiler.py::get_tile_url — a hillshade is already a finished 0-255 relief
  // image, and TiTiler applies rescale AFTER the algorithm, so a data-range stretch flattens it.
  // Contours is the same picture for a different reason: it returns a finished RGB image, and it
  // CONSUMES the stretch as its colour range instead (see below).
  // An explicit colour-per-VALUE mapping is keyed on the raw values, so a stretch destroys it:
  // rescale maps the data into 0–255 before the lookup, and a classification of 0/1/2 arrives as
  // 0/127/255 where only one key still matches. Mirrors services/titiler.py::get_tile_url.
  const explicitCmap = explicitColormap(style?.color_classes, style?.colormap_reverse)
  if (style?.rescale && style?.algorithm !== 'hillshade' && style?.algorithm !== 'contours'
      && !explicitCmap) {
    params.push(`rescale=${style.rescale}`)
  }
  if (style?.algorithm) {
    params.push(`algorithm=${style.algorithm}`)
    if (style.algorithm === 'hillshade' && style.zfactor && Number(style.zfactor) !== 1) {
      params.push(`expression=b1*${style.zfactor}`)
    }
    if (style.algorithm === 'contours') {
      const p = contourParams(style)
      if (p) params.push(`algorithm_params=${encodeURIComponent(p)}`)
    }
  } else if (bands.length !== 3) {
    // A CLASSIFIED raster, which this used to drop entirely: only `colormap` was mirrored, so a
    // layer coloured per pixel VALUE drew here in plain grayscale while the published portal drew
    // its classes. The editor is supposed to be the preview — a preview that disagrees with the
    // thing it previews is worse than none.
    if (explicitCmap) params.push(`colormap=${encodeURIComponent(explicitCmap)}`)
    else if (style?.colormap) {
      // Mirrors services/titiler.py: matplotlib spells a reversed ramp with an `_r` suffix, and the
      // flag is the single source of truth — a stored `viridis_r` with reverse off is forward.
      const base_ = style.colormap.endsWith('_r') ? style.colormap.slice(0, -2) : style.colormap
      params.push(`colormap_name=${style.colormap_reverse ? base_ + '_r' : base_}`)
    }
  }
  const url = base + (params.length ? '&' + params.join('&') : '')
  return url.startsWith('/') ? location.origin + url : url
}
