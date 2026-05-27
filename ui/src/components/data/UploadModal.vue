<template>
  <div class="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4">
    <div class="card w-full max-w-md p-6 space-y-4">
      <div class="flex items-center justify-between">
        <h2 class="text-lg font-semibold">
          Upload {{ type === 'vector' ? 'vector file' : 'raster file' }}
        </h2>
        <button @click="$emit('close')" class="text-gray-400 hover:text-gray-600 text-xl leading-none">&times;</button>
      </div>

      <div v-if="!uploading"
        class="border-2 border-dashed border-gray-300 rounded-xl p-8 text-center cursor-pointer hover:border-brand-400 hover:bg-brand-50 transition-colors"
        @dragover.prevent @drop.prevent="onDrop" @click="fileInput.click()"
      >
        <UploadIcon class="w-8 h-8 text-gray-400 mx-auto mb-3" />
        <p class="text-sm font-medium text-gray-700">Drop file here or click to browse</p>
        <p class="text-xs text-gray-400 mt-1">{{ accept }}</p>
        <input ref="fileInput" type="file" class="hidden" :accept="acceptAttr" @change="onFileChange" />
      </div>

      <!-- Upload progress -->
      <div v-else class="space-y-3">
        <div class="flex justify-between text-sm">
          <span class="text-gray-600">{{ fileName }}</span>
          <span class="font-medium">{{ uploadProgress }}%</span>
        </div>
        <div class="h-2 bg-gray-100 rounded-full overflow-hidden">
          <div class="h-full bg-brand-500 rounded-full transition-all" :style="{ width: uploadProgress + '%' }" />
        </div>
        <p class="text-xs text-gray-500">Uploading… GeoDeploy will process it in the background.</p>
      </div>

      <div v-if="error" class="text-sm text-red-600 bg-red-50 p-3 rounded-lg">{{ error }}</div>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { UploadIcon } from '@/views/icons'
import { useUpload } from '@/composables/useUpload'

const props = defineProps({ type: String })
const emit = defineEmits(['close'])

const fileInput = ref(null)
const fileName = ref('')
const { uploading, uploadProgress, error, uploadFile } = useUpload()

const acceptMap = {
  vector: { accept: 'Shapefile (.zip), GeoJSON, GeoPackage (.gpkg)', acceptAttr: '.zip,.geojson,.json,.gpkg' },
  raster: { accept: 'GeoTIFF (.tif / .tiff)', acceptAttr: '.tif,.tiff' },
}
const { accept, acceptAttr } = acceptMap[props.type]

async function handleFile(file) {
  fileName.value = file.name
  try {
    await uploadFile(file, props.type)
    setTimeout(() => emit('close'), 800)
  } catch {}
}

function onDrop(e) {
  const file = e.dataTransfer.files[0]
  if (file) handleFile(file)
}

function onFileChange(e) {
  const file = e.target.files[0]
  if (file) handleFile(file)
}
</script>
