<template>
  <div class="py-2 border-b border-gray-100 last:border-0">
    <!-- Header row (click name/icon to expand styling) -->
    <div class="flex items-center gap-2">
      <span class="flex-shrink-0 flex items-center justify-center w-[18px] h-[18px]"
        :class="config.layer_type === 'raster' ? 'text-amber-600' : 'text-blue-600'"
        :title="geomLabel" v-html="geomSvg"></span>
      <button type="button" @click="expanded = !expanded"
        class="text-xs font-medium flex-1 truncate text-left" :title="layerName">{{ layerName }}</button>
      <button @click="$emit('zoom')" class="text-gray-400 hover:text-brand-600 flex-shrink-0" title="Zoom to layer">
        <LocateIcon class="w-3.5 h-3.5" />
      </button>
      <button @click="expanded = !expanded" class="text-gray-400 hover:text-gray-600 text-xs px-0.5"
        :title="expanded ? 'Collapse' : 'Expand styling'">
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

      <!-- Raster: colormap (single-band only) -->
      <template v-else-if="config.layer_type === 'raster' && layer?.band_count === 1">
        <div>
          <label class="text-xs text-gray-500">Color ramp</label>
          <select :value="config.style?.colormap || ''"
            @change="emitStyle({ colormap: $event.target.value || null })"
            class="mt-0.5 w-full text-xs border border-gray-200 rounded px-1.5 py-1 focus:outline-none focus:ring-1 focus:ring-brand-400"
          >
            <option value="">None (grayscale)</option>
            <option v-for="cm in colormaps" :key="cm" :value="cm">{{ cm }}</option>
          </select>
        </div>
      </template>

      <!-- Default style actions -->
      <div class="flex items-center gap-2 pt-1 border-t border-gray-100">
        <button
          v-if="layer?.default_style"
          @click="useDefault"
          class="text-xs text-brand-600 hover:text-brand-700 font-medium"
          title="Apply saved default style to this portal"
        >↩ Use default</button>
        <button
          @click="saveDefault"
          :disabled="savingDefault"
          class="text-xs text-gray-500 hover:text-gray-700 ml-auto"
          title="Save current style as the default for this layer"
        >{{ savingDefault ? 'Saving…' : '⭐ Save as default' }}</button>
      </div>

    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useDataStore } from '@/stores/data'
import { saveVectorDefaultStyle, saveRasterDefaultStyle, listColormaps } from '@/api'
import { TrashIcon, LocateIcon } from '@/views/icons'

const props = defineProps({
  config: Object,
  initialExpanded: { type: Boolean, default: false },
})
const emit = defineEmits(['remove', 'update', 'zoom'])

const dataStore = useDataStore()
const expanded = ref(props.initialExpanded)
const savingDefault = ref(false)
const colormaps = ref([])

onMounted(async () => {
  if (props.config.layer_type === 'raster') {
    try {
      const { data } = await listColormaps()
      colormaps.value = data
    } catch {}
  }
})

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

const geomKind = computed(() => props.config.layer_type === 'raster' ? 'raster' : geomType.value)
const geomLabel = computed(() => ({
  polygon: 'Polygons', line: 'Lines', point: 'Points', raster: 'Raster',
}[geomKind.value] || 'Vector'))
const geomSvg = computed(() => {
  const a = 'width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"'
  switch (geomKind.value) {
    case 'polygon': return `<svg ${a}><path d="M12 3l8 6-3 11H7L4 9z"/></svg>`
    case 'line': return `<svg ${a}><polyline points="3 17 9 11 14 15 21 5"/></svg>`
    case 'raster': return `<svg ${a}><rect x="3" y="3" width="18" height="18" rx="1"/><line x1="3" y1="9" x2="21" y2="9"/><line x1="3" y1="15" x2="21" y2="15"/><line x1="9" y1="3" x2="9" y2="21"/><line x1="15" y1="3" x2="15" y2="21"/></svg>`
    default: return `<svg width="16" height="16" viewBox="0 0 24 24"><circle cx="12" cy="12" r="5" fill="currentColor"/></svg>`
  }
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
  let current = props.config.popup_fields?.length
    ? [...props.config.popup_fields]
    : cols.map(c => c.name)

  if (checked) {
    if (!current.includes(name)) current.push(name)
  } else {
    current = current.filter(n => n !== name)
  }

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

async function saveDefault() {
  if (!layer.value) return
  savingDefault.value = true
  try {
    const body = props.config.layer_type === 'vector'
      ? { opacity: props.config.opacity, style: props.config.style, popup_fields: props.config.popup_fields }
      : { opacity: props.config.opacity, colormap: props.config.style?.colormap || null }
    const fn = props.config.layer_type === 'vector' ? saveVectorDefaultStyle : saveRasterDefaultStyle
    const { data: updated } = await fn(layer.value.id, body)
    const list = props.config.layer_type === 'vector' ? dataStore.vectorLayers : dataStore.rasterLayers
    const idx = list.findIndex(l => l.id === layer.value.id)
    if (idx !== -1) list[idx] = updated
  } finally {
    savingDefault.value = false
  }
}

function useDefault() {
  if (!layer.value?.default_style) return
  const ds = layer.value.default_style
  emit('update', {
    opacity: ds.opacity ?? 1.0,
    style: props.config.layer_type === 'vector' ? (ds.style ?? {}) : { colormap: ds.colormap || null },
    ...(props.config.layer_type === 'vector' ? { popup_fields: ds.popup_fields ?? [] } : {}),
  })
}
</script>
