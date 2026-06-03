<template>
  <div class="p-6 lg:p-8">
    <div class="max-w-6xl mx-auto space-y-6">
      <!-- Header -->
      <div class="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <h1 class="text-2xl font-semibold tracking-tight text-gray-900">My Data</h1>
          <p class="text-sm text-gray-500 mt-1">Upload, connect, and manage the spatial layers behind your portals.</p>
        </div>
        <button @click="showDiscover = true" class="btn-secondary">
          <DownloadIcon class="w-4 h-4" /> Import existing
        </button>
      </div>

      <!-- Vector layers -->
      <section class="card overflow-hidden">
        <header class="flex items-center gap-3 px-5 py-3.5 border-b border-gray-100">
          <span class="w-9 h-9 rounded-lg bg-blue-50 text-blue-600 flex items-center justify-center flex-shrink-0">
            <DatabaseIcon class="w-5 h-5" />
          </span>
          <div class="flex-1 min-w-0">
            <h2 class="text-sm font-semibold text-gray-900">Vector layers</h2>
            <p class="text-xs text-gray-400">Stored in PostGIS · served as vector tiles</p>
          </div>
          <span class="text-xs font-medium text-gray-500 bg-gray-100 rounded-full px-2 py-0.5">{{ dataStore.vectorLayers.length }}</span>
          <button @click="showVectorUpload = true" class="btn-primary text-xs px-3 py-1.5">
            <UploadIcon class="w-3.5 h-3.5" /> Upload
          </button>
        </header>
        <div v-if="!dataStore.vectorLayers.length" class="px-5 py-10 text-center">
          <DatabaseIcon class="w-8 h-8 text-gray-300 mx-auto mb-2" />
          <p class="text-sm font-medium text-gray-600">No vector layers yet</p>
          <p class="text-xs text-gray-400 mt-0.5">Upload a Shapefile (.zip), GeoJSON, GeoPackage, or CSV.</p>
        </div>
        <div v-else class="divide-y divide-gray-100">
          <VectorRow v-for="layer in dataStore.vectorLayers" :key="layer.id" :layer="layer"
            @delete="dataStore.removeVector(layer.id)" />
        </div>
      </section>

      <!-- Raster files -->
      <section class="card overflow-hidden">
        <header class="flex items-center gap-3 px-5 py-3.5 border-b border-gray-100">
          <span class="w-9 h-9 rounded-lg bg-amber-50 text-amber-600 flex items-center justify-center flex-shrink-0">
            <ImageIcon class="w-5 h-5" />
          </span>
          <div class="flex-1 min-w-0">
            <h2 class="text-sm font-semibold text-gray-900">Raster files</h2>
            <p class="text-xs text-gray-400">Cloud-optimised GeoTIFFs in object storage</p>
          </div>
          <span class="text-xs font-medium text-gray-500 bg-gray-100 rounded-full px-2 py-0.5">{{ dataStore.rasterLayers.length }}</span>
          <button @click="showRasterUpload = true" class="btn-primary text-xs px-3 py-1.5">
            <UploadIcon class="w-3.5 h-3.5" /> Upload
          </button>
        </header>
        <div v-if="!dataStore.rasterLayers.length" class="px-5 py-10 text-center">
          <ImageIcon class="w-8 h-8 text-gray-300 mx-auto mb-2" />
          <p class="text-sm font-medium text-gray-600">No raster files yet</p>
          <p class="text-xs text-gray-400 mt-0.5">Upload a GeoTIFF (.tif / .tiff).</p>
        </div>
        <div v-else class="divide-y divide-gray-100">
          <RasterRow v-for="layer in dataStore.rasterLayers" :key="layer.id" :layer="layer"
            @delete="dataStore.removeRaster(layer.id)" />
        </div>
      </section>

      <!-- External sources -->
      <section class="card overflow-hidden">
        <header class="flex items-center gap-3 px-5 py-3.5 border-b border-gray-100">
          <span class="w-9 h-9 rounded-lg bg-emerald-50 text-emerald-600 flex items-center justify-center flex-shrink-0">
            <LinkIcon class="w-5 h-5" />
          </span>
          <div class="flex-1 min-w-0">
            <h2 class="text-sm font-semibold text-gray-900">External sources</h2>
            <p class="text-xs text-gray-400">WMS · XYZ · WFS — shown in portals without importing</p>
          </div>
          <span class="text-xs font-medium text-gray-500 bg-gray-100 rounded-full px-2 py-0.5">{{ dataStore.externalSources.length }}</span>
          <button @click="showAddSource = true" class="btn-secondary text-xs px-3 py-1.5">
            <PlusIcon class="w-3.5 h-3.5" /> Connect
          </button>
        </header>
        <div v-if="!dataStore.externalSources.length" class="px-5 py-10 text-center">
          <LinkIcon class="w-8 h-8 text-gray-300 mx-auto mb-2" />
          <p class="text-sm font-medium text-gray-600">No external sources</p>
          <p class="text-xs text-gray-400 mt-0.5">Connect a WMS, XYZ/WMTS, or WFS service to show it in portals.</p>
        </div>
        <div v-else class="divide-y divide-gray-100">
          <SourceRow v-for="src in dataStore.externalSources" :key="src.id" :source="src"
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
import { ref, onMounted } from 'vue'
import { useDataStore } from '@/stores/data'
import { UploadIcon, DatabaseIcon, ImageIcon, LinkIcon, DownloadIcon, PlusIcon } from './icons'
import VectorRow from '@/components/data/VectorRow.vue'
import RasterRow from '@/components/data/RasterRow.vue'
import UploadModal from '@/components/data/UploadModal.vue'
import SourceRow from '@/components/data/SourceRow.vue'
import AddSourceModal from '@/components/data/AddSourceModal.vue'
import DiscoverModal from '@/components/data/DiscoverModal.vue'

const dataStore = useDataStore()
const showVectorUpload = ref(false)
const showRasterUpload = ref(false)
const showAddSource = ref(false)
const showDiscover = ref(false)

onMounted(() => dataStore.refresh())
</script>
