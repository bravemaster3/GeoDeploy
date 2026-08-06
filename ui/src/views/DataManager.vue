<template>
  <div class="p-6 lg:p-8">
    <div class="max-w-6xl mx-auto space-y-6">
      <!-- Header -->
      <div class="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <h1 class="text-2xl font-semibold tracking-tight text-foreground">My Data</h1>
          <p class="text-sm text-muted-foreground mt-1">Upload, connect, and manage the spatial layers behind your portals.</p>
        </div>
        <div class="flex items-center gap-2">
          <!-- Creator filter (shared workspace): admins use this to review a member's
               uploads in bulk, e.g. before deleting the account. Client-side only. -->
          <select v-if="creators.length > 1" v-model="creatorFilter"
            class="text-xs bg-background text-foreground border border-border rounded-lg px-2.5 py-2 focus:outline-none focus:ring-1 focus:ring-primary/60">
            <option value="">Everyone</option>
            <option v-for="c in creators" :key="c" :value="c">{{ c }}</option>
          </select>
          <button v-if="auth.canEdit" @click="showDiscover = true" class="btn-secondary">
            <DownloadIcon class="w-4 h-4" /> Import existing
          </button>
        </div>
      </div>

      <!-- Vector layers -->
      <section class="card overflow-hidden">
        <header class="flex flex-wrap items-center gap-3 px-5 py-3.5 border-b border-border/60">
          <span class="w-9 h-9 rounded-lg bg-blue-500/15 text-blue-400 flex items-center justify-center flex-shrink-0">
            <DatabaseIcon class="w-5 h-5" />
          </span>
          <div class="flex-1 min-w-0">
            <h2 class="text-sm font-semibold text-foreground">Vector layers</h2>
            <!-- Was "Stored in PostGIS · served as vector tiles", which stopped being true once
                 GeoParquet layers arrived: those are files in object storage, read directly or via
                 PMTiles, never loaded into PostGIS. Each row already shows its own backend, so the
                 heading no longer claims one. -->
            <p class="text-xs text-muted-foreground/70">PostGIS tables and GeoParquet files</p>
          </div>
          <input v-if="dataStore.vectorLayers.length > 3" v-model="vectorSearch" type="search"
            id="vector-search" name="vector-search" placeholder="Search…"
            class="w-36 max-w-full text-xs bg-background text-foreground placeholder:text-muted-foreground/60 border border-border rounded-lg px-2.5 py-1.5 focus:outline-none focus:ring-1 focus:ring-primary/60" />
          <span class="text-xs font-medium text-muted-foreground bg-muted rounded-full px-2 py-0.5">{{ dataStore.vectorLayers.length }}</span>
          <button @click="toggleSection('vector')" class="flex-shrink-0 w-6 h-6 rounded flex items-center justify-center text-muted-foreground/70 hover:text-foreground hover:bg-muted"
            :title="collapsed.vector ? 'Expand' : 'Collapse'" :aria-expanded="!collapsed.vector">
            <svg class="w-4 h-4 transition-transform" :class="collapsed.vector ? '-rotate-90' : ''"
              viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="m6 9 6 6 6-6" />
            </svg>
          </button>
          <button v-if="auth.canEdit" @click="showVectorUpload = true" class="btn-primary text-xs px-3 py-1.5">
            <UploadIcon class="w-3.5 h-3.5" /> Upload
          </button>
        </header>
        <div v-if="collapsed.vector" class="px-5 py-2.5 text-xs text-muted-foreground/60">
          {{ filteredVectors.length }} hidden — click the chevron to expand.
        </div>
        <div v-else-if="!dataStore.vectorLayers.length" class="px-5 py-10 text-center">
          <DatabaseIcon class="w-8 h-8 text-muted-foreground/40 mx-auto mb-2" />
          <p class="text-sm font-medium text-muted-foreground">No vector layers yet</p>
          <p class="text-xs text-muted-foreground/70 mt-0.5">Upload a Shapefile (.zip), GeoJSON, GeoPackage, or CSV.</p>
        </div>
        <div v-else-if="!filteredVectors.length" class="px-5 py-6 text-center text-xs text-muted-foreground/70">
          No vector layer matches “{{ vectorSearch }}”.
        </div>
        <div v-else class="divide-y divide-border/60">
          <VectorRow v-for="layer in pagedVectors" :key="layer.id" :layer="layer"
            @delete="askDelete('vector', layer)" />
        </div>
        <!-- Pagination, shown only once it earns its place. Every row renders whether or not it is
             near the viewport, so an unbounded list costs both screen space and frame time. -->
        <div v-if="!collapsed.vector && pageCount(filteredVectors.length) > 1"
          class="flex items-center justify-between gap-3 px-5 py-2.5 border-t border-border/60">
          <span class="text-[11px] text-muted-foreground/70">
            {{ pageLabel('vector', filteredVectors.length) }}
          </span>
          <span class="flex items-center gap-1.5">
            <button @click="setPage('vector', page.vector - 1)" :disabled="page.vector <= 1"
              class="btn-secondary text-xs px-2 py-1 disabled:opacity-40">Prev</button>
            <span class="text-xs text-muted-foreground px-1">{{ Math.min(page.vector, pageCount(filteredVectors.length)) }} / {{ pageCount(filteredVectors.length) }}</span>
            <button @click="setPage('vector', page.vector + 1)" :disabled="page.vector >= pageCount(filteredVectors.length)"
              class="btn-secondary text-xs px-2 py-1 disabled:opacity-40">Next</button>
          </span>
        </div>
      </section>

      <!-- Raster files -->
      <section class="card overflow-hidden">
        <header class="flex flex-wrap items-center gap-3 px-5 py-3.5 border-b border-border/60">
          <span class="w-9 h-9 rounded-lg bg-amber-500/15 text-amber-400 flex items-center justify-center flex-shrink-0">
            <ImageIcon class="w-5 h-5" />
          </span>
          <div class="flex-1 min-w-0">
            <h2 class="text-sm font-semibold text-foreground">Raster files</h2>
            <p class="text-xs text-muted-foreground/70">Cloud-optimised GeoTIFFs in object storage</p>
          </div>
          <input v-if="dataStore.rasterLayers.length > 3" v-model="rasterSearch" type="search"
            id="raster-search" name="raster-search" placeholder="Search…"
            class="w-36 max-w-full text-xs bg-background text-foreground placeholder:text-muted-foreground/60 border border-border rounded-lg px-2.5 py-1.5 focus:outline-none focus:ring-1 focus:ring-primary/60" />
          <span class="text-xs font-medium text-muted-foreground bg-muted rounded-full px-2 py-0.5">{{ dataStore.rasterLayers.length }}</span>
          <button @click="toggleSection('raster')" class="flex-shrink-0 w-6 h-6 rounded flex items-center justify-center text-muted-foreground/70 hover:text-foreground hover:bg-muted"
            :title="collapsed.raster ? 'Expand' : 'Collapse'" :aria-expanded="!collapsed.raster">
            <svg class="w-4 h-4 transition-transform" :class="collapsed.raster ? '-rotate-90' : ''"
              viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="m6 9 6 6 6-6" />
            </svg>
          </button>
          <button v-if="auth.canEdit" @click="showRasterUpload = true" class="btn-primary text-xs px-3 py-1.5">
            <UploadIcon class="w-3.5 h-3.5" /> Upload
          </button>
        </header>
        <div v-if="collapsed.raster" class="px-5 py-2.5 text-xs text-muted-foreground/60">
          {{ filteredRasters.length }} hidden — click the chevron to expand.
        </div>
        <div v-else-if="!dataStore.rasterLayers.length" class="px-5 py-10 text-center">
          <ImageIcon class="w-8 h-8 text-muted-foreground/40 mx-auto mb-2" />
          <p class="text-sm font-medium text-muted-foreground">No raster files yet</p>
          <p class="text-xs text-muted-foreground/70 mt-0.5">Upload a GeoTIFF (.tif / .tiff).</p>
        </div>
        <div v-else-if="!filteredRasters.length" class="px-5 py-6 text-center text-xs text-muted-foreground/70">
          No raster file matches “{{ rasterSearch }}”.
        </div>
        <div v-else class="divide-y divide-border/60">
          <RasterRow v-for="layer in pagedRasters" :key="layer.id" :layer="layer"
            @delete="askDelete('raster', layer)" />
        </div>
        <!-- Pagination, shown only once it earns its place. Every row renders whether or not it is
             near the viewport, so an unbounded list costs both screen space and frame time. -->
        <div v-if="!collapsed.raster && pageCount(filteredRasters.length) > 1"
          class="flex items-center justify-between gap-3 px-5 py-2.5 border-t border-border/60">
          <span class="text-[11px] text-muted-foreground/70">
            {{ pageLabel('raster', filteredRasters.length) }}
          </span>
          <span class="flex items-center gap-1.5">
            <button @click="setPage('raster', page.raster - 1)" :disabled="page.raster <= 1"
              class="btn-secondary text-xs px-2 py-1 disabled:opacity-40">Prev</button>
            <span class="text-xs text-muted-foreground px-1">{{ Math.min(page.raster, pageCount(filteredRasters.length)) }} / {{ pageCount(filteredRasters.length) }}</span>
            <button @click="setPage('raster', page.raster + 1)" :disabled="page.raster >= pageCount(filteredRasters.length)"
              class="btn-secondary text-xs px-2 py-1 disabled:opacity-40">Next</button>
          </span>
        </div>
      </section>

      <!-- External sources -->
      <section class="card overflow-hidden">
        <header class="flex flex-wrap items-center gap-3 px-5 py-3.5 border-b border-border/60">
          <span class="w-9 h-9 rounded-lg bg-emerald-500/15 text-emerald-400 flex items-center justify-center flex-shrink-0">
            <LinkIcon class="w-5 h-5" />
          </span>
          <div class="flex-1 min-w-0">
            <h2 class="text-sm font-semibold text-foreground">External sources</h2>
            <p class="text-xs text-muted-foreground/70">WMS · XYZ · WFS — shown in portals without importing</p>
          </div>
          <span class="text-xs font-medium text-muted-foreground bg-muted rounded-full px-2 py-0.5">{{ dataStore.externalSources.length }}</span>
          <button @click="toggleSection('source')" class="flex-shrink-0 w-6 h-6 rounded flex items-center justify-center text-muted-foreground/70 hover:text-foreground hover:bg-muted"
            :title="collapsed.source ? 'Expand' : 'Collapse'" :aria-expanded="!collapsed.source">
            <svg class="w-4 h-4 transition-transform" :class="collapsed.source ? '-rotate-90' : ''"
              viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="m6 9 6 6 6-6" />
            </svg>
          </button>
          <button v-if="auth.canEdit" @click="showAddSource = true" class="btn-secondary text-xs px-3 py-1.5">
            <PlusIcon class="w-3.5 h-3.5" /> Connect
          </button>
        </header>
        <div v-if="collapsed.source" class="px-5 py-2.5 text-xs text-muted-foreground/60">
          {{ filteredSources.length }} hidden — click the chevron to expand.
        </div>
        <div v-else-if="!dataStore.externalSources.length" class="px-5 py-10 text-center">
          <LinkIcon class="w-8 h-8 text-muted-foreground/40 mx-auto mb-2" />
          <p class="text-sm font-medium text-muted-foreground">No external sources</p>
          <p class="text-xs text-muted-foreground/70 mt-0.5">Connect a WMS, XYZ/WMTS, or WFS service to show it in portals.</p>
        </div>
        <div v-else class="divide-y divide-border/60">
          <SourceRow v-for="src in pagedSources" :key="src.id" :source="src"
            @delete="askDelete('source', src)" />
        </div>
        <!-- Pagination, shown only once it earns its place. Every row renders whether or not it is
             near the viewport, so an unbounded list costs both screen space and frame time. -->
        <div v-if="!collapsed.source && pageCount(filteredSources.length) > 1"
          class="flex items-center justify-between gap-3 px-5 py-2.5 border-t border-border/60">
          <span class="text-[11px] text-muted-foreground/70">
            {{ pageLabel('source', filteredSources.length) }}
          </span>
          <span class="flex items-center gap-1.5">
            <button @click="setPage('source', page.source - 1)" :disabled="page.source <= 1"
              class="btn-secondary text-xs px-2 py-1 disabled:opacity-40">Prev</button>
            <span class="text-xs text-muted-foreground px-1">{{ Math.min(page.source, pageCount(filteredSources.length)) }} / {{ pageCount(filteredSources.length) }}</span>
            <button @click="setPage('source', page.source + 1)" :disabled="page.source >= pageCount(filteredSources.length)"
              class="btn-secondary text-xs px-2 py-1 disabled:opacity-40">Next</button>
          </span>
        </div>
      </section>
    </div>

    <!-- Modals -->
    <UploadModal v-if="showVectorUpload" type="vector" @close="showVectorUpload = false" />
    <UploadModal v-if="showRasterUpload" type="raster" @close="showRasterUpload = false" />
    <AddSourceModal v-if="showAddSource" @close="showAddSource = false" />
    <DiscoverModal v-if="showDiscover" @close="showDiscover = false" />
    <ConfirmDeleteModal v-if="del" :name="del.name" :usage="del.usage"
      :loading-usage="del.loadingUsage" :busy="del.busy"
      @confirm="confirmDelete" @cancel="del = null" />
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted } from 'vue'
import { useAuthStore } from '@/stores/auth'
import { useDataStore } from '@/stores/data'
import { UploadIcon, DatabaseIcon, ImageIcon, LinkIcon, DownloadIcon, PlusIcon } from './icons'
import VectorRow from '@/components/data/VectorRow.vue'
import RasterRow from '@/components/data/RasterRow.vue'
import UploadModal from '@/components/data/UploadModal.vue'
import SourceRow from '@/components/data/SourceRow.vue'
import AddSourceModal from '@/components/data/AddSourceModal.vue'
import DiscoverModal from '@/components/data/DiscoverModal.vue'
import ConfirmDeleteModal from '@/components/data/ConfirmDeleteModal.vue'
import { getVectorUsage, getRasterUsage, getSourceUsage } from '@/api'

