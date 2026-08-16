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
  <!-- ONE root that always renders. The map is created on mount by `useMaplibre`, so its
       container has to be in the DOM by then — gating the whole page on the layer meant that on a
       hard refresh (store still empty) MapLibre threw "Container 'gd-layer-map' not found" and no
       map ever appeared. The layer-dependent parts wait; the container does not. -->
  <div class="space-y-5 p-4 sm:p-6 max-w-[1600px] mx-auto">
    <!-- Header ------------------------------------------------------------------------------ -->
    <div v-if="layer" class="flex items-start justify-between gap-4 flex-wrap">
      <div class="min-w-0">
        <RouterLink to="/data"
          class="text-xs text-muted-foreground/70 hover:text-foreground inline-flex items-center gap-1">
          <span aria-hidden="true">←</span> My Data
        </RouterLink>
        <div class="flex items-center gap-2 mt-1">
          <LegendSwatch :geom="swatchGeom" :color="swatch" :marker="markerShape"
            :dash="lineDash" :size="18" />
          <h1 v-if="!renaming" class="text-2xl font-semibold truncate">{{ layer.name }}</h1>
          <input v-else ref="nameInput" v-model="draftName" @keyup.enter="commitRename"
            @keyup.esc="renaming = false" @blur="commitRename"
            class="input text-2xl font-semibold py-0.5" />
          <button v-if="auth.canEdit && !renaming && !isExternal" @click="startRename"
            class="text-muted-foreground/60 hover:text-foreground text-sm" title="Rename layer">✎</button>
        </div>
        <p class="text-xs text-muted-foreground/70 mt-1">
          {{ kindLabel }}<span v-if="layer.created_by"> · added by {{ layer.created_by }}</span>
          <span v-if="layer.created_at"> · {{ new Date(layer.created_at).toLocaleDateString() }}</span>
        </p>
      </div>

      <!-- Status and actions on one line, beside the title and ABOVE the map. In a card of their
           own they had a strip of empty space to themselves; here they read as a toolbar for the
           thing named next to them, which is what they are. -->
      <div class="flex items-center gap-2 flex-wrap justify-end">
        <span class="badge" :class="statusClass">{{ layer.status }}</span>
        <span v-if="layer.tile_status === 'ready'" class="badge badge-muted"
          title="Tiled to PMTiles — renders as fast static vector tiles">Tiled</span>
        <span class="badge badge-muted">{{ visibilityLabel }}</span>
        <div class="w-px h-6 bg-border mx-1" aria-hidden="true" />
      <button v-if="auth.canEdit && ready && !isExternal" @click="showStyle = true"
        class="gd-act" title="Default style — colour, size, classification">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
          stroke-linecap="round" stroke-linejoin="round" class="w-4 h-4">
          <circle cx="13.5" cy="6.5" r="2.5" /><circle cx="18" cy="13" r="2.5" />
          <circle cx="6.5" cy="10.5" r="2.5" /><circle cx="10" cy="18" r="2.5" />
          <path d="M12 2a10 10 0 1 0 0 20c1.1 0 2-.9 2-2 0-1.4-1-1.9-1-3 0-.6.4-1 1-1h2a5 5 0 0 0 5-5c0-5-4.5-9-9-9z" />
        </svg>
      </button>

      <button v-if="ready && !isExternal" @click="showLinks = true"
        class="gd-act" title="Share links — use this layer in QGIS, GeoLibre, MapLibre…">
        <LinkIcon class="w-4 h-4" />
      </button>

      <button v-if="auth.canEdit && ready && !isExternal" @click="showSharing = true"
        class="gd-act" :title="`Visibility and metadata — currently ${visibilityLabel.toLowerCase()}`">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
          stroke-linecap="round" stroke-linejoin="round" class="w-4 h-4">
          <circle cx="12" cy="12" r="10" /><line x1="2" y1="12" x2="22" y2="12" />
          <path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z" />
        </svg>
      </button>

      <button v-if="auth.canEdit && canTile && ready" @click="onTile" :disabled="tiling"
        class="gd-act disabled:opacity-40"
        :title="layer.tile_status === 'ready' ? 'Re-tile for fast display (regenerate PMTiles)'
                                              : 'Tile for fast seamless display (PMTiles)'">
        <LayersIcon class="w-4 h-4" :class="tiling ? 'animate-pulse' : ''" />
      </button>

      <button v-if="auth.canEdit && isVector && !isExternal" @click="onReprocess"
        :disabled="restarting" class="gd-act disabled:opacity-40"
        title="Restart processing — re-convert from the uploaded file, no re-upload needed">
        <RefreshIcon class="w-4 h-4" :class="restarting ? 'animate-spin' : ''" />
      </button>

      <div class="w-px h-6 bg-border mx-1" aria-hidden="true" />

      <!-- The one action that creates something stays a labelled button: it is the reason most
           people are on this page, and an icon would hide it. -->
      <button v-if="auth.canEdit && ready" @click="showPortal = true" class="btn-primary text-sm">
        Create a portal from this layer
      </button>

      <button v-if="auth.canEdit" @click="confirmDelete = true"
        class="gd-act hover:text-red-400 ml-auto" title="Delete layer">
        <TrashIcon class="w-4 h-4" />
      </button>
      </div>
    </div>

    <!-- The map, full width. A fixed side column left a tall void next to it whenever a layer had
         no legend and no metadata, which is most of them — the facts read better as a row of cards
         under the map, and they reflow instead of stacking into one narrow strip. -->
    <div class="card overflow-hidden">
      <div class="relative">
        <div id="gd-layer-map" class="w-full h-[52vh] min-h-[340px] bg-muted/40" />
        <!-- The legend, in the map where a portal keeps it — above the zoom controls, and closed
             until asked for, because on most layers it is one swatch. -->
        <div v-if="layer" class="absolute top-2.5 left-2.5 z-10 max-w-[15rem]">
          <button @click="legendOpen = !legendOpen"
            class="w-full flex items-center gap-1.5 px-2 py-1.5 rounded-md text-xs font-medium
                   bg-card/95 border border-border shadow backdrop-blur hover:bg-muted"
            :title="legendOpen ? 'Hide the legend' : 'Show the legend'">
            <LegendSwatch :geom="swatchGeom" :color="swatch" :marker="markerShape"
              :dash="lineDash" :size="16" />
            <span class="truncate">Legend</span>
            <span class="ml-auto text-muted-foreground/70" aria-hidden="true">
              {{ legendOpen ? '▾' : '▸' }}
            </span>
          </button>
          <div v-if="legendOpen"
            class="mt-1 p-2 rounded-md bg-card/95 border border-border shadow backdrop-blur
                   max-h-[40vh] overflow-y-auto">
            <p class="text-[11px] text-muted-foreground/80 truncate mb-1">{{ layer.name }}</p>
            <p v-if="colorField" class="text-[11px] text-muted-foreground/70 mb-1">
              Colour by <span class="font-medium">{{ colorField }}</span>
            </p>
            <div v-if="legend.length" class="space-y-1">
              <div v-for="(e, i) in legend" :key="i" class="flex items-center gap-2">
                <LegendSwatch :geom="swatchGeom" :color="e.color" :marker="markerShape"
                  :dash="lineDash" :size="16" />
                <span class="text-[11px] text-muted-foreground truncate">{{ e.label }}</span>
              </div>
            </div>
            <!-- A raster ramp is continuous: a strip, not swatches. -->
            <div v-else-if="isRaster && rampCss" class="space-y-1">
              <div class="h-3 rounded" :style="{ background: rampCss }" />
              <div class="flex justify-between text-[10px] text-muted-foreground/80 tabular-nums">
                <span>{{ rampRange[0] }}</span><span>{{ rampRange[1] }}</span>
              </div>
            </div>
            <div v-else class="flex items-center gap-2">
              <LegendSwatch :geom="swatchGeom" :color="swatch" :marker="markerShape"
                :dash="lineDash" :size="16" />
              <span class="text-[11px] text-muted-foreground">Single symbol</span>
            </div>
            <!-- Size varies independently of colour, so a legend showing only classes
                 describes half the map. Two ends, because the size expression interpolates. -->
            <div v-if="sizeLegend" class="mt-2 pt-2 border-t border-border/50">
              <p class="text-[11px] text-muted-foreground/70 mb-1">
                Size by <span class="font-medium">{{ sizeLegend.field }}</span>
              </p>
              <div class="flex items-end gap-4">
                <div v-for="(end, i) in sizeLegend.ends" :key="i"
                  class="flex flex-col items-center gap-1">
                  <span class="flex items-end justify-center" style="min-height:26px">
                    <span v-if="swatchGeom === 'line'" :style="{
                      display: 'block', width: '22px',
                      height: Math.max(2, Math.min(22, end.px)) + 'px',
                      borderRadius: (Math.max(2, Math.min(22, end.px)) / 2) + 'px',
                      background: swatch }" />
                    <span v-else :style="{
                      display: 'block',
                      width: (Math.max(2, Math.min(13, end.px)) * 2) + 'px',
                      height: (Math.max(2, Math.min(13, end.px)) * 2) + 'px',
                      borderRadius: '50%', background: swatch }" />
                  </span>
                  <span class="text-[10px] text-muted-foreground/80 tabular-nums">{{ end.value }}</span>
                </div>
              </div>
            </div>
            <p class="text-[10px] text-muted-foreground/60 mt-1.5 pt-1.5 border-t border-border/50">
              Dashed outline = the layer's extent
            </p>
          </div>
        </div>
      </div>
      <p v-if="mapNote" class="text-xs text-amber-300/90 px-4 py-2 border-t border-border/60">
        {{ mapNote }}
      </p>
    </div>

    <!-- Not found / still loading. Below the map, which stays mounted. -->
    <div v-if="!layer" class="card p-8 text-center text-muted-foreground">
      <p v-if="dataStore.loading" class="text-sm">Loading…</p>
      <template v-else>
        <p class="text-sm">That layer is not here any more.</p>
        <RouterLink to="/data" class="btn-secondary mt-4 inline-flex">Back to My Data</RouterLink>
      </template>
    </div>

    <!-- Facts. `auto-fit` rather than a fixed column count: with two cards in a four-column grid
         two thirds of the row was empty. They now share the width they have. -->
    <div v-if="layer"
      class="grid gap-5"
      style="grid-template-columns: repeat(auto-fit, minmax(280px, 1fr))">
      <section class="card p-4">
        <h2 class="text-sm font-semibold mb-3">What it is</h2>
        <dl class="space-y-1.5 text-sm">
          <Fact label="Geometry" :value="layer.geometry_type" />
          <Fact label="Features" :value="layer.feature_count?.toLocaleString()" />
          <Fact label="Bands" :value="layer.band_count" />
          <Fact label="CRS" :value="layer.crs" mono />
          <Fact label="Size" :value="prettySize" />
        </dl>
        </section>

        <!-- Its own card: the extent is a picture, and pinning it under the facts left one column
             tall and the next half empty. Three boxes of similar height line up instead. -->
        <section v-if="extent" class="card p-4">
          <h2 class="text-sm font-semibold mb-3">
            Where it is <span class="text-muted-foreground/60 font-normal text-xs">(EPSG:4326)</span>
          </h2>
          <div class="flex flex-col items-center gap-1 text-[11px] font-mono tabular-nums py-1">
            <span class="text-muted-foreground">{{ extent.north }}</span>
            <div class="flex items-center gap-2 w-full">
              <span class="text-muted-foreground text-right flex-1">{{ extent.west }}</span>
              <span class="border border-primary/50 bg-primary/10 rounded-sm flex-shrink-0"
                :style="{ width: extent.boxW + 'px', height: extent.boxH + 'px' }" />
              <span class="text-muted-foreground flex-1">{{ extent.east }}</span>
            </div>
            <span class="text-muted-foreground">{{ extent.south }}</span>
          </div>
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

    </div>

    <!-- Fields -------------------------------------------------------------------------------- -->
    <section v-if="fields.length" class="card p-4">
      <button class="flex items-center gap-2 w-full text-left" @click="fieldsOpen = !fieldsOpen">
        <span class="text-xs text-muted-foreground/60">{{ fieldsOpen ? '▾' : '▸' }}</span>
        <h2 class="text-sm font-semibold">
          Fields <span class="text-muted-foreground/60 font-normal">({{ fields.length }})</span>
        </h2>
      </button>
      <!-- Two or three columns, not one row per field: a table of 8 fields took more of the page
           than the map, and a layer with 40 would have been unusable. -->
      <div v-if="fieldsOpen"
        class="mt-3 grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-x-6 gap-y-1 max-h-64 overflow-y-auto pr-1">
        <div v-for="f in fields" :key="f.name"
          class="flex items-baseline justify-between gap-3 border-b border-border/25 py-1">
          <span class="font-mono text-xs truncate" :title="f.name">{{ f.name }}</span>
          <span class="text-[11px] text-muted-foreground/70 flex-shrink-0">{{ f.type }}</span>
        </div>
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
import { computed, h, nextTick, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter, RouterLink } from 'vue-router'

