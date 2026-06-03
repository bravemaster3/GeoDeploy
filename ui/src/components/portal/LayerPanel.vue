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

        <!-- Line: width + style -->
        <div v-else-if="geomType === 'line'" class="space-y-2">
          <div>
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
          <div>
            <label class="text-xs text-gray-500">Line style</label>
            <select :value="config.style?.lineType || 'solid'"
              @change="emitStyle({ lineType: $event.target.value })"
              class="mt-0.5 w-full text-xs border border-gray-200 rounded px-1.5 py-1 focus:outline-none focus:ring-1 focus:ring-brand-400"
            >
              <option value="solid">Solid</option>
              <option value="dashed">Dashed</option>
              <option value="dotted">Dotted</option>
            </select>
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

      <!-- Raster styling -->
      <template v-else-if="config.layer_type === 'raster'">
        <!-- single-band: palette + hillshade -->
        <template v-if="layer?.band_count === 1">
          <div>
            <label class="text-xs text-gray-500">Color palette</label>
            <select :value="config.style?.colormap || ''"
              :disabled="config.style?.algorithm === 'hillshade'"
              @change="emitStyle({ colormap: $event.target.value || null })"
              class="mt-0.5 w-full text-xs border border-gray-200 rounded px-1.5 py-1 focus:outline-none focus:ring-1 focus:ring-brand-400 disabled:opacity-50"
            >
              <option value="">None (grayscale)</option>
              <option v-for="cm in colormaps" :key="cm" :value="cm">{{ cm }}</option>
            </select>
          </div>
          <div class="flex items-center gap-3">
            <label class="flex items-center gap-1.5 text-xs text-gray-600 cursor-pointer">
              <input type="checkbox" :checked="config.style?.algorithm === 'hillshade'"
                @change="emitStyle({ algorithm: $event.target.checked ? 'hillshade' : null })"
                class="accent-brand-500 flex-shrink-0" />
              Hillshade
            </label>
            <div v-if="config.style?.algorithm === 'hillshade'" class="flex items-center gap-1.5"
              title="Vertical exaggeration (Z factor)">
              <label class="text-xs text-gray-500">Z</label>
              <input type="number" min="0.1" max="10" step="0.1" :value="config.style?.zfactor ?? 1"
                @input="emitStyle({ zfactor: parseFloat($event.target.value) || 1 })"
                class="w-14 text-xs border border-gray-200 rounded px-1.5 py-0.5 focus:outline-none focus:ring-1 focus:ring-brand-400" />
            </div>
          </div>
        </template>
        <p v-else class="text-[10px] text-gray-400">
          Multi-band image ({{ layer?.band_count }} bands) — use stretch to adjust brightness.
        </p>

        <!-- stretch / rescale (all rasters) -->
        <div>
          <div class="flex items-center justify-between mb-0.5">
            <label class="text-xs text-gray-500">Stretch (min / max)</label>
            <button @click="autoStretch" :disabled="autoStretching"
              class="text-xs text-brand-600 hover:text-brand-700 font-medium disabled:opacity-50"
              title="Compute min/max from the raster (2–98th percentile)">
              {{ autoStretching ? 'Computing…' : '⚡ Auto' }}
            </button>
          </div>
          <div class="flex items-center gap-2">
            <input type="number" :value="rescaleMin" @input="setRescale('min', $event.target.value)"
              placeholder="min"
              class="w-16 text-xs border border-gray-200 rounded px-1.5 py-0.5 focus:outline-none focus:ring-1 focus:ring-brand-400" />
            <span class="text-gray-300">–</span>
            <input type="number" :value="rescaleMax" @input="setRescale('max', $event.target.value)"
              placeholder="max"
              class="w-16 text-xs border border-gray-200 rounded px-1.5 py-0.5 focus:outline-none focus:ring-1 focus:ring-brand-400" />
          </div>
          <p class="text-[10px] text-gray-400 mt-0.5">For non-8-bit imagery (e.g. 0–4095). Blank = default.</p>
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
import { saveVectorDefaultStyle, saveRasterDefaultStyle, listColormaps, getRasterStats } from '@/api'
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
// Legend swatch mirroring the layer's actual symbol — colour + line dash for vectors.
const geomSvg = computed(() => {
  const k = geomKind.value
  const col = props.config.style?.color || '#3b82f6'
  if (k === 'polygon')
    return `<svg width="18" height="18" viewBox="0 0 18 18"><rect x="2.5" y="4" width="13" height="10" fill="${col}" fill-opacity="0.45" stroke="${col}" stroke-width="1.5"/></svg>`
  if (k === 'line') {
    const lt = props.config.style?.lineType
    const da = lt === 'dashed' ? ' stroke-dasharray="3 2"' : lt === 'dotted' ? ' stroke-dasharray="0.6 3"' : ''
    return `<svg width="18" height="18" viewBox="0 0 18 18"><line x1="2" y1="9" x2="16" y2="9" stroke="${col}" stroke-width="3" stroke-linecap="round"${da}/></svg>`
  }
  if (k === 'raster')
    return '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="1"/><line x1="3" y1="9" x2="21" y2="9"/><line x1="3" y1="15" x2="21" y2="15"/><line x1="9" y1="3" x2="9" y2="21"/><line x1="15" y1="3" x2="15" y2="21"/></svg>'
  return `<svg width="18" height="18" viewBox="0 0 18 18"><circle cx="9" cy="9" r="5" fill="${col}" stroke="#fff" stroke-width="1.5"/></svg>`
})

function emitStyle(patch) {
  emit('update', { style: { ...props.config.style, ...patch } })
}

const rescaleMin = computed(() => (props.config.style?.rescale || '').split(',')[0] || '')
const rescaleMax = computed(() => (props.config.style?.rescale || '').split(',')[1] || '')
const autoStretching = ref(false)
async function autoStretch() {
  if (!layer.value) return
  autoStretching.value = true
  try {
    const { data } = await getRasterStats(layer.value.id)
    if (data?.rescale) emitStyle({ rescale: data.rescale })
  } catch { /* leave manual values */ } finally {
    autoStretching.value = false
  }
}
function setRescale(which, val) {
  const parts = (props.config.style?.rescale || ',').split(',')
  let mn = which === 'min' ? val : parts[0]
  let mx = which === 'max' ? val : parts[1]
  const rescale = (mn !== '' && mn != null && mx !== '' && mx != null) ? `${mn},${mx}` : null
  emitStyle({ rescale })
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
      : {
          opacity: props.config.opacity,
          colormap: props.config.style?.colormap || null,
          rescale: props.config.style?.rescale || null,
          algorithm: props.config.style?.algorithm || null,
          zfactor: props.config.style?.zfactor ?? null,
        }
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
    style: props.config.layer_type === 'vector'
      ? (ds.style ?? {})
      : { colormap: ds.colormap || null, rescale: ds.rescale || null, algorithm: ds.algorithm || null, zfactor: ds.zfactor ?? null },
    ...(props.config.layer_type === 'vector' ? { popup_fields: ds.popup_fields ?? [] } : {}),
  })
}
</script>
