<template>
  <div class="flex h-screen">
    <!-- Left panel -->
    <div class="w-80 flex-shrink-0 bg-white border-r border-gray-200 flex flex-col overflow-hidden">
      <div class="px-4 py-3 border-b border-gray-200 flex items-center justify-between">
        <button @click="$router.push('/portals')" class="text-sm text-gray-500 hover:text-gray-900">← Back</button>
        <span class="text-sm font-semibold truncate mx-2">{{ portal?.title }}</span>
        <button @click="handlePublish" :disabled="busy || !portal" class="btn-primary text-xs py-1.5">
          {{ portal?.published ? 'Re-publish' : 'Publish' }}
        </button>
      </div>

      <div class="flex-1 overflow-y-auto">
        <!-- Layers -->
        <section class="p-4 border-b border-gray-100">
          <div class="flex items-center justify-between mb-3">
            <h3 class="text-xs font-semibold text-gray-500 uppercase tracking-wider">Layers</h3>
            <button @click="showAddLayer = !showAddLayer" class="text-xs text-brand-600 hover:text-brand-700 font-medium">+ Add</button>
          </div>

          <!-- Add layer picker -->
          <div v-if="showAddLayer" class="mb-3 p-2 bg-gray-50 rounded-lg text-xs space-y-1 max-h-40 overflow-y-auto">
            <div v-for="layer in availableLayers" :key="`${layer.type}-${layer.id}`"
              class="flex items-center justify-between p-1.5 hover:bg-white rounded cursor-pointer"
              @click="addLayer(layer)"
            >
              <span>{{ layer.name }}</span>
              <span class="text-gray-400">{{ layer.type }}</span>
            </div>
          </div>

          <div v-if="!layerConfigs.length" class="text-xs text-gray-400">No layers added yet.</div>
          <LayerPanel v-for="(cfg, i) in layerConfigs" :key="i" :config="cfg"
            @remove="layerConfigs.splice(i, 1)"
            @update="layerConfigs[i] = { ...layerConfigs[i], ...$event }"
          />
        </section>

        <!-- Template -->
        <section class="p-4 border-b border-gray-100">
          <h3 class="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-3">Template</h3>
          <div class="grid grid-cols-2 gap-2">
            <button v-for="t in templates" :key="t.id"
              class="p-2 rounded-lg border text-xs font-medium transition-colors"
              :class="selectedTemplate === t.id ? 'border-brand-500 bg-brand-50 text-brand-700' : 'border-gray-200 hover:border-gray-300'"
              @click="selectedTemplate = t.id"
            >{{ t.name }}</button>
          </div>
        </section>
      </div>

      <!-- Save -->
      <div class="p-4 border-t border-gray-200">
        <button @click="save" :disabled="busy" class="btn-secondary w-full justify-center text-sm">
          Save changes
        </button>
        <p v-if="saveMsg" class="text-xs text-center mt-2" :class="saveMsg.type === 'ok' ? 'text-green-600' : 'text-red-600'">
          {{ saveMsg.text }}
        </p>
      </div>
    </div>

    <!-- Map preview -->
    <div class="flex-1 relative">
      <div id="portal-preview-map" class="w-full h-full" />
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import { useRoute } from 'vue-router'
import { usePortalsStore } from '@/stores/portals'
import { useDataStore } from '@/stores/data'
import { listTemplates } from '@/api'
import { useMaplibre } from '@/composables/useMaplibre'
import LayerPanel from '@/components/portal/LayerPanel.vue'

const route = useRoute()
const portalsStore = usePortalsStore()
const dataStore = useDataStore()

const portal = ref(null)
const layerConfigs = ref([])
const selectedTemplate = ref('minimal')
const templates = ref([])
const showAddLayer = ref(false)
const busy = ref(false)
const saveMsg = ref(null)

const { map, loaded } = useMaplibre('portal-preview-map')

onMounted(async () => {
  await Promise.all([portalsStore.refresh(), dataStore.refresh()])
  portal.value = portalsStore.portals.find(p => p.id === parseInt(route.params.id))
  if (portal.value) {
    layerConfigs.value = portal.value.layer_configs || []
    selectedTemplate.value = portal.value.template_id
  }
  const { data } = await listTemplates()
  templates.value = data
})

const availableLayers = computed(() => [
  ...dataStore.vectorLayers.filter(l => l.status === 'ready').map(l => ({ ...l, type: 'vector' })),
  ...dataStore.rasterLayers.filter(l => l.status === 'ready').map(l => ({ ...l, type: 'raster' })),
].filter(l => !layerConfigs.value.some(c => c.layer_id === l.id && c.layer_type === l.type)))

function addLayer(layer) {
  layerConfigs.value.push({
    layer_id: layer.id,
    layer_type: layer.type,
    visible: true,
    opacity: 1.0,
    style: {},
    popup_fields: [],
  })
  showAddLayer.value = false
}

async function save() {
  if (!portal.value) return
  busy.value = true
  saveMsg.value = null
  try {
    await portalsStore.update(portal.value.id, {
      layer_configs: layerConfigs.value,
      template_id: selectedTemplate.value,
    })
    saveMsg.value = { type: 'ok', text: 'Saved' }
  } catch (err) {
    saveMsg.value = { type: 'err', text: err.message }
  } finally {
    busy.value = false
  }
}

async function handlePublish() {
  await save()
  busy.value = true
  try {
    await portalsStore.publish(portal.value.id)
    portal.value = portalsStore.portals.find(p => p.id === portal.value.id)
  } catch (err) {
    alert(err.response?.data?.detail || err.message)
  } finally {
    busy.value = false
  }
}
</script>
