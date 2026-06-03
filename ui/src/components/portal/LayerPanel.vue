<template>
  <div class="flex items-center gap-1.5 py-1 px-1 rounded hover:bg-gray-50">
    <span class="text-gray-300 cursor-grab flex-shrink-0 flex items-center" title="Drag to reorder" v-html="dragSvg"></span>
    <button @click="toggleVisible" class="text-gray-400 hover:text-gray-700 flex-shrink-0 flex items-center"
      :class="{ 'opacity-50': !visible }" :title="visible ? 'Hide' : 'Show'" v-html="visible ? eyeSvg : eyeOffSvg"></button>
    <button ref="swatchBtn" @click.stop="toggleStyle"
      class="flex-shrink-0 flex items-center justify-center w-[22px] h-[22px] rounded hover:bg-gray-200"
      :class="config.layer_type === 'raster' ? 'text-amber-600' : ''" :title="geomLabel" v-html="geomSvg"></button>
    <span class="text-xs font-medium flex-1 truncate" :class="visible ? '' : 'text-gray-400'" :title="layerName">{{ layerName }}</span>
    <button @click="$emit('zoom')" class="text-gray-400 hover:text-brand-600 flex-shrink-0" title="Zoom to layer">
      <LocateIcon class="w-3.5 h-3.5" />
    </button>
    <button @click="$emit('remove')" class="text-gray-400 hover:text-red-500 flex-shrink-0" title="Remove">
      <TrashIcon class="w-3.5 h-3.5" />
    </button>

    <!-- Symbology popover (opens from the swatch) -->
    <Teleport to="body">
      <div v-if="showStyle" ref="popEl" :style="popStyle"
        class="fixed z-[60] bg-white border border-gray-200 rounded-lg shadow-xl text-gray-700">
        <div class="flex items-center justify-between gap-2 px-3 py-2 border-b border-gray-100 text-xs font-semibold">
          <span class="truncate">{{ layerName }}</span>
          <button @click="showStyle = false" class="text-gray-400 hover:text-gray-700 text-lg leading-none flex-shrink-0">&times;</button>
        </div>
        <div class="px-3 py-2.5 space-y-3 max-h-[70vh] overflow-auto">

          <!-- Opacity (all layers) -->
          <div>
            <div class="flex items-center justify-between mb-0.5">
              <label class="text-xs text-gray-500">Opacity</label>
              <span class="text-xs text-gray-400">{{ Math.round(config.opacity * 100) }}%</span>
            </div>
            <input type="range" min="0" max="1" step="0.05" :value="config.opacity"
              @input="$emit('update', { opacity: parseFloat($event.target.value) })"
              class="w-full h-1 accent-brand-500" />
          </div>

          <!-- Vector style controls -->
          <template v-if="config.layer_type === 'vector'">
            <div>
              <label class="text-xs text-gray-500">Color</label>
              <div class="flex items-center gap-2 mt-0.5">
                <input type="color" :value="config.style?.color || '#3b82f6'"
                  @input="emitStyle({ color: $event.target.value })"
                  class="w-6 h-6 rounded border border-gray-200 cursor-pointer p-0" />
                <span class="text-xs text-gray-400 font-mono">{{ config.style?.color || '#3b82f6' }}</span>
              </div>
            </div>

            <template v-if="geomType === 'polygon'">
              <div>
                <div class="flex items-center justify-between mb-0.5">
                  <label class="text-xs text-gray-500">Fill opacity</label>
                  <span class="text-xs text-gray-400">{{ Math.round((config.style?.fill_opacity ?? 0.45) * 100) }}%</span>
                </div>
                <input type="range" min="0" max="1" step="0.05" :value="config.style?.fill_opacity ?? 0.45"
                  @input="emitStyle({ fill_opacity: parseFloat($event.target.value) })" class="w-full h-1 accent-brand-500" />
              </div>
              <div>
                <label class="text-xs text-gray-500">Outline color</label>
                <div class="flex items-center gap-2 mt-0.5">
                  <input type="color" :value="config.style?.outline_color || '#1d4ed8'"
                    @input="emitStyle({ outline_color: $event.target.value })"
                    class="w-6 h-6 rounded border border-gray-200 cursor-pointer p-0" />
                  <span class="text-xs text-gray-400 font-mono">{{ config.style?.outline_color || '#1d4ed8' }}</span>
                </div>
              </div>
            </template>

            <div v-else-if="geomType === 'line'" class="space-y-2">
              <div>
                <label class="text-xs text-gray-500">Line width</label>
                <div class="flex items-center gap-2 mt-0.5">
                  <input type="number" min="0.5" max="20" step="0.5" :value="config.style?.line_width ?? 2"
                    @input="emitStyle({ line_width: parseFloat($event.target.value) })"
                    class="w-16 text-xs border border-gray-200 rounded px-1.5 py-0.5 focus:outline-none focus:ring-1 focus:ring-brand-400" />
                  <span class="text-xs text-gray-400">px</span>
                </div>
              </div>
              <div>
                <label class="text-xs text-gray-500">Line style</label>
                <select :value="config.style?.lineType || 'solid'" @change="emitStyle({ lineType: $event.target.value })"
                  class="mt-0.5 w-full text-xs border border-gray-200 rounded px-1.5 py-1 focus:outline-none focus:ring-1 focus:ring-brand-400">
                  <option value="solid">Solid</option>
                  <option value="dashed">Dashed</option>
                  <option value="dotted">Dotted</option>
                </select>
              </div>
            </div>

            <div v-else-if="geomType === 'point'" class="space-y-2">
              <div>
                <label class="text-xs text-gray-500">Marker shape</label>
                <select :value="config.style?.marker || 'circle'" @change="emitStyle({ marker: $event.target.value })"
                  class="mt-0.5 w-full text-xs border border-gray-200 rounded px-1.5 py-1 capitalize focus:outline-none focus:ring-1 focus:ring-brand-400">
                  <option v-for="s in markerShapes" :key="s" :value="s">{{ s }}</option>
                </select>
              </div>
              <div>
                <label class="text-xs text-gray-500">Point size</label>
                <div class="flex items-center gap-2 mt-0.5">
                  <input type="number" min="1" max="30" step="1" :value="config.style?.radius ?? 5"
                    @input="emitStyle({ radius: parseFloat($event.target.value) })"
                    class="w-16 text-xs border border-gray-200 rounded px-1.5 py-0.5 focus:outline-none focus:ring-1 focus:ring-brand-400" />
                  <span class="text-xs text-gray-400">px</span>
                </div>
              </div>
            </div>

            <!-- Popup fields -->
            <div v-if="layer?.columns?.length">
              <div class="flex items-center justify-between mb-1">
                <label class="text-xs text-gray-500">Popup fields</label>
                <button v-if="config.popup_fields?.length" @click="$emit('update', { popup_fields: [] })"
                  class="text-xs text-brand-600 hover:text-brand-700">Reset (all)</button>
              </div>
              <div class="space-y-0.5 max-h-36 overflow-y-auto pr-1">
                <label v-for="col in layer.columns" :key="col.name" class="flex items-center gap-1.5 text-xs py-0.5 cursor-pointer group">
                  <input type="checkbox" :checked="isFieldSelected(col.name)" @change="toggleField(col.name, $event.target.checked)"
                    class="accent-brand-500 flex-shrink-0" />
                  <span class="truncate group-hover:text-gray-900 transition-colors">{{ col.name }}</span>
                  <span class="text-gray-300 ml-auto flex-shrink-0 font-mono text-[10px]">{{ shortType(col.type) }}</span>
                </label>
              </div>
            </div>
          </template>

          <!-- Raster styling -->
          <template v-else-if="config.layer_type === 'raster'">
            <!-- Band selection (multiband rasters only) -->
            <template v-if="bandCount > 1">
              <div>
                <label class="text-xs text-gray-500">Bands</label>
                <select :value="bandMode" @change="setBandMode($event.target.value)"
                  class="mt-0.5 w-full text-xs border border-gray-200 rounded px-1.5 py-1 focus:outline-none focus:ring-1 focus:ring-brand-400">
                  <option value="rgb">RGB composite</option>
                  <option value="single">Single band</option>
                </select>
              </div>
              <div v-if="bandMode === 'rgb'" class="flex items-center gap-2">
                <div v-for="(chan, i) in ['R', 'G', 'B']" :key="chan" class="flex items-center gap-1">
                  <label class="text-xs font-medium" :class="['text-red-500','text-green-600','text-blue-500'][i]">{{ chan }}</label>
                  <select :value="rgbBands[i]" @change="setRgbBand(i, $event.target.value)"
                    class="text-xs border border-gray-200 rounded px-1 py-0.5 focus:outline-none focus:ring-1 focus:ring-brand-400">
                    <option v-for="b in bandList" :key="b" :value="b">{{ b }}</option>
                  </select>
                </div>
              </div>
              <div v-else>
                <label class="text-xs text-gray-500">Band</label>
                <select :value="singleBand" @change="setSingleBand($event.target.value)"
                  class="mt-0.5 w-full text-xs border border-gray-200 rounded px-1.5 py-1 focus:outline-none focus:ring-1 focus:ring-brand-400">
                  <option v-for="b in bandList" :key="b" :value="b">Band {{ b }}</option>
                </select>
              </div>
            </template>

            <!-- Palette + hillshade: single-band raster, or a multiband raster in single-band mode -->
            <template v-if="bandCount === 1 || bandMode === 'single'">
              <div>
                <label class="text-xs text-gray-500">Color palette</label>
                <select :value="config.style?.colormap || ''" :disabled="config.style?.algorithm === 'hillshade'"
                  @change="emitStyle({ colormap: $event.target.value || null })"
                  class="mt-0.5 w-full text-xs border border-gray-200 rounded px-1.5 py-1 focus:outline-none focus:ring-1 focus:ring-brand-400 disabled:opacity-50">
                  <option value="">None (grayscale)</option>
                  <option v-for="cm in colormaps" :key="cm" :value="cm">{{ cm }}</option>
                </select>
              </div>
              <div class="flex items-center gap-3">
                <label class="flex items-center gap-1.5 text-xs text-gray-600 cursor-pointer">
                  <input type="checkbox" :checked="config.style?.algorithm === 'hillshade'"
                    @change="emitStyle({ algorithm: $event.target.checked ? 'hillshade' : null })" class="accent-brand-500 flex-shrink-0" />
                  Hillshade
                </label>
                <div v-if="config.style?.algorithm === 'hillshade'" class="flex items-center gap-1.5" title="Vertical exaggeration (Z factor)">
                  <label class="text-xs text-gray-500">Z</label>
                  <input type="number" min="0.1" max="10" step="0.1" :value="config.style?.zfactor ?? 1"
                    @input="emitStyle({ zfactor: parseFloat($event.target.value) || 1 })"
                    class="w-14 text-xs border border-gray-200 rounded px-1.5 py-0.5 focus:outline-none focus:ring-1 focus:ring-brand-400" />
                </div>
              </div>
            </template>

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
                <input type="number" :value="rescaleMin" @input="setRescale('min', $event.target.value)" placeholder="min"
                  class="w-16 text-xs border border-gray-200 rounded px-1.5 py-0.5 focus:outline-none focus:ring-1 focus:ring-brand-400" />
                <span class="text-gray-300">–</span>
                <input type="number" :value="rescaleMax" @input="setRescale('max', $event.target.value)" placeholder="max"
                  class="w-16 text-xs border border-gray-200 rounded px-1.5 py-0.5 focus:outline-none focus:ring-1 focus:ring-brand-400" />
              </div>
              <p class="text-[10px] text-gray-400 mt-0.5">For non-8-bit imagery (e.g. 0–4095). Blank = default.</p>
            </div>
          </template>

          <!-- Default style actions -->
          <div class="flex items-center gap-2 pt-1 border-t border-gray-100">
            <button v-if="layer?.default_style" @click="useDefault" class="text-xs text-brand-600 hover:text-brand-700 font-medium"
              title="Apply saved default style to this portal">↩ Use default</button>
            <button @click="saveDefault" :disabled="savingDefault" class="text-xs text-gray-500 hover:text-gray-700 ml-auto"
              title="Save current style as the default for this layer">{{ savingDefault ? 'Saving…' : '⭐ Save as default' }}</button>
          </div>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount, nextTick } from 'vue'
