<template>
  <Teleport to="body">
  <div class="fixed inset-0 bg-gray-900/50 backdrop-blur-sm flex items-center justify-center z-50 p-4">
    <div class="card w-full max-w-md p-6 space-y-4 shadow-2xl max-h-[90vh] overflow-y-auto">
      <div class="flex items-center justify-between">
        <h2 class="text-lg font-semibold">
          {{ csvFile ? 'Import CSV as a layer' : (type === 'vector' ? 'Upload vector file' : 'Upload raster file') }}
        </h2>
        <button @click="$emit('close')" class="text-muted-foreground/70 hover:text-foreground text-xl leading-none">&times;</button>
      </div>

      <!-- Batch: one row per file, so a failure in the middle is visible and named rather than
           replaced by whatever the last file did. -->
      <div v-if="batch.length" class="space-y-2">
        <p class="text-sm text-muted-foreground">
          {{ batch.filter(b => b.state === 'done').length }} of {{ batch.length }} uploaded
        </p>
        <ul class="space-y-1 max-h-56 overflow-y-auto">
          <li v-for="(item, i) in batch" :key="item.name + i"
              class="flex items-center gap-2 text-sm">
            <span class="w-4 flex-shrink-0 text-center" aria-hidden="true">
              <span v-if="item.state === 'done'" class="text-emerald-400">✓</span>
              <span v-else-if="item.state === 'failed'" class="text-red-400">✕</span>
              <span v-else-if="item.state === 'uploading'" class="text-primary">•</span>
              <span v-else class="text-muted-foreground/50">·</span>
            </span>
            <span class="truncate" :class="item.state === 'failed' ? 'text-red-400' : ''">
              {{ item.name }}
            </span>
            <span v-if="item.state === 'uploading'" class="ml-auto text-xs text-muted-foreground">
              {{ uploadProgress }}%
            </span>
          </li>
        </ul>
        <p v-if="batchIndex >= 0" class="text-xs text-muted-foreground">
          Uploading one at a time — each is processed in the background as it arrives.
        </p>
        <!-- The queue has finished and something failed. Without a way back the list is a
             dead end: the only exit would be closing the modal and starting over. -->
        <div v-else class="flex justify-end pt-1">
          <button @click="resetBatch" class="btn-secondary text-sm">Upload more</button>
        </div>
      </div>

      <!-- Upload progress -->
      <div v-else-if="uploading" class="space-y-3">
        <div class="flex justify-between text-sm">
          <span class="text-muted-foreground">{{ fileName }}</span>
          <span class="font-medium">{{ uploadProgress }}%</span>
        </div>
        <div class="h-2 bg-muted rounded-full overflow-hidden">
          <div class="h-full bg-primary/100 rounded-full transition-all" :style="{ width: uploadProgress + '%' }" />
        </div>
        <p class="text-xs text-muted-foreground">Uploading… GeoDeploy will process it in the background.</p>
      </div>

      <!-- Handed over. Shown for the moment between the upload finishing and the modal closing.
           Without it the modal fell back to the DROPZONE for that instant, so a completed upload
           ended with "Drop file here" flashing up — indistinguishable from the upload having been
           discarded. -->
      <div v-else-if="done" class="space-y-2">
        <div class="flex items-center gap-2 text-sm text-emerald-400">
          <span aria-hidden="true">✓</span>
          <span class="font-medium truncate">{{ fileName }}</span>
        </div>
        <p class="text-xs text-muted-foreground">
          Uploaded. GeoDeploy is processing it in the background — it will appear in your list as it
          becomes ready.
        </p>
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
        <div>
          <label class="text-xs text-muted-foreground block mb-1">Geometry</label>
          <select v-model="csvGeomMode" class="input w-full text-sm">
            <option value="xy">Points from X / Y columns</option>
            <option value="wkt">WKT geometry column (points, lines or polygons)</option>
          </select>
        </div>
        <div class="flex gap-2">
          <template v-if="csvGeomMode === 'xy'">
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
          </template>
          <div v-else class="flex-1 min-w-0">
            <label class="text-xs text-muted-foreground block mb-1">WKT column</label>
            <select v-model="csvWkt" class="input w-full text-sm">
              <option v-for="c in csvColumns" :key="c" :value="c">{{ c }}</option>
            </select>
          </div>
          <div class="w-32 flex-shrink-0">
            <label class="text-xs text-muted-foreground block mb-1">Coordinate system</label>
            <input v-model.number="csvSrid" type="number" list="gd-csv-epsg" placeholder="4326"
              class="input w-full text-sm" title="EPSG code of the X/Y (or WKT) coordinates" />
            <datalist id="gd-csv-epsg">
              <option value="4326">WGS 84 (lon/lat)</option>
              <option value="3857">Web Mercator</option>
              <option value="4258">ETRS89</option>
              <option value="3035">ETRS89 / LAEA Europe</option>
              <option value="32630">WGS 84 / UTM 30N</option>
              <option value="32633">WGS 84 / UTM 33N</option>
              <option value="32733">WGS 84 / UTM 33S</option>
              <option value="27700">OSGB36 / British National Grid</option>
              <option value="2154">RGF93 / Lambert-93 (France)</option>
            </datalist>
          </div>
        </div>
        <p class="text-[11px] text-muted-foreground/70">
          The EPSG of your coordinates (default <span class="font-mono">4326</span> = lon/lat). The layer is
          stored in this CRS; the map reprojects for display.
        </p>
        <p v-if="!csvColumns.length" class="text-xs text-amber-400">Couldn't read columns from the header — check the file.</p>
        <div class="flex justify-end gap-2 pt-1">
          <button @click="resetCsv" class="btn-secondary text-sm">Back</button>
          <button @click="importCsv" :disabled="!csvReady" class="btn-primary text-sm">Import layer</button>
        </div>
      </div>

      <!-- Dropzone -->
      <div v-else
        class="border-2 border-dashed border-border rounded-xl p-8 text-center cursor-pointer hover:border-primary/60 hover:bg-primary/10 transition-colors"
        @dragover.prevent @drop.prevent="onDrop" @click="fileInput.click()"
      >
        <UploadIcon class="w-8 h-8 text-muted-foreground/70 mx-auto mb-3" />
        <p class="text-sm font-medium text-foreground/85">Drop file here or click to browse</p>
        <p v-if="type === 'vector'" class="text-xs text-muted-foreground/70 mt-1">
          Shapefile: select the .shp <em>together with</em> its .shx, .dbf and .prj — they are one dataset
        </p>
        <p class="text-xs text-muted-foreground/70 mt-1">{{ accept }}</p>
        <input ref="fileInput" type="file" class="hidden" multiple :accept="acceptAttr" @change="onFileChange" />
      </div>

      <div v-if="packaging" class="text-sm text-muted-foreground">Packaging the shapefile…</div>
      <div v-if="warning" class="text-sm text-amber-400 bg-amber-500/15 p-3 rounded-lg">{{ warning }}</div>
      <div v-if="error" class="text-sm text-red-400 bg-red-500/15 p-3 rounded-lg">{{ error }}</div>
    </div>
  </div>
  </Teleport>
