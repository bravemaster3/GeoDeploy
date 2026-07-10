<template>
  <Teleport to="body">
  <div class="fixed inset-0 bg-gray-900/50 backdrop-blur-sm flex items-center justify-center z-50 p-4">
    <div class="card w-full max-w-md p-6 space-y-4 shadow-2xl">
      <div class="flex items-center justify-between">
        <h2 class="text-lg font-semibold">
          {{ csvFile ? 'Import CSV as points' : (type === 'vector' ? 'Upload vector file' : 'Upload raster file') }}
        </h2>
        <button @click="$emit('close')" class="text-muted-foreground/70 hover:text-foreground text-xl leading-none">&times;</button>
      </div>

      <!-- Upload progress -->
      <div v-if="uploading" class="space-y-3">
        <div class="flex justify-between text-sm">
          <span class="text-muted-foreground">{{ fileName }}</span>
          <span class="font-medium">{{ uploadProgress }}%</span>
        </div>
        <div class="h-2 bg-muted rounded-full overflow-hidden">
          <div class="h-full bg-primary/100 rounded-full transition-all" :style="{ width: uploadProgress + '%' }" />
        </div>
        <p class="text-xs text-muted-foreground">Uploading… GeoDeploy will process it in the background.</p>
      </div>

      <!-- CSV options (X/Y/CRS) -->
      <div v-else-if="csvFile" class="space-y-3">
        <p class="text-sm font-medium text-foreground/85 truncate">{{ csvFile.name }}</p>
        <div class="flex gap-2">
          <div class="flex-1 min-w-0">
            <label class="text-xs text-muted-foreground block mb-1">Layer name</label>
            <input v-model="csvName" class="input w-full text-sm" placeholder="Layer name" />
          </div>
          <div class="w-32 flex-shrink-0">
            <label class="text-xs text-muted-foreground block mb-1">Delimiter</label>
            <select v-model="csvDelim" @change="parseCsvHeader(csvFile)" class="input w-full text-sm">
              <option value="comma">Comma ,</option>
              <option value="semicolon">Semicolon ;</option>
              <option value="tab">Tab</option>
              <option value="pipe">Pipe |</option>
            </select>
          </div>
        </div>
        <div class="flex gap-2">
          <div class="flex-1 min-w-0">
            <label class="text-xs text-muted-foreground block mb-1">X / longitude</label>
            <select v-model="csvX" class="input w-full text-sm">
              <option v-for="c in csvColumns" :key="c" :value="c">{{ c }}</option>
            </select>
          </div>
          <div class="flex-1 min-w-0">
            <label class="text-xs text-muted-foreground block mb-1">Y / latitude</label>
            <select v-model="csvY" class="input w-full text-sm">
              <option v-for="c in csvColumns" :key="c" :value="c">{{ c }}</option>
            </select>
          </div>
          <div class="w-20 flex-shrink-0">
            <label class="text-xs text-muted-foreground block mb-1">EPSG</label>
            <input v-model.number="csvSrid" type="number" class="input w-full text-sm" />
          </div>
        </div>
        <p v-if="!csvColumns.length" class="text-xs text-amber-400">Couldn't read columns from the header — check the file.</p>
        <div class="flex justify-end gap-2 pt-1">
          <button @click="resetCsv" class="btn-secondary text-sm">Back</button>
          <button @click="importCsv" :disabled="!csvX || !csvY || !csvColumns.length" class="btn-primary text-sm">Import points</button>
        </div>
      </div>

      <!-- Dropzone -->
      <div v-else
        class="border-2 border-dashed border-border rounded-xl p-8 text-center cursor-pointer hover:border-primary/60 hover:bg-primary/10 transition-colors"
        @dragover.prevent @drop.prevent="onDrop" @click="fileInput.click()"
      >
        <UploadIcon class="w-8 h-8 text-muted-foreground/70 mx-auto mb-3" />
        <p class="text-sm font-medium text-foreground/85">Drop file here or click to browse</p>
        <p class="text-xs text-muted-foreground/70 mt-1">{{ accept }}</p>
        <input ref="fileInput" type="file" class="hidden" :accept="acceptAttr" @change="onFileChange" />
      </div>

      <div v-if="error" class="text-sm text-red-400 bg-red-500/15 p-3 rounded-lg">{{ error }}</div>
    </div>
  </div>
  </Teleport>
