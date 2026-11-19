// lib/mapStyle.buildMapStyle — the style the layer page and the portal editor's preview draw.
// Node's own runner; mapStyle imports through Vite's `@/` alias, so it is bundled first with the
// esbuild Vite already installs:   cd ui && node --test tests/mapStyle.test.mjs
import assert from 'node:assert/strict'
import { test } from 'node:test'
import os from 'node:os'
import path from 'node:path'
import { createRequire } from 'node:module'
import { fileURLToPath, pathToFileURL } from 'node:url'

const UI = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const esbuild = createRequire(path.join(UI, 'package.json'))('esbuild')
const out = path.join(os.tmpdir(), `gd-mapStyle-${process.pid}.mjs`)
await esbuild.build({ entryPoints: [path.join(UI, 'src/lib/mapStyle.js')], bundle: true, format: 'esm',
  platform: 'node', outfile: out, alias: { '@': path.join(UI, 'src') }, logLevel: 'error' })
globalThis.location = { origin: 'https://example.org' }
const { buildMapStyle } = await import(pathToFileURL(out).href)

const layer = { id: 7, status: 'ready', geometry_type: 'MultiPolygon', schema_name: 's', table_name: 't',
                bbox: [13.6, 51.0, 13.9, 51.1] }
const build = (style) => buildMapStyle({
  configs: [{ layer_id: 7, layer_type: 'vector', visible: true, opacity: 1, style }],
  layers: [layer], rasters: [], sources: [], basemap: 'osm' }).style.layers.map(l => l.id)
const rules = [{ filter: ['==', ['get', 'k'], 'a'], style: { color: '#ff0000' } },
               { filter: ['==', ['get', 'k'], 'b'], style: { color: '#0000ff' } }]
const dupes = ids => ids.filter((id, i) => ids.indexOf(id) !== i)

// The layer page went EMPTY for every rule-based layer with labels: the labels were emitted once
// per RULE, under one id, and MapLibre rejects a whole style over a duplicate layer id
// ("duplicate layer id vector_65-labels-r0") — 7 of 11 layers on a real OSM project.
test('a rule-based layer with label rules: every id once, labels once, above the rules', () => {
  // The shape the QGIS plugin sends: the top level keeps the first rule's text, and each rule
  // carries its own.
  const ids = build({ rules, labels: { enabled: true, expression: ['get', 'name'], rules: [
    { filter: ['has', 'name'], labels: { expression: ['get', 'name'] } },
    { filter: ['has', 'ref'], labels: { expression: ['get', 'ref'] } }] } })
  assert.deepEqual(dupes(ids), [])
  assert.deepEqual(ids.filter(id => id.includes('labels')), ['vector_7-labels-r0', 'vector_7-labels-r1'])
  assert.ok(ids.indexOf('vector_7-labels-r0') > ids.indexOf('vector_7-r1'), ids.join(', '))
})

test('a rule-based layer with one plain label: one label layer', () => {
  const ids = build({ rules, labels: { enabled: true, field: 'name' } })
  assert.deepEqual(dupes(ids), [])
  assert.deepEqual(ids.filter(id => id.includes('labels')), ['vector_7-labels'])
})

test('a single-symbol layer still labels', () => {
  const ids = build({ color: '#00ff00', labels: { enabled: true, field: 'name' } })
  assert.deepEqual(ids.filter(id => id.includes('labels')), ['vector_7-labels'])
})
