<!--
  A vector layer's attribute table, read from the instance's own OGC API - Features
  (`/api/ogc/collections/vector-<uid>/items`).

  Why that endpoint and not the map: the map only holds what the tiles in view carry — clipped,
  thinned at low zoom, and (on a GeoParquet layer) sometimes no attributes at all. OGC API serves
  the TABLE, from PostGIS or GeoParquet alike, paged, bbox-filtered, with the same visibility rule
  as every other surface (a signed-in user's token rides along through the shared `api` client, a
  public visitor sees a public layer) and with GeoDeploy's internal `__gd_*` columns already
  stripped. One reader for every storage backend, and it is the one QGIS uses, so a value shown
  here is the value QGIS gets.
-->
<template>
  <section class="card p-4" ref="root">
    <div class="flex items-center gap-2 flex-wrap">
      <button class="flex items-center gap-2 text-left" @click="toggle">
        <span class="text-xs text-muted-foreground/60">{{ open ? '▾' : '▸' }}</span>
        <h2 class="text-sm font-semibold">Attribute table</h2>
      </button>
      <template v-if="open">
        <label class="flex items-center gap-1.5 text-[11px] text-muted-foreground cursor-pointer select-none ml-2"
          title="Only the features inside the map's current view; follows the map as you pan and zoom">
          <input type="checkbox" v-model="inView" class="accent-primary" />
          In map view only
        </label>
        <span class="ml-auto text-[11px] text-muted-foreground/80 tabular-nums">{{ rangeLabel }}</span>
        <button class="gd-page" :disabled="offset === 0 || loading" @click="go(-1)"
          title="Previous page" aria-label="Previous page">‹</button>
        <button class="gd-page" :disabled="!hasNext || loading" @click="go(1)"
          title="Next page" aria-label="Next page">›</button>
      </template>
    </div>

    <div v-if="open" class="mt-3">
      <p v-if="error" class="text-xs text-red-400">{{ error }}</p>
      <p v-else-if="!loading && !rows.length" class="text-xs text-muted-foreground">
        {{ inView ? 'No features in the current map view.' : 'This layer has no features.' }}
      </p>
      <div v-else class="overflow-auto max-h-[60vh] border border-border/60 rounded-md"
        :class="loading ? 'opacity-60' : ''">
        <table class="text-xs w-full border-collapse">
          <thead class="sticky top-0 bg-card z-[1]">
            <tr>
              <th v-for="c in columns" :key="c"
                class="text-left font-medium text-muted-foreground px-2 py-1.5 border-b border-border whitespace-nowrap">
                {{ c }}
              </th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(r, i) in rows" :key="i" :ref="el => { if (i === highlighted) highlightEl = el }"
              @click="pick(i, r)"
              class="cursor-pointer border-b border-border/30 hover:bg-muted/60"
              :class="i === highlighted ? 'bg-primary/15' : ''"
              title="Click to show this feature on the map">
              <td v-for="c in columns" :key="c" class="px-2 py-1 max-w-[18rem] truncate"
                :title="cell(r, c)">{{ cell(r, c) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </section>
</template>

<script setup>
import { computed, nextTick, ref, watch } from 'vue'
import api from '@/api'
import { isInternalField } from '@/lib/identify'

const props = defineProps({
  layer: { type: Object, required: true },
  //: () => [minx, miny, maxx, maxy] in EPSG:4326 — the map's current view.
  viewBbox: { type: Function, required: true },
  //: Bumped by the page whenever the map stops moving, so "in view" follows it.
  viewVersion: { type: Number, default: 0 },
})
const emit = defineEmits(['select'])

const PAGE = 100
const root = ref(null)
const open = ref(false)
const inView = ref(false)
const offset = ref(0)
const rows = ref([])            // [{ properties, geometry }]
const matched = ref(null)
const hasNext = ref(false)
const loading = ref(false)
const error = ref('')
const highlighted = ref(-1)
let highlightEl = null
let wanted = null              // properties to highlight once the page that holds them arrives
let seq = 0

const collection = computed(() => `vector-${props.layer.uid || props.layer.id}`)
// The layer's own columns, in their order, from ingest. Only when that list is missing (a layer
// ingested before it was stored) do the features' keys stand in — otherwise OGC API's own `id`
// (the table's primary key, which no popup shows) would appear as a column of the data.
const columns = computed(() => {
  const out = (props.layer.columns || []).map(c => c?.name).filter(n => n && !isInternalField(n))
  if (out.length) return out
  for (const r of rows.value) {
    for (const k of Object.keys(r.properties || {})) {
      if (!out.includes(k) && !isInternalField(k)) out.push(k)
    }
  }
  return out
})
const rangeLabel = computed(() => {
  if (!rows.value.length) return loading.value ? 'Loading…' : ''
  const a = offset.value + 1
  const b = offset.value + rows.value.length
  const total = matched.value != null ? ` of ${matched.value.toLocaleString()}` : ''
  return `${a.toLocaleString()}–${b.toLocaleString()}${total}`
})

function cell(r, c) {
  const v = r.properties?.[c]
  return v == null ? '' : (typeof v === 'object' ? JSON.stringify(v) : String(v))
}

async function load() {
  const mine = ++seq
  loading.value = true
  error.value = ''
  const params = { limit: PAGE, offset: offset.value }
  if (inView.value) {
    const b = props.viewBbox()
    if (b) params.bbox = b.map(v => Number(v).toFixed(6)).join(',')
  }
  try {
    const { data } = await api.get(`/ogc/collections/${collection.value}/items`, { params })
    if (mine !== seq) return          // a newer request (page, view) has superseded this one
    rows.value = (data.features || []).map(f => ({ properties: f.properties || {}, geometry: f.geometry }))
    matched.value = data.numberMatched ?? null
    hasNext.value = (data.links || []).some(l => l.rel === 'next')
    markWanted()
  } catch (e) {
    if (mine !== seq) return
    rows.value = []
    error.value = e.response?.status === 404
      ? 'The table is not available for this layer.'
      : (e.response?.data?.detail || 'Could not load the attribute table.')
  } finally {
    if (mine === seq) loading.value = false
  }
}

function same(a, b) {
  const keys = Object.keys(a || {}).filter(k => !isInternalField(k))
  return keys.length > 0 && keys.every(k => String(a[k] ?? '') === String(b?.[k] ?? ''))
}

async function markWanted() {
  highlighted.value = wanted ? rows.value.findIndex(r => same(wanted, r.properties)) : -1
  if (highlighted.value < 0) return
  highlightEl = null
  await nextTick()
  highlightEl?.scrollIntoView({ block: 'nearest' })
}

/** A row clicked: it stays marked, so after the map has flown away it is clear which one it was. */
function pick(i, r) {
  wanted = r.properties
  highlighted.value = i
  emit('select', r)
}

function toggle() {
  open.value = !open.value
  if (open.value && !rows.value.length) load()
}
function go(step) {
  offset.value = Math.max(0, offset.value + step * PAGE)
  load()
}

watch(inView, () => { offset.value = 0; if (open.value) load() })
watch(() => props.viewVersion, () => { if (open.value && inView.value) { offset.value = 0; load() } })
watch(collection, () => { offset.value = 0; rows.value = []; if (open.value) load() })

/**
 * Open the table and bring it into view — from the map popup's "View attribute table". With
 * `feature`, the table switches to the map view (where the clicked feature certainly is, and
 * usually on the first page) and highlights its row once that page arrives.
 */
async function reveal(feature = null) {
  wanted = feature
  const wasOpen = open.value
  open.value = true
  if (feature && !inView.value) inView.value = true          // the watcher reloads
  else if (!wasOpen || !rows.value.length) { offset.value = 0; await load() }
  else markWanted()
  await nextTick()
  root.value?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}
defineExpose({ reveal })
</script>

<style scoped>
.gd-page {
  @apply px-2 py-0.5 rounded border border-border bg-card text-muted-foreground text-sm leading-none
         hover:text-foreground hover:bg-muted disabled:opacity-40 disabled:hover:bg-card;
}
</style>
