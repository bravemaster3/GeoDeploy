<template>
  <Teleport to="body">
  <div class="fixed inset-0 bg-gray-900/50 backdrop-blur-sm flex items-center justify-center z-50 p-4">
    <div class="card w-full max-w-md p-6 space-y-4 shadow-2xl max-h-[90vh] overflow-y-auto">
      <div class="flex items-center justify-between">
        <h2 class="text-lg font-semibold">Connect external source</h2>
        <button @click="$emit('close')" class="text-muted-foreground/70 hover:text-foreground text-xl leading-none">&times;</button>
      </div>

      <p class="text-xs text-muted-foreground">
        Display a third-party map service in your portals without importing it. Tiles/features are
        fetched from the provider — make sure their licence permits this and add an attribution.
      </p>

      <!-- Type -->
      <div>
        <label class="text-xs text-muted-foreground block mb-1">Service type</label>
        <div class="grid grid-cols-4 gap-2">
          <button v-for="t in types" :key="t.value" type="button"
            class="p-2 rounded-lg border text-xs font-medium transition-colors"
            :class="form.source_type === t.value ? 'border-primary bg-primary/10 text-primary' : 'border-border hover:border-muted-foreground/40 text-foreground/85'"
            @click="form.source_type = t.value">{{ t.label }}</button>
        </div>
        <p class="text-[10px] text-muted-foreground/70 mt-1">{{ typeHint }}</p>
      </div>

      <!-- Name -->
      <div>
        <label class="text-xs text-muted-foreground block mb-1">Display name</label>
        <input v-model="form.name" type="text" placeholder="e.g. National basemap" class="input w-full text-sm" />
      </div>

      <!-- URL -->
      <div>
        <label class="text-xs text-muted-foreground block mb-1">{{ urlLabel }}</label>
        <input v-model="form.url" type="text" :placeholder="urlPlaceholder" class="input w-full text-sm font-mono" />
      </div>

      <!-- Which layer of the service. Not every kind has one, and the ones that do call it
           different things — so the label names what THAT service calls it. -->
      <div v-if="needsLayerName">
        <label class="text-xs text-muted-foreground block mb-1">
          {{ layerNameLabel }}
          <span v-if="form.source_type === 'ogcapi'" class="text-muted-foreground/70">(optional if the URL names it)</span>
        </label>
        <input v-model="form.layer_name" type="text" :placeholder="layerNamePlaceholder" class="input w-full text-sm font-mono" />
      </div>

      <!-- The layer INSIDE a vector tile. A tile is a container of named layers, and a style that
           names none draws nothing at all — so this is asked for, and probed where the provider
           publishes it (a TileJSON, a PMTiles header). -->
      <div v-if="needsSourceLayer">
        <label class="text-xs text-muted-foreground block mb-1">
          Layer inside the tiles
          <span class="text-muted-foreground/70">(read automatically when the service publishes it)</span>
        </label>
        <input v-model="form.source_layer" type="text" placeholder="e.g. water" class="input w-full text-sm font-mono" />
      </div>

      <!-- WMTS tile matrix set. Nearly every server publishes the Web Mercator one beside its own. -->
      <div v-if="form.source_type === 'wmts'">
        <label class="text-xs text-muted-foreground block mb-1">
          Tile matrix set <span class="text-muted-foreground/70">(default GoogleMapsCompatible)</span>
        </label>
        <input v-model="form.matrix_set" type="text" placeholder="GoogleMapsCompatible" class="input w-full text-sm font-mono" />
      </div>

      <!-- Attribution -->
      <div>
        <label class="text-xs text-muted-foreground block mb-1">Attribution <span class="text-muted-foreground/70">(shown on the map)</span></label>
        <input v-model="form.attribution" type="text" placeholder="© Provider name" class="input w-full text-sm" />
      </div>

      <div v-if="error" class="text-sm text-red-400 bg-red-500/15 p-3 rounded-lg">{{ error }}</div>

      <div class="flex justify-end gap-2 pt-1">
        <button @click="$emit('close')" class="btn-secondary text-sm">Cancel</button>
        <button @click="submit" :disabled="!canSubmit || saving" class="btn-primary text-sm">
          {{ saving ? 'Connecting…' : 'Add source' }}
        </button>
      </div>
    </div>
  </div>
  </Teleport>
