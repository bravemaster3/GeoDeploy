<template>
  <Teleport to="body">
  <div class="fixed inset-0 bg-gray-900/50 backdrop-blur-sm flex items-center justify-center z-50 p-4">
    <div class="card w-full max-w-lg p-6 space-y-4 shadow-2xl max-h-[90vh] overflow-y-auto">
      <div class="flex items-center justify-between">
        <h2 class="text-lg font-semibold">Import existing data</h2>
        <button @click="$emit('close')" class="text-muted-foreground/70 hover:text-foreground text-xl leading-none">&times;</button>
      </div>
      <p class="text-xs text-muted-foreground">
        Register data that already lives in your connected database / storage as catalog entries —
        no copy or re-upload. Useful when you connect GeoDeploy to a database or bucket that already has data.
      </p>

      <!-- Tabs -->
      <div class="flex gap-4 border-b border-border text-sm">
        <button class="pb-1.5 -mb-px border-b-2 font-medium"
          :class="tab === 'db' ? 'border-primary text-primary' : 'border-transparent text-muted-foreground hover:text-foreground'"
          @click="tab = 'db'">Database tables</button>
        <button class="pb-1.5 -mb-px border-b-2 font-medium"
          :class="tab === 'storage' ? 'border-primary text-primary' : 'border-transparent text-muted-foreground hover:text-foreground'"
          @click="tab = 'storage'">Storage files</button>
      </div>

      <!-- Database tables -->
      <div v-if="tab === 'db'" class="max-h-72 overflow-auto -mx-1 px-1">
        <p v-if="loadingDb" class="text-xs text-muted-foreground/70 py-6 text-center">Scanning database…</p>
        <p v-else-if="dbError" class="text-xs text-red-400 py-2">{{ dbError }}</p>
        <p v-else-if="!dbTables.length" class="text-xs text-muted-foreground/70 py-6 text-center">No spatial tables found.</p>
        <div v-for="t in dbTables" :key="dbKey(t)"
          class="flex items-center gap-2 p-1.5 rounded text-xs"
          :class="t.already_imported ? 'opacity-50' : 'hover:bg-muted/60'">
          <input type="checkbox" class="accent-primary flex-shrink-0" :disabled="t.already_imported"
            :checked="t.already_imported || dbSel.includes(dbKey(t))" @change="toggleDb(dbKey(t))" />
          <div class="flex-1 min-w-0">
            <div class="font-mono truncate">{{ t.schema_name }}.{{ t.table_name }}</div>
            <input v-if="!t.already_imported && dbSel.includes(dbKey(t))" v-model="t.importName"
              class="mt-0.5 w-full text-xs border border-border rounded px-1.5 py-0.5 focus:outline-none focus:ring-1 focus:ring-primary/60"
              placeholder="Layer name" />
          </div>
          <span class="text-muted-foreground/70 flex-shrink-0">{{ t.geometry_type }} · EPSG:{{ t.srid }}</span>
          <span v-if="t.already_imported" class="text-[10px] text-muted-foreground/70 flex-shrink-0">imported</span>
        </div>
      </div>

      <!-- Storage files -->
      <div v-else class="max-h-72 overflow-auto -mx-1 px-1">
        <p v-if="loadingSt" class="text-xs text-muted-foreground/70 py-6 text-center">Scanning storage…</p>
        <p v-else-if="stError" class="text-xs text-red-400 py-2">{{ stError }}</p>
        <p v-else-if="!stFiles.length" class="text-xs text-muted-foreground/70 py-6 text-center">No GeoTIFF, GeoParquet or CSV files found in the bucket.</p>
        <div v-for="f in stFiles" :key="f.key"
          class="p-1.5 rounded text-xs"
          :class="f.already_imported ? 'opacity-50' : 'hover:bg-muted/60'">
          <div class="flex items-center gap-2">
            <input type="checkbox" class="accent-primary flex-shrink-0" :disabled="f.already_imported"
              :checked="f.already_imported || stSel.includes(f.key)" @change="toggleStorage(f)" />
            <div class="flex-1 min-w-0">
              <div class="font-mono truncate">{{ f.key }}</div>
              <input v-if="!f.already_imported && stSel.includes(f.key)" v-model="f.importName"
                class="mt-0.5 w-full text-xs border border-border rounded px-1.5 py-0.5 focus:outline-none focus:ring-1 focus:ring-primary/60"
                placeholder="Layer name" />
            </div>
            <span class="text-[10px] uppercase px-1.5 py-0.5 rounded-full flex-shrink-0"
              :class="f.kind === 'csv' ? 'bg-emerald-500/15 text-emerald-400'
                : f.kind === 'geoparquet' ? 'bg-violet-500/15 text-violet-400'
                : 'bg-amber-500/15 text-amber-400'">{{ f.kind }}</span>
            <span class="text-muted-foreground/70 flex-shrink-0">{{ fmtSize(f.size) }}</span>
            <span v-if="f.already_imported" class="text-[10px] text-muted-foreground/70 flex-shrink-0">imported</span>
          </div>
          <!-- CSV needs a geometry source (X/Y point columns or a WKT column) + CRS -->
          <div v-if="f.kind === 'csv' && stSel.includes(f.key)" class="mt-1.5 ml-6 flex flex-wrap items-center gap-2">
            <label class="flex items-center gap-1"><span class="text-muted-foreground">Delim</span>
              <select v-model="f.delim" @change="fetchCsvColumns(f)" class="text-xs border border-border rounded px-1 py-0.5">
                <option value="comma">,</option>
                <option value="semicolon">;</option>
                <option value="tab">Tab</option>
                <option value="pipe">|</option>
              </select></label>
            <span v-if="f.colLoading" class="text-muted-foreground/70">Reading columns…</span>
            <span v-else-if="f.colError" class="text-red-400">{{ f.colError }}</span>
            <template v-else-if="f.columns && f.columns.length">
              <label class="flex items-center gap-1"><span class="text-muted-foreground">Geometry</span>
                <select v-model="f.geomMode" class="text-xs border border-border rounded px-1 py-0.5">
                  <option value="xy">X/Y points</option>
                  <option value="wkt">WKT column</option>
                </select></label>
              <template v-if="f.geomMode === 'xy'">
                <label class="flex items-center gap-1"><span class="text-muted-foreground">X</span>
                  <select v-model="f.xCol" class="text-xs border border-border rounded px-1 py-0.5">
                    <option v-for="c in f.columns" :key="c" :value="c">{{ c }}</option>
                  </select></label>
                <label class="flex items-center gap-1"><span class="text-muted-foreground">Y</span>
                  <select v-model="f.yCol" class="text-xs border border-border rounded px-1 py-0.5">
                    <option v-for="c in f.columns" :key="c" :value="c">{{ c }}</option>
                  </select></label>
              </template>
              <label v-else class="flex items-center gap-1"><span class="text-muted-foreground">WKT</span>
                <select v-model="f.wktCol" class="text-xs border border-border rounded px-1 py-0.5">
                  <option v-for="c in f.columns" :key="c" :value="c">{{ c }}</option>
                </select></label>
              <label class="flex items-center gap-1"><span class="text-muted-foreground">EPSG</span>
                <input v-model.number="f.srid" type="number" class="w-20 text-xs border border-border rounded px-1 py-0.5" /></label>
            </template>
          </div>
        </div>
      </div>

      <div v-if="importError" class="text-sm text-red-400 bg-red-500/15 p-3 rounded-lg">{{ importError }}</div>

      <div class="flex justify-end gap-2">
        <button @click="$emit('close')" class="btn-secondary text-sm">Cancel</button>
        <button @click="doImport" :disabled="!canImport || importing" class="btn-primary text-sm">
          {{ importing ? 'Importing…' : (selectedCount ? `Import ${selectedCount}` : 'Import') }}
        </button>
      </div>
    </div>
  </div>
  </Teleport>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import {
  discoverDatabase, importDatabase, discoverStorage, importStorage,
  getCsvColumns, importCsv,
} from '@/api'
import { useDataStore } from '@/stores/data'