import { useDataStore } from '@/stores/data'
import { saveVectorDefaultStyle, saveRasterDefaultStyle, listColormaps, getRasterStats } from '@/api'
import { TrashIcon, LocateIcon } from '@/views/icons'

const props = defineProps({ config: Object })
const emit = defineEmits(['remove', 'update', 'zoom'])

const dataStore = useDataStore()
const savingDefault = ref(false)
const colormaps = ref([])

const showStyle = ref(false)
const swatchBtn = ref(null)
const popEl = ref(null)
const popStyle = ref({})

const dragSvg = '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><circle cx="9" cy="6" r="1.4"/><circle cx="15" cy="6" r="1.4"/><circle cx="9" cy="12" r="1.4"/><circle cx="15" cy="12" r="1.4"/><circle cx="9" cy="18" r="1.4"/><circle cx="15" cy="18" r="1.4"/></svg>'
const eyeSvg = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-7 11-7 11 7 11 7-4 7-11 7-11-7-11-7z"/><circle cx="12" cy="12" r="3"/></svg>'
const eyeOffSvg = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17.94 17.94A10.94 10.94 0 0 1 12 19c-7 0-11-7-11-7a18.5 18.5 0 0 1 5.06-5.94M9.9 4.24A11 11 0 0 1 12 4c7 0 11 7 11 7a18.5 18.5 0 0 1-2.16 3.19M1 1l22 22"/></svg>'

