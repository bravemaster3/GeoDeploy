// Unit tests for lib/identify.js — the map identify helpers (popup, raster point query, hover
// cursor) shared by the portal editor and the layer page. Node's own runner, no dependencies:
//   cd ui && node --test tests/identify.test.mjs
import assert from 'node:assert/strict'
import { test } from 'node:test'
import * as m from '../src/lib/identify.js'

const ok = (c, msg) => assert.ok(c, msg)

test('identify helpers', async () => {
  // raster point url: same rewrite portal.js does, on the shapes buildMapStyle emits
  const t = 'https://x.org/cog/tiles/WebMercatorQuad/{z}/{x}/{y}.png?url=s3%3A%2F%2Fb%2Fr.tif&rescale=0,255&colormap_name=viridis'
  ok(m.rasterPointUrl(t, { lng: 13.7, lat: 51.05 }) === 'https://x.org/cog/point/13.7,51.05?url=s3%3A%2F%2Fb%2Fr.tif', m.rasterPointUrl(t, { lng: 13.7, lat: 51.05 }))
  ok(m.rasterPointUrl('https://x.org/cog/tiles/WebMercatorQuad/{z}/{x}/{y}?url=a', { lng: 1, lat: 2 }) === 'https://x.org/cog/point/1,2?url=a')
  ok(m.rasterPointUrl('https://x.org/no-query/{z}', { lng: 1, lat: 2 }) === null, 'no query -> null')
  ok(m.rasterPointUrl('https://wms.example/?service=WMS', { lng: 1, lat: 2 }) === null, 'not a cog -> null')
  // bbox of every geometry kind
  ok(JSON.stringify(m.geometryBbox({ type: 'Point', coordinates: [1, 2] })) === '[1,2,1,2]')
  ok(JSON.stringify(m.geometryBbox({ type: 'MultiPolygon', coordinates: [[[[0, 0], [2, 0], [2, 3], [0, 0]]], [[[-1, 5], [0, 5], [0, 6], [-1, 5]]]] })) === '[-1,0,2,6]')
  ok(JSON.stringify(m.geometryBbox({ type: 'GeometryCollection', geometries: [{ type: 'Point', coordinates: [4, 4] }, { type: 'LineString', coordinates: [[0, 1], [9, 2]] }] })) === '[0,1,9,4]')
  ok(m.geometryBbox(null) === null)
  // popup: internal fields hidden, all fields when limit Infinity, 8 by default, html escaped
  const props = { __gd_area: 5, a: 1, b: '<x>', c: 3, d: 4, e: 5, f: 6, g: 7, h: 8, i: 9, j: null }
  const all = m.attrTableHtml('T', props, null, { limit: Infinity })
  ok(!all.includes('__gd_area') && all.includes('>i<') && !all.includes('>j<'), 'all visible, none internal, null skipped')
  ok(all.includes('&lt;x&gt;') && !all.includes('<x>'), 'escaped')
  const eight = m.attrTableHtml('T', props, null)
  ok((eight.match(/<tr>/g) || []).length === 8, 'default 8')
  const btn = m.attrTableHtml('T', props, null, { action: { id: 'table', label: 'View ▸' } })
  ok(btn.includes('data-gd-action="table"'), 'action button')
  ok(m.attrTableHtml('T', props, ['b', 'zz']).match(/<tr>/g).length === 1, 'configured fields only')
  ok(m.isInternalField('__gd_length') && !m.isInternalField('gd_x'))
  ok(m.FEATURE_CURSOR === 'default')
  // hoverCursor: arrow on hit, pan hand off, deck pick settles, detach cleans up
  const handlers = {}; const canvas = { style: { cursor: '' } }
  const map = { on: (e, f) => { handlers[e] = f }, off: (e) => { delete handlers[e] }, getCanvas: () => canvas }
  let hit = false, deck = false
  const detach = m.hoverCursor(map, () => hit, () => deck, 5)
  hit = true; handlers.mousemove({ point: { x: 1, y: 1 } }); ok(canvas.style.cursor === 'default', 'arrow on hit')
  hit = false; handlers.mousemove({ point: { x: 2, y: 2 } }); ok(canvas.style.cursor === '', 'hand off')
  deck = true; handlers.mousemove({ point: { x: 3, y: 3 } }); ok(canvas.style.cursor === '', 'deck waits to settle')
  await new Promise(r => setTimeout(r, 20)); ok(canvas.style.cursor === 'default', 'deck hit after settle')
  handlers.mouseout(); ok(canvas.style.cursor === '', 'mouseout resets')
  detach(); ok(!handlers.mousemove && !handlers.mouseout, 'detached')
})