import { useDataStore } from '@/stores/data'
import { useAuthStore } from '@/stores/auth'
import { useMaplibre } from '@/composables/useMaplibre'
import { buildMapStyle, lonLatBbox } from '@/lib/mapStyle'
import { registerMarkerImages, setMarkerSpecs } from '@/lib/markerImage'
import { DEFAULT_BASEMAP } from '@/lib/basemaps'
import { legendEntries, representativeColor } from '@/lib/symbology'
import StyleModal from '@/components/data/StyleModal.vue'
import SharingModal from '@/components/data/SharingModal.vue'
import ShareLinksModal from '@/components/data/ShareLinksModal.vue'
import ConfirmDeleteModal from '@/components/data/ConfirmDeleteModal.vue'
import CreatePortalModal from '@/components/portal/CreatePortalModal.vue'
import LegendSwatch from '@/components/LegendSwatch.vue'
import { LinkIcon, TrashIcon, RefreshIcon, LayersIcon } from '@/views/icons'

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

const kind = computed(() => ['raster', 'external'].includes(route.params.kind)
  ? route.params.kind : 'vector')
const isExternal = computed(() => kind.value === 'external')
// An external RASTER source draws through the raster path; a vector one through the vector path.
// `kind` is what the layer_config calls it, which is 'external' for both.
const isRaster = computed(() => kind.value === 'raster'
  || (isExternal.value && layer.value?.kind === 'raster'))
