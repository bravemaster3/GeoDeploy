<template>
  <div class="flex items-center gap-4 px-4 py-3 hover:bg-muted/60 group">
    <!-- SELECTION. Shown only to someone who may delete, because choosing rows you are not
         allowed to act on is an offer the page cannot keep. `.stop` so ticking a row never
         triggers whatever else the row does when clicked. -->
    <label v-if="selectable" class="flex-shrink-0 flex items-center" @click.stop
      :title="selected ? 'Deselect' : 'Select'">
      <input type="checkbox" :checked="selected" @change="$emit('toggle')"
        class="w-4 h-4 rounded border-border text-primary focus:ring-1 focus:ring-primary/60
               bg-background cursor-pointer" />
    </label>

    <div class="w-8 h-8 rounded-md flex items-center justify-center text-[10px] font-bold flex-shrink-0"
      :class="badgeClass">{{ source.source_type.toUpperCase() }}</div>
    <div class="flex-1 min-w-0">
      <RouterLink :to="`/data/external/${source.id}`"
        class="text-sm font-medium truncate block hover:text-primary hover:underline"
        title="Open this source's page">{{ source.name }}</RouterLink>
      <div class="text-xs text-muted-foreground flex gap-3 mt-0.5">
        <span class="uppercase">{{ source.kind }}</span>
        <span v-if="source.geometry_type">{{ source.geometry_type }}</span>
        <span v-if="source.layer_name" class="font-mono truncate max-w-[12rem]">{{ source.layer_name }}</span>
      </div>
      <div class="text-[10px] text-muted-foreground/70 truncate font-mono">{{ source.url }}</div>
    </div>
    <span v-if="source.created_by" class="text-xs text-muted-foreground/70 flex-shrink-0">by {{ source.created_by }}</span>
    <span class="text-[10px] px-2 py-0.5 rounded-full bg-muted text-muted-foreground flex-shrink-0">external</span>
    <VisibilitySelect v-if="auth.canEdit" :model-value="source.visibility || 'organization'"
      :disabled="savingVis" @change="onVisibility" class="flex-shrink-0" />
    <span v-else-if="source.visibility === 'private'"
      class="text-[10px] px-2 py-0.5 rounded-full bg-amber-500/15 text-amber-400 flex-shrink-0">Private</span>
    <button v-if="auth.canEdit" @click="$emit('delete')"
      class="opacity-0 group-hover:opacity-100 p-1.5 text-muted-foreground/70 hover:text-red-500 rounded transition-all"
      title="Remove source"
    >
      <TrashIcon class="w-4 h-4" />
    </button>
  </div>
</template>

<script setup>
import { RouterLink } from 'vue-router'
import { computed, ref } from 'vue'
import { TrashIcon } from '@/views/icons'
import { useAuthStore } from '@/stores/auth'
import { setSourceSharing } from '@/api'
import VisibilitySelect from '@/components/shared/VisibilitySelect.vue'

const props = defineProps({
  source: Object,
  // Selection is DRIVEN BY THE LIST, not held here: the list is what knows how many are
  // chosen across all three sections, and a row that remembered its own tick would keep it
  // through a filter change that hid the row.
  selectable: { type: Boolean, default: false },
  selected: { type: Boolean, default: false },
})
defineEmits(['delete', 'toggle'])

const auth = useAuthStore()
const savingVis = ref(false)

async function onVisibility(v) {
  savingVis.value = true
  try {
    const { data } = await setSourceSharing(props.source.id, v)
    Object.assign(props.source, data)
  } catch (e) {
    alert(e.response?.data?.detail || 'Could not change sharing.')
  } finally {
    savingVis.value = false
  }
}

const badgeClass = computed(() => props.source.kind === 'vector'
  ? 'bg-emerald-500/15 text-emerald-400'
  : 'bg-amber-500/15 text-amber-400')
</script>
