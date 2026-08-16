<!--
  One layer, on its own page.

  My Data lists layers but never shows you one — to actually LOOK at a layer you had to build a
  portal around it, which is a strange price for answering "what is in this file?". This is that
  page: the layer on a map exactly as a portal would draw it, what it is, how it is served, and
  every action the list row offers.

  Nothing here re-implements anything. The map comes from `lib/mapStyle.buildMapStyle` — the same
  function the portal editor uses and the twin of `portal_generator.generate_style` — so what you
  see here IS what a portal shows. The actions are the same modals and the same store calls the row
  uses. A second implementation of either would drift, and the drift would be a page that lies
  about the layer it describes.
-->
<template>
  <div v-if="!layer" class="p-8 text-center text-muted-foreground">
    <p v-if="dataStore.loading">Loading…</p>
    <template v-else>
      <p class="text-sm">That layer is not here any more.</p>
      <RouterLink to="/data" class="btn-secondary mt-4 inline-flex">Back to My Data</RouterLink>
    </template>
  </div>

  <div v-else class="space-y-5">
    <!-- Header ------------------------------------------------------------------------------ -->
    <div class="flex items-start justify-between gap-4 flex-wrap">
      <div class="min-w-0">
        <RouterLink to="/data"
          class="text-xs text-muted-foreground/70 hover:text-foreground inline-flex items-center gap-1">
          <span aria-hidden="true">←</span> My Data
        </RouterLink>
        <div class="flex items-center gap-2 mt-1">
          <span class="w-3 h-3 rounded-sm flex-shrink-0 ring-1 ring-black/20"
            :style="{ background: swatch }" :title="`Drawn in ${swatch}`" />
          <h1 v-if="!renaming" class="text-2xl font-semibold truncate">{{ layer.name }}</h1>
          <input v-else ref="nameInput" v-model="draftName" @keyup.enter="commitRename"
            @keyup.esc="renaming = false" @blur="commitRename"
            class="input text-2xl font-semibold py-0.5" />
          <button v-if="auth.canEdit && !renaming" @click="startRename"
            class="text-muted-foreground/60 hover:text-foreground text-sm" title="Rename layer">✎</button>
        </div>
        <p class="text-xs text-muted-foreground/70 mt-1">
          {{ kindLabel }}<span v-if="layer.created_by"> · added by {{ layer.created_by }}</span>
          <span v-if="layer.created_at"> · {{ new Date(layer.created_at).toLocaleDateString() }}</span>
        </p>
      </div>

      <div class="flex items-center gap-2 flex-wrap">
        <span class="badge" :class="statusClass">{{ layer.status }}</span>
        <span v-if="layer.tile_status === 'ready'" class="badge badge-muted"
          title="Tiled to PMTiles — renders as fast static vector tiles">Tiled</span>
        <span class="badge badge-muted">{{ visibilityLabel }}</span>
      </div>
    </div>

    <!-- Map + facts ------------------------------------------------------------------------- -->
    <div class="grid grid-cols-1 lg:grid-cols-3 gap-5">
      <div class="lg:col-span-2 card overflow-hidden">
        <div ref="mapEl" class="w-full h-[460px] bg-muted/40" />
        <p v-if="mapNote" class="text-xs text-amber-300/90 px-4 py-2 border-t border-border/60">
          {{ mapNote }}
        </p>
      </div>

      <div class="space-y-5">
        <section class="card p-4">
          <h2 class="text-sm font-semibold mb-3">What it is</h2>
          <dl class="space-y-1.5 text-sm">
            <Fact label="Geometry" :value="layer.geometry_type" />
            <Fact label="Features" :value="layer.feature_count?.toLocaleString()" />
            <Fact label="Bands" :value="layer.band_count" />
            <Fact label="CRS" :value="layer.crs" mono />
            <Fact label="Size" :value="prettySize" />
            <Fact label="Extent" :value="prettyExtent" mono
              hint="West, south, east, north in EPSG:4326" />
          </dl>
        </section>

        <section class="card p-4">
          <h2 class="text-sm font-semibold mb-3">How it is served</h2>
          <dl class="space-y-1.5 text-sm">
            <Fact label="Storage" :value="storageLabel" />
            <Fact label="Tiles" :value="tilesLabel" />
            <Fact v-if="isRaster" label="Zoom floor"
              :value="layer.low_zoom_ok === false ? 'High zoom only' : 'Draws when zoomed out'"
              hint="Measured from the file's overview pyramid at ingest" />
            <Fact v-if="isRaster && rasterStyle.colormap" label="Colormap" :value="rasterStyle.colormap" />
            <Fact v-if="isRaster && rasterStyle.rescale" label="Stretch" :value="rasterStyle.rescale" mono />
          </dl>
        </section>

        <section v-if="hasDescription" class="card p-4">
          <h2 class="text-sm font-semibold mb-3">Metadata</h2>
          <dl class="space-y-1.5 text-sm">
            <Fact label="Licence" :value="layer.license" />
            <Fact label="Attribution" :value="layer.attribution" />
            <Fact label="Keywords" :value="layer.keywords" />
          </dl>
          <p v-if="layer.abstract" class="text-xs text-muted-foreground mt-2 whitespace-pre-line">
            {{ layer.abstract }}
          </p>
          <p class="text-[11px] text-muted-foreground/60 mt-2">
            Edited under Sharing — it travels with the layer into STAC, OGC API and the catalog.
          </p>
        </section>

        <section v-if="legend.length" class="card p-4">
          <h2 class="text-sm font-semibold mb-1">Legend</h2>
          <p v-if="colorField" class="text-[11px] text-muted-foreground/70 mb-2">
            Colour by <span class="font-medium text-muted-foreground">{{ colorField }}</span>
          </p>
          <div class="space-y-1 max-h-52 overflow-y-auto pr-1">
            <div v-for="(e, i) in legend" :key="i" class="flex items-center gap-2">
              <span class="w-3.5 h-3.5 rounded-sm flex-shrink-0 ring-1 ring-black/25"
                :style="{ background: e.color }" />
              <span class="text-xs text-muted-foreground truncate">{{ e.label }}</span>
            </div>
          </div>
        </section>
      </div>
    </div>

    <!-- Actions ------------------------------------------------------------------------------ -->
    <section class="card p-4">
      <h2 class="text-sm font-semibold mb-3">Do something with it</h2>
      <div class="flex flex-wrap gap-2">
        <button v-if="auth.canEdit && ready" @click="showStyle = true" class="btn-secondary text-sm">
          Style
        </button>
        <button v-if="ready" @click="showLinks = true" class="btn-secondary text-sm">
          Share links
        </button>
        <button v-if="auth.canEdit && ready" @click="showSharing = true" class="btn-secondary text-sm">
          {{ visibilityLabel === 'Public' ? 'Sharing — public' : 'Sharing' }}
        </button>
        <button v-if="auth.canEdit && ready" @click="showPortal = true" class="btn-primary text-sm">
          Create a portal from this layer
        </button>
        <button v-if="auth.canEdit && isVector && ready" @click="onTile" :disabled="tiling"
          class="btn-secondary text-sm disabled:opacity-60"
          :title="layer.tile_status === 'ready' ? 'Regenerate the PMTiles archive' : 'Tile for fast display'">
          {{ tiling ? 'Tiling…' : (layer.tile_status === 'ready' ? 'Re-tile' : 'Tile for fast display') }}
        </button>
        <button v-if="auth.canEdit && isVector" @click="onReprocess" :disabled="restarting"
          class="btn-secondary text-sm disabled:opacity-60"
          title="Re-convert from the uploaded file — no re-upload needed">
          {{ restarting ? 'Restarting…' : 'Reprocess' }}
        </button>
        <button v-if="auth.canEdit" @click="confirmDelete = true"
          class="btn-secondary text-sm text-red-400 hover:text-red-300 ml-auto">
          Delete
        </button>
      </div>
    </section>

    <!-- Fields -------------------------------------------------------------------------------- -->
    <section v-if="fields.length" class="card p-4">
      <h2 class="text-sm font-semibold mb-3">Fields <span class="text-muted-foreground/60 font-normal">({{ fields.length }})</span></h2>
      <div class="overflow-x-auto">
        <table class="w-full text-sm">
          <thead>
            <tr class="text-left text-xs text-muted-foreground/70 border-b border-border/60">
              <th class="py-1.5 pr-4 font-medium">Name</th>
              <th class="py-1.5 font-medium">Type</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="f in fields" :key="f.name" class="border-b border-border/30 last:border-0">
              <td class="py-1.5 pr-4 font-mono text-xs">{{ f.name }}</td>
              <td class="py-1.5 text-muted-foreground text-xs">{{ f.type }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <StyleModal v-if="showStyle" :layer="layer" :layer-type="kind" @close="onStyleClosed" />
    <SharingModal v-if="showSharing" :layer="layer" :layer-type="kind" @close="showSharing = false" />
    <ShareLinksModal v-if="showLinks" :layer="layer" :layer-type="kind" @close="showLinks = false" />
    <CreatePortalModal v-if="showPortal" :seed-layers="[portalSeed]" @close="showPortal = false" />
    <ConfirmDeleteModal v-if="confirmDelete" :name="layer.name"
      @cancel="confirmDelete = false" @confirm="onDelete" />
  </div>
</template>

<script setup>
import { computed, h, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter, RouterLink } from 'vue-router'
import maplibregl from 'maplibre-gl'

import { useDataStore } from '@/stores/data'
import { useAuthStore } from '@/stores/auth'
import { buildMapStyle, lonLatBbox } from '@/lib/mapStyle'
import { DEFAULT_BASEMAP } from '@/lib/basemaps'
import { legendEntries, representativeColor } from '@/lib/symbology'
import StyleModal from '@/components/data/StyleModal.vue'
import SharingModal from '@/components/data/SharingModal.vue'
import ShareLinksModal from '@/components/data/ShareLinksModal.vue'
import ConfirmDeleteModal from '@/components/data/ConfirmDeleteModal.vue'
import CreatePortalModal from '@/components/portal/CreatePortalModal.vue'

// A label/value row. Rendered rather than templated because it must vanish entirely when the value
// is absent — a "Bands: —" line on a vector layer is noise pretending to be information.
const Fact = (props) => (props.value === undefined || props.value === null || props.value === '')
  ? null
  : h('div', { class: 'flex items-baseline justify-between gap-3' }, [
      h('dt', { class: 'text-xs text-muted-foreground/70 flex-shrink-0', title: props.hint || '' },
        props.label),
      h('dd', { class: props.mono ? 'text-xs font-mono text-right break-all' : 'text-sm text-right' },
        String(props.value)),
    ])
Fact.props = ['label', 'value', 'mono', 'hint']

const route = useRoute()
const router = useRouter()
const dataStore = useDataStore()
const auth = useAuthStore()

const kind = computed(() => (route.params.kind === 'raster' ? 'raster' : 'vector'))
const isRaster = computed(() => kind.value === 'raster')
const isVector = computed(() => kind.value === 'vector')

const layer = computed(() => {
  const id = Number(route.params.id)
  const list = isRaster.value ? dataStore.rasterLayers : dataStore.vectorLayers
  return list.find(l => l.id === id) || null
})
const ready = computed(() => layer.value?.status === 'ready')

// A vector's default style nests the visual part under `style`; a raster's is flat. Same split the
// API stores, so this is where it is unpacked rather than in three places downstream.
const vectorStyle = computed(() => (layer.value?.default_style?.style) || {})
const rasterStyle = computed(() => layer.value?.default_style || {})
const styleForMap = computed(() => (isRaster.value ? rasterStyle.value : vectorStyle.value))

const legend = computed(() => (isVector.value ? legendEntries(vectorStyle.value) : []))
const colorField = computed(() =>
  (vectorStyle.value.color_mode && vectorStyle.value.color_mode !== 'single')
    ? vectorStyle.value.color_field : null)
const swatch = computed(() => (isRaster.value ? '#64748b' : representativeColor(vectorStyle.value)))

const fields = computed(() => (layer.value?.columns || []).filter(c => c?.name))
const hasDescription = computed(() => Boolean(
  layer.value?.abstract || layer.value?.keywords || layer.value?.license || layer.value?.attribution))

/** This layer as a portal's first layer_config — the same shape the editor writes. */
const portalSeed = computed(() => ({
  layer_id: layer.value.id, layer_type: kind.value, visible: true,
  opacity: layer.value.default_style?.opacity ?? 1.0,
  style: styleForMap.value, popup_fields: [],
}))

const kindLabel = computed(() => isRaster.value
  ? 'Raster'
  : (layer.value?.storage_backend === 'geoparquet' ? 'Vector · GeoParquet' : 'Vector · PostGIS'))
const storageLabel = computed(() => isRaster.value
  ? 'Cloud-Optimized GeoTIFF'
  : (layer.value?.storage_backend === 'geoparquet' ? 'GeoParquet in object storage' : 'PostGIS table'))
const tilesLabel = computed(() => {
  if (isRaster.value) return 'TiTiler, rendered on demand'
  if (layer.value?.storage_backend === 'geoparquet') {
    return layer.value?.tile_status === 'ready' ? 'PMTiles archive' : 'Not tiled'
  }
  return 'Martin vector tiles'
})
const visibilityLabel = computed(() =>
  layer.value?.is_public ? 'Public' : (layer.value?.visibility === 'private' ? 'Private' : 'Organization'))
const statusClass = computed(() => ({
  ready: 'badge-success', error: 'badge-error',
}[layer.value?.status] || 'badge-muted'))

const prettySize = computed(() => {
  const b = layer.value?.file_size
  if (!b) return null
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let n = b, i = 0
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i += 1 }
  return `${n < 10 && i > 0 ? n.toFixed(1) : Math.round(n)} ${units[i]}`
})
const prettyExtent = computed(() => {
  const b = lonLatBbox(layer.value?.bbox)
  return b ? b.map(v => Number(v).toFixed(4)).join(', ') : null
})