const isVector = computed(() => !isRaster.value && !isExternal.value)

const layer = computed(() => {
  // The URL carries the UID: integer ids are per-kind sequences that renumber on a restore, so a
  // bookmarked /data/vector/12 could come back pointing at a different layer. An integer is still
  // accepted, for links made before this and for anyone typing one by hand.
  const ref_ = String(route.params.id || '')
  const list = kind.value === 'external' ? dataStore.externalSources
    : (kind.value === 'raster' ? dataStore.rasterLayers : dataStore.vectorLayers)
  return list.find(l => l.uid === ref_) || list.find(l => String(l.id) === ref_) || null
})
const ready = computed(() => isExternal.value || layer.value?.status === 'ready')

// A vector's default style nests the visual part under `style`; a raster's is flat. Same split the
// API stores, so this is where it is unpacked rather than in three places downstream.
const vectorStyle = computed(() => (layer.value?.default_style?.style) || {})
const rasterStyle = computed(() => layer.value?.default_style || {})
const styleForMap = computed(() => {
  if (isExternal.value) return {}      // the remote service decides how it draws
  return isRaster.value ? rasterStyle.value : vectorStyle.value
})

const legend = computed(() => (isVector.value ? legendEntries(vectorStyle.value) : []))
const colorField = computed(() =>
  (vectorStyle.value.color_mode && vectorStyle.value.color_mode !== 'single')
    ? vectorStyle.value.color_field : null)
