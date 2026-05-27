<template>
  <div class="flex items-center gap-4 px-4 py-3 hover:bg-gray-50 group">
    <div class="w-8 h-8 rounded-md bg-amber-100 text-amber-600 flex items-center justify-center text-xs font-bold flex-shrink-0">R</div>
    <div class="flex-1 min-w-0">
      <div class="text-sm font-medium truncate">{{ layer.name }}</div>
      <div class="text-xs text-gray-500 flex gap-3 mt-0.5">
        <span v-if="layer.band_count">{{ layer.band_count }} band{{ layer.band_count > 1 ? 's' : '' }}</span>
        <span v-if="layer.crs">{{ layer.crs }}</span>
        <span v-if="layer.file_size">{{ formatBytes(layer.file_size) }}</span>
        <span v-if="layer.status === 'ready'" class="text-green-600">COG ✓</span>
      </div>
    </div>
    <StatusBadge :status="layer.status" :progress="layer._job?.progress" :step="layer._job?.current_step" />
    <button @click="$emit('delete')"
      class="opacity-0 group-hover:opacity-100 p-1.5 text-gray-400 hover:text-red-500 rounded transition-all"
    >
      <TrashIcon class="w-4 h-4" />
    </button>
  </div>
</template>

<script setup>
import { TrashIcon } from '@/views/icons'
import StatusBadge from '@/components/shared/StatusBadge.vue'

defineProps({ layer: Object })
defineEmits(['delete'])

const formatBytes = (b) => b > 1e9 ? `${(b/1e9).toFixed(1)} GB` : b > 1e6 ? `${(b/1e6).toFixed(1)} MB` : `${(b/1e3).toFixed(0)} KB`
</script>