</template>

<script setup>
import { ref, computed } from 'vue'
import { createExternalSource } from '@/api'
import { useDataStore } from '@/stores/data'

const emit = defineEmits(['close'])
const dataStore = useDataStore()

const types = [
  { value: 'xyz', label: 'XYZ' },
  { value: 'wms', label: 'WMS' },
  { value: 'wmts', label: 'WMTS' },
  { value: 'wfs', label: 'WFS' },
  { value: 'ogcapi', label: 'OGC API' },
  { value: 'vectortile', label: 'Vector tiles' },
  { value: 'pmtiles', label: 'PMTiles' },
]

const form = ref({
  source_type: 'xyz',
  name: '',
  url: '',
  layer_name: '',
  source_layer: '',
  matrix_set: '',
  attribution: '',
})
const saving = ref(false)
const error = ref('')

const typeHint = computed(() => ({
  xyz: 'Raster tiles. Paste a tile template with {z}/{x}/{y} (a WMTS RESTful template works too).',
  wms: 'Rendered map images. Paste the WMS base URL and the layer name.',
  wmts: 'Tiled map images from a WMTS endpoint. Paste the service URL and the layer; a RESTful template is XYZ instead.',
  wfs: 'Vector features (validated on add, fetched as GeoJSON). Paste the WFS base URL and feature type.',
  ogcapi: 'OGC API - Features. Paste the landing page, the collection, or the items URL — all three work.',
  vectortile: 'A third-party vector tile set. Paste a {z}/{x}/{y}.pbf template or its TileJSON. Served through this instance, so the provider needs no CORS policy.',
  pmtiles: 'A remote PMTiles archive, read a tile at a time by this instance — the whole file is never downloaded, and the provider needs no CORS policy.',
}[form.value.source_type]))

//: Which kinds have more than one layer behind one address, and what that service calls it.
const LAYER_LABELS = {
  wms: 'WMS layer name (layers=)',
  wmts: 'WMTS layer',
  wfs: 'WFS feature type (typeName)',
  ogcapi: 'Collection id',
}
const needsLayerName = computed(() => !!LAYER_LABELS[form.value.source_type])
const layerNameLabel = computed(() => LAYER_LABELS[form.value.source_type] || 'Layer')
const layerNamePlaceholder = computed(() =>
  form.value.source_type === 'ogcapi' ? 'e.g. roads' : 'e.g. topp:states')
const needsSourceLayer = computed(() =>
  ['vectortile', 'pmtiles'].includes(form.value.source_type))

const urlLabel = computed(() => ({
  xyz: 'Tile URL template',
  vectortile: 'Tile template or TileJSON URL',
  pmtiles: 'Archive URL',
  ogcapi: 'Landing page, collection or items URL',
}[form.value.source_type] || 'Service base URL'))
const urlPlaceholder = computed(() => ({
  xyz: 'https://tiles.example.com/{z}/{x}/{y}.png',
  vectortile: 'https://tiles.example.com/{z}/{x}/{y}.pbf',
  pmtiles: 'https://files.example.com/basemap.pmtiles',
  ogcapi: 'https://example.com/ogc/collections/roads',
}[form.value.source_type] || 'https://example.com/geoserver/ows'))

const canSubmit = computed(() => {
  if (!form.value.name.trim() || !form.value.url.trim()) return false
  // A collection can be in the URL instead of the field, which is how people usually have it.
  if (form.value.source_type === 'ogcapi') return true
  if (needsLayerName.value && !form.value.layer_name.trim()) return false
  return true
})

async function submit() {
  if (!canSubmit.value) return
  saving.value = true
  error.value = ''
  try {
    const { data } = await createExternalSource({
      name: form.value.name.trim(),
      source_type: form.value.source_type,
      url: form.value.url.trim(),
      layer_name: form.value.layer_name.trim() || null,
      source_layer: form.value.source_layer.trim() || null,
      matrix_set: form.value.matrix_set.trim() || null,
      attribution: form.value.attribution.trim() || null,
    })
    dataStore.addExternal(data)
    emit('close')
  } catch (err) {
    error.value = err.response?.data?.detail || err.message
  } finally {
    saving.value = false
  }
}
</script>
