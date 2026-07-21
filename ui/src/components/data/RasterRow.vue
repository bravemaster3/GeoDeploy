<template>
  <div class="flex items-center gap-4 px-4 py-3 hover:bg-muted/60 group">
    <div class="w-8 h-8 rounded-md bg-amber-500/15 text-amber-400 flex items-center justify-center text-xs font-bold flex-shrink-0">R</div>
    <div class="flex-1 min-w-0">
      <div class="flex items-center gap-1.5 min-w-0">
        <input v-if="editing" ref="nameInput" v-model="draft"
          @keyup.enter="saveName" @keyup.esc="cancelEdit" @blur="saveName"
          class="text-sm font-medium bg-transparent border border-primary/60 rounded px-1 py-0.5 flex-1 min-w-0 focus:outline-none" />
        <template v-else>
          <span class="text-sm font-medium truncate">{{ layer.name }}</span>
          <button v-if="auth.canEdit" @click="startEdit" title="Rename layer"
            class="opacity-0 group-hover:opacity-100 text-muted-foreground/60 hover:text-foreground flex-shrink-0 transition-opacity">
            <svg class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4z"/></svg>
          </button>
        </template>
      </div>
      <div class="text-xs text-muted-foreground flex gap-3 mt-0.5">
        <span v-if="layer.band_count">{{ layer.band_count }} band{{ layer.band_count > 1 ? 's' : '' }}</span>
        <span v-if="layer.crs">{{ layer.crs }}</span>
        <span v-if="layer.file_size">{{ formatBytes(layer.file_size) }}</span>
        <span v-if="layer.status === 'ready'" class="text-green-400">COG ✓</span>
        <span v-if="layer.visibility === 'public'"
          class="px-1.5 py-0.5 rounded bg-emerald-500/15 text-emerald-400 font-medium text-[10px] uppercase tracking-wide">Public data</span>
        <span v-else-if="layer.visibility === 'private'"
          class="px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-400 font-medium text-[10px] uppercase tracking-wide">Private</span>
        <span v-if="layer.created_by" class="text-muted-foreground/70">by {{ layer.created_by }}</span>
      </div>
    </div>
    <StatusBadge :status="layer.status" :progress="layer._job?.progress ?? layer.progress" :step="layer._job?.current_step ?? layer.current_step" />
    <!-- Sharing: workspace visibility (private / organization / public catalog + raw COG) -->
    <button v-if="auth.canEdit && layer.status === 'ready'" @click="showSharing = true"
      class="p-1.5 rounded transition-all"
      :class="sharingBtn.class"
      :title="sharingBtn.title"
    >
      <svg class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/></svg>
    </button>
    <button v-if="auth.canEdit" @click="$emit('delete')"
      class="opacity-0 group-hover:opacity-100 p-1.5 text-muted-foreground/70 hover:text-red-500 rounded transition-all"
    >
      <TrashIcon class="w-4 h-4" />
    </button>
    <SharingModal v-if="showSharing" :layer="layer" layer-type="raster" @close="showSharing = false" />
  </div>
</template>

<script setup>
import { computed, ref, nextTick } from 'vue'
import { TrashIcon } from '@/views/icons'
import { useAuthStore } from '@/stores/auth'
import { useDataStore } from '@/stores/data'
import StatusBadge from '@/components/shared/StatusBadge.vue'
import SharingModal from '@/components/data/SharingModal.vue'

const props = defineProps({ layer: Object })
defineEmits(['delete'])

const auth = useAuthStore()
const dataStore = useDataStore()
const showSharing = ref(false)

// Inline rename
const editing = ref(false)
const draft = ref('')
const nameInput = ref(null)
function startEdit() { draft.value = props.layer.name; editing.value = true; nextTick(() => nameInput.value?.focus()) }
function cancelEdit() { editing.value = false }
async function saveName() {
  if (!editing.value) return       // guard against enter→blur double-fire / esc
  const name = draft.value.trim()
  editing.value = false
  if (!name || name === props.layer.name) return
  try { await dataStore.renameRaster(props.layer.id, name) }
  catch (e) { alert(e.response?.data?.detail || 'Could not rename layer.') }
}
const sharingBtn = computed(() => {
  const v = props.layer.visibility
  if (v === 'public') return { class: 'text-emerald-400', title: 'Public — in the data catalog; edit sharing & metadata' }
  if (v === 'private') return { class: 'text-amber-400', title: 'Private — only you and admins; change sharing' }
  return { class: 'opacity-0 group-hover:opacity-100 text-muted-foreground/70 hover:text-sky-400', title: 'Sharing (organization / private / public)' }
})
const formatBytes = (b) => b > 1e9 ? `${(b/1e9).toFixed(1)} GB` : b > 1e6 ? `${(b/1e6).toFixed(1)} MB` : `${(b/1e3).toFixed(0)} KB`
</script>