// -- the map ---------------------------------------------------------------------------------
const mapEl = ref(null)
const mapNote = ref('')
let map = null

/** The layer as a portal would configure it — one entry, drawn by the shared builder. */
function configFor(l) {
  return {
    layer_id: l.id,
    layer_type: kind.value,
    visible: true,
    opacity: l.default_style?.opacity ?? 1.0,
    style: styleForMap.value,
  }
}

function renderMap() {
  const l = layer.value
  if (!l || !mapEl.value) return

  // An untiled GeoParquet layer draws through the portal's data view (deck.gl over a viewport
  // query), which this page does not run. Saying so beats an empty basemap that looks broken.
  mapNote.value = (isVector.value && l.storage_backend === 'geoparquet'
                   && l.tile_status !== 'ready')
    ? 'This GeoParquet layer is not tiled, so there is nothing to draw here yet. Tile it for a preview.'
    : ''

  const { style, bounds } = buildMapStyle({
    configs: [configFor(l)],
    layers: isVector.value ? [l] : [],
    rasters: isRaster.value ? [l] : [],
    sources: [],
    basemap: DEFAULT_BASEMAP,
  })

  if (map) { map.setStyle(style); fit(bounds); return }
  map = new maplibregl.Map({ container: mapEl.value, style, center: [0, 20], zoom: 1.4,
                             attributionControl: { compact: true } })
  map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right')
  map.on('load', () => fit(bounds))
}

