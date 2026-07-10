<template>
  <div class="flex h-screen">
    <!-- Left panel -->
    <div class="w-80 flex-shrink-0 bg-white border-r border-gray-200 flex flex-col overflow-hidden">

      <!-- Top bar -->
      <div class="px-4 py-3 border-b border-gray-200 flex items-center justify-between gap-2">
        <button @click="$router.push('/portals')" class="text-sm text-gray-500 hover:text-gray-900 flex-shrink-0">← Back</button>
        <span class="text-sm font-semibold truncate flex-1 text-center">{{ portal?.title }}</span>
        <button @click="handlePublish" :disabled="busy || !portal"
          class="btn-primary text-xs py-1.5 flex-shrink-0">
          {{ portal?.published ? 'Re-publish' : 'Publish' }}
        </button>
      </div>

      <!-- Live URL bar -->
      <div v-if="portal?.published" class="px-4 py-2 bg-green-50 border-b border-green-100 flex items-center gap-2">
        <span class="w-2 h-2 rounded-full bg-green-500 flex-shrink-0 animate-pulse" />
        <a :href="`/portals/${portal.slug}/`" target="_blank"
          class="text-xs text-green-700 hover:text-green-900 truncate font-medium flex-1">
          /portals/{{ portal.slug }}/
        </a>
        <ExternalLinkIcon class="w-3.5 h-3.5 text-green-500 flex-shrink-0" />
      </div>

      <!-- Scrollable body -->
      <div class="flex-1 overflow-y-auto">

        <!-- Layers section -->
        <section class="p-4 border-b border-gray-100">
          <div class="flex items-center justify-between mb-3">
            <h3 class="text-xs font-semibold text-gray-500 uppercase tracking-wider">Layers</h3>
            <button @click="showAddLayer = !showAddLayer" class="text-xs text-brand-600 hover:text-brand-700 font-medium">+ Add</button>
          </div>

          <div v-if="showAddLayer" class="mb-3 p-2 bg-gray-50 rounded-lg text-xs space-y-0.5 max-h-40 overflow-y-auto border border-gray-200">
            <p v-if="!availableLayers.length" class="text-gray-400 p-1">No ready layers available.</p>
            <div v-for="layer in availableLayers" :key="`${layer.type}-${layer.id}`"
              class="flex items-center justify-between p-1.5 hover:bg-white rounded cursor-pointer"
              @click="addLayer(layer)"
            >
              <span class="font-medium">{{ layer.name }}</span>
              <span class="text-gray-400 text-[10px] uppercase">{{ layer.type }}</span>
            </div>
          </div>

          <div v-if="!layerConfigs.length" class="text-xs text-gray-400 py-1">No layers added yet.</div>
          <div v-for="(cfg, i) in layerConfigs" :key="`${cfg.layer_type}-${cfg.layer_id}`"
            draggable="true"
            @dragstart="onDragStart(i)"
            @dragover.prevent="onDragOver(i)"
            @drop="onDragEnd"
            @dragend="onDragEnd"
            :class="{ 'opacity-40': dragIndex === i }"
          >
            <LayerPanel
              :config="cfg"
              @remove="layerConfigs.splice(i, 1)"
              @update="layerConfigs[i] = { ...layerConfigs[i], ...$event }"
              @zoom="zoomToLayer(cfg)"
            />
          </div>
        </section>

        <!-- Template section -->
        <section class="p-4 border-b border-gray-100">
          <h3 class="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-3">Template</h3>
          <div class="grid grid-cols-2 gap-2">
            <button v-for="t in templates" :key="t.id"
              class="p-2 rounded-lg border text-xs font-medium transition-colors text-left"
              :class="selectedTemplate === t.id
                ? 'border-brand-500 bg-brand-50 text-brand-700'
                : 'border-gray-200 hover:border-gray-300 text-gray-700'"
              @click="selectedTemplate = t.id"
            >{{ t.name }}</button>
          </div>
        </section>

        <!-- Access control section -->
        <section class="p-4">
          <h3 class="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-3">Access</h3>
          <div class="space-y-1.5">
            <label v-for="opt in accessOptions" :key="opt.value"
              class="flex items-start gap-2.5 p-2 rounded-lg border cursor-pointer transition-colors"
              :class="accessType === opt.value
                ? 'border-brand-500 bg-brand-50'
                : 'border-gray-200 hover:border-gray-300'"
            >
              <input type="radio" :value="opt.value" v-model="accessType" class="mt-0.5 accent-brand-500 flex-shrink-0" />
              <div>
                <div class="text-xs font-medium" :class="accessType === opt.value ? 'text-brand-700' : 'text-gray-700'">
                  {{ opt.label }}
                </div>
                <div class="text-[10px] text-gray-400 mt-0.5">{{ opt.desc }}</div>
              </div>
            </label>
          </div>
          <div v-if="accessType === 'password'" class="mt-3">
            <label class="text-xs text-gray-500 block mb-1">Password</label>
            <input v-model="accessPassword" type="password" placeholder="Set portal password"
              class="w-full text-xs border border-gray-200 rounded px-2 py-1.5 focus:outline-none focus:ring-1 focus:ring-brand-400"
            />
          </div>
        </section>

        <!-- About / documentation: shown in the published portal's About panel together with
             each layer's catalog metadata and public data links -->
        <section>
          <p class="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-2">About this portal</p>
          <textarea v-model="description" rows="5"
            class="w-full text-xs border border-gray-200 rounded px-2 py-1.5 focus:outline-none focus:ring-1 focus:ring-brand-400"
            placeholder="Documentation shown to portal visitors (About panel). Markdown supported: # headings, **bold**, [links](https://…), - lists."
          ></textarea>
          <p class="text-[10px] text-gray-400 mt-1">
            Visitors also see each layer's abstract, license and public data links (set them via the
            globe icon in My Data).
          </p>
        </section>

      </div>

      <!-- Save footer -->
      <div class="p-4 border-t border-gray-200 space-y-2">
        <button @click="save" :disabled="busy" class="btn-secondary w-full justify-center text-sm">
          Save changes
        </button>
        <p v-if="saveMsg" class="text-xs text-center"
          :class="saveMsg.type === 'ok' ? 'text-green-600' : 'text-red-600'">
          {{ saveMsg.text }}
        </p>
      </div>
    </div>

    <!-- Map preview -->
    <div class="flex-1 relative bg-gray-100">
      <div id="portal-preview-map" class="w-full h-full" />

      <!-- GeoParquet detail fetch in flight (mirrors the published portal's loading pill) -->
      <div v-if="deckLoading > 0"
        class="absolute top-3 left-1/2 -translate-x-1/2 z-20 flex items-center gap-2 bg-white/95 shadow-md border border-gray-200 rounded-full px-3.5 py-1.5 text-xs font-medium text-gray-700 pointer-events-none">
        <span class="inline-block w-3 h-3 rounded-full border-2 border-blue-500 border-t-transparent animate-spin" />
        Loading features…
      </div>

      <!-- Zoom to all layers. The current view is saved on "Save changes" and becomes
           the published portal's initial extent. -->
      <button v-if="layerConfigs.length" @click="zoomToAll"
        class="absolute top-3 left-3 z-10 flex items-center gap-1.5 bg-white/95 hover:bg-white shadow-md border border-gray-200 rounded-lg px-2.5 py-1.5 text-xs font-medium text-gray-700"
        title="Zoom to the full extent of all layers. The current view is saved on Save and becomes the published portal's starting view.">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M3 8V5a2 2 0 0 1 2-2h3M16 3h3a2 2 0 0 1 2 2v3M21 16v3a2 2 0 0 1-2 2h-3M8 21H5a2 2 0 0 1-2-2v-3" />
        </svg>
        Zoom to all
      </button>

      <div v-if="!layerConfigs.length"
        class="absolute inset-0 flex items-center justify-center pointer-events-none">
        <span class="text-xs text-gray-400 bg-white/80 px-3 py-1.5 rounded-full">
          Add layers to see a preview
        </span>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import { useRoute } from 'vue-router'
import { usePortalsStore } from '@/stores/portals'
import { useDataStore } from '@/stores/data'
import { listTemplates, getRasterStats, getVectorFeatures } from '@/api'
import { useMaplibre } from '@/composables/useMaplibre'
import { MapboxOverlay } from '@deck.gl/mapbox'
import { GeoJsonLayer } from '@deck.gl/layers'
import { ExternalLinkIcon } from '@/views/icons'
import LayerPanel from '@/components/portal/LayerPanel.vue'

const route = useRoute()
const portalsStore = usePortalsStore()
const dataStore = useDataStore()

const portal = ref(null)
const layerConfigs = ref([])
const selectedTemplate = ref('minimal')
const templates = ref([])
const showAddLayer = ref(false)
const lastAddedKey = ref(null)
const accessType = ref('public')

// Drag-to-reorder layers (top of list = top of map)
const dragIndex = ref(null)
function onDragStart(i) { dragIndex.value = i }
function onDragOver(i) {
  if (dragIndex.value === null || dragIndex.value === i) return
  const arr = layerConfigs.value
  const [moved] = arr.splice(dragIndex.value, 1)
  arr.splice(i, 0, moved)
  dragIndex.value = i
}
function onDragEnd() { dragIndex.value = null }
const accessPassword = ref('')
const busy = ref(false)
const saveMsg = ref(null)
const description = ref('')  // About-panel documentation (markdown), baked at publish

const accessOptions = [
  { value: 'public',   label: 'Public',   desc: 'Anyone with the URL can view' },
  { value: 'password', label: 'Password', desc: 'Require a password to view' },
  { value: 'private',  label: 'Private',  desc: 'Only signed-in users can view' },
]

const { map, loaded, applyStyle, fitToBbox, jumpTo } = useMaplibre('portal-preview-map')

// Admin-pinned view (center/zoom) for the published portal; null = fit to all layers.
const savedView = ref(null)

onMounted(async () => {
  await Promise.all([portalsStore.refresh(), dataStore.refresh()])
  portal.value = portalsStore.portals.find(p => p.id === parseInt(route.params.id))
  if (portal.value) {
    layerConfigs.value = portal.value.layer_configs || []
    selectedTemplate.value = portal.value.template_id
    accessType.value = portal.value.access_type || 'public'
    savedView.value = portal.value.initial_view || null
    description.value = portal.value.description || ''
  }
  const { data } = await listTemplates()
  templates.value = data
})

// ── deck.gl overlay for GeoParquet layers ───────────────────────────────────
// GeoParquet layers are too big for a MapLibre geojson source, so they render in a deck.gl
// MapboxOverlay (added as a control → survives setStyle) fed by the viewport query
// (getVectorFeatures → covering-column-pruned GeoJSON). Refetched on pan/zoom (moveend) and when a
// new layer first appears; pure style edits rebuild from cached data without a network refetch.
let deckOverlay = null
const deckData = {}        // layer_id → cached FeatureCollection for the current view
const deckLoading = ref(0) // detail fetches in flight → shows the "Loading features…" pill
// Per-viewport feature cap, scaled by zoom (matches portal.js): a zoomed-out view is a capped
// subset either way, and a flat 50k limit made low-zoom responses tens of MB (slow query,
// slow JSON parse). More than the eye resolves at each band.
function deckLimit() {
  const z = map.value ? map.value.getZoom() : 10
  return z < 7 ? 10000 : z < 10 ? 25000 : 50000
}

// ── detail/overview switch (mirrors templates/shared/portal.js — keep in sync) ──────────────
// A HEAVY prepped layer whose viewport spans more than DECK_MAX_FILES partition files renders as
// a density-shaded partition-grid overview built from the layer's manifest (per-cell counts —
// instant, zero data reads) instead of per-feature detail; zooming in loads real features.
// LIGHT layers (total features ≤ DECK_DETAIL_MAX) always show full detail at every zoom.
const DECK_MAX_FILES = 16  // keep equal to portal.js WASM_MAX_FILES (same switch moment)
const DECK_DETAIL_MAX = 50000
const DECK_DETAIL_MAX_ROWS = 400000  // keep equal to portal.js DETAIL_MAX_ROWS
const deckManifests = {}   // layer_id → manifest object | 'none'

async function deckManifest(id) {
  if (deckManifests[id] !== undefined) return deckManifests[id]
  try {
    const r = await fetch(`/api/data/vector/${id}/parquet/manifest.json`)
    const m = r.ok ? await r.json() : null
    deckManifests[id] = (m && m.grid && m.cells && (!m.crs || m.crs === 'EPSG:4326')) ? m : 'none'
  } catch { deckManifests[id] = 'none' }
  return deckManifests[id]
}

// Same grid math as the server/portal.js: cell = ix*grid + iy, +1-cell pad for the FILE list.
// Rows are weighted by the fraction of each cell the viewport covers (whole-cell sums locked
// dense regions in overview mode forever — the pad alone spans ≥9 cells at any deep zoom).
// Keep in sync with portal.js viewportLoad.
function deckViewportLoad(m, b) {
  const g = m.grid, gsz = g.grid | 0, pad = 1, dx = g.spanx / gsz, dy = g.spany / gsz
  const ci = (v, lo, span) => Math.floor((v - lo) / (span || 1.0) * gsz)
  const ix0 = Math.max(0, ci(b[0], g.minx, g.spanx) - pad)
  const ix1 = Math.min(gsz - 1, ci(b[2], g.minx, g.spanx) + pad)
  const iy0 = Math.max(0, ci(b[1], g.miny, g.spany) - pad)
  const iy1 = Math.min(gsz - 1, ci(b[3], g.miny, g.spany) + pad)
  let files = 0, rows = 0
  if (ix0 <= ix1 && iy0 <= iy1)
    for (let ix = ix0; ix <= ix1; ix++)
      for (let iy = iy0; iy <= iy1; iy++) {
        const list = m.cells[String(ix * gsz + iy)] || []
        if (!list.length) continue
        const x0 = g.minx + ix * dx, y0 = g.miny + iy * dy
        const ox = Math.max(0, Math.min(b[2], x0 + dx) - Math.max(b[0], x0))
        const oy = Math.max(0, Math.min(b[3], y0 + dy) - Math.max(b[1], y0))
        const frac = Math.min(1, (ox * oy) / (dx * dy || 1))
        for (const f of list) { files += 1; rows += (f.rows || 0) * frac }
      }
  return { files, rows }
}

function deckOverviewGeojson(m) {
  if (m.__overviewFc) return m.__overviewFc
  const g = m.grid, gsz = g.grid | 0, dx = g.spanx / gsz, dy = g.spany / gsz
  let max = 0
  const counts = {}
  for (const k of Object.keys(m.cells)) {
    counts[k] = (m.cells[k] || []).reduce((a, f) => a + (f.rows || 0), 0)
    if (counts[k] > max) max = counts[k]
  }
  const features = Object.keys(m.cells).map(k => {
    const c = +k, ix = Math.floor(c / gsz), iy = c % gsz
    const x0 = g.minx + ix * dx, y0 = g.miny + iy * dy
    return {
      type: 'Feature',
      properties: { count: counts[k], density: max ? Math.sqrt(counts[k] / max) : 0 },
      geometry: { type: 'Polygon', coordinates: [[[x0, y0], [x0 + dx, y0], [x0 + dx, y0 + dy], [x0, y0 + dy], [x0, y0]]] },
    }
  })
  const fc = { type: 'FeatureCollection', features }
  fc.__overview = true
  m.__overviewFc = fc
  return fc
}

function hexToRgb(hex) {
  const h = String(hex || '#3b82f6').replace('#', '')
  const f = h.length === 3 ? h.split('').map(c => c + c).join('') : h
  const n = parseInt(f, 16)
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255]
}