const auth = useAuthStore()
const dataStore = useDataStore()

// ── Delete confirmation (irreversible; warns which portals use the layer, then prunes + re-publishes)
const del = ref(null)  // { type, id, name, usage:[], loadingUsage, busy }
const _usageApi = { vector: getVectorUsage, raster: getRasterUsage, source: getSourceUsage }
const _removeFn = {
  vector: (id) => dataStore.removeVector(id),
  raster: (id) => dataStore.removeRaster(id),
  source: (id) => dataStore.removeExternal(id),
}

async function askDelete(type, item) {
  del.value = { type, id: item.id, name: item.name, usage: [], loadingUsage: true, busy: false }
  try {
    del.value.usage = (await _usageApi[type](item.id)).data
  } catch { /* show the confirm without usage if the check fails */ }
  finally { if (del.value) del.value.loadingUsage = false }
}

async function confirmDelete() {
  if (!del.value || del.value.busy) return  // guard: one delete, no double-fire race
  del.value.busy = true
  try {
    await _removeFn[del.value.type](del.value.id)
  } finally {
    del.value = null
  }
}

// Per-section search (shown once a section holds more than a handful of layers) — matches on
// name plus catalog keywords/abstract so shared metadata makes layers findable.
const vectorSearch = ref('')
const rasterSearch = ref('')
const matches = (layer, q) => {
  const needle = q.trim().toLowerCase()
  if (!needle) return true
  return [layer.name, layer.keywords, layer.abstract, layer.geometry_type]
    .some((v) => v && String(v).toLowerCase().includes(needle))
}

