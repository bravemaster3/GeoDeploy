<template>
  <div class="flex items-center gap-4 px-4 py-3 hover:bg-muted/60 group">
    <div class="w-8 h-8 rounded-md bg-blue-500/15 text-blue-400 flex items-center justify-center text-xs font-bold flex-shrink-0">V</div>
    <div class="flex-1 min-w-0">
      <div class="text-sm font-medium truncate">{{ layer.name }}</div>
      <div class="text-xs text-muted-foreground flex gap-3 mt-0.5 items-center">
        <span v-if="layer.storage_backend === 'geoparquet'"
          class="px-1.5 py-0.5 rounded bg-violet-500/15 text-violet-400 font-medium text-[10px] uppercase tracking-wide">GeoParquet</span>
        <!-- GeoParquet layers display via deck.gl over the prepared file — PMTiles tiling is OPT-IN
             (POST /{id}/tile), so only show this badge for an ACTUAL tiling attempt. tile_status
             'none'/null means "not tiled" (the normal deck.gl case), NOT "tiling in progress". -->
        <span v-if="layer.storage_backend === 'geoparquet' && (layer.tile_status === 'tiling' || layer.tile_status === 'error')"
          class="px-1.5 py-0.5 rounded text-[10px] font-medium"
          :class="layer.tile_status === 'error' ? 'bg-red-500/15 text-red-400' : 'bg-amber-500/15 text-amber-400'">
          {{ layer.tile_status === 'error' ? 'tiling failed' : 'tiling…' }}</span>
        <!-- Tiled to PMTiles: renders via fast static vector tiles (not the deck.gl/DuckDB path) -->
        <span v-if="layer.storage_backend === 'geoparquet' && layer.tile_status === 'ready'"
          class="px-1.5 py-0.5 rounded bg-sky-500/15 text-sky-400 font-medium text-[10px] uppercase tracking-wide"
          title="Tiled to PMTiles — renders via fast static vector tiles">Tiled</span>
        <span v-if="layer.feature_count">{{ layer.feature_count?.toLocaleString() }} features</span>
        <span v-if="layer.geometry_type">{{ layer.geometry_type }}</span>
        <span v-if="layer.file_size">{{ formatBytes(layer.file_size) }}</span>
        <span v-if="layer.is_public"
          class="px-1.5 py-0.5 rounded bg-emerald-500/15 text-emerald-400 font-medium text-[10px] uppercase tracking-wide">Public data</span>
        <span v-if="layer.created_by" class="text-muted-foreground/70">by {{ layer.created_by }}</span>
      </div>
    </div>
    <!-- Re-tile to PMTiles: tiling now runs automatically after prep, but this stays for a manual
         re-tile (e.g. after a workflow change). Placed to the LEFT of the status badge so every
         "Ready" badge lines up at the same right-hand position regardless of storage backend. -->
    <button v-if="auth.canEdit && layer.storage_backend === 'geoparquet' && layer.status === 'ready'"
      @click="onTile" :disabled="tiling || layer.tile_status === 'tiling'"
      class="p-1.5 rounded transition-all text-muted-foreground/70 hover:text-violet-400 disabled:opacity-40"
      :class="layer.tile_status === 'tiling' ? '' : 'opacity-0 group-hover:opacity-100'"
      :title="layer.tile_status === 'ready' ? 'Re-tile for fast display (regenerate PMTiles)' : 'Tile for fast seamless display (PMTiles)'"
    >
      <svg class="w-4 h-4" :class="(tiling || layer.tile_status === 'tiling') ? 'animate-pulse' : ''"
        viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/>
        <rect x="14" y="14" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/></svg>
    </button>
    <!-- Restart processing: a file-backed (GeoParquet) layer whose convert/prep stalled or failed —
         re-runs the right stage without a re-upload (e.g. the worker was restarted mid-job). Left of
         the badge too (only shows for error/processing, so it never shifts a "Ready" badge). -->
    <button v-if="auth.canEdit && layer.storage_backend === 'geoparquet' && (layer.status === 'error' || layer.status === 'processing')"
      @click="onReprocess" :disabled="restarting"
      class="p-1.5 rounded transition-all text-muted-foreground/70 hover:text-primary disabled:opacity-40"
      :class="layer.status === 'error' ? 'text-amber-400' : 'opacity-0 group-hover:opacity-100'"
      title="Restart processing (re-convert / re-prepare — no re-upload needed)"
    >
      <RefreshIcon class="w-4 h-4" :class="restarting ? 'animate-spin' : ''" />
    </button>
    <StatusBadge :status="layer.status" :progress="layer._job?.progress" :step="layer._job?.current_step" />
    <!-- Data sharing: public catalog (STAC) listing + metadata -->
    <button v-if="auth.canEdit && layer.status === 'ready'" @click="showSharing = true"
      class="p-1.5 rounded transition-all"
      :class="layer.is_public ? 'text-emerald-400' : 'opacity-0 group-hover:opacity-100 text-muted-foreground/70 hover:text-emerald-400'"
      :title="layer.is_public ? 'Shared in the public data catalog — edit sharing & metadata' : 'Data sharing & catalog metadata'"
    >
      <svg class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/></svg>
    </button>
    <button v-if="auth.canEdit" @click="$emit('delete')"
      class="opacity-0 group-hover:opacity-100 p-1.5 text-muted-foreground/70 hover:text-red-500 rounded transition-all"
      title="Delete layer"
    >
      <TrashIcon class="w-4 h-4" />
    </button>
    <SharingModal v-if="showSharing" :layer="layer" layer-type="vector" @close="showSharing = false" />
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { TrashIcon, RefreshIcon } from '@/views/icons'
import { useAuthStore } from '@/stores/auth'
import { useDataStore } from '@/stores/data'
import StatusBadge from '@/components/shared/StatusBadge.vue'
import SharingModal from '@/components/data/SharingModal.vue'

const props = defineProps({ layer: Object })
defineEmits(['delete'])

const auth = useAuthStore()
const dataStore = useDataStore()
const showSharing = ref(false)
const restarting = ref(false)
const tiling = ref(false)
async function onReprocess() {
  if (restarting.value) return
  restarting.value = true
  try { await dataStore.reprocessVector(props.layer.id) }
  catch (e) { alert(e.response?.data?.detail || 'Could not restart processing.') }
  finally { restarting.value = false }
}
async function onTile() {
  if (tiling.value || props.layer.tile_status === 'tiling') return
  tiling.value = true
  try { await dataStore.tileVector(props.layer.id) }
  catch (e) { alert(e.response?.data?.detail || 'Could not start tiling.') }
  finally { tiling.value = false }
}
const formatBytes = (b) => b > 1e9 ? `${(b/1e9).toFixed(1)} GB` : b > 1e6 ? `${(b/1e6).toFixed(1)} MB` : `${(b/1e3).toFixed(0)} KB`
</script>
