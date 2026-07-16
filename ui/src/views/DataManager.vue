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
        <header class="flex items-center gap-3 px-5 py-3.5 border-b border-border/60">
          <span class="w-9 h-9 rounded-lg bg-blue-500/15 text-blue-400 flex items-center justify-center flex-shrink-0">
            <DatabaseIcon class="w-5 h-5" />
          </span>
          <div class="flex-1 min-w-0">
            <h2 class="text-sm font-semibold text-foreground">Vector layers</h2>
            <p class="text-xs text-muted-foreground/70">Stored in PostGIS · served as vector tiles</p>
          </div>
          <input v-if="dataStore.vectorLayers.length > 3" v-model="vectorSearch" type="search"
            id="vector-search" name="vector-search" placeholder="Search…"
            class="w-36 text-xs bg-background text-foreground placeholder:text-muted-foreground/60 border border-border rounded-lg px-2.5 py-1.5 focus:outline-none focus:ring-1 focus:ring-primary/60" />
          <span class="text-xs font-medium text-muted-foreground bg-muted rounded-full px-2 py-0.5">{{ dataStore.vectorLayers.length }}</span>
          <button v-if="auth.canEdit" @click="showVectorUpload = true" class="btn-primary text-xs px-3 py-1.5">
            <UploadIcon class="w-3.5 h-3.5" /> Upload
          </button>
        </header>
        <div v-if="!dataStore.vectorLayers.length" class="px-5 py-10 text-center">
          <DatabaseIcon class="w-8 h-8 text-muted-foreground/40 mx-auto mb-2" />
          <p class="text-sm font-medium text-muted-foreground">No vector layers yet</p>
          <p class="text-xs text-muted-foreground/70 mt-0.5">Upload a Shapefile (.zip), GeoJSON, GeoPackage, or CSV.</p>
        </div>
        <div v-else-if="!filteredVectors.length" class="px-5 py-6 text-center text-xs text-muted-foreground/70">
          No vector layer matches “{{ vectorSearch }}”.
        </div>
        <div v-else class="divide-y divide-border/60">
          <VectorRow v-for="layer in filteredVectors" :key="layer.id" :layer="layer"
            @delete="dataStore.removeVector(layer.id)" />
        </div>
      </section>

      <!-- Raster files -->
      <section class="card overflow-hidden">
        <header class="flex items-center gap-3 px-5 py-3.5 border-b border-border/60">
          <span class="w-9 h-9 rounded-lg bg-amber-500/15 text-amber-400 flex items-center justify-center flex-shrink-0">
            <ImageIcon class="w-5 h-5" />
          </span>
          <div class="flex-1 min-w-0">
            <h2 class="text-sm font-semibold text-foreground">Raster files</h2>
            <p class="text-xs text-muted-foreground/70">Cloud-optimised GeoTIFFs in object storage</p>
          </div>
          <input v-if="dataStore.rasterLayers.length > 3" v-model="rasterSearch" type="search"
            id="raster-search" name="raster-search" placeholder="Search…"
            class="w-36 text-xs bg-background text-foreground placeholder:text-muted-foreground/60 border border-border rounded-lg px-2.5 py-1.5 focus:outline-none focus:ring-1 focus:ring-primary/60" />
          <span class="text-xs font-medium text-muted-foreground bg-muted rounded-full px-2 py-0.5">{{ dataStore.rasterLayers.length }}</span>
          <button v-if="auth.canEdit" @click="showRasterUpload = true" class="btn-primary text-xs px-3 py-1.5">
            <UploadIcon class="w-3.5 h-3.5" /> Upload
          </button>
        </header>
        <div v-if="!dataStore.rasterLayers.length" class="px-5 py-10 text-center">
          <ImageIcon class="w-8 h-8 text-muted-foreground/40 mx-auto mb-2" />
          <p class="text-sm font-medium text-muted-foreground">No raster files yet</p>
          <p class="text-xs text-muted-foreground/70 mt-0.5">Upload a GeoTIFF (.tif / .tiff).</p>
        </div>
        <div v-else-if="!filteredRasters.length" class="px-5 py-6 text-center text-xs text-muted-foreground/70">
          No raster file matches “{{ rasterSearch }}”.
        </div>
        <div v-else class="divide-y divide-border/60">
          <RasterRow v-for="layer in filteredRasters" :key="layer.id" :layer="layer"
            @delete="dataStore.removeRaster(layer.id)" />
        </div>
      </section>

      <!-- External sources -->
      <section class="card overflow-hidden">
        <header class="flex items-center gap-3 px-5 py-3.5 border-b border-border/60">
          <span class="w-9 h-9 rounded-lg bg-emerald-500/15 text-emerald-400 flex items-center justify-center flex-shrink-0">
            <LinkIcon class="w-5 h-5" />
          </span>
          <div class="flex-1 min-w-0">
            <h2 class="text-sm font-semibold text-foreground">External sources</h2>
            <p class="text-xs text-muted-foreground/70">WMS · XYZ · WFS — shown in portals without importing</p>
          </div>
          <span class="text-xs font-medium text-muted-foreground bg-muted rounded-full px-2 py-0.5">{{ dataStore.externalSources.length }}</span>
          <button v-if="auth.canEdit" @click="showAddSource = true" class="btn-secondary text-xs px-3 py-1.5">
            <PlusIcon class="w-3.5 h-3.5" /> Connect
          </button>
        </header>
        <div v-if="!dataStore.externalSources.length" class="px-5 py-10 text-center">
          <LinkIcon class="w-8 h-8 text-muted-foreground/40 mx-auto mb-2" />
          <p class="text-sm font-medium text-muted-foreground">No external sources</p>
          <p class="text-xs text-muted-foreground/70 mt-0.5">Connect a WMS, XYZ/WMTS, or WFS service to show it in portals.</p>
        </div>
        <div v-else class="divide-y divide-border/60">
          <SourceRow v-for="src in filteredSources" :key="src.id" :source="src"
            @delete="dataStore.removeExternal(src.id)" />
        </div>
      </section>
    </div>

    <!-- Modals -->
    <UploadModal v-if="showVectorUpload" type="vector" @close="showVectorUpload = false" />
    <UploadModal v-if="showRasterUpload" type="raster" @close="showRasterUpload = false" />
    <AddSourceModal v-if="showAddSource" @close="showAddSource = false" />
    <DiscoverModal v-if="showDiscover" @close="showDiscover = false" />
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useAuthStore } from '@/stores/auth'
import { useDataStore } from '@/stores/data'
import { UploadIcon, DatabaseIcon, ImageIcon, LinkIcon, DownloadIcon, PlusIcon } from './icons'
import VectorRow from '@/components/data/VectorRow.vue'
import RasterRow from '@/components/data/RasterRow.vue'
import UploadModal from '@/components/data/UploadModal.vue'
import SourceRow from '@/components/data/SourceRow.vue'
import AddSourceModal from '@/components/data/AddSourceModal.vue'
import DiscoverModal from '@/components/data/DiscoverModal.vue'

const auth = useAuthStore()
const dataStore = useDataStore()

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

const showVectorUpload = ref(false)
const showRasterUpload = ref(false)
const showAddSource = ref(false)
const showDiscover = ref(false)

onMounted(() => dataStore.refresh())
</script>