// Creator filter (shared workspace): client-side over the loaded lists — no API param needed
// since lists are fetched whole. Rendered only when more than one creator exists.
const creatorFilter = ref('')
const creators = computed(() => {
  const names = new Set()
  for (const list of [dataStore.vectorLayers, dataStore.rasterLayers, dataStore.externalSources])
    for (const item of list) if (item.created_by) names.add(item.created_by)
  return [...names].sort()
})
const byCreator = (item) => !creatorFilter.value || item.created_by === creatorFilter.value

const filteredVectors = computed(() =>
  dataStore.vectorLayers.filter((l) => matches(l, vectorSearch.value) && byCreator(l)))
const filteredRasters = computed(() =>
  dataStore.rasterLayers.filter((l) => matches(l, rasterSearch.value) && byCreator(l)))
const filteredSources = computed(() => dataStore.externalSources.filter(byCreator))

// ── Collapse + pagination ────────────────────────────────────────────────────
// This page only grows. With a few hundred layers the three sections push each other off the
// screen, so reaching Rasters means scrolling past every vector you own — and the page gets slower
// with every row, since each one renders regardless of whether it is anywhere near the viewport.
//
// Two independent controls, because they solve different halves: COLLAPSE hides a whole section you
// are not working in, PAGINATION bounds the one you are.
const PAGE_SIZE = 20

