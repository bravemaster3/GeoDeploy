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
    <div v-if="layer" class="min-w-0">
      <div>
        <RouterLink v-if="auth.isAuthenticated" to="/data"
          class="text-xs text-muted-foreground/70 hover:text-foreground inline-flex items-center gap-1">
          <span aria-hidden="true">←</span> My Data
        </RouterLink>
        <!-- Signed out, "My Data" is not somewhere this visitor can go. -->
        <RouterLink v-else to="/login"
          class="text-xs text-muted-foreground/70 hover:text-foreground inline-flex items-center gap-1">
          Sign in <span aria-hidden="true">→</span>
        </RouterLink>
        <div class="flex items-center gap-2 mt-1">
          <LegendSwatch :geom="swatchGeom" :color="swatch" :marker="markerShape"
            :dash="lineDash" :size="18"
            :outline-color="outlineColor" :outline-width="outlineWidth" />
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

    <!-- The toolbar is its own row, directly above the map it acts on — the title belongs with
         the layer's identity, and the actions belong with the thing they change. Sharing one row
         put the buttons level with the back link, which reads as page navigation. -->
    <div v-if="layer" class="flex items-center gap-2 flex-wrap justify-end -mb-2">
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

      <!-- The SAME grid icon and the same wording My Data uses for this action, from one shared
           definition — it is one action and it should not look or read like two. -->
      <button v-if="auth.canEdit && canTile && ready" @click="onTile"
        :disabled="tiling || isTiling(layer)" class="gd-act disabled:opacity-40"
        :title="tileTitle(layer)">
        <TilesIcon class="w-4 h-4" :class="(tiling || isTiling(layer)) ? 'animate-pulse' : ''" />
      </button>

      <!-- Low-zoom point clustering. Sits beside the Tile action because it IS that action: the
           clustering is baked into the archive by tippecanoe, so changing it without re-tiling
           would change nothing on the map. -->
      <label v-if="auth.canEdit && canTile && ready && isPointLayer"
        class="flex items-center gap-1.5 text-[11px] text-muted-foreground cursor-pointer select-none"
        :title="clusterPoints
          ? 'Nearby points will be merged into counted circles at low zoom, on the next tiling.'
          : 'Points thin out at low zoom. Tick to merge them into counted circles instead.'">
        <input type="checkbox" v-model="clusterPoints" class="accent-primary" />
        Cluster
      </label>

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
              :dash="lineDash" :size="16"
              :outline-color="outlineColor" :outline-width="outlineWidth" />
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
                <!-- Each entry's OWN symbol where it has one. A classified layer varies only by
                     colour, so every row shared the layer's dash and marker — but a rule-based
                     layer varies by everything at once, and drawing its rules with one base symbol
                     reports a dashed rule, a hatched rule and a star rule as identical. -->
                <LegendSwatch :geom="swatchGeom" :color="e.color"
                  :marker="e.shape || markerShape" :dash="e.dash || lineDash" :size="16"
                  :image="e.marker_image" :pattern="e.fill_pattern" :ramp="e.ramp"
                  :outline-color="e.outline_color || outlineColor"
                  :outline-width="e.outline_width ?? outlineWidth" />
                <span class="text-[11px] text-muted-foreground truncate">{{ e.label }}</span>
              </div>
            </div>
            <!-- A raster classified by VALUE — land cover, soil types — is a list of swatches.
                 A gradient between class 3 and class 4 would claim a meaning that is not there. -->
            <div v-else-if="isRaster && rasterClasses.length" class="space-y-1">
              <div v-for="c in rasterClasses" :key="c.value" class="flex items-center gap-2">
                <span class="w-4 h-3 rounded-sm border border-border/60 flex-shrink-0"
                  :style="{ background: c.color }" />
                <span class="text-[11px] text-muted-foreground truncate">{{ c.label }}</span>
              </div>
            </div>
            <!-- A raster ramp is continuous: a strip, not swatches. -->
            <div v-else-if="isRaster && rampCss" class="space-y-1">
              <div class="h-3 rounded" :style="{ background: rampCss }" />
              <div class="flex justify-between text-[10px] text-muted-foreground/80 tabular-nums">
                <span>{{ rampRange[0] }}</span><span>{{ rampRange[1] }}</span>
              </div>
              <!-- The interval is the whole point of a contour map, and it is nowhere else on
                   the page: the strip says what the colours mean, this says what the lines do. -->
              <div v-if="contourInterval != null"
                class="flex justify-between text-[10px] text-muted-foreground/80 tabular-nums">
                <span>contour lines</span><span>every {{ contourInterval }}</span>
              </div>
            </div>
            <div v-else class="flex items-center gap-2">
              <LegendSwatch :geom="swatchGeom" :color="swatch" :marker="markerShape"
                :dash="lineDash" :size="16"
              :outline-color="outlineColor" :outline-width="outlineWidth" />
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
      <p v-if="publicError" class="text-sm">{{ publicError }}</p>
      <p v-else-if="dataStore.loading" class="text-sm">Loading…</p>
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
        <!-- A description is free text somebody pasted, and it very often holds a source URL —
             one unbroken run of characters with nowhere to wrap, which pushed the text out through
             the side of the card. `anywhere` breaks such a run only when it has to. -->
        <p v-if="layer.abstract"
           class="text-xs text-muted-foreground mt-2 whitespace-pre-line [overflow-wrap:anywhere]">
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
import { getPublicLayer } from '@/api'
import StyleModal from '@/components/data/StyleModal.vue'
import SharingModal from '@/components/data/SharingModal.vue'
import ShareLinksModal from '@/components/data/ShareLinksModal.vue'
import ConfirmDeleteModal from '@/components/data/ConfirmDeleteModal.vue'
import CreatePortalModal from '@/components/portal/CreatePortalModal.vue'
import LegendSwatch from '@/components/LegendSwatch.vue'
import { LinkIcon, TrashIcon, RefreshIcon, TilesIcon } from '@/views/icons'
import { tileTitle, isTiling, confirmTiling } from '@/lib/tiling'