// A CSS gradient standing in for the raster's colormap. Approximate on purpose — it is a key for
// reading the map, not a second renderer — and it follows the same reverse flag the tiles use.
const RAMP_STOPS = {
  viridis: ['#440154', '#3b528b', '#21918c', '#5ec962', '#fde725'],
  plasma: ['#0d0887', '#7e03a8', '#cc4778', '#f89540', '#f0f921'],
  inferno: ['#000004', '#57106e', '#bc3754', '#f98e09', '#fcffa4'],
  magma: ['#000004', '#51127c', '#b73779', '#fc8961', '#fcfdbf'],
  cividis: ['#00224e', '#35456c', '#666970', '#9c8f5f', '#fee838'],
  gray: ['#000000', '#ffffff'],
  terrain: ['#333399', '#00b0b0', '#4ddb4d', '#f2f28c', '#8c5a3b'],
  rdylgn: ['#a50026', '#f46d43', '#ffffbf', '#66bd63', '#006837'],
  rdbu: ['#67001f', '#f7f7f7', '#053061'],
  spectral: ['#9e0142', '#fdae61', '#ffffbf', '#66c2a5', '#5e4fa2'],
}
const rampCss = computed(() => {
  if (!isRaster.value) return null
  const name = (rasterStyle.value.colormap || '').replace(/_r$/, '').toLowerCase()
  let stops = RAMP_STOPS[name]
  if (!stops) return null
  if (rasterStyle.value.colormap_reverse) stops = [...stops].reverse()
  return `linear-gradient(to right, ${stops.join(', ')})`
})
const rampRange = computed(() => {
  const parts = String(rasterStyle.value.rescale || '').split(',')
  return parts.length === 2 ? parts : ['min', 'max']
})