const visible = computed(() => props.config.visible !== false)
function toggleVisible() { emit('update', { visible: !visible.value }) }

function toggleStyle() {
  showStyle.value = !showStyle.value
  if (showStyle.value) nextTick(positionPop)
}
function positionPop() {
  const el = swatchBtn.value
  if (!el) return
  const r = el.getBoundingClientRect()
  const w = 230
  let left = r.right + 8
  if (left + w > window.innerWidth) left = Math.max(8, r.left - w - 8)
  popStyle.value = { left: left + 'px', top: Math.min(r.top, window.innerHeight - 340) + 'px', width: w + 'px' }
}
function onDocClick(e) {
  if (!showStyle.value) return
  if (popEl.value && !popEl.value.contains(e.target) && swatchBtn.value && !swatchBtn.value.contains(e.target)) {
    showStyle.value = false
  }
}
onMounted(async () => {
  document.addEventListener('mousedown', onDocClick)
  if (props.config.layer_type === 'raster') {
    try { const { data } = await listColormaps(); colormaps.value = data } catch {}
  }
})
onBeforeUnmount(() => document.removeEventListener('mousedown', onDocClick))

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
  return `<svg width="18" height="18" viewBox="0 0 18 18">${markerSvg(props.config.style?.marker || 'circle', col)}</svg>`
})