const emit = defineEmits(['close'])
const dataStore = useDataStore()

const tab = ref('db')
const dbTables = ref([]); const loadingDb = ref(true); const dbError = ref('')
const stFiles = ref([]); const loadingSt = ref(true); const stError = ref('')
const dbSel = ref([]); const stSel = ref([])
const importing = ref(false); const importError = ref('')

const dbKey = (t) => `${t.schema_name}.${t.table_name}`
// NB: in the template a ref auto-unwraps, so we can't pass dbSel/stSel into a generic helper —
// these wrappers keep the ref in JS scope.
function toggle(listRef, k) {
  listRef.value = listRef.value.includes(k) ? listRef.value.filter(x => x !== k) : [...listRef.value, k]
}
function toggleDb(k) { toggle(dbSel, k) }
const selectedCount = computed(() => tab.value === 'db' ? dbSel.value.length : stSel.value.length)
const canImport = computed(() => selectedCount.value > 0)
const fmtSize = (b) => b > 1e9 ? `${(b / 1e9).toFixed(1)} GB` : b > 1e6 ? `${(b / 1e6).toFixed(1)} MB` : `${(b / 1e3).toFixed(0)} KB`

const baseName = (key) => key.split('/').pop().replace(/\.[^.]+$/, '')

onMounted(() => {
  discoverDatabase()
    .then(({ data }) => { dbTables.value = data.map(t => ({ ...t, importName: t.table_name })) })
    .catch(e => { dbError.value = e.response?.data?.detail || e.message })
    .finally(() => { loadingDb.value = false })
  discoverStorage()
    .then(({ data }) => {
      stFiles.value = data.map(f => ({
        ...f, importName: baseName(f.key),
        columns: null, geomMode: 'xy', xCol: '', yCol: '', wktCol: '',
        srid: 4326, delim: 'comma', colLoading: false, colError: '',
      }))
    })
    .catch(e => { stError.value = e.response?.data?.detail || e.message })
    .finally(() => { loadingSt.value = false })
})