// Visible GeoParquet layer configs that the deck overlay (not MapLibre) is responsible for.
// A layer that was explicitly tiled (ready PMTiles) uses the pmtiles:// fallback in the style instead.
function deckConfigs() {
  return [...layerConfigs.value].filter(cfg => {
    if (cfg.visible === false || cfg.layer_type !== 'vector') return false
    const layer = dataStore.vectorLayers.find(l => l.id === cfg.layer_id)
    return layer && layer.status === 'ready' && layer.storage_backend === 'geoparquet' &&
      !(layer.tile_status === 'ready' && layer.pmtiles_key)
  })
}

function makeDeckLayer(cfg) {
  const data = deckData[cfg.layer_id]
  if (!data) return null
  const layer = dataStore.vectorLayers.find(l => l.id === cfg.layer_id)
  const geom = (layer?.geometry_type || '').toLowerCase()
  const rgb = hexToRgb(cfg.style?.color || '#3b82f6')
  const opacity = cfg.opacity ?? 1.0
  const outline = hexToRgb(cfg.style?.outline_color || '#1d4ed8')
  const isPoly = geom.includes('polygon'), isLine = geom.includes('line')
  if (data.__overview) {
    // Large-scale representation: partition grid shaded by feature density (see portal.js twin).
    return new GeoJsonLayer({
      id: `deck_${cfg.layer_id}`,
      data,
      pickable: false,
      filled: true,
      stroked: true,
      getFillColor: f => [...rgb, Math.round(200 * opacity * f.properties.density)],
      getLineColor: [...rgb, Math.round(60 * opacity)],
      lineWidthUnits: 'pixels',
      getLineWidth: 0.5,
    })
  }
  return new GeoJsonLayer({
    id: `deck_${cfg.layer_id}`,
    data,
    pickable: false,
    filled: !isLine,
    stroked: true,
    getFillColor: [...rgb, Math.round(255 * opacity * (isPoly ? (cfg.style?.fill_opacity ?? 0.45) : 1))],
    getLineColor: isPoly ? [...outline, Math.round(255 * opacity)] : [...rgb, Math.round(255 * opacity)],
    lineWidthUnits: 'pixels',
    getLineWidth: cfg.style?.line_width ?? (isLine ? 2 : 1),
    lineWidthMinPixels: isLine ? (cfg.style?.line_width ?? 2) : 1,
    pointType: 'circle',
    pointRadiusUnits: 'pixels',
    getPointRadius: cfg.style?.radius ?? 5,
    pointRadiusMinPixels: 2,
  })
}

