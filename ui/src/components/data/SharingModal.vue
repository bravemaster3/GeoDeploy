<template>
  <Teleport to="body">
  <div class="fixed inset-0 bg-gray-900/50 backdrop-blur-sm flex items-center justify-center z-50 p-4">
    <div class="card w-full max-w-md p-6 space-y-4 shadow-2xl">
      <div class="flex items-center justify-between">
        <h2 class="text-lg font-semibold">Data sharing</h2>
        <button @click="$emit('close')" class="text-gray-400 hover:text-gray-600 text-xl leading-none">&times;</button>
      </div>

      <p class="text-xs text-gray-500">
        Shared layers are listed in this instance's public data catalog
        (<a :href="stacUrl" target="_blank" class="text-brand-600 hover:underline font-mono">/api/stac</a>)
        with ready-to-use access URLs — anyone can find and load them in QGIS, DuckDB, or scripts.
        The metadata below is what consumers see in the catalog.
      </p>

      <!-- Share toggle -->
      <label class="flex items-center gap-3 p-3 rounded-lg border cursor-pointer transition-colors"
        :class="form.is_public ? 'border-emerald-300 bg-emerald-50' : 'border-gray-200'">
        <input id="gd-share-public" name="gd-share-public" type="checkbox" v-model="form.is_public" class="w-4 h-4 accent-emerald-600" />
        <span class="text-sm font-medium">{{ form.is_public ? 'Shared in the public catalog' : 'Not shared (private)' }}</span>
      </label>

      <div>
        <label class="text-xs text-gray-500 block mb-1">Abstract</label>
        <textarea id="gd-share-abstract" name="gd-share-abstract" v-model="form.abstract" rows="3" class="input w-full text-sm"
          placeholder="What is this dataset? Coverage, vintage, method…"></textarea>
      </div>

      <div>
        <label class="text-xs text-gray-500 block mb-1">Keywords <span class="text-gray-400">(comma-separated)</span></label>
        <input id="gd-share-keywords" name="gd-share-keywords" v-model="form.keywords" type="text" class="input w-full text-sm" placeholder="landcover, france, 2018" />
      </div>

      <div class="grid grid-cols-2 gap-3">
        <div>
          <label class="text-xs text-gray-500 block mb-1">License</label>
          <input id="gd-share-license" name="gd-share-license" v-model="form.license" type="text" list="gd-licenses" class="input w-full text-sm" placeholder="CC-BY-4.0" />
          <datalist id="gd-licenses">
            <option value="CC-BY-4.0" /><option value="CC-BY-SA-4.0" /><option value="CC0-1.0" />
            <option value="ODbL-1.0" /><option value="proprietary" />
          </datalist>
        </div>
        <div>
          <label class="text-xs text-gray-500 block mb-1">Attribution</label>
          <input id="gd-share-attribution" name="gd-share-attribution" v-model="form.attribution" type="text" class="input w-full text-sm" placeholder="© Provider" />
        </div>
      </div>

      <div class="flex items-center justify-end gap-3 pt-1">
        <span v-if="error" class="text-xs text-red-600 mr-auto">{{ error }}</span>
        <button @click="$emit('close')" class="text-sm text-gray-600 hover:text-gray-800 px-3 py-2">Cancel</button>
        <button @click="save" :disabled="saving"
          class="text-sm font-semibold text-white bg-brand-600 hover:bg-brand-700 disabled:opacity-60 rounded-lg px-4 py-2">
          {{ saving ? 'Saving…' : 'Save' }}
        </button>
      </div>
    </div>
  </div>
  </Teleport>
</template>

<script setup>
import { reactive, ref, computed } from 'vue'
import { setVectorSharing, setRasterSharing } from '@/api'

const props = defineProps({
  layer: Object,
  layerType: { type: String, default: 'vector' },  // 'vector' | 'raster'
})
const emit = defineEmits(['close'])

const form = reactive({
  is_public: !!props.layer.is_public,
  abstract: props.layer.abstract || '',
  keywords: props.layer.keywords || '',
  license: props.layer.license || '',
  attribution: props.layer.attribution || '',
})
const saving = ref(false)
const error = ref('')
const stacUrl = computed(() => `${location.origin}/api/stac`)

async function save() {
  saving.value = true
  error.value = ''
  try {
    const fn = props.layerType === 'raster' ? setRasterSharing : setVectorSharing
    const { data } = await fn(props.layer.id, {
      is_public: form.is_public,
      abstract: form.abstract || null,
      keywords: form.keywords || null,
      license: form.license || null,
      attribution: form.attribution || null,
    })
    Object.assign(props.layer, data)
    emit('close')
  } catch (e) {
    error.value = 'Could not save — try again.'
  } finally {
    saving.value = false
  }
}
</script>
