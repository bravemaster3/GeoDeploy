<template>
  <div class="flex items-center gap-4 px-4 py-3 hover:bg-muted/60 group">
    <div class="w-8 h-8 rounded-md bg-amber-500/15 text-amber-400 flex items-center justify-center text-xs font-bold flex-shrink-0">R</div>
    <div class="flex-1 min-w-0">
      <div class="text-sm font-medium truncate">{{ layer.name }}</div>
      <div class="text-xs text-muted-foreground flex gap-3 mt-0.5">
        <span v-if="layer.band_count">{{ layer.band_count }} band{{ layer.band_count > 1 ? 's' : '' }}</span>
        <span v-if="layer.crs">{{ layer.crs }}</span>
        <span v-if="layer.file_size">{{ formatBytes(layer.file_size) }}</span>
        <span v-if="layer.status === 'ready'" class="text-green-400">COG ✓</span>
        <span v-if="layer.is_public"
          class="px-1.5 py-0.5 rounded bg-emerald-500/15 text-emerald-400 font-medium text-[10px] uppercase tracking-wide">Public data</span>
      </div>
    </div>
    <StatusBadge :status="layer.status" :progress="layer._job?.progress" :step="layer._job?.current_step" />
    <!-- Data sharing: public catalog (STAC) listing + raw COG access + metadata -->
    <button v-if="layer.status === 'ready'" @click="showSharing = true"
      class="p-1.5 rounded transition-all"
      :class="layer.is_public ? 'text-emerald-400' : 'opacity-0 group-hover:opacity-100 text-muted-foreground/70 hover:text-emerald-400'"
      :title="layer.is_public ? 'Shared in the public data catalog — edit sharing & metadata' : 'Data sharing & catalog metadata'"
    >
      <svg class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/></svg>
    </button>
    <button @click="$emit('delete')"
      class="opacity-0 group-hover:opacity-100 p-1.5 text-muted-foreground/70 hover:text-red-500 rounded transition-all"
    >
      <TrashIcon class="w-4 h-4" />
    </button>
    <SharingModal v-if="showSharing" :layer="layer" layer-type="raster" @close="showSharing = false" />
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { TrashIcon } from '@/views/icons'
import StatusBadge from '@/components/shared/StatusBadge.vue'
import SharingModal from '@/components/data/SharingModal.vue'

defineProps({ layer: Object })
defineEmits(['delete'])

const showSharing = ref(false)
const formatBytes = (b) => b > 1e9 ? `${(b/1e9).toFixed(1)} GB` : b > 1e6 ? `${(b/1e6).toFixed(1)} MB` : `${(b/1e3).toFixed(0)} KB`
</script>