</template>

<script setup>
import { ref } from 'vue'
import { UploadIcon } from '@/views/icons'
import { useUpload } from '@/composables/useUpload'
import { useDataStore } from '@/stores/data'
import { uploadCsvFile } from '@/api'

const props = defineProps({ type: String })
const emit = defineEmits(['close'])

const fileInput = ref(null)
const fileName = ref('')
const { uploading, uploadProgress, error, uploadFile, uploadGeoParquet } = useUpload()
const dataStore = useDataStore()

// CSV import state (vector only)
const csvFile = ref(null)
const csvColumns = ref([])
const csvX = ref('')
const csvY = ref('')
const csvSrid = ref(4326)
const csvName = ref('')
const csvDelim = ref('comma')
const DELIM_CHAR = { comma: ',', semicolon: ';', tab: '\t', pipe: '|' }

const acceptMap = {
  vector: { accept: 'Shapefile (.zip), GeoJSON, GeoPackage (.gpkg), GeoParquet (.parquet), CSV (X/Y points)', acceptAttr: '.zip,.geojson,.json,.gpkg,.parquet,.geoparquet,.csv' },
  raster: { accept: 'GeoTIFF (.tif / .tiff)', acceptAttr: '.tif,.tiff' },
}
const MAX_GEOPARQUET = 10 * 1024 * 1024 * 1024  // 10 GB
const { accept, acceptAttr } = acceptMap[props.type]

function resetCsv() {
  csvFile.value = null
  csvColumns.value = []
}

function parseCsvHeader(file) {
  if (!file) return
  const reader = new FileReader()
  reader.onload = () => {
    const first = String(reader.result).split(/\r?\n/)[0] || ''
    const cols = first.split(DELIM_CHAR[csvDelim.value] || ',').map(c => c.trim().replace(/^"|"$/g, '')).filter(Boolean)
    csvColumns.value = cols
    csvX.value = cols.find(c => /^(x|lon|long|longitude|easting|e)$/i.test(c)) || cols[0] || ''
    csvY.value = cols.find(c => /^(y|lat|latitude|northing|n)$/i.test(c)) || cols[1] || cols[0] || ''
  }
  reader.readAsText(file.slice(0, 65536))
}

async function handleFile(file) {
  const lower = file.name.toLowerCase()
  // CSV needs X/Y/CRS first — show the options form instead of uploading immediately.
  if (props.type === 'vector' && lower.endsWith('.csv')) {
    csvFile.value = file
    csvName.value = file.name.replace(/\.csv$/i, '')
    parseCsvHeader(file)
    return
  }
  // GeoParquet uploads DIRECT to storage (presigned) — never through the API.
  if (props.type === 'vector' && (lower.endsWith('.parquet') || lower.endsWith('.geoparquet'))) {
    if (file.size > MAX_GEOPARQUET) {
      error.value = 'File exceeds the 10 GB limit.'
      return
    }
    fileName.value = file.name
    try {
      await uploadGeoParquet(file, file.name.replace(/\.(geo)?parquet$/i, ''))
      setTimeout(() => emit('close'), 800)
    } catch { /* error shown via `error` */ }
    return
  }
  fileName.value = file.name
  try {
    await uploadFile(file, props.type)
    setTimeout(() => emit('close'), 800)
  } catch { /* error shown via `error` */ }
}

async function importCsv() {
  if (!csvX.value || !csvY.value) return
  fileName.value = csvFile.value.name
  uploading.value = true
  uploadProgress.value = 0
  error.value = null
  try {
    const { data: job } = await uploadCsvFile(csvFile.value, {
      x_column: csvX.value, y_column: csvY.value,
      srid: Number(csvSrid.value) || 4326, name: csvName.value, delimiter: csvDelim.value,
    }, (p) => (uploadProgress.value = p))
    dataStore.vectorLayers.unshift({ id: job.layer_id, name: csvName.value || csvFile.value.name, status: 'processing', _job: job })
    dataStore.pollJob(job.id, 'vector', job.layer_id).catch(() => {})
    setTimeout(() => emit('close'), 800)
  } catch (err) {
    error.value = err.response?.data?.detail || err.message
  } finally {
    uploading.value = false
  }
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
