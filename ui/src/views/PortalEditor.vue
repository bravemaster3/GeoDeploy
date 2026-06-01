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
          <LayerPanel v-for="(cfg, i) in layerConfigs" :key="`${cfg.layer_type}-${cfg.layer_id}`"
            :config="cfg"
            :initial-expanded="`${cfg.layer_type}-${cfg.layer_id}` === lastAddedKey"
            @remove="layerConfigs.splice(i, 1)"
            @update="layerConfigs[i] = { ...layerConfigs[i], ...$event }"
            @zoom="zoomToLayer(cfg)"
          />
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
import { listTemplates, getRasterStats } from '@/api'
import { useMaplibre } from '@/composables/useMaplibre'
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
const accessPassword = ref('')
const busy = ref(false)
const saveMsg = ref(null)

const accessOptions = [
  { value: 'public',   label: 'Public',   desc: 'Anyone with the URL can view' },
  { value: 'password', label: 'Password', desc: 'Require a password to view' },
  { value: 'private',  label: 'Private',  desc: 'Only signed-in users can view' },
]

const { map, loaded, applyStyle, fitToBbox } = useMaplibre('portal-preview-map')

onMounted(async () => {
  await Promise.all([portalsStore.refresh(), dataStore.refresh()])
  portal.value = portalsStore.portals.find(p => p.id === parseInt(route.params.id))
  if (portal.value) {
    layerConfigs.value = portal.value.layer_configs || []
    selectedTemplate.value = portal.value.template_id
    accessType.value = portal.value.access_type || 'public'
  }
  const { data } = await listTemplates()
  templates.value = data
})

// Rebuild preview whenever configs or layer data change
watch([layerConfigs, loaded], () => {
  if (!loaded.value) return
  const { style, bounds } = buildPreviewStyle()
  applyStyle(style)
  if (bounds) fitToBbox(bounds)
}, { deep: true })

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

  let bounds = null

  for (const cfg of layerConfigs.value) {
    if (cfg.layer_type === 'vector') {
      const layer = dataStore.vectorLayers.find(l => l.id === cfg.layer_id)
      if (!layer || layer.status !== 'ready') continue

      const srcId = `vector_${layer.id}`
      style.sources[srcId] = {
        type: 'vector',
        tiles: [`${location.origin}/tiles/${layer.schema_name}.${layer.table_name}/{z}/{x}/{y}`],
        minzoom: 0, maxzoom: 22,
      }
      const sourceLayer = `${layer.schema_name}.${layer.table_name}`
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
        style.layers.push({
          id: srcId, type: 'line', source: srcId, 'source-layer': sourceLayer,
          paint: { 'line-color': color, 'line-width': cfg.style?.line_width ?? 2, 'line-opacity': opacity },
        })
      } else {
        style.layers.push({
          id: srcId, type: 'circle', source: srcId, 'source-layer': sourceLayer,
          paint: {
            'circle-color': color, 'circle-radius': cfg.style?.radius ?? 5,
            'circle-opacity': opacity, 'circle-stroke-width': 1, 'circle-stroke-color': '#fff',
          },
        })
      }

      if (layer.bbox) bounds = layer.bbox

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
      if (layer.bbox) bounds = layer.bbox
    }
  }

  return { style, bounds }
}

// Build a raster tile URL from the layer's base URL + the configured raster style.
function rasterTilesUrl(baseTileUrl, style) {
  const base = (baseTileUrl || '').split('&')[0]  // s3 key has no '&', so this keeps ?url=...
  const params = []
  if (style?.rescale) params.push(`rescale=${style.rescale}`)
  if (style?.algorithm) {
    params.push(`algorithm=${style.algorithm}`)
    if (style.algorithm === 'hillshade' && style.zfactor && Number(style.zfactor) !== 1) {
      params.push(`expression=b1*${style.zfactor}`)
    }
  } else if (style?.colormap) {
    params.push(`colormap_name=${style.colormap}`)
  }
  const url = base + (params.length ? '&' + params.join('&') : '')
  return url.startsWith('/') ? location.origin + url : url
}

const availableLayers = computed(() => [
  ...dataStore.vectorLayers.filter(l => l.status === 'ready').map(l => ({ ...l, type: 'vector' })),
  ...dataStore.rasterLayers.filter(l => l.status === 'ready').map(l => ({ ...l, type: 'raster' })),
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
  } else {
    style = {}
    if (ds?.colormap) style.colormap = ds.colormap
    if (ds?.rescale) style.rescale = ds.rescale
    if (ds?.algorithm) style.algorithm = ds.algorithm
    if (ds?.zfactor != null) style.zfactor = ds.zfactor
  }
  layerConfigs.value.push({
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
  const list = cfg.layer_type === 'vector' ? dataStore.vectorLayers : dataStore.rasterLayers
  const layer = list.find(l => l.id === cfg.layer_id)
  if (layer?.bbox) fitToBbox(layer.bbox)
}

async function save() {
  if (!portal.value) return
  busy.value = true
  saveMsg.value = null
  try {
    const payload = {
      layer_configs: layerConfigs.value,
      template_id: selectedTemplate.value,
      access_type: accessType.value,
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