const markerShapes = ['circle', 'square', 'triangle', 'diamond', 'star', 'cross']
function starPts(cx, cy, r) {
  const p = []
  for (let i = 0; i < 10; i++) { const a = -Math.PI / 2 + i * Math.PI / 5, rr = (i % 2) ? r * 0.45 : r; p.push((cx + Math.cos(a) * rr).toFixed(1) + ',' + (cy + Math.sin(a) * rr).toFixed(1)) }
  return p.join(' ')
}
function crossPts(cx, cy, r) {
  const t = r * 0.38
  return [[-t, -r], [t, -r], [t, -t], [r, -t], [r, t], [t, t], [t, r], [-t, r], [-t, t], [-r, t], [-r, -t], [-t, -t]]
    .map(d => (cx + d[0]).toFixed(1) + ',' + (cy + d[1]).toFixed(1)).join(' ')
}
function markerSvg(shape, c) {
  const s = ' stroke="#fff" stroke-width="1.5" stroke-linejoin="round"'
  if (shape === 'square') return `<rect x="3" y="3" width="12" height="12" fill="${c}"${s}/>`
  if (shape === 'triangle') return `<polygon points="9,2.5 15.5,15 2.5,15" fill="${c}"${s}/>`
  if (shape === 'diamond') return `<polygon points="9,2 16,9 9,16 2,9" fill="${c}"${s}/>`
  if (shape === 'star') return `<polygon points="${starPts(9, 9, 6.5)}" fill="${c}"${s}/>`
  if (shape === 'cross') return `<polygon points="${crossPts(9, 9, 6.5)}" fill="${c}"${s}/>`
  return `<circle cx="9" cy="9" r="5.5" fill="${c}"${s}/>`
}

function emitStyle(patch) {
  emit('update', { style: { ...props.config.style, ...patch } })
}

// ── Multiband band selection (bidx) ──────────────────────────────────────────
// bidx in the style: [n] = single band, [r,g,b] = RGB composite, absent = TiTiler default.
const bandCount = computed(() => layer.value?.band_count || 1)
const bandList = computed(() => Array.from({ length: bandCount.value }, (_, i) => i + 1))
const bidx = computed(() => props.config.style?.bidx || null)
// Default a multiband raster to RGB; one selected band means single-band mode.
const bandMode = computed(() => (bidx.value && bidx.value.length === 1) ? 'single' : 'rgb')
const rgbBands = computed(() =>
  (bidx.value && bidx.value.length === 3)
    ? bidx.value
    : [1, Math.min(2, bandCount.value), Math.min(3, bandCount.value)])
const singleBand = computed(() => (bidx.value && bidx.value.length === 1) ? bidx.value[0] : 1)

function setBandMode(mode) {
  if (mode === 'rgb') emitStyle({ bidx: rgbBands.value.slice(), colormap: null, algorithm: null })
  else emitStyle({ bidx: [singleBand.value] })
}
function setRgbBand(i, val) {
  const b = rgbBands.value.slice()
  b[i] = parseInt(val)
  emitStyle({ bidx: b, colormap: null, algorithm: null })
}
function setSingleBand(val) {
  emitStyle({ bidx: [parseInt(val)] })
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
  let current = props.config.popup_fields?.length ? [...props.config.popup_fields] : cols.map(c => c.name)
  if (checked) { if (!current.includes(name)) current.push(name) }
  else { current = current.filter(n => n !== name) }
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
          bidx: props.config.style?.bidx || null,
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
      : { colormap: ds.colormap || null, rescale: ds.rescale || null, algorithm: ds.algorithm || null, zfactor: ds.zfactor ?? null, bidx: ds.bidx || null },
    ...(props.config.layer_type === 'vector' ? { popup_fields: ds.popup_fields ?? [] } : {}),
  })
}
</script>