// What KIND of thing this layer draws, for the swatch shape.
// Only a GeoParquet layer has PMTiles to build; PostGIS is already tiled by Martin.
const canTile = computed(() =>
  isVector.value && !isExternal.value && layer.value?.storage_backend === 'geoparquet')

const swatchGeom = computed(() => {
  if (isRaster.value) return 'raster'
  const g = (layer.value?.geometry_type || '').toLowerCase()
  if (g.includes('line')) return 'line'
  if (g.includes('polygon')) return 'polygon'
  return 'point'
})
const markerShape = computed(() => vectorStyle.value.marker || 'circle')
const lineDash = computed(() => vectorStyle.value.lineType || 'solid')

/** The two ends of a proportional size scale — mirrors services/symbology.size_legend. */
const sizeLegend = computed(() => {
  const st = vectorStyle.value
  if ((st.size_mode || 'fixed') !== 'proportional') return null
  const stops = (st.size_stops || []).filter(x => Array.isArray(x) && x.length === 2)
  if (!st.size_field || stops.length < 2) return null
  const ordered = [...stops].sort((a, b) => a[0] - b[0])
  const lo = ordered[0], hi = ordered[ordered.length - 1]
  return { field: st.size_field,
           ends: [{ value: lo[0], px: Number(lo[1]) || 2 },
                  { value: hi[0], px: Number(hi[1]) || 8 }] }
})

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

const kindLabel = computed(() => isExternal.value
  ? `External ${layer.value?.kind || ''} · ${(layer.value?.source_type || '').toUpperCase()}`
  : isRaster.value
  ? 'Raster'
  : (layer.value?.storage_backend === 'geoparquet' ? 'Vector · GeoParquet' : 'Vector · PostGIS'))
const storageLabel = computed(() => isExternal.value
  ? 'Served by another organisation'
  : isRaster.value
  ? 'Cloud-Optimized GeoTIFF'
  : (layer.value?.storage_backend === 'geoparquet' ? 'GeoParquet in object storage' : 'PostGIS table'))