async function refreshDeck(refetch) {
  if (!deckOverlay || !map.value) return
  const configs = deckConfigs()
  if (refetch || configs.some(c => !deckData[c.layer_id])) {
    const b = map.value.getBounds()
    const nb = [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()]
    const bbox = nb.join(',')
    await Promise.all(configs.map(async cfg => {
      if (!refetch && deckData[cfg.layer_id]) return
      try {
        // Heavy prepped layer at large scale → density-grid overview from the manifest
        // (instant, no feature query). Light layers and zoomed-in views load real features.
        const m = await deckManifest(cfg.layer_id)
        if (m !== 'none' && (m.feature_count || 0) > DECK_DETAIL_MAX) {
          const load = deckViewportLoad(m, nb)
          if (load.files > DECK_MAX_FILES || load.rows > DECK_DETAIL_MAX_ROWS) {
            deckData[cfg.layer_id] = deckOverviewGeojson(m)
            return
          }
        }
        // Detail fetch: clear a stale overview grid immediately (never show the whole-extent
        // grid at a zoomed-in view while features load) — mirrors portal.js.
        if (deckData[cfg.layer_id] && deckData[cfg.layer_id].__overview) {
          deckData[cfg.layer_id] = { type: 'FeatureCollection', features: [] }
          deckOverlay.setProps({ layers: [...configs].reverse().map(makeDeckLayer).filter(Boolean) })
        }
        deckLoading.value++
        try {
          const { data } = await getVectorFeatures(cfg.layer_id, bbox, deckLimit())
          deckData[cfg.layer_id] = data
        } finally { deckLoading.value-- }
      } catch { deckData[cfg.layer_id] = deckData[cfg.layer_id] || { type: 'FeatureCollection', features: [] } }
    }))
  }
  // config[0] = top of list → must draw on top → last in the deck layer array.
  const layers = [...configs].reverse().map(makeDeckLayer).filter(Boolean)
  deckOverlay.setProps({ layers })
}