</template>

<script setup>
import { computed, ref } from 'vue'
import { UploadIcon } from '@/views/icons'
import { useUpload, LARGE_UPLOAD_THRESHOLD } from '@/composables/useUpload'
import { useDataStore } from '@/stores/data'
import { uploadCsvFile } from '@/api'
import { planSelection, zipShapefileSet } from '@/lib/zip'

const props = defineProps({ type: String })
const emit = defineEmits(['close'])

const fileInput = ref(null)
const fileName = ref('')
// Zipping a shapefile set happens before any request, so it needs its own "busy": `uploading`
// belongs to the upload itself and a large .dbf takes a visible moment to read and compress.
const packaging = ref(false)
// One row per file when several were selected, so a batch shows what succeeded and what did not
// instead of collapsing to a single "uploading…".
const batch = ref([])
const batchIndex = ref(-1)
// A missing .prj does not stop the upload — it changes what the user should check afterwards.
const warning = ref('')
// True from the instant an upload succeeds until the modal closes. `uploading` goes false as soon as
// the request resolves, so without this flag the template has no branch to show during the closing
// delay and falls through to the dropzone.
const done = ref(false)

// Every success path ends here, so the hand-off looks the same however the file was sent.
function finishAndClose() {
  done.value = true
  setTimeout(() => emit('close'), 1200)
}
const { uploading, uploadProgress, error, uploadFile, uploadGeoParquet, uploadLargeVector, uploadLargeRaster } = useUpload()
const dataStore = useDataStore()

