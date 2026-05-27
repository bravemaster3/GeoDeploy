<template>
  <div class="p-6 space-y-6">
    <div class="flex items-center justify-between">
      <h1 class="text-xl font-semibold">My Data</h1>
      <div class="flex gap-2">
        <button @click="showVectorUpload = true" class="btn-primary">
          <UploadIcon class="w-4 h-4" /> Upload vector
        </button>
        <button @click="showRasterUpload = true" class="btn-secondary">
          <UploadIcon class="w-4 h-4" /> Upload raster
        </button>
      </div>
    </div>

    <!-- Vector layers -->
    <section>
      <h2 class="text-sm font-semibold text-gray-500 uppercase tracking-wider mb-3">
        Vector layers (PostGIS)
      </h2>
      <div class="card divide-y divide-gray-100">
        <div v-if="!dataStore.vectorLayers.length" class="px-4 py-6 text-sm text-gray-400 text-center">
          No vector layers yet. Upload a Shapefile, GeoJSON, or GeoPackage.
        </div>
        <VectorRow v-for="layer in dataStore.vectorLayers" :key="layer.id" :layer="layer"
          @delete="dataStore.removeVector(layer.id)" />
      </div>
    </section>

    <!-- Raster layers -->
    <section>
      <h2 class="text-sm font-semibold text-gray-500 uppercase tracking-wider mb-3">
        Raster files (Cloud storage)
      </h2>
      <div class="card divide-y divide-gray-100">
        <div v-if="!dataStore.rasterLayers.length" class="px-4 py-6 text-sm text-gray-400 text-center">
          No raster files yet. Upload a GeoTIFF.
        </div>
        <RasterRow v-for="layer in dataStore.rasterLayers" :key="layer.id" :layer="layer"
          @delete="dataStore.removeRaster(layer.id)" />
      </div>
    </section>

    <!-- Upload modals -->
    <UploadModal v-if="showVectorUpload" type="vector" @close="showVectorUpload = false" />
    <UploadModal v-if="showRasterUpload" type="raster" @close="showRasterUpload = false" />
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useDataStore } from '@/stores/data'
import { UploadIcon } from './icons'
import VectorRow from '@/components/data/VectorRow.vue'
import RasterRow from '@/components/data/RasterRow.vue'
import UploadModal from '@/components/data/UploadModal.vue'

const dataStore = useDataStore()
const showVectorUpload = ref(false)
const showRasterUpload = ref(false)

onMounted(() => dataStore.refresh())
</script>
