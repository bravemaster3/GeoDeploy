<template>
  <div class="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4">
    <div class="card w-full max-w-lg p-6 space-y-4">
      <div class="flex items-center justify-between">
        <h2 class="text-lg font-semibold">Import existing data</h2>
        <button @click="$emit('close')" class="text-gray-400 hover:text-gray-600 text-xl leading-none">&times;</button>
      </div>
      <p class="text-xs text-gray-500">
        Register data that already lives in your connected database / storage as catalog entries —
        no copy or re-upload. Useful when you connect GeoDeploy to a database or bucket that already has data.
      </p>

      <!-- Tabs -->
      <div class="flex gap-4 border-b border-gray-200 text-sm">
        <button class="pb-1.5 -mb-px border-b-2 font-medium"
          :class="tab === 'db' ? 'border-brand-500 text-brand-700' : 'border-transparent text-gray-500 hover:text-gray-700'"
          @click="tab = 'db'">Database tables</button>
        <button class="pb-1.5 -mb-px border-b-2 font-medium"
          :class="tab === 'storage' ? 'border-brand-500 text-brand-700' : 'border-transparent text-gray-500 hover:text-gray-700'"
          @click="tab = 'storage'">Storage files</button>
      </div>

      <!-- Database tables -->
      <div v-if="tab === 'db'" class="max-h-72 overflow-auto -mx-1 px-1">
        <p v-if="loadingDb" class="text-xs text-gray-400 py-6 text-center">Scanning database…</p>
        <p v-else-if="dbError" class="text-xs text-red-600 py-2">{{ dbError }}</p>
        <p v-else-if="!dbTables.length" class="text-xs text-gray-400 py-6 text-center">No spatial tables found.</p>
        <label v-for="t in dbTables" :key="dbKey(t)"
          class="flex items-center gap-2 p-1.5 rounded text-xs"
          :class="t.already_imported ? 'opacity-50' : 'hover:bg-gray-50 cursor-pointer'">
          <input type="checkbox" class="accent-brand-500 flex-shrink-0" :disabled="t.already_imported"
            :checked="t.already_imported || dbSel.includes(dbKey(t))" @change="toggle(dbSel, dbKey(t))" />
          <span class="font-mono flex-1 truncate">{{ t.schema_name }}.{{ t.table_name }}</span>
          <span class="text-gray-400 flex-shrink-0">{{ t.geometry_type }} · EPSG:{{ t.srid }}</span>
          <span v-if="t.already_imported" class="text-[10px] text-gray-400 flex-shrink-0">imported</span>
        </label>
      </div>

      <!-- Storage files -->
      <div v-else class="max-h-72 overflow-auto -mx-1 px-1">
        <p v-if="loadingSt" class="text-xs text-gray-400 py-6 text-center">Scanning storage…</p>
        <p v-else-if="stError" class="text-xs text-red-600 py-2">{{ stError }}</p>
        <p v-else-if="!stFiles.length" class="text-xs text-gray-400 py-6 text-center">No GeoTIFFs found in the bucket.</p>
        <label v-for="f in stFiles" :key="f.key"
          class="flex items-center gap-2 p-1.5 rounded text-xs"
          :class="f.already_imported ? 'opacity-50' : 'hover:bg-gray-50 cursor-pointer'">
          <input type="checkbox" class="accent-brand-500 flex-shrink-0" :disabled="f.already_imported"
            :checked="f.already_imported || stSel.includes(f.key)" @change="toggle(stSel, f.key)" />
          <span class="font-mono flex-1 truncate">{{ f.key }}</span>
          <span class="text-gray-400 flex-shrink-0">{{ fmtSize(f.size) }}</span>
          <span v-if="f.already_imported" class="text-[10px] text-gray-400 flex-shrink-0">imported</span>
        </label>
      </div>

      <div v-if="importError" class="text-sm text-red-600 bg-red-50 p-3 rounded-lg">{{ importError }}</div>

      <div class="flex justify-end gap-2">
        <button @click="$emit('close')" class="btn-secondary text-sm">Cancel</button>
        <button @click="doImport" :disabled="!canImport || importing" class="btn-primary text-sm">
          {{ importing ? 'Importing…' : (selectedCount ? `Import ${selectedCount}` : 'Import') }}
        </button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import {
  discoverDatabase, importDatabase, discoverStorage, importStorage,
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
function toggle(listRef, k) {
  listRef.value = listRef.value.includes(k) ? listRef.value.filter(x => x !== k) : [...listRef.value, k]
}
const selectedCount = computed(() => tab.value === 'db' ? dbSel.value.length : stSel.value.length)
const canImport = computed(() => selectedCount.value > 0)
const fmtSize = (b) => b > 1e9 ? `${(b / 1e9).toFixed(1)} GB` : b > 1e6 ? `${(b / 1e6).toFixed(1)} MB` : `${(b / 1e3).toFixed(0)} KB`

onMounted(() => {
  discoverDatabase()
    .then(({ data }) => { dbTables.value = data })
    .catch(e => { dbError.value = e.response?.data?.detail || e.message })
    .finally(() => { loadingDb.value = false })
  discoverStorage()
    .then(({ data }) => { stFiles.value = data })
    .catch(e => { stError.value = e.response?.data?.detail || e.message })
    .finally(() => { loadingSt.value = false })
})

async function doImport() {
  importing.value = true
  importError.value = ''
  try {
    if (tab.value === 'db') {
      const tables = dbTables.value
        .filter(t => dbSel.value.includes(dbKey(t)))
        .map(t => ({
          schema_name: t.schema_name, table_name: t.table_name,
          geometry_column: t.geometry_column, srid: t.srid,
          geometry_type: t.geometry_type, name: t.table_name,
        }))
      await importDatabase(tables)
    } else {
      await importStorage(stSel.value.slice())
    }
    await dataStore.refresh()
    emit('close')
  } catch (e) {
    importError.value = e.response?.data?.detail || e.message
  } finally {
    importing.value = false
  }
}
</script>
