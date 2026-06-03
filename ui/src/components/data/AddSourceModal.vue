<template>
  <div class="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4">
    <div class="card w-full max-w-md p-6 space-y-4">
      <div class="flex items-center justify-between">
        <h2 class="text-lg font-semibold">Connect external source</h2>
        <button @click="$emit('close')" class="text-gray-400 hover:text-gray-600 text-xl leading-none">&times;</button>
      </div>

      <p class="text-xs text-gray-500">
        Display a third-party map service in your portals without importing it. Tiles/features are
        fetched from the provider — make sure their licence permits this and add an attribution.
      </p>

      <!-- Type -->
      <div>
        <label class="text-xs text-gray-500 block mb-1">Service type</label>
        <div class="grid grid-cols-3 gap-2">
          <button v-for="t in types" :key="t.value" type="button"
            class="p-2 rounded-lg border text-xs font-medium transition-colors"
            :class="form.source_type === t.value ? 'border-brand-500 bg-brand-50 text-brand-700' : 'border-gray-200 hover:border-gray-300 text-gray-700'"
            @click="form.source_type = t.value">{{ t.label }}</button>
        </div>
        <p class="text-[10px] text-gray-400 mt-1">{{ typeHint }}</p>
      </div>

      <!-- Name -->
      <div>
        <label class="text-xs text-gray-500 block mb-1">Display name</label>
        <input v-model="form.name" type="text" placeholder="e.g. National basemap" class="input w-full text-sm" />
      </div>

      <!-- URL -->
      <div>
        <label class="text-xs text-gray-500 block mb-1">{{ urlLabel }}</label>
        <input v-model="form.url" type="text" :placeholder="urlPlaceholder" class="input w-full text-sm font-mono" />
      </div>

      <!-- Layer name (WMS/WFS) -->
      <div v-if="form.source_type !== 'xyz'">
        <label class="text-xs text-gray-500 block mb-1">
          {{ form.source_type === 'wms' ? 'WMS layer name (layers=)' : 'WFS feature type (typeName)' }}
        </label>
        <input v-model="form.layer_name" type="text" placeholder="e.g. topp:states" class="input w-full text-sm font-mono" />
      </div>

      <!-- Attribution -->
      <div>
        <label class="text-xs text-gray-500 block mb-1">Attribution <span class="text-gray-400">(shown on the map)</span></label>
        <input v-model="form.attribution" type="text" placeholder="© Provider name" class="input w-full text-sm" />
      </div>

      <div v-if="error" class="text-sm text-red-600 bg-red-50 p-3 rounded-lg">{{ error }}</div>

      <div class="flex justify-end gap-2 pt-1">
        <button @click="$emit('close')" class="btn-secondary text-sm">Cancel</button>
        <button @click="submit" :disabled="!canSubmit || saving" class="btn-primary text-sm">
          {{ saving ? 'Connecting…' : 'Add source' }}
        </button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import { createExternalSource } from '@/api'
import { useDataStore } from '@/stores/data'

const emit = defineEmits(['close'])
const dataStore = useDataStore()

const types = [
  { value: 'xyz', label: 'XYZ / WMTS' },
  { value: 'wms', label: 'WMS' },
  { value: 'wfs', label: 'WFS' },
]

const form = ref({
  source_type: 'xyz',
  name: '',
  url: '',
  layer_name: '',
  attribution: '',
})
const saving = ref(false)
const error = ref('')

const typeHint = computed(() => ({
  xyz: 'Raster tiles. Paste a tile template with {z}/{x}/{y} (a WMTS RESTful template works too).',
  wms: 'Rendered map images. Paste the WMS base URL and the layer name.',
  wfs: 'Vector features (validated on add, fetched as GeoJSON). Paste the WFS base URL and feature type.',
}[form.value.source_type]))

const urlLabel = computed(() => form.value.source_type === 'xyz' ? 'Tile URL template' : 'Service base URL')
const urlPlaceholder = computed(() => form.value.source_type === 'xyz'
  ? 'https://tiles.example.com/{z}/{x}/{y}.png'
  : 'https://example.com/geoserver/ows')

const canSubmit = computed(() => {
  if (!form.value.name.trim() || !form.value.url.trim()) return false
  if (form.value.source_type !== 'xyz' && !form.value.layer_name.trim()) return false
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