// Persisted: someone who works mostly with rasters should not re-collapse Vectors on every visit.
// Read defensively — a malformed value must not take the page down with it.
const collapsed = ref(loadCollapsed())
function loadCollapsed() {
  try {
    const v = JSON.parse(localStorage.getItem('gd-data-collapsed') || '{}')
    return { vector: !!v.vector, raster: !!v.raster, source: !!v.source }
  } catch { return { vector: false, raster: false, source: false } }
}
function toggleSection(key) {
  collapsed.value = { ...collapsed.value, [key]: !collapsed.value[key] }
  try { localStorage.setItem('gd-data-collapsed', JSON.stringify(collapsed.value)) } catch { /* private mode */ }
}

const page = ref({ vector: 1, raster: 1, source: 1 })
const pageCount = (total) => Math.max(1, Math.ceil(total / PAGE_SIZE))
function setPage(key, n) {
  page.value = { ...page.value, [key]: Math.max(1, Math.min(n, pageCount(sectionTotal(key)))) }
}
function sectionTotal(key) {
  return key === 'vector' ? filteredVectors.value.length
    : key === 'raster' ? filteredRasters.value.length
    : filteredSources.value.length
}
// Clamp rather than reset: deleting the last row of page 3 should leave you on the new last page,
// not throw you back to the top of the list.
const paged = (list, key) => {
  const p = Math.min(page.value[key], pageCount(list.length))
  return list.slice((p - 1) * PAGE_SIZE, p * PAGE_SIZE)
}
// "1–20 of 137" — built here rather than inline: the template version needed the page clamped
// twice inside one interpolation, which is unreadable and easy to get subtly wrong.
function pageLabel(key, total) {
  const p = Math.min(page.value[key], pageCount(total))
  return `${(p - 1) * PAGE_SIZE + 1}–${Math.min(p * PAGE_SIZE, total)} of ${total}`
}

const pagedVectors = computed(() => paged(filteredVectors.value, 'vector'))
const pagedRasters = computed(() => paged(filteredRasters.value, 'raster'))
const pagedSources = computed(() => paged(filteredSources.value, 'source'))

// A search narrows the list under your feet; staying on page 4 of a 1-page result shows nothing.
watch([vectorSearch, creatorFilter], () => setPage('vector', 1))
watch([rasterSearch, creatorFilter], () => setPage('raster', 1))
watch(creatorFilter, () => setPage('source', 1))

const showVectorUpload = ref(false)
const showRasterUpload = ref(false)
const showAddSource = ref(false)
const showDiscover = ref(false)

onMounted(() => dataStore.refresh())
</script>