// Rebuild the preview style on any config/layer change, but only move the camera on
// the FIRST build (restore the saved view, else fit to all layers). After that, style
// edits (band/colour/etc.) must NOT yank the view — setStyle keeps the current camera.
let viewInitialized = false
watch([layerConfigs, loaded], () => {
  if (!loaded.value) return
  const { style, bounds } = buildPreviewStyle()
  applyStyle(style)
  refreshDeck(false)  // rebuild deck layers (fetch only newly-appeared geoparquet layers)
  if (!viewInitialized) {
    if (savedView.value) { jumpTo(savedView.value); viewInitialized = true }
    else if (bounds) { fitToBbox(bounds); viewInitialized = true }
  }
}, { deep: true })

// Point marker icons — mirror of templates/shared/portal.js. Generated on demand
// when a symbol layer references a missing icon image (styleimagemissing).
const markerSpecs = {}
watch(loaded, (v) => {
  if (!v || !map.value) return
  map.value.on('styleimagemissing', (e) => {
    if (!e.id || !e.id.startsWith('gd-pt-') || map.value.hasImage(e.id)) return
    const spec = markerSpecs[e.id]
    if (!spec) return
    const im = markerImage(spec.shape, spec.color, spec.size)
    try { map.value.addImage(e.id, im, { pixelRatio: im.pixelRatio }) } catch { /* ignore */ }
  })
  // deck.gl overlay (once): a control so it survives setStyle; refetch the viewport on pan/zoom.
  if (!deckOverlay) {
    deckOverlay = new MapboxOverlay({ interleaved: false, layers: [] })
    map.value.addControl(deckOverlay)
    map.value.on('moveend', () => refreshDeck(true))
    // Mid-gesture: hide the coarse overview grid the moment the viewport qualifies for detail —
    // don't wait for moveend + the fetch (mirrors portal.js).
    let moveRaf = false
    map.value.on('move', () => {
      if (moveRaf) return
      moveRaf = true
      requestAnimationFrame(() => {
        moveRaf = false
        if (!map.value) return
        const b = map.value.getBounds()
        const vb = [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()]
        let changed = false
        for (const cfg of deckConfigs()) {
          const data = deckData[cfg.layer_id]
          if (!data || !data.__overview) continue
          const m = deckManifests[cfg.layer_id]
          if (!m || m === 'none' || !m.grid) continue
          const load = deckViewportLoad(m, vb)
          const light = (m.feature_count || 0) <= DECK_DETAIL_MAX
          if (light || (load.files <= DECK_MAX_FILES && load.rows <= DECK_DETAIL_MAX_ROWS)) {
            deckData[cfg.layer_id] = { type: 'FeatureCollection', features: [] }
            changed = true
          }
        }
        if (changed) refreshDeck(false)
      })
    })
    refreshDeck(true)
  }
})
function starPts(cx, cy, r) {
  const p = []
  for (let i = 0; i < 10; i++) { const a = -Math.PI / 2 + i * Math.PI / 5, rr = (i % 2) ? r * 0.45 : r; p.push([cx + Math.cos(a) * rr, cy + Math.sin(a) * rr]) }
  return p
}
function crossPts(cx, cy, r) {
  const t = r * 0.38
  return [[-t, -r], [t, -r], [t, -t], [r, -t], [r, t], [t, t], [t, r], [-t, r], [-t, t], [-r, t], [-r, -t], [-t, -t]].map(d => [cx + d[0], cy + d[1]])
}
function markerImage(shape, color, size) {
  const dpr = 2, r = Math.max(3, Number(size) || 5), stroke = Math.max(1, r * 0.28)
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
  ctx.strokeStyle = '#ffffff'; ctx.lineWidth = stroke; ctx.stroke()
  const d = ctx.getImageData(0, 0, dim * dpr, dim * dpr)
  return { width: dim * dpr, height: dim * dpr, data: d.data, pixelRatio: dpr }
}