function fit(bounds) {
  const b = lonLatBbox(bounds) || lonLatBbox(layer.value?.bbox)
  if (b && map) map.fitBounds([[b[0], b[1]], [b[2], b[3]]], { padding: 36, duration: 0, maxZoom: 16 })
}

onMounted(async () => {
  if (!dataStore.vectorLayers.length && !dataStore.rasterLayers.length) await dataStore.refresh()
  await nextTick()
  renderMap()
})
onBeforeUnmount(() => { if (map) { map.remove(); map = null } })
// Re-render when the layer arrives, when its style changes, or when the route moves to another one.
watch([layer, styleForMap], () => renderMap(), { deep: true })

// -- actions ---------------------------------------------------------------------------------
const showStyle = ref(false)
const showSharing = ref(false)
const showLinks = ref(false)
const showPortal = ref(false)
const confirmDelete = ref(false)
const tiling = ref(false)
const restarting = ref(false)

const renaming = ref(false)
const draftName = ref('')
const nameInput = ref(null)

async function startRename() {
  draftName.value = layer.value.name
  renaming.value = true
  await nextTick()
  nameInput.value?.focus()
}
async function commitRename() {
  if (!renaming.value) return
  renaming.value = false
  const name = draftName.value.trim()
  if (!name || name === layer.value.name) return
  if (isRaster.value) await dataStore.renameRaster(layer.value.id, name)
  else await dataStore.renameVector(layer.value.id, name)
}

function onStyleClosed() {
  showStyle.value = false
  renderMap()            // the map is the preview of what was just saved
}

async function onTile() {
  tiling.value = true
  try { await dataStore.tileVector(layer.value.id) } finally { tiling.value = false }
}
async function onReprocess() {
  restarting.value = true
  try { await dataStore.reprocessVector(layer.value.id) } finally { restarting.value = false }
}
async function onDelete() {
  confirmDelete.value = false
  if (isRaster.value) await dataStore.removeRaster(layer.value.id)
  else await dataStore.removeVector(layer.value.id)
  router.push('/data')
}
</script>