const tilesLabel = computed(() => {
  if (isExternal.value) return 'From the remote service'
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
/** The extent as a little box: the four edges labelled, sized to the layer's own aspect ratio. */
const extent = computed(() => {
  const b = lonLatBbox(layer.value?.bbox)
  if (!b) return null
  const fmt = v => Number(v).toFixed(4)
  // A shape, not a scale drawing — clamped so a very thin layer is still a visible rectangle.
  const w = Math.abs(b[2] - b[0]) || 1
  const h = Math.abs(b[3] - b[1]) || 1
  const ratio = Math.min(Math.max(w / h, 0.35), 2.8)
  const boxH = 46
  return { west: fmt(b[0]), south: fmt(b[1]), east: fmt(b[2]), north: fmt(b[3]),
           boxH, boxW: Math.round(boxH * ratio) }
})

// -- the map ---------------------------------------------------------------------------------
// Through the shared composable, not a hand-rolled maplibregl.Map: it is what registers the
// `pmtiles://` protocol (a tiled GeoParquet layer fails with 'URL scheme "pmtiles" is not
// supported' without it), and it owns the map's lifecycle and the globe/zoom controls.
const { map, loaded, applyStyle, fitToBbox } = useMaplibre('gd-layer-map',
  { version: 8, sources: {}, layers: [] })
const mapNote = ref('')

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

/**
 * The layer's extent, as a dashed outline.
 *
 * Worth drawing even when the layer itself is on screen, and essential when it is not: a
 * GeoParquet layer that has no preview, or a small raster on a world view, is otherwise an empty
 * map with no clue whether the data is missing or merely elsewhere. The outline says "it is there,
 * and this is where".
 */
function addExtentOutline(style, bbox) {
  const b = lonLatBbox(bbox)
  if (!b) return
  style.sources['gd-extent'] = {
    type: 'geojson',
    data: {
      type: 'Feature',
      properties: {},
      geometry: {
        type: 'Polygon',
        coordinates: [[[b[0], b[1]], [b[2], b[1]], [b[2], b[3]], [b[0], b[3]], [b[0], b[1]]]],
      },
    },
  }
  // Appended last so it sits ON TOP of the layer rather than under it.
  style.layers.push({
    id: 'gd-extent-line', type: 'line', source: 'gd-extent',
    paint: { 'line-color': '#22c55e', 'line-width': 1.5, 'line-dasharray': [3, 2],
             'line-opacity': 0.9 },
  })
}

function renderMap() {
  const l = layer.value
  if (!l || !map.value || !loaded.value) return

  // An untiled GeoParquet layer draws through the portal's data view (deck.gl over a viewport
  // query), which this page does not run. Saying so beats an empty basemap that looks broken.
  mapNote.value = (isVector.value && l.storage_backend === 'geoparquet'
                   && l.tile_status !== 'ready')
    ? 'This GeoParquet layer is not tiled, so there is nothing to draw here yet. Tile it for a preview.'
    : ''

  const { style, bounds, markerSpecs } = buildMapStyle({
    configs: [configFor(l)],
    layers: (isVector.value && !isExternal.value) ? [l] : [],
    rasters: (isRaster.value && !isExternal.value) ? [l] : [],
    sources: isExternal.value ? [l] : [],
    basemap: DEFAULT_BASEMAP,
  })
  addExtentOutline(style, l.bbox)
  // Points are symbol layers whose icons are generated on demand — without this they draw nothing.
  registerMarkerImages(map.value, markerSpecs)
  setMarkerSpecs(map.value, markerSpecs)
  applyStyle(style)
  fitToBbox(lonLatBbox(bounds) || lonLatBbox(l.bbox))
}

onMounted(async () => {
  if (!dataStore.vectorLayers.length && !dataStore.rasterLayers.length) await dataStore.refresh()
})
// The map is created on mount by the composable, so wait for it AND for the layer to arrive.
watch([loaded, layer, styleForMap], () => renderMap(), { deep: true, immediate: true })

// -- actions ---------------------------------------------------------------------------------
const showStyle = ref(false)
const showSharing = ref(false)
const showLinks = ref(false)
const showPortal = ref(false)
const confirmDelete = ref(false)
const fieldsOpen = ref(true)
const legendOpen = ref(false)
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

async function onStyleClosed() {
  showStyle.value = false
  // Pull the saved style back before redrawing. Without this the map rebuilt from the layer the
  // store still held, so a just-saved raster could come back looking unchanged — or briefly not
  // at all — until the page was reloaded by hand.
  await dataStore.refresh()
  renderMap()
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
  if (isExternal.value) await dataStore.removeExternal(layer.value.id)
  else if (isRaster.value) await dataStore.removeRaster(layer.value.id)
  else await dataStore.removeVector(layer.value.id)
  router.push('/data')
}
</script>

<style scoped>
/* One class for every icon action, so they are the same size and hit-area as the list rows'. */
.gd-act {
  @apply p-2 rounded-lg border border-border bg-card text-muted-foreground
         hover:text-foreground hover:bg-muted transition-colors;
}
</style>