// CSV import state (vector only)
const csvFile = ref(null)
const csvColumns = ref([])
const csvGeomMode = ref('xy')  // 'xy' (points) | 'wkt' (any geometry, e.g. polygon footprints)
const csvX = ref('')
const csvY = ref('')
const csvWkt = ref('')
const csvSrid = ref(4326)
const csvName = ref('')
const csvDelim = ref('comma')
const DELIM_CHAR = { comma: ',', semicolon: ';', tab: '\t', pipe: '|' }
const csvReady = computed(() => csvColumns.value.length &&
  (csvGeomMode.value === 'wkt' ? !!csvWkt.value : (!!csvX.value && !!csvY.value)))

const acceptMap = {
  vector: { accept: 'Shapefile (.shp + sidecars, or .zip), GeoJSON, GeoPackage (.gpkg), GeoParquet (.parquet), CSV (X/Y points or WKT geometry)', acceptAttr: '.zip,.geojson,.json,.gpkg,.parquet,.geoparquet,.csv,.shp,.shx,.dbf,.prj,.cpg,.qpj,.sbn,.sbx,.qix,.fix' },
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
    // A column that looks like WKT (e.g. Google Open Buildings' `geometry`) → preselect WKT mode.
    const wktGuess = cols.find(c => /^(wkt|geometry|geom|the_geom|wkt_geometry)$/i.test(c))
    csvWkt.value = wktGuess || cols[0] || ''
    if (wktGuess) csvGeomMode.value = 'wkt'
  }
  reader.readAsText(file.slice(0, 65536))
}

async function handleFile(file) {
  const ok = await uploadOne(file)
  if (ok) finishAndClose()
}

/**
 * Upload ONE file and report whether it worked. Does not close the modal: in a batch, the next file
 * follows, and closing on the first success would abandon the rest mid-flight.
 *
 * Returns false for CSV, which cannot be uploaded unattended — it needs its X/Y columns and CRS
 * chosen first, and `handleFile`'s caller shows that form.
 */
async function uploadOne(file) {
  const lower = file.name.toLowerCase()
  // CSV needs X/Y/CRS first — show the options form instead of uploading immediately.
  if (props.type === 'vector' && lower.endsWith('.csv')) {
    csvFile.value = file
    csvName.value = file.name.replace(/\.csv$/i, '')
    parseCsvHeader(file)
    return false
  }
  // GeoParquet uploads DIRECT to storage (presigned) — never through the API.
  if (props.type === 'vector' && (lower.endsWith('.parquet') || lower.endsWith('.geoparquet'))) {
    if (file.size > MAX_GEOPARQUET) {
      error.value = 'File exceeds the 10 GB limit.'
      return false
    }
    fileName.value = file.name
    try {
      await uploadGeoParquet(file, file.name.replace(/\.(geo)?parquet$/i, ''))
      return true
    } catch { return false }
  }
  fileName.value = file.name
  // Big RASTERS also bypass the API: a GeoTIFF over the CDN's request-body cap never reaches it.
  if (props.type === 'raster' && file.size >= LARGE_UPLOAD_THRESHOLD) {
    try {
      await uploadLargeRaster(file, file.name.replace(/\.[^.]+$/, ''))
      return true
    } catch { return false }
  }
  // Vector files too big for a single request upload direct-to-storage and convert to GeoParquet
  // in the background instead of being rejected.
  if (props.type === 'vector' && file.size >= LARGE_UPLOAD_THRESHOLD) {
    try {
      await uploadLargeVector(file, file.name.replace(/\.[^.]+$/, ''))
      return true
    } catch { return false }
  }
  try {
    await uploadFile(file, props.type)
    return true
  } catch { return false }
}

async function importCsv() {
  if (!csvReady.value) return
  fileName.value = csvFile.value.name
  const geom = csvGeomMode.value === 'wkt'
    ? { wkt_column: csvWkt.value }
    : { x_column: csvX.value, y_column: csvY.value }
  const srid = Number(csvSrid.value) || 4326
  // A CSV too big for the API multipart cap uploads direct-to-storage and converts to GeoParquet
  // in the background (deck.gl layer) instead of loading points into PostGIS.
  if (csvFile.value.size >= LARGE_UPLOAD_THRESHOLD) {
    try {
      await uploadLargeVector(csvFile.value, csvName.value || csvFile.value.name.replace(/\.csv$/i, ''),
        { ...geom, srid, delimiter: csvDelim.value })
      finishAndClose()
    } catch { /* error shown via `error` */ }
    return
  }
  uploading.value = true
  uploadProgress.value = 0
  error.value = null
  try {
    const { data: job } = await uploadCsvFile(csvFile.value, {
      ...geom, srid, name: csvName.value, delimiter: csvDelim.value,
    }, (p) => (uploadProgress.value = p))
    dataStore.vectorLayers.unshift({ id: job.layer_id, name: csvName.value || csvFile.value.name, status: 'processing', _job: job })
    dataStore.pollJob(job.id, 'vector', job.layer_id).catch(() => {})
    finishAndClose()
  } catch (err) {
    error.value = err.response?.data?.detail || err.message
  } finally {
    uploading.value = false
  }
}