// CSV rows need their header to offer X/Y pickers — fetch lazily on first selection.
function toggleStorage(f) {
  toggle(stSel, f.key)
  if (f.kind === 'csv' && stSel.value.includes(f.key) && !f.columns && !f.colLoading) {
    fetchCsvColumns(f)
  }
}
function fetchCsvColumns(f) {
  f.colLoading = true
  f.colError = ''
  getCsvColumns(f.key, f.delim)
    .then(({ data }) => {
      const cols = data.columns || []
      f.columns = cols
      f.xCol = cols.find(c => /^(x|lon|long|longitude|easting|e)$/i.test(c)) || cols[0] || ''
      f.yCol = cols.find(c => /^(y|lat|latitude|northing|n)$/i.test(c)) || cols[1] || cols[0] || ''
      // A column that looks like WKT (e.g. Google Open Buildings' `geometry`) → preselect WKT mode.
      const wktGuess = cols.find(c => /^(wkt|geometry|geom|the_geom|wkt_geometry)$/i.test(c))
      f.wktCol = wktGuess || cols[0] || ''
      if (wktGuess) f.geomMode = 'wkt'
    })
    .catch(e => { f.colError = e.response?.data?.detail || e.message; f.columns = [] })
    .finally(() => { f.colLoading = false })
}

async function doImport() {
  importing.value = true
  importError.value = ''
  const csvJobs = []
  try {
    if (tab.value === 'db') {
      const tables = dbTables.value
        .filter(t => dbSel.value.includes(dbKey(t)))
        .map(t => ({
          schema_name: t.schema_name, table_name: t.table_name,
          geometry_column: t.geometry_column, srid: t.srid,
          geometry_type: t.geometry_type,
          name: (t.importName || '').trim() || t.table_name,
        }))
      await importDatabase(tables)
    } else {
      const selected = stFiles.value.filter(f => stSel.value.includes(f.key))
      const files = selected.filter(f => f.kind === 'raster' || f.kind === 'geoparquet')
      const csvs = selected.filter(f => f.kind === 'csv')
      for (const f of csvs) {
        if (f.geomMode === 'wkt' ? !f.wktCol : (!f.xCol || !f.yCol))
          throw new Error(`Pick ${f.geomMode === 'wkt' ? 'a WKT column' : 'X and Y columns'} for ${f.key}`)
      }
      if (files.length) {
        // Rasters register immediately; GeoParquet files come back as background jobs
        // (inspect + spatial prep) — poll them like the CSV jobs below.
        const { data } = await importStorage(files.map(f => ({ key: f.key, name: (f.importName || '').trim() || baseName(f.key) })))
        for (const j of (data.jobs || [])) csvJobs.push({ jobId: j.id, layerId: j.layer_id })
      }
      // CSV import runs as a background job — collect the job ids to poll after refresh.
      for (const f of csvs) {
        const geom = f.geomMode === 'wkt' ? { wkt_column: f.wktCol } : { x_column: f.xCol, y_column: f.yCol }
        const { data } = await importCsv({
          key: f.key, name: (f.importName || '').trim() || baseName(f.key),
          ...geom, srid: Number(f.srid) || 4326, delimiter: f.delim,
        })
        csvJobs.push({ jobId: data.id, layerId: data.layer_id })
      }
    }
    await dataStore.refresh()
    // Poll the CSV jobs so My Data flips processing → ready (the modal can close meanwhile).
    csvJobs.forEach(j => dataStore.pollJob(j.jobId, 'vector', j.layerId).catch(() => {}))
    emit('close')
  } catch (e) {
    importError.value = e.response?.data?.detail || e.message
  } finally {
    importing.value = false
  }
}
</script>
