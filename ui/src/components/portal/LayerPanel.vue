<template>
  <div class="py-2 border-b border-gray-100 last:border-0">
    <div class="flex items-center gap-2">
      <span class="w-5 h-5 rounded text-xs flex items-center justify-center font-bold"
        :class="config.layer_type === 'vector' ? 'bg-blue-100 text-blue-600' : 'bg-amber-100 text-amber-600'"
      >{{ config.layer_type === 'vector' ? 'V' : 'R' }}</span>
      <span class="text-xs font-medium flex-1 truncate">{{ layerName }}</span>
      <button @click="expanded = !expanded" class="text-gray-400 hover:text-gray-600 text-xs">
        {{ expanded ? '▲' : '▼' }}
      </button>
      <button @click="$emit('remove')" class="text-gray-400 hover:text-red-500">
        <TrashIcon class="w-3.5 h-3.5" />
      </button>
    </div>

    <div v-if="expanded" class="mt-2 space-y-2 pl-7">
      <div>
        <label class="text-xs text-gray-500">Opacity</label>
        <input type="range" min="0" max="1" step="0.05" :value="config.opacity"
          @input="$emit('update', { opacity: parseFloat($event.target.value) })"
          class="w-full h-1 accent-brand-500"
        />
      </div>
      <div v-if="config.layer_type === 'vector'">
        <label class="text-xs text-gray-500">Color</label>
        <div class="flex items-center gap-2 mt-0.5">
          <input type="color" :value="config.style?.color || '#3b82f6'"
            @input="$emit('update', { style: { ...config.style, color: $event.target.value } })"
            class="w-6 h-6 rounded border border-gray-200 cursor-pointer"
          />
          <span class="text-xs text-gray-400">{{ config.style?.color || '#3b82f6' }}</span>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import { useDataStore } from '@/stores/data'
import { TrashIcon } from '@/views/icons'

const props = defineProps({ config: Object })
defineEmits(['remove', 'update'])

const dataStore = useDataStore()
const expanded = ref(false)

const layerName = computed(() => {
  const list = props.config.layer_type === 'vector' ? dataStore.vectorLayers : dataStore.rasterLayers
  return list.find(l => l.id === props.config.layer_id)?.name || `Layer ${props.config.layer_id}`
})
</script>