function onDrop(e) {
  takeSelection(e.dataTransfer.files)
}

function onFileChange(e) {
  takeSelection(e.target.files)
}

/**
 * A selection may be one ordinary file, or the several files that make up ONE shapefile.
 *
 * The browser cannot read the files next to the one you picked — there is no API for it, and there
 * should not be — so the set has to be selected, and the .zip the server already ingests is built
 * here. Anything that is not a shapefile takes exactly the path it did before.
 */
async function takeSelection(fileList) {
  const files = Array.from(fileList || [])
  if (!files.length) return
  error.value = ''
  warning.value = ''

  const { shapefiles, others } = planSelection(files)

  // Refuse an incomplete shapefile before anything is sent: a .shp without its .shx/.dbf cannot
  // ingest, so uploading it only moves the failure somewhere less useful.
  const broken = shapefiles.filter((set) => set.missing.length)
  if (broken.length) {
    error.value = broken.map((set) =>
      `${set.stem}: missing ${set.missing.join(' and ')}`).join('; ') +
      '. A shapefile needs its sidecar files — select them together with the .shp.'
    return
  }

  // CSV cannot ride along in a batch: it needs its X/Y columns and CRS chosen, per file.
  const csvs = others.filter((f) => f.name.toLowerCase().endsWith('.csv'))
  if (csvs.length && (others.length + shapefiles.length) > 1) {
    error.value = 'A CSV has to be imported on its own — it needs its X/Y columns and CRS chosen ' +
      'first. Upload the others, then the CSV separately.'
    return
  }

  let queue = []
  if (shapefiles.length) {
    packaging.value = true
    try {
      for (const set of shapefiles) {
        queue.push(await zipShapefileSet(set))
        if (!set.files.some((f) => f.name.toLowerCase().endsWith('.prj'))) {
          // Recoverable — the CRS can be set afterwards — but silence about it becomes a mystery
          // when the layer draws in the wrong hemisphere.
          warning.value = `${set.stem} has no .prj, so it carries no CRS of its own. ` +
            'You may need to set it after upload.'
        }
      }
    } catch (err) {
      error.value = err.message || 'Could not package the shapefile.'
      return
    } finally {
      packaging.value = false
    }
  }
  queue = queue.concat(others)

  if (queue.length === 1) {
    handleFile(queue[0])           // one file: the CSV form and every existing path, unchanged
    return
  }
  await runQueue(queue)
}

/**
 * Upload several files one after another.
 *
 * Sequentially on purpose. Each upload already parallelises its own parts for anything large, so
 * running files concurrently would compete for the same bandwidth without finishing the set any
 * sooner — and it would make the progress bar meaningless.
 *
 * One failure does not stop the rest. Eight files where the third is corrupt should upload seven
 * and tell you about the third, not stop at two.
 */
function resetBatch() {
  batch.value = []
  batchIndex.value = -1
  error.value = ''
  warning.value = ''
  fileName.value = ''
}

async function runQueue(files) {
  batch.value = files.map((f) => ({ name: f.name, state: 'pending' }))
  for (let i = 0; i < files.length; i++) {
    batchIndex.value = i
    batch.value[i].state = 'uploading'
    error.value = ''
    const ok = await uploadOne(files[i])
    batch.value[i].state = ok ? 'done' : 'failed'
    batch.value[i].message = ok ? '' : (error.value || 'Upload failed.')
  }
  batchIndex.value = -1
  const failed = batch.value.filter((b) => b.state === 'failed')
  error.value = failed.length
    ? `${failed.length} of ${files.length} did not upload: ` +
      failed.map((f) => f.name).join(', ')
    : ''
  if (!failed.length) finishAndClose()
}

</script>