function buildPreviewStyle() {
  const style = {
    version: 8,
    glyphs: 'https://demotiles.maplibre.org/font/{fontstack}/{range}.pbf',
    sources: {
      basemap: {
        type: 'raster',
        tiles: [
          'https://a.basemaps.cartocdn.com/light_all/{z}/{x}/{y}@2x.png',
          'https://b.basemaps.cartocdn.com/light_all/{z}/{x}/{y}@2x.png',
        ],
        tileSize: 256,
        attribution: '© OpenStreetMap contributors © CARTO',
      },
    },
    layers: [{ id: 'basemap', type: 'raster', source: 'basemap' }],
  }

  // Merge every visible layer's bbox (skipping non-lon/lat bboxes, e.g. an old
  // projected raster) so "fit"/zoom-to-all covers all layers, not just the last one.
  let bounds = null
  const expandBounds = (b) => {
    const ok = Array.isArray(b) && b.length === 4 &&
      b[0] >= -180 && b[2] <= 180 && b[0] < b[2] && b[1] >= -90 && b[3] <= 90 && b[1] < b[3]
    if (!ok) return
    bounds = bounds
      ? [Math.min(bounds[0], b[0]), Math.min(bounds[1], b[1]), Math.max(bounds[2], b[2]), Math.max(bounds[3], b[3])]
      : b.slice()
  }

  // config[0] is the top of the list → draw it on top → build in reverse.
  for (const cfg of [...layerConfigs.value].reverse()) {
    if (cfg.visible === false) continue
    if (cfg.layer_type === 'vector') {
      const layer = dataStore.vectorLayers.find(l => l.id === cfg.layer_id)
      if (!layer || layer.status !== 'ready') continue

      const srcId = `vector_${layer.id}`
      let sourceLayer
      if (layer.storage_backend === 'geoparquet') {
        // File-backed (GeoParquet). PRIMARY display = a deck.gl overlay fed by the viewport query
        // (rendered outside this MapLibre style — see refreshDeck), so EXCLUDE the layer here and
        // just keep its bbox for zoom-to-all. FALLBACK: a layer explicitly tiled (ready PMTiles)
        // renders via the pmtiles:// vector source instead.
        if (!(layer.tile_status === 'ready' && layer.pmtiles_key)) { expandBounds(layer.bbox); continue }
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
      const color = cfg.style?.color || '#3b82f6'
      const opacity = cfg.opacity ?? 1.0
      const geom = (layer.geometry_type || '').toLowerCase()

      if (geom.includes('polygon')) {
        style.layers.push({
          id: srcId, type: 'fill', source: srcId, 'source-layer': sourceLayer,
          paint: {
            'fill-color': color,
            'fill-opacity': opacity * (cfg.style?.fill_opacity ?? 0.45),
            'fill-outline-color': cfg.style?.outline_color || '#1d4ed8',
          },
        })
      } else if (geom.includes('line')) {
        const linePaint = { 'line-color': color, 'line-width': cfg.style?.line_width ?? 2, 'line-opacity': opacity }
        if (cfg.style?.lineType === 'dashed') linePaint['line-dasharray'] = [2, 1.5]
        else if (cfg.style?.lineType === 'dotted') linePaint['line-dasharray'] = [0.4, 1.8]
        style.layers.push({
          id: srcId, type: 'line', source: srcId, 'source-layer': sourceLayer, paint: linePaint,
        })
      } else {
        // Points render as a symbol layer with a runtime-generated icon (so shapes
        // work on raster basemaps). Icon id encodes the style so it refreshes on change.
        const shape = cfg.style?.marker || 'circle'
        const mSize = cfg.style?.radius ?? 5
        const iconId = `gd-pt-${layer.id}-${shape}-${String(color).replace('#', '')}-${mSize}`
        markerSpecs[iconId] = { shape, color, size: mSize }
        style.layers.push({
          id: srcId, type: 'symbol', source: srcId, 'source-layer': sourceLayer,
          layout: { 'icon-image': iconId, 'icon-allow-overlap': true, 'icon-ignore-placement': true },
          paint: { 'icon-opacity': opacity },
        })
      }

      expandBounds(layer.bbox)

    } else if (cfg.layer_type === 'raster') {
      const layer = dataStore.rasterLayers.find(l => l.id === cfg.layer_id)
      if (!layer || layer.status !== 'ready' || !layer.tile_url) continue

      const srcId = `raster_${layer.id}`
      const absTileUrl = rasterTilesUrl(layer.tile_url, cfg.style)
      style.sources[srcId] = { type: 'raster', tiles: [absTileUrl], tileSize: 256 }
      style.layers.push({
        id: srcId, type: 'raster', source: srcId,
        paint: { 'raster-opacity': cfg.opacity ?? 1.0 },
      })
      expandBounds(layer.bbox)

    } else if (cfg.layer_type === 'external') {
      const src = dataStore.externalSources.find(s => s.id === cfg.layer_id)
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

  return { style, bounds }
}

// Build a raster tile URL from the layer's base URL + the configured raster style.
function rasterTilesUrl(baseTileUrl, style) {
  const base = (baseTileUrl || '').split('&')[0]  // s3 key has no '&', so this keeps ?url=...
  const params = []
  const bands = Array.isArray(style?.bidx) ? style.bidx.filter(b => b != null) : []
  bands.forEach(b => params.push(`bidx=${b}`))
  if (style?.rescale) params.push(`rescale=${style.rescale}`)
  if (style?.algorithm) {
    params.push(`algorithm=${style.algorithm}`)
    if (style.algorithm === 'hillshade' && style.zfactor && Number(style.zfactor) !== 1) {
      params.push(`expression=b1*${style.zfactor}`)
    }
  } else if (style?.colormap && bands.length !== 3) {
    params.push(`colormap_name=${style.colormap}`)
  }
  const url = base + (params.length ? '&' + params.join('&') : '')
  return url.startsWith('/') ? location.origin + url : url
}

const availableLayers = computed(() => [
  ...dataStore.vectorLayers.filter(l => l.status === 'ready').map(l => ({ ...l, type: 'vector' })),
  ...dataStore.rasterLayers.filter(l => l.status === 'ready').map(l => ({ ...l, type: 'raster' })),
  ...dataStore.externalSources.map(s => ({ ...s, type: 'external' })),
].filter(l => !layerConfigs.value.some(c => c.layer_id === l.id && c.layer_type === l.type)))

const LAYER_COLORS = [
  '#3b82f6', '#ef4444', '#10b981', '#f59e0b', '#8b5cf6',
  '#ec4899', '#06b6d4', '#84cc16', '#f97316', '#6366f1',
]

function nextColor() {
  const used = layerConfigs.value.map(c => c.style?.color).filter(Boolean)
  return LAYER_COLORS.find(c => !used.includes(c)) || LAYER_COLORS[layerConfigs.value.length % LAYER_COLORS.length]
}

async function addLayer(layer) {
  const ds = layer.default_style
  let style
  if (layer.type === 'vector') {
    style = ds?.style ?? { color: nextColor() }
  } else if (layer.type === 'external') {
    // External vector (WFS) gets a colour; external raster (WMS/XYZ) has no style.
    style = layer.kind === 'vector' ? { color: nextColor() } : {}
  } else {
    style = {}
    if (ds?.colormap) style.colormap = ds.colormap
    if (ds?.rescale) style.rescale = ds.rescale
    if (ds?.algorithm) style.algorithm = ds.algorithm
    if (ds?.zfactor != null) style.zfactor = ds.zfactor
    if (Array.isArray(ds?.bidx) && ds.bidx.length) style.bidx = ds.bidx.slice()
  }
  // Add to the top of the list (and the top of the map).
  layerConfigs.value.unshift({
    layer_id: layer.id,
    layer_type: layer.type,
    visible: true,
    opacity: ds?.opacity ?? 1.0,
    style,
    popup_fields: ds?.popup_fields ?? [],
  })
  lastAddedKey.value = `${layer.type}-${layer.id}`
  showAddLayer.value = false

  // Auto-stretch a freshly added raster that has no stretch yet
  if (layer.type === 'raster' && !style.rescale) {
    try {
      const { data } = await getRasterStats(layer.id)
      if (data?.rescale) {
        const idx = layerConfigs.value.findIndex(c => c.layer_id === layer.id && c.layer_type === 'raster')
        if (idx !== -1) {
          layerConfigs.value[idx] = {
            ...layerConfigs.value[idx],
            style: { ...layerConfigs.value[idx].style, rescale: data.rescale },
          }
        }
      }
    } catch { /* leave unstretched */ }
  }
}

function zoomToLayer(cfg) {
  const list = cfg.layer_type === 'external' ? dataStore.externalSources
    : cfg.layer_type === 'vector' ? dataStore.vectorLayers : dataStore.rasterLayers
  const layer = list.find(l => l.id === cfg.layer_id)
  if (layer?.bbox) fitToBbox(layer.bbox)
}

// Fit the preview to the merged extent of all (visible) layers.
function zoomToAll() {
  const { bounds } = buildPreviewStyle()
  if (bounds) fitToBbox(bounds)
}

// The map's current center/zoom — persisted so the published portal opens here.
function currentView() {
  const m = map.value
  if (!m) return null
  const c = m.getCenter()
  return {
    center: [c.lng, c.lat],
    zoom: m.getZoom(),
    bearing: m.getBearing(),
    pitch: m.getPitch(),
  }
}

async function save() {
  if (!portal.value) return
  busy.value = true
  saveMsg.value = null
  try {
    const view = currentView()
    if (view) savedView.value = view
    const payload = {
      layer_configs: layerConfigs.value,
      template_id: selectedTemplate.value,
      access_type: accessType.value,
      initial_view: view,
      description: description.value,
    }
    if (accessType.value === 'password' && accessPassword.value) {
      payload.access_password = accessPassword.value
    }
    const updated = await portalsStore.update(portal.value.id, payload)
    portal.value = updated
    accessPassword.value = ''
    saveMsg.value = { type: 'ok', text: 'Saved' }
    setTimeout(() => { saveMsg.value = null }, 3000)
  } catch (err) {
    saveMsg.value = { type: 'err', text: err.response?.data?.detail || err.message }
  } finally {
    busy.value = false
  }
}

async function handlePublish() {
  await save()
  if (saveMsg.value?.type === 'err') return
  busy.value = true
  try {
    const updated = await portalsStore.publish(portal.value.id)
    portal.value = updated
  } catch (err) {
    saveMsg.value = { type: 'err', text: err.response?.data?.detail || err.message }
  } finally {
    busy.value = false
  }
}
</script>
