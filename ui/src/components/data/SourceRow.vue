<template>
  <div class="flex items-center gap-4 px-4 py-3 hover:bg-gray-50 group">
    <div class="w-8 h-8 rounded-md flex items-center justify-center text-[10px] font-bold flex-shrink-0"
      :class="badgeClass">{{ source.source_type.toUpperCase() }}</div>
    <div class="flex-1 min-w-0">
      <div class="text-sm font-medium truncate">{{ source.name }}</div>
      <div class="text-xs text-gray-500 flex gap-3 mt-0.5">
        <span class="uppercase">{{ source.kind }}</span>
        <span v-if="source.geometry_type">{{ source.geometry_type }}</span>
        <span v-if="source.layer_name" class="font-mono truncate max-w-[12rem]">{{ source.layer_name }}</span>
      </div>
      <div class="text-[10px] text-gray-400 truncate font-mono">{{ source.url }}</div>
    </div>
    <span class="text-[10px] px-2 py-0.5 rounded-full bg-gray-100 text-gray-500 flex-shrink-0">external</span>
    <button @click="$emit('delete')"
      class="opacity-0 group-hover:opacity-100 p-1.5 text-gray-400 hover:text-red-500 rounded transition-all"
      title="Remove source"
    >
      <TrashIcon class="w-4 h-4" />
    </button>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { TrashIcon } from '@/views/icons'

const props = defineProps({ source: Object })
defineEmits(['delete'])

const badgeClass = computed(() => props.source.kind === 'vector'
  ? 'bg-emerald-100 text-emerald-600'
  : 'bg-amber-100 text-amber-600')
</script>