// A label/value row. Rendered rather than templated because it must vanish entirely when the value
// is absent — a "Bands: —" line on a vector layer is noise pretending to be information.
const Fact = (props) => (props.value === undefined || props.value === null || props.value === '')
  ? null
  // `min-w-0` + `overflow-wrap: anywhere` on the value, because a flex child will not shrink below
  // its content's intrinsic width — so an attribution holding a long URL pushed straight out
  // through the side of the card instead of wrapping. `anywhere` rather than `break-all`: it breaks
  // a run of characters only when there is no other way, so ordinary prose still wraps at spaces.
  : h('div', { class: 'flex items-baseline justify-between gap-3' }, [
      h('dt', { class: 'text-xs text-muted-foreground/70 flex-shrink-0', title: props.hint || '' },
        props.label),
      h('dd', { class: 'min-w-0 [overflow-wrap:anywhere] ' + (props.mono
        ? 'text-xs font-mono text-right break-all' : 'text-sm text-right') },
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

// Set only on the PUBLIC route, when the layer could not come from the store — see `onMounted`.
const publicLayer = ref(null)
const publicError = ref('')
// `/layers/...` is the shareable public address; `/data/...` is the same page inside the app.
const isPublicRoute = computed(() => route.path.startsWith('/layers/'))

const layer = computed(() => {
  // The URL carries the UID: integer ids are per-kind sequences that renumber on a restore, so a
  // bookmarked /data/vector/12 could come back pointing at a different layer. An integer is still
  // accepted, for links made before this and for anyone typing one by hand.
  const ref_ = String(route.params.id || '')
  const list = kind.value === 'external' ? dataStore.externalSources
    : (kind.value === 'raster' ? dataStore.rasterLayers : dataStore.vectorLayers)
  return list.find(l => l.uid === ref_) || list.find(l => String(l.id) === ref_)
    || publicLayer.value || null
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
// WHICH ramp is on the map, which is not always the layer's colormap. A hillshade IS a grey relief
// image and TiTiler's contours colours the terrain with its own built-in ramp and ignores the
// colormap entirely — so reading `colormap` alone left both with NO gradient at all, and the legend
// fell through to "Single symbol" for a raster that is plainly not one. Same three-way rule as
// `templates/shared/portal.js::rasterLegendHtml`, because the two legends describe the same tiles.
const rasterRamp = computed(() => {
  const style = rasterStyle.value
  const algorithm = (style.algorithm || '').toLowerCase()
  if (algorithm === 'hillshade') return 'gray'
  if (algorithm === 'contours') return 'terrain'
  return (style.colormap || '').replace(/_r$/, '').toLowerCase()
})
// A raster classified by VALUE is a list of swatches, not a strip: interpolating between class 3
// and class 4 means nothing, and a gradient would claim it does.
const rasterClasses = computed(() => {
  if (!isRaster.value) return []
  return (rasterStyle.value.color_classes || [])
    .filter(c => c && c.value != null && c.color)
    .map(c => ({ value: c.value, color: String(c.color).slice(0, 7), label: c.label ?? c.value }))
})
const contourInterval = computed(() =>
  (rasterStyle.value.algorithm || '').toLowerCase() === 'contours'
    ? (rasterStyle.value.increment ?? 35) : null)
const rampCss = computed(() => {
  if (!isRaster.value || rasterClasses.value.length) return null
  let stops = RAMP_STOPS[rasterRamp.value]
  if (!stops) return null
  // A named ramp is reversible; the algorithms' own ramps are not the layer's to flip.
  if (rasterStyle.value.colormap_reverse && !rasterStyle.value.algorithm) stops = [...stops].reverse()
  return `linear-gradient(to right, ${stops.join(', ')})`
})
const rampRange = computed(() => {
  const parts = String(rasterStyle.value.rescale || '').split(',')
  return parts.length === 2 ? parts : ['min', 'max']
})

// What KIND of thing this layer draws, for the swatch shape.
// Only a GeoParquet layer has PMTiles to build; PostGIS is already tiled by Martin.
//: Positively a point layer. Clustering is offered for points only — a clustered polygon layer is a
//: centroid heatmap in disguise, and the choropleth the portal can already draw says more. Matches
//: `portal_generator._is_point`: an unknown or mixed type answers NO.
const isPointLayer = computed(() => {
  const g = (layer.value?.geometry_type || '').toLowerCase()
  return g.includes('point') && !g.includes('polygon') && !g.includes('line')
})
//: Seeded from what the ARCHIVE was actually built with, so the box shows the current state of the
//: tiles rather than an assumption. Only follows the layer's own value — once the author has ticked
//: it, their pending choice must survive the poll that refreshes the layer while tiling runs.
const clusterPoints = ref(false)
let clusterSeeded = false
watch(layer, (l) => {
  if (l && !clusterSeeded) { clusterPoints.value = !!l.cluster_points; clusterSeeded = true }
}, { immediate: true })

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
// A polygon's outline, so the swatch shows the border the map draws rather than the fill colour at
// a fixed width. `outline_width` is PIXELS on a polygon and a RATIO of the radius on a point, so it
// is only read for the geometry it means something on.
const outlineColor = computed(() => {
  const c = vectorStyle.value.outline_color
  if (c === 'none') return 'transparent'
  return c || (swatchGeom.value === 'polygon' ? '#1d4ed8' : '')
})
const outlineWidth = computed(() =>
  swatchGeom.value === 'polygon' ? (vectorStyle.value.outline_width ?? 1) : null)
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
const { map, loaded, applyStyle, fitToBbox, addFullscreen, addZoomToExtent, addTilt } =
  useMaplibre('gd-layer-map', { version: 8, sources: {}, layers: [] })
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
  tiltIfThreeD(style)
}

onMounted(async () => {
  // The router skips its auth check on a `public: true` route, so nobody has asked who this is yet.
  // Ask now, and ignore a failure: a signed-in visitor following a public link should still get the
  // full page with its edit actions, and a signed-out one is exactly who this route is for.
  if (isPublicRoute.value && !auth.user) {
    try { await auth.fetchMe() } catch { /* signed out, which is fine here */ }
  }
  // SIGNED IN: the store is the source, and it carries everything the actions need.
  if (auth.isAuthenticated && !isPublicRoute.value) {
    if (!dataStore.vectorLayers.length && !dataStore.rasterLayers.length) await dataStore.refresh()
    return
  }
  // A signed-in visitor arriving by the PUBLIC link still gets the full page — try the store first
  // so the edit actions light up, and fall back to the public endpoint if it has nothing.
  if (auth.isAuthenticated) {
    try {
      if (!dataStore.vectorLayers.length && !dataStore.rasterLayers.length) await dataStore.refresh()
    } catch { /* not fatal here: the public endpoint below serves anyone */ }
    if (layer.value) return
  }
  try {
    const { data } = await getPublicLayer(kind.value === 'raster' ? 'raster' : 'vector',
                                          String(route.params.id || ''))
    publicLayer.value = data
  } catch (e) {
    // 404 means "not public", which for a signed-out visitor is usually "sign in and look again"
    // rather than "does not exist" — so say that instead of showing an empty page.
    publicError.value = e.response?.status === 404
      ? 'This layer is not shared publicly. Sign in to see whether you have access to it.'
      : (e.response?.data?.detail || 'Could not load this layer.')
  }
})
// The map is created on mount by the composable, so wait for it AND for the layer to arrive.
watch([loaded, layer, styleForMap], () => renderMap(), { deep: true, immediate: true })

// THE CONTROLS THIS MAP WAS MISSING, added once the map exists. Zoom and the globe come from the
// composable; these three are what a layer preview actually needs and had none of:
//   * TILT, as a real BUTTON — the same one the portal has. `visualizePitch` on the navigation
//     control was not enough: it shows pitch on the compass and lets you drag it, but right-drag
//     and compass-drag are not things a reader knows to try, so a 2.5D or extruded layer still had
//     no visible way to be seen from the side.
//   * FULLSCREEN, because a 52vh map is not much to inspect a raster in.
//   * ZOOM TO THE LAYER, which MapLibre has no control for and which matters most here: a layer
//     that never came into view leaves an empty map with no clue whether the data is missing or
//     merely elsewhere.
// The one automatic tilt, when the layer being previewed is 3D. Same argument as the portal and
// the editor: an extrusion or a raised terrain seen from directly overhead is indistinguishable
// from the flat version, so the preview would look like the feature had done nothing.
let pitched3D = false
function tiltIfThreeD(style) {
  if (pitched3D || !style) return
  const is3D = !!style.terrain || (style.layers || []).some(l => l.type === 'fill-extrusion')
  if (!is3D) return
  pitched3D = true
  if (map.value && map.value.getPitch() === 0) map.value.easeTo({ pitch: 45, duration: 600 })
}

let controlsAdded = false
watch(loaded, (ready) => {
  if (!ready || controlsAdded) return
  controlsAdded = true
  addTilt()
  addFullscreen()
  addZoomToExtent(() => lonLatBbox(layer.value?.bbox), 'Zoom to this layer')
}, { immediate: true })

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
  // Already tiled? Ask first — a restart discards a finished archive and re-runs a job that can
  // take minutes. See lib/tiling.js; My Data asks the same question in the same words.
  if (!confirmTiling(layer.value)) return
  tiling.value = true
  // Sent only for a point layer: for anything else the flag is meaningless, and posting it would
  // record a setting the tiler will never act on.
  try { await dataStore.tileVector(layer.value.id, isPointLayer.value ? clusterPoints.value : undefined) }
  catch (e) { alert(e.response?.data?.detail || 'Could not start tiling.') }
  finally { tiling.value = false }
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
