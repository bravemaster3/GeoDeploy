<template>
  <div class="py-2 border-b border-gray-100 last:border-0">
    <!-- Header row -->
    <div class="flex items-center gap-2">
      <span class="w-5 h-5 rounded text-xs flex items-center justify-center font-bold flex-shrink-0"
        :class="config.layer_type === 'vector' ? 'bg-blue-100 text-blue-600' : 'bg-amber-100 text-amber-600'"
      >{{ config.layer_type === 'vector' ? 'V' : 'R' }}</span>
      <span class="text-xs font-medium flex-1 truncate" :title="layerName">{{ layerName }}</span>
      <button @click="expanded = !expanded" class="text-gray-400 hover:text-gray-600 text-xs px-0.5">
        {{ expanded ? '▲' : '▼' }}
      </button>
      <button @click="$emit('remove')" class="text-gray-400 hover:text-red-500 flex-shrink-0">
        <TrashIcon class="w-3.5 h-3.5" />
      </button>
    </div>

    <!-- Expanded controls -->
    <div v-if="expanded" class="mt-2 space-y-3 pl-7">

      <!-- Opacity (all layers) -->
      <div>
        <div class="flex items-center justify-between mb-0.5">
          <label class="text-xs text-gray-500">Opacity</label>
          <span class="text-xs text-gray-400">{{ Math.round(config.opacity * 100) }}%</span>
        </div>
        <input type="range" min="0" max="1" step="0.05" :value="config.opacity"
          @input="$emit('update', { opacity: parseFloat($event.target.value) })"
          class="w-full h-1 accent-brand-500"
        />
      </div>

      <!-- Vector style controls -->
      <template v-if="config.layer_type === 'vector'">

        <!-- Color (all geom types) -->
        <div>
          <label class="text-xs text-gray-500">Color</label>
          <div class="flex items-center gap-2 mt-0.5">
            <input type="color" :value="config.style?.color || '#3b82f6'"
              @input="emitStyle({ color: $event.target.value })"
              class="w-6 h-6 rounded border border-gray-200 cursor-pointer p-0"
            />
            <span class="text-xs text-gray-400 font-mono">{{ config.style?.color || '#3b82f6' }}</span>
          </div>
        </div>

        <!-- Polygon: fill opacity + outline color -->
        <template v-if="geomType === 'polygon'">
          <div>
            <div class="flex items-center justify-between mb-0.5">
              <label class="text-xs text-gray-500">Fill opacity</label>
              <span class="text-xs text-gray-400">{{ Math.round((config.style?.fill_opacity ?? 0.45) * 100) }}%</span>
            </div>
            <input type="range" min="0" max="1" step="0.05"
              :value="config.style?.fill_opacity ?? 0.45"
              @input="emitStyle({ fill_opacity: parseFloat($event.target.value) })"
              class="w-full h-1 accent-brand-500"
            />
          </div>
          <div>
            <label class="text-xs text-gray-500">Outline color</label>
            <div class="flex items-center gap-2 mt-0.5">
              <input type="color" :value="config.style?.outline_color || '#1d4ed8'"
                @input="emitStyle({ outline_color: $event.target.value })"
                class="w-6 h-6 rounded border border-gray-200 cursor-pointer p-0"
              />
              <span class="text-xs text-gray-400 font-mono">{{ config.style?.outline_color || '#1d4ed8' }}</span>
            </div>
          </div>
        </template>

        <!-- Line: width -->
        <div v-else-if="geomType === 'line'">
          <label class="text-xs text-gray-500">Line width</label>
          <div class="flex items-center gap-2 mt-0.5">
            <input type="number" min="0.5" max="20" step="0.5"
              :value="config.style?.line_width ?? 2"
              @input="emitStyle({ line_width: parseFloat($event.target.value) })"
              class="w-16 text-xs border border-gray-200 rounded px-1.5 py-0.5 focus:outline-none focus:ring-1 focus:ring-brand-400"
            />
            <span class="text-xs text-gray-400">px</span>
          </div>
        </div>

        <!-- Point: radius -->
        <div v-else-if="geomType === 'point'">
          <label class="text-xs text-gray-500">Point size</label>
          <div class="flex items-center gap-2 mt-0.5">
            <input type="number" min="1" max="30" step="1"
              :value="config.style?.radius ?? 5"
              @input="emitStyle({ radius: parseFloat($event.target.value) })"
              class="w-16 text-xs border border-gray-200 rounded px-1.5 py-0.5 focus:outline-none focus:ring-1 focus:ring-brand-400"
            />
            <span class="text-xs text-gray-400">px</span>
          </div>
        </div>

        <!-- Popup fields -->
        <div v-if="layer?.columns?.length">
          <div class="flex items-center justify-between mb-1">
            <label class="text-xs text-gray-500">Popup fields</label>
            <button
              v-if="config.popup_fields?.length"
              @click="$emit('update', { popup_fields: [] })"
              class="text-xs text-brand-600 hover:text-brand-700"
            >Reset (all)</button>
          </div>
          <div class="space-y-0.5 max-h-36 overflow-y-auto pr-1">
            <label v-for="col in layer.columns" :key="col.name"
              class="flex items-center gap-1.5 text-xs py-0.5 cursor-pointer group"
            >
              <input type="checkbox"
                :checked="isFieldSelected(col.name)"
                @change="toggleField(col.name, $event.target.checked)"
                class="accent-brand-500 flex-shrink-0"
              />
              <span class="truncate group-hover:text-gray-900 transition-colors">{{ col.name }}</span>
              <span class="text-gray-300 ml-auto flex-shrink-0 font-mono text-[10px]">{{ shortType(col.type) }}</span>
            </label>
          </div>
        </div>

      </template>
    </div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import { useDataStore } from '@/stores/data'
import { TrashIcon } from '@/views/icons'

const props = defineProps({ config: Object })
const emit = defineEmits(['remove', 'update'])

const dataStore = useDataStore()
const expanded = ref(false)

const layer = computed(() => {
  const list = props.config.layer_type === 'vector' ? dataStore.vectorLayers : dataStore.rasterLayers
  return list.find(l => l.id === props.config.layer_id) || null
})

const layerName = computed(() => layer.value?.name || `Layer ${props.config.layer_id}`)

const geomType = computed(() => {
  const g = (layer.value?.geometry_type || '').toLowerCase()
  if (g.includes('polygon')) return 'polygon'
  if (g.includes('line')) return 'line'
  if (g.includes('point')) return 'point'
  return 'unknown'
})

function emitStyle(patch) {
  emit('update', { style: { ...props.config.style, ...patch } })
}

function isFieldSelected(name) {
  const fields = props.config.popup_fields
  return !fields?.length || fields.includes(name)
}

function toggleField(name, checked) {
  const cols = layer.value?.columns || []
  // Start from explicit list; if empty, expand to all columns first
  let current = props.config.popup_fields?.length
    ? [...props.config.popup_fields]
    : cols.map(c => c.name)

  if (checked) {
    if (!current.includes(name)) current.push(name)
  } else {
    current = current.filter(n => n !== name)
  }

  // If every column is selected, store as [] (means "all")
  const allSelected = cols.every(c => current.includes(c.name))
  emit('update', { popup_fields: allSelected ? [] : current })
}

function shortType(type) {
  const t = (type || '').toLowerCase()
  if (t.includes('int') || t.includes('num')) return 'num'
  if (t.includes('float') || t.includes('real') || t.includes('double')) return 'dec'
  if (t.includes('bool')) return 'bool'
  if (t.includes('date') || t.includes('time')) return 'date'
  return 'str'
}
</script>
