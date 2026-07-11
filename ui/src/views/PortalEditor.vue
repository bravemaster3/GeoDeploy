<template>
  <div class="flex h-screen">
    <!-- Left panel -->
    <div class="w-80 flex-shrink-0 bg-card border-r border-border flex flex-col overflow-hidden">

      <!-- Top bar -->
      <div class="px-4 py-3 border-b border-border flex items-center justify-between gap-2">
        <button @click="$router.push('/portals')" class="text-sm text-muted-foreground hover:text-foreground flex-shrink-0">← Back</button>
        <input v-if="renaming" ref="renameInput" v-model="renameTitle"
          @blur="commitRename" @keydown.enter.prevent="commitRename" @keydown.esc="cancelRename"
          maxlength="120"
          class="text-sm font-semibold flex-1 min-w-0 text-center bg-transparent border-b border-primary text-foreground focus:outline-none" />
        <button v-else @click="startRename" :disabled="!portal"
          class="text-sm font-semibold truncate flex-1 text-center hover:text-primary transition-colors"
          title="Rename portal (also updates the published URL)">
          {{ portal?.title }}
        </button>
        <button @click="handlePublish" :disabled="busy || !portal"
          class="btn-primary text-xs py-1.5 flex-shrink-0">
          {{ portal?.published ? 'Re-publish' : 'Publish' }}
        </button>
      </div>

      <!-- Live URL bar -->
      <div v-if="portal?.published" class="px-4 py-2 bg-green-500/15 border-b border-green-500/30 flex items-center gap-2">
        <span class="w-2 h-2 rounded-full bg-green-500 flex-shrink-0 animate-pulse" />
        <a :href="`/portals/${portal.slug}/`" target="_blank"
          class="text-xs text-green-400 hover:text-green-900 truncate font-medium flex-1">
          /portals/{{ portal.slug }}/
        </a>
        <ExternalLinkIcon class="w-3.5 h-3.5 text-green-500 flex-shrink-0" />
      </div>

      <!-- Scrollable body -->
      <div class="flex-1 overflow-y-auto">

        <!-- Layers section -->
        <section class="p-4 border-b border-border/60">
          <div class="flex items-center justify-between mb-3">
            <h3 class="text-xs font-semibold text-muted-foreground uppercase tracking-wider">Layers</h3>
            <button @click="showAddLayer = !showAddLayer" class="text-xs text-primary hover:text-primary/80 font-medium">+ Add</button>
          </div>

          <div v-if="showAddLayer" class="mb-3 p-2 bg-muted/40 rounded-lg text-xs space-y-0.5 max-h-40 overflow-y-auto border border-border">
            <p v-if="!availableLayers.length" class="text-muted-foreground/70 p-1">No ready layers available.</p>
            <div v-for="layer in availableLayers" :key="`${layer.type}-${layer.id}`"
              class="flex items-center justify-between p-1.5 hover:bg-card rounded cursor-pointer"
              @click="addLayer(layer)"
            >
              <span class="font-medium">{{ layer.name }}</span>
              <span class="text-muted-foreground/70 text-[10px] uppercase">{{ layer.type }}</span>
            </div>
          </div>

          <div v-if="!layerConfigs.length" class="text-xs text-muted-foreground/70 py-1">No layers added yet.</div>
          <div v-for="(cfg, i) in layerConfigs" :key="`${cfg.layer_type}-${cfg.layer_id}`"
            draggable="true"
            @dragstart="onDragStart(i)"
            @dragover.prevent="onDragOver(i)"
            @drop="onDragEnd"
            @dragend="onDragEnd"
            :class="{ 'opacity-40': dragIndex === i }"
          >
            <LayerPanel
              :config="cfg"
              @remove="layerConfigs.splice(i, 1)"
              @update="layerConfigs[i] = { ...layerConfigs[i], ...$event }"
              @zoom="zoomToLayer(cfg)"
            />
          </div>
        </section>

        <!-- Template section -->
        <section class="p-4 border-b border-border/60">
          <h3 class="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-3">Template</h3>
          <div class="grid grid-cols-2 gap-2">
            <button v-for="t in templates" :key="t.id"
              class="p-2 rounded-lg border text-xs font-medium transition-colors text-left"
              :class="selectedTemplate === t.id
                ? 'border-primary bg-primary/10 text-primary'
                : 'border-border hover:border-muted-foreground/40 text-foreground/85'"
              @click="selectedTemplate = t.id"
            >{{ t.name }}</button>
          </div>
        </section>

        <!-- Access control section -->
        <section class="p-4">
          <h3 class="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-3">Access</h3>
          <div class="space-y-1.5">
            <label v-for="opt in accessOptions" :key="opt.value"
              class="flex items-start gap-2.5 p-2 rounded-lg border cursor-pointer transition-colors"
              :class="accessType === opt.value
                ? 'border-primary bg-primary/10'
                : 'border-border hover:border-muted-foreground/40'"
            >
              <input type="radio" :value="opt.value" v-model="accessType" class="mt-0.5 accent-primary flex-shrink-0" />
              <div>
                <div class="text-xs font-medium" :class="accessType === opt.value ? 'text-primary' : 'text-foreground/85'">
                  {{ opt.label }}
                </div>
                <div class="text-[10px] text-muted-foreground/70 mt-0.5">{{ opt.desc }}</div>
              </div>
            </label>
          </div>
          <div v-if="accessType === 'password'" class="mt-3">
            <label class="text-xs text-muted-foreground block mb-1">Password</label>
            <input v-model="accessPassword" type="password" placeholder="Set portal password"
              class="w-full text-xs border border-border rounded px-2 py-1.5 focus:outline-none focus:ring-1 focus:ring-primary/60"
            />
          </div>
        </section>

        <!-- About / documentation: shown in the published portal's About panel together with
             each layer's catalog metadata and public data links -->
        <section>
          <p class="text-xs font-semibold text-muted-foreground/70 uppercase tracking-wide mb-2">About this portal</p>
          <p v-if="description" class="text-xs text-muted-foreground line-clamp-3 whitespace-pre-line mb-2">{{ description }}</p>
          <p v-else class="text-xs text-muted-foreground/70 italic mb-2">No documentation yet.</p>
          <button type="button" @click="showAboutEditor = true"
            class="w-full text-xs font-medium border border-border hover:border-primary/60 text-foreground/85 rounded px-2 py-1.5">
            {{ description ? 'Edit About page' : 'Write the About page' }}
          </button>
          <p class="text-[10px] text-muted-foreground/70 mt-1">
            Shown to portal visitors, together with each layer's abstract, license and public data
            links (set those via the globe icon in My Data).
          </p>
        </section>

        <!-- About page editor: WYSIWYG (TipTap) — stored as markdown, rendered safely in the portal -->
        <Teleport to="body">
          <div v-if="showAboutEditor"
            class="fixed inset-0 bg-gray-900/50 backdrop-blur-sm flex items-center justify-center z-50 p-4">
            <div class="card w-full max-w-3xl p-6 shadow-2xl flex flex-col" style="max-height: 88vh">
              <div class="flex items-center justify-between mb-3">
                <h2 class="text-lg font-semibold">About this portal</h2>
                <button @click="closeAboutEditor"
                  class="text-muted-foreground/70 hover:text-foreground text-xl leading-none">&times;</button>
              </div>
              <!-- Toolbar -->
              <div v-if="aboutEditor" class="flex flex-wrap items-center gap-1 border border-border rounded-t-lg bg-muted/40 px-2 py-1.5">
                <button v-for="btn in toolbarButtons" :key="btn.label" type="button"
                  class="px-2 py-1 rounded text-xs font-semibold transition-colors"
                  :class="btn.active() ? 'bg-primary/15 text-primary' : 'text-muted-foreground hover:bg-muted'"
                  :title="btn.title" @click="btn.run()">
                  <span v-html="btn.label"></span>
                </button>
              </div>
              <!-- Editor -->
              <div class="flex-1 min-h-0 overflow-y-auto border border-t-0 border-border rounded-b-lg"
                style="min-height: 320px; max-height: 52vh">
                <EditorContent :editor="aboutEditor" class="gd-tiptap h-full" />
              </div>
              <!-- Footer stays INSIDE the card: fixed row under the editor -->
              <div class="flex items-center justify-between gap-3 pt-3 mt-3 border-t border-border/60 flex-shrink-0">
                <p class="text-[10px] text-muted-foreground/70">
                  Published as the portal's About page (about.html). Save changes + re-publish to
                  update it.
                </p>
                <button @click="closeAboutEditor" class="btn-secondary text-sm flex-shrink-0">Done</button>
              </div>
              <input ref="aboutImageInput" type="file" accept="image/png,image/jpeg,image/gif,image/webp"
                class="hidden" id="portal-about-image" name="portal-about-image" @change="insertAboutImage" />
            </div>
          </div>
        </Teleport>

      </div>

      <!-- Save footer -->
      <div class="p-4 border-t border-border space-y-2">
        <button @click="save" :disabled="busy" class="btn-secondary w-full justify-center text-sm">
          Save changes
        </button>
        <p v-if="saveMsg" class="text-xs text-center"
          :class="saveMsg.type === 'ok' ? 'text-green-400' : 'text-red-400'">
          {{ saveMsg.text }}
        </p>
      </div>
    </div>

    <!-- Map preview -->
    <div class="flex-1 relative bg-muted">
      <div id="portal-preview-map" class="w-full h-full" />

      <!-- GeoParquet detail fetch in flight (mirrors the published portal's loading pill) -->
      <div v-if="deckLoading > 0"
        class="absolute top-3 left-1/2 -translate-x-1/2 z-20 flex items-center gap-2 bg-card/95 shadow-md border border-border rounded-full px-3.5 py-1.5 text-xs font-medium text-foreground/85 pointer-events-none">
        <span class="inline-block w-3 h-3 rounded-full border-2 border-blue-500 border-t-transparent animate-spin" />
        Loading features…
      </div>

      <!-- Zoom to all layers. The current view is saved on "Save changes" and becomes
           the published portal's initial extent. -->
      <button v-if="layerConfigs.length" @click="zoomToAll"
        class="absolute top-3 left-3 z-10 flex items-center gap-1.5 bg-card/95 hover:bg-card shadow-md border border-border rounded-lg px-2.5 py-1.5 text-xs font-medium text-foreground/85"
        title="Zoom to the full extent of all layers. The current view is saved on Save and becomes the published portal's starting view.">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M3 8V5a2 2 0 0 1 2-2h3M16 3h3a2 2 0 0 1 2 2v3M21 16v3a2 2 0 0 1-2 2h-3M8 21H5a2 2 0 0 1-2-2v-3" />
        </svg>
        Zoom to all
      </button>

      <div v-if="!layerConfigs.length"
        class="absolute inset-0 flex items-center justify-center pointer-events-none">
        <span class="text-xs text-muted-foreground/70 bg-card/80 px-3 py-1.5 rounded-full">
          Add layers to see a preview
        </span>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, watch, nextTick } from 'vue'
import { useRoute } from 'vue-router'
import { useEditor, EditorContent } from '@tiptap/vue-3'
import StarterKit from '@tiptap/starter-kit'
import TipTapLink from '@tiptap/extension-link'
import TipTapImage from '@tiptap/extension-image'
import { Markdown } from 'tiptap-markdown'
import { usePortalsStore } from '@/stores/portals'
import { useDataStore } from '@/stores/data'
import { listTemplates, getRasterStats, getVectorFeatures, identifyVectorFeatures, uploadPortalAsset } from '@/api'
import { useMaplibre } from '@/composables/useMaplibre'
import maplibregl from 'maplibre-gl'
import { MapboxOverlay } from '@deck.gl/mapbox'
import { GeoJsonLayer } from '@deck.gl/layers'
import { ExternalLinkIcon } from '@/views/icons'
import LayerPanel from '@/components/portal/LayerPanel.vue'

const route = useRoute()
const portalsStore = usePortalsStore()
const dataStore = useDataStore()

const portal = ref(null)
const layerConfigs = ref([])
const selectedTemplate = ref('minimal')
const templates = ref([])
const showAddLayer = ref(false)
const lastAddedKey = ref(null)
const accessType = ref('public')

// Drag-to-reorder layers (top of list = top of map)
const dragIndex = ref(null)
function onDragStart(i) { dragIndex.value = i }
function onDragOver(i) {
  if (dragIndex.value === null || dragIndex.value === i) return
  const arr = layerConfigs.value
  const [moved] = arr.splice(dragIndex.value, 1)
  arr.splice(i, 0, moved)
  dragIndex.value = i
}
function onDragEnd() { dragIndex.value = null }
const accessPassword = ref('')
const busy = ref(false)
const saveMsg = ref(null)

// ── Rename the portal (click the title) ─────────────────────────────────────
// Renaming also changes the URL slug (server-side); if the portal is published it's re-published
// under the new slug and the old URL is removed. The editor route uses the portal id, so this
// page's own URL doesn't change — but the published /portals/{slug}/ link updates live.
const renaming = ref(false)
const renameTitle = ref('')
const renameInput = ref(null)
function startRename() {
  if (!portal.value) return
  renameTitle.value = portal.value.title
  renaming.value = true
  nextTick(() => { renameInput.value?.focus(); renameInput.value?.select() })
}
function cancelRename() { renaming.value = false }
async function commitRename() {
  if (!renaming.value) return           // guard against blur firing after Enter already committed
  const name = renameTitle.value.trim()
  renaming.value = false
  if (!name || name === portal.value.title) return
  busy.value = true
  try {
    const updated = await portalsStore.update(portal.value.id, { title: name })
    portal.value = updated
    saveMsg.value = { type: 'ok', text: 'Renamed' }
    setTimeout(() => { saveMsg.value = null }, 3000)
  } catch (err) {
    saveMsg.value = { type: 'err', text: err.response?.data?.detail || err.message }
  } finally {
    busy.value = false
  }
}
const description = ref('')  // About-panel documentation (markdown), baked at publish
const showAboutEditor = ref(false)

// WYSIWYG About editor (TipTap) — the document is STORED as markdown (tiptap-markdown), so the
// published portal keeps rendering through its safe escape-first mini-markdown (portal.js
// mdToHtml) and no HTML sanitizer is needed anywhere.
const aboutEditor = useEditor({
  extensions: [
    StarterKit.configure({ heading: { levels: [1, 2, 3] } }),
    TipTapLink.configure({ openOnClick: false }),
    TipTapImage,
    Markdown.configure({ html: false, linkify: true }),
  ],
  content: '',
  onUpdate: ({ editor }) => { description.value = editor.storage.markdown.getMarkdown() },
})

// Image embedding: pick a file → upload to the portal's asset store → insert the public URL.
const aboutImageInput = ref(null)
const uploadingImage = ref(false)
async function insertAboutImage(e) {
  const file = e.target.files && e.target.files[0]
  e.target.value = ''
  if (!file || !portal.value) return
  uploadingImage.value = true
  try {
    const { data } = await uploadPortalAsset(portal.value.id, file)
    aboutEditor.value.chain().focus().setImage({ src: data.url, alt: file.name.replace(/\.\w+$/, '') }).run()
  } catch (err) {
    saveMsg.value = { type: 'err', text: err.response?.data?.detail || 'Image upload failed' }
  } finally { uploadingImage.value = false }
}

// Load the saved markdown whenever the modal opens (the portal may load after editor setup).
watch(showAboutEditor, (open) => {
  if (open && aboutEditor.value) {
    aboutEditor.value.commands.setContent(description.value || '')
  }
})

function closeAboutEditor() {
  if (aboutEditor.value) description.value = aboutEditor.value.storage.markdown.getMarkdown()
  showAboutEditor.value = false
}

function promptLink() {
  const prev = aboutEditor.value?.getAttributes('link')?.href || ''
  const url = window.prompt('Link URL', prev)
  if (url === null) return
  if (!url) aboutEditor.value.chain().focus().unsetLink().run()
  else aboutEditor.value.chain().focus().setLink({ href: url }).run()
}

const toolbarButtons = [
  { label: 'H2', title: 'Section heading', run: () => aboutEditor.value.chain().focus().toggleHeading({ level: 2 }).run(), active: () => aboutEditor.value?.isActive('heading', { level: 2 }) },
  { label: 'H3', title: 'Subheading', run: () => aboutEditor.value.chain().focus().toggleHeading({ level: 3 }).run(), active: () => aboutEditor.value?.isActive('heading', { level: 3 }) },
  { label: '<b>B</b>', title: 'Bold', run: () => aboutEditor.value.chain().focus().toggleBold().run(), active: () => aboutEditor.value?.isActive('bold') },
  { label: '<i>I</i>', title: 'Italic', run: () => aboutEditor.value.chain().focus().toggleItalic().run(), active: () => aboutEditor.value?.isActive('italic') },
  { label: '• List', title: 'Bullet list', run: () => aboutEditor.value.chain().focus().toggleBulletList().run(), active: () => aboutEditor.value?.isActive('bulletList') },
  { label: '1. List', title: 'Numbered list', run: () => aboutEditor.value.chain().focus().toggleOrderedList().run(), active: () => aboutEditor.value?.isActive('orderedList') },
  { label: '&ldquo;&rdquo;', title: 'Quote', run: () => aboutEditor.value.chain().focus().toggleBlockquote().run(), active: () => aboutEditor.value?.isActive('blockquote') },
  { label: '&lt;/&gt;', title: 'Inline code', run: () => aboutEditor.value.chain().focus().toggleCode().run(), active: () => aboutEditor.value?.isActive('code') },
  { label: '🔗', title: 'Link', run: promptLink, active: () => aboutEditor.value?.isActive('link') },
  { label: '🖼', title: 'Insert image', run: () => aboutImageInput.value && aboutImageInput.value.click(), active: () => false },
]

const accessOptions = [
  { value: 'public',   label: 'Public',   desc: 'Anyone with the URL can view' },
  { value: 'password', label: 'Password', desc: 'Require a password to view' },
  { value: 'private',  label: 'Private',  desc: 'Only signed-in users can view' },
]

const { map, loaded, applyStyle, fitToBbox, jumpTo } = useMaplibre('portal-preview-map')

// Admin-pinned view (center/zoom) for the published portal; null = fit to all layers.
const savedView = ref(null)

onMounted(async () => {
  await Promise.all([portalsStore.refresh(), dataStore.refresh()])
  portal.value = portalsStore.portals.find(p => p.id === parseInt(route.params.id))
  if (portal.value) {
    layerConfigs.value = portal.value.layer_configs || []
    selectedTemplate.value = portal.value.template_id
    accessType.value = portal.value.access_type || 'public'
    savedView.value = portal.value.initial_view || null
    description.value = portal.value.description || ''
  }
  const { data } = await listTemplates()
  templates.value = data
})

// ── deck.gl overlay for GeoParquet layers ───────────────────────────────────
// GeoParquet layers are too big for a MapLibre geojson source, so they render in a deck.gl
// MapboxOverlay (added as a control → survives setStyle) fed by the viewport query
// (getVectorFeatures → covering-column-pruned GeoJSON). Refetched on pan/zoom (moveend) and when a
// new layer first appears; pure style edits rebuild from cached data without a network refetch.
let deckOverlay = null
const deckData = {}        // layer_id → cached FeatureCollection for the current view
const deckFetched = {}     // layer_id → { bbox:[w,s,e,n], band } region already loaded (see below)
const deckLoading = ref(0) // detail fetches in flight → shows the "Loading features…" pill
// In-flight viewport fetches are ABORTED when a newer view supersedes them (and before Save/Publish)
// so rapid pans over a heavy GeoParquet can't pile up requests and saturate the browser's ~6
// per-host connection limit — which otherwise starves the Save/Publish request and made it "hang".
let deckAbort = null
function abortDeckFetches() { if (deckAbort) { deckAbort.abort(); deckAbort = null } }
// Incremental viewport loading (mirrors portal.js fetchDeck): fetch a BUFFERED bbox (bigger than the
// screen) and skip refetching while the viewport stays inside the region already loaded at this zoom,
// so panning doesn't reload data already on screen and returning to a loaded area is instant. The row
// limit is scaled to the buffer's area so on-screen density is preserved.
const DECK_FETCH_PAD = 0.35
const DECK_PAD_AREA = (1 + 2 * DECK_FETCH_PAD) ** 2
const DECK_FETCH_MAX = 150000
const bboxContains = (o, i) => !!o && i[0] >= o[0] && i[1] >= o[1] && i[2] <= o[2] && i[3] <= o[3]
const padBbox = (b, f) => { const dx = (b[2] - b[0]) * f, dy = (b[3] - b[1]) * f; return [b[0] - dx, b[1] - dy, b[2] + dx, b[3] + dy] }
// Per-viewport feature cap, scaled by zoom (matches portal.js): a zoomed-out view is a capped
// subset either way, and a flat 50k limit made low-zoom responses tens of MB (slow query,
// slow JSON parse). More than the eye resolves at each band.
function deckLimit() {
  const z = map.value ? map.value.getZoom() : 10
  return z < 7 ? 10000 : z < 10 ? 25000 : 50000
}

// ── detail/overview switch (mirrors templates/shared/portal.js — keep in sync) ──────────────
// A HEAVY prepped layer whose viewport spans more than DECK_MAX_FILES partition files renders as
// a density-shaded partition-grid overview built from the layer's manifest (per-cell counts —
// instant, zero data reads) instead of per-feature detail; zooming in loads real features.
// LIGHT layers (total features ≤ DECK_DETAIL_MAX) always show full detail at every zoom.
const DECK_MAX_FILES = 16  // keep equal to portal.js WASM_MAX_FILES (same switch moment)
const DECK_DETAIL_MAX = 50000
const DECK_DETAIL_MAX_ROWS = 400000  // keep equal to portal.js DETAIL_MAX_ROWS
const deckManifests = {}   // layer_id → manifest object | 'none'

async function deckManifest(id) {
  if (deckManifests[id] !== undefined) return deckManifests[id]
  try {
    const r = await fetch(`/api/data/vector/${id}/parquet/manifest.json`)
    const m = r.ok ? await r.json() : null
    deckManifests[id] = (m && m.grid && m.cells && (!m.crs || m.crs === 'EPSG:4326')) ? m : 'none'
  } catch { deckManifests[id] = 'none' }
  return deckManifests[id]
}

// Same grid math as the server/portal.js: cell = ix*grid + iy, +1-cell pad for the FILE list.
// Rows are weighted by the fraction of each cell the viewport covers (whole-cell sums locked
// dense regions in overview mode forever — the pad alone spans ≥9 cells at any deep zoom).
// Keep in sync with portal.js viewportLoad.
function deckViewportLoad(m, b) {
  const g = m.grid, gsz = g.grid | 0, pad = 1, dx = g.spanx / gsz, dy = g.spany / gsz
  const ci = (v, lo, span) => Math.floor((v - lo) / (span || 1.0) * gsz)
  const ix0 = Math.max(0, ci(b[0], g.minx, g.spanx) - pad)
  const ix1 = Math.min(gsz - 1, ci(b[2], g.minx, g.spanx) + pad)
  const iy0 = Math.max(0, ci(b[1], g.miny, g.spany) - pad)
  const iy1 = Math.min(gsz - 1, ci(b[3], g.miny, g.spany) + pad)
  let files = 0, rows = 0
  if (ix0 <= ix1 && iy0 <= iy1)
    for (let ix = ix0; ix <= ix1; ix++)
      for (let iy = iy0; iy <= iy1; iy++) {
        const list = m.cells[String(ix * gsz + iy)] || []
        if (!list.length) continue
        const x0 = g.minx + ix * dx, y0 = g.miny + iy * dy
        const ox = Math.max(0, Math.min(b[2], x0 + dx) - Math.max(b[0], x0))
        const oy = Math.max(0, Math.min(b[3], y0 + dy) - Math.max(b[1], y0))
        const frac = Math.min(1, (ox * oy) / (dx * dy || 1))
        for (const f of list) { files += 1; rows += (f.rows || 0) * frac }
      }
  return { files, rows }
}

function deckOverviewGeojson(m) {
  if (m.__overviewFc) return m.__overviewFc
  const g = m.grid, gsz = g.grid | 0, dx = g.spanx / gsz, dy = g.spany / gsz
  let max = 0
  const counts = {}
  for (const k of Object.keys(m.cells)) {
    counts[k] = (m.cells[k] || []).reduce((a, f) => a + (f.rows || 0), 0)
    if (counts[k] > max) max = counts[k]
  }
  const features = Object.keys(m.cells).map(k => {
    const c = +k, ix = Math.floor(c / gsz), iy = c % gsz
    const x0 = g.minx + ix * dx, y0 = g.miny + iy * dy
    return {
      type: 'Feature',
      properties: { count: counts[k], density: max ? Math.sqrt(counts[k] / max) : 0 },
      geometry: { type: 'Polygon', coordinates: [[[x0, y0], [x0 + dx, y0], [x0 + dx, y0 + dy], [x0, y0 + dy], [x0, y0]]] },
    }
  })
  const fc = { type: 'FeatureCollection', features }
  fc.__overview = true
  m.__overviewFc = fc
  return fc
}

function hexToRgb(hex) {
  const h = String(hex || '#3b82f6').replace('#', '')
  const f = h.length === 3 ? h.split('').map(c => c + c).join('') : h
  const n = parseInt(f, 16)
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255]
}

// Visible GeoParquet layer configs that the deck overlay (not MapLibre) is responsible for.
// A layer that was explicitly tiled (ready PMTiles) uses the pmtiles:// fallback in the style instead.
function deckConfigs() {
  return [...layerConfigs.value].filter(cfg => {
    if (cfg.visible === false || cfg.layer_type !== 'vector') return false
    const layer = dataStore.vectorLayers.find(l => l.id === cfg.layer_id)
    return layer && layer.status === 'ready' && layer.storage_backend === 'geoparquet' &&
      !(layer.tile_status === 'ready' && layer.pmtiles_key)
  })
}

function makeDeckLayer(cfg) {
  const data = deckData[cfg.layer_id]
  if (!data) return null
  const layer = dataStore.vectorLayers.find(l => l.id === cfg.layer_id)
  const geom = (layer?.geometry_type || '').toLowerCase()
  const rgb = hexToRgb(cfg.style?.color || '#3b82f6')
  const opacity = cfg.opacity ?? 1.0
  const outline = hexToRgb(cfg.style?.outline_color || '#1d4ed8')
  const isPoly = geom.includes('polygon'), isLine = geom.includes('line')
  if (data.__overview) {
    // Large-scale representation: partition grid shaded by feature density (see portal.js twin).
    return new GeoJsonLayer({
      id: `deck_${cfg.layer_id}`,
      data,
      pickable: false,
      filled: true,
      stroked: true,
      getFillColor: f => [...rgb, Math.round(200 * opacity * f.properties.density)],
      getLineColor: [...rgb, Math.round(60 * opacity)],
      lineWidthUnits: 'pixels',
      getLineWidth: 0.5,
    })
  }
  return new GeoJsonLayer({
    id: `deck_${cfg.layer_id}`,
    data,
    pickable: false,
    filled: !isLine,
    stroked: true,
    getFillColor: [...rgb, Math.round(255 * opacity * (isPoly ? (cfg.style?.fill_opacity ?? 0.45) : 1))],
    getLineColor: isPoly ? [...outline, Math.round(255 * opacity)] : [...rgb, Math.round(255 * opacity)],
    lineWidthUnits: 'pixels',
    getLineWidth: cfg.style?.line_width ?? (isLine ? 2 : 1),
    lineWidthMinPixels: isLine ? (cfg.style?.line_width ?? 2) : 1,
    pointType: 'circle',
    pointRadiusUnits: 'pixels',
    getPointRadius: cfg.style?.radius ?? 5,
    pointRadiusMinPixels: 2,
  })
}

async function refreshDeck(refetch) {
  if (!deckOverlay || !map.value) return
  const configs = deckConfigs()
  if (refetch || configs.some(c => !deckData[c.layer_id])) {
    const b = map.value.getBounds()
    const nb = [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()]
    const band = Math.round(map.value.getZoom())
    // Supersede any still-running fetch from a previous view before starting this one.
    abortDeckFetches()
    deckAbort = new AbortController()
    const signal = deckAbort.signal
    await Promise.all(configs.map(async cfg => {
      if (!refetch && deckData[cfg.layer_id]) return
      // Already loaded a buffered region covering this viewport at this zoom → nothing to fetch.
      const cached = deckFetched[cfg.layer_id]
      if (refetch && deckData[cfg.layer_id] && cached && cached.band === band && bboxContains(cached.bbox, nb)) return
      // Fetch a BUFFERED bbox (bigger than the screen) so nearby pans stay within it; decide
      // overview-vs-detail on this SAME padded bbox so we only load detail when the area-capped
      // fetch would be reasonably complete (matches portal.js fetchDeckLayer).
      const fb = padBbox(nb, DECK_FETCH_PAD)
      const lim = Math.min(DECK_FETCH_MAX, Math.round(deckLimit() * DECK_PAD_AREA))
      try {
        // Heavy prepped layer at large scale → density-grid overview from the manifest
        // (instant, no feature query). Light layers and zoomed-in views load real features.
        const m = await deckManifest(cfg.layer_id)
        if (m !== 'none' && (m.feature_count || 0) > DECK_DETAIL_MAX) {
          const load = deckViewportLoad(m, fb)
          // Gate on ROWS only. The editor fetches detail from the SERVER in one request
          // (getVectorFeatures), so the partition-FILE count is irrelevant — gating on it (like
          // portal.js's disabled wasm path did) locked dense city cells, split into many files,
          // into the overview at every zoom (mirrors portal.js fitsDetail).
          if (load.rows > DECK_DETAIL_MAX_ROWS) {
            deckData[cfg.layer_id] = deckOverviewGeojson(m)
            deckFetched[cfg.layer_id] = { bbox: [-180, -90, 180, 90], band } // grid spans the extent
            return
          }
        }
        // Detail fetch: clear a stale overview grid immediately (never show the whole-extent
        // grid at a zoomed-in view while features load) — mirrors portal.js.
        if (deckData[cfg.layer_id] && deckData[cfg.layer_id].__overview) {
          deckData[cfg.layer_id] = { type: 'FeatureCollection', features: [] }
          deckOverlay.setProps({ layers: [...configs].reverse().map(makeDeckLayer).filter(Boolean) })
        }
        deckLoading.value++
        try {
          const { data } = await getVectorFeatures(cfg.layer_id, fb.join(','), lim, signal)
          deckData[cfg.layer_id] = data
          deckFetched[cfg.layer_id] = { bbox: fb, band }
        } finally { deckLoading.value-- }
      } catch (err) {
        // An aborted fetch (newer view or a save in progress) is expected — keep the last data.
        if (err?.code === 'ERR_CANCELED' || err?.name === 'CanceledError') return
        deckData[cfg.layer_id] = deckData[cfg.layer_id] || { type: 'FeatureCollection', features: [] }
      }
    }))
  }
  // config[0] = top of list → must draw on top → last in the deck layer array.
  const layers = [...configs].reverse().map(makeDeckLayer).filter(Boolean)
  deckOverlay.setProps({ layers })
}

// ── Preview identify popup ──────────────────────────────────────────────────
// Clicking the preview shows feature attributes, like the published portal: MVT (PostGIS)
// layers via queryRenderedFeatures; GeoParquet (deck-rendered) layers via the server identify
// endpoint — the deck data is geometry-only/capped, so attributes are fetched per click.
// Mirrors the popup logic in templates/shared/portal.js (keep in sync).
const previewPopup = new maplibregl.Popup({ closeButton: true, closeOnClick: false, maxWidth: '300px' })
const escAttr = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')

function attrTableHtml(title, props, fields) {
  const keys = fields && fields.length
    ? fields.filter(k => props[k] != null)
    : Object.keys(props).filter(k => props[k] != null).slice(0, 8)
  const body = keys.length
    ? '<table style="font-size:12px;border-collapse:collapse">' + keys.map(k =>
        `<tr><th style="text-align:left;padding:2px 8px 2px 0;color:#6b7280">${escAttr(k)}</th><td style="padding:2px 0">${escAttr(props[k])}</td></tr>`).join('') + '</table>'
    : '<div style="font-size:12px;color:#6b7280">No attributes</div>'
  return `<div style="font-weight:600;font-size:12px;margin-bottom:4px">${escAttr(title)}</div>` + body
}

async function onPreviewClick(e) {
  if (!map.value) return
  const sections = []
  // MVT layers under the click (small pixel pad so lines/points are hittable).
  const pad = 5
  const box = [[e.point.x - pad, e.point.y - pad], [e.point.x + pad, e.point.y + pad]]
  const vecIds = (map.value.getStyle().layers || [])
    .map(l => l.id).filter(id => id.startsWith('vector_') && map.value.getLayer(id))
  try {
    const feats = vecIds.length ? map.value.queryRenderedFeatures(box, { layers: vecIds }) : []
    if (feats.length) {
      const f = feats[0]
      const lid = Number(String(f.layer.id).replace('vector_', ''))
      const layer = dataStore.vectorLayers.find(l => l.id === lid)
      const cfg = layerConfigs.value.find(c => c.layer_type === 'vector' && c.layer_id === lid)
      sections.push(attrTableHtml(layer?.name || f.layer.id, f.properties || {}, cfg?.popup_fields))
    }
  } catch { /* style mid-rebuild */ }

  // GeoParquet layers showing real detail (clicking the density-grid overview is meaningless).
  const deckQ = deckConfigs().filter(cfg => {
    const d = deckData[cfg.layer_id]
    return d && !d.__overview
  })
  if (!sections.length && !deckQ.length) return
  const p1 = map.value.unproject([e.point.x - pad, e.point.y])
  const p2 = map.value.unproject([e.point.x + pad, e.point.y])
  const tol = Math.max(Math.abs(p2.lng - p1.lng) / 2, 1e-7)

  previewPopup.setLngLat(e.lngLat)
    .setHTML(sections.join('<hr style="margin:6px 0;border-color:#e5e7eb">') +
      (deckQ.length ? '<div style="font-size:12px;color:#6b7280">Reading attributes…</div>' : ''))
    .addTo(map.value)

  if (!deckQ.length) return
  const results = await Promise.all(deckQ.map(async cfg => {
    try {
      const { data } = await identifyVectorFeatures(cfg.layer_id, e.lngLat.lng, e.lngLat.lat, tol, 5)
      if (!data.features?.length) return null
      const layer = dataStore.vectorLayers.find(l => l.id === cfg.layer_id)
      let html = attrTableHtml(layer?.name || `Layer ${cfg.layer_id}`, data.features[0], cfg.popup_fields)
      if (data.features.length > 1)
        html += `<div style="font-size:11px;color:#6b7280;margin-top:2px">+${data.features.length - 1} more feature${data.features.length > 2 ? 's' : ''} here</div>`
      return html
    } catch { return null }
  }))
  const all = sections.concat(results.filter(Boolean))
  if (!all.length) { previewPopup.remove(); return }
  previewPopup.setHTML(all.join('<hr style="margin:6px 0;border-color:#e5e7eb">'))
}

// Rebuild the preview style on any config/layer change, but only move the camera on
// the FIRST build (restore the saved view, else fit to all layers). After that, style
// edits (band/colour/etc.) must NOT yank the view — setStyle keeps the current camera.
let viewInitialized = false
watch([layerConfigs, loaded], () => {
  if (!loaded.value) return
  const { style, bounds } = buildPreviewStyle()
  applyStyle(style)
  refreshDeck(false)  // rebuild deck layers (fetch only newly-appeared geoparquet layers)
  if (!viewInitialized) {
    if (savedView.value) { jumpTo(savedView.value); viewInitialized = true }
    else if (bounds) { fitToBbox(bounds); viewInitialized = true }
  }
}, { deep: true })

// Point marker icons — mirror of templates/shared/portal.js. Generated on demand
// when a symbol layer references a missing icon image (styleimagemissing).
const markerSpecs = {}
watch(loaded, (v) => {
  if (!v || !map.value) return
  map.value.on('styleimagemissing', (e) => {
    if (!e.id || !e.id.startsWith('gd-pt-') || map.value.hasImage(e.id)) return
    const spec = markerSpecs[e.id]
    if (!spec) return
    const im = markerImage(spec.shape, spec.color, spec.size)
    try { map.value.addImage(e.id, im, { pixelRatio: im.pixelRatio }) } catch { /* ignore */ }
  })
  // deck.gl overlay (once): a control so it survives setStyle; refetch the viewport on pan/zoom.
  if (!deckOverlay) {
    deckOverlay = new MapboxOverlay({ interleaved: false, layers: [] })
    map.value.addControl(deckOverlay)
    map.value.on('click', onPreviewClick)
    map.value.on('moveend', () => refreshDeck(true))
    // Mid-gesture: hide the coarse overview grid the moment the viewport qualifies for detail —
    // don't wait for moveend + the fetch (mirrors portal.js).
    let moveRaf = false
    map.value.on('move', () => {
      if (moveRaf) return
      moveRaf = true
      requestAnimationFrame(() => {
        moveRaf = false
        if (!map.value) return
        const b = map.value.getBounds()
        const vb = [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()]
        let changed = false
        for (const cfg of deckConfigs()) {
          const data = deckData[cfg.layer_id]
          if (!data || !data.__overview) continue
          const m = deckManifests[cfg.layer_id]
          if (!m || m === 'none' || !m.grid) continue
          const load = deckViewportLoad(m, padBbox(vb, DECK_FETCH_PAD))  // same padded bbox as the fetch
          const light = (m.feature_count || 0) <= DECK_DETAIL_MAX
          // Rows-only gate (server fetch → file count irrelevant; see the refetch branch above).
          if (light || load.rows <= DECK_DETAIL_MAX_ROWS) {
            deckData[cfg.layer_id] = { type: 'FeatureCollection', features: [] }
            delete deckFetched[cfg.layer_id]  // drop the cached region so moveend refetches detail
            changed = true
          }
        }
        if (changed) refreshDeck(false)
      })
    })
    refreshDeck(true)
  }
})
function starPts(cx, cy, r) {
  const p = []
  for (let i = 0; i < 10; i++) { const a = -Math.PI / 2 + i * Math.PI / 5, rr = (i % 2) ? r * 0.45 : r; p.push([cx + Math.cos(a) * rr, cy + Math.sin(a) * rr]) }
  return p
}
function crossPts(cx, cy, r) {
  const t = r * 0.38
  return [[-t, -r], [t, -r], [t, -t], [r, -t], [r, t], [t, t], [t, r], [-t, r], [-t, t], [-r, t], [-r, -t], [-t, -t]].map(d => [cx + d[0], cy + d[1]])
}
function markerImage(shape, color, size) {
  const dpr = 2, r = Math.max(3, Number(size) || 5), stroke = Math.max(1, r * 0.28)
  const dim = 80  // fixed canvas (see portal.js): constant dims let updateImage handle size changes
  const cv = document.createElement('canvas')
  cv.width = dim * dpr; cv.height = dim * dpr
  const ctx = cv.getContext('2d')
  ctx.scale(dpr, dpr); ctx.lineJoin = 'round'
  const cx = dim / 2, cy = dim / 2
  ctx.beginPath()
  if (shape === 'square') ctx.rect(cx - r, cy - r, r * 2, r * 2)
  else if (shape === 'triangle') { ctx.moveTo(cx, cy - r); ctx.lineTo(cx + r * 0.92, cy + r * 0.72); ctx.lineTo(cx - r * 0.92, cy + r * 0.72); ctx.closePath() }
  else if (shape === 'diamond') { ctx.moveTo(cx, cy - r); ctx.lineTo(cx + r, cy); ctx.lineTo(cx, cy + r); ctx.lineTo(cx - r, cy); ctx.closePath() }
  else if (shape === 'star') { starPts(cx, cy, r).forEach((p, i) => i ? ctx.lineTo(p[0], p[1]) : ctx.moveTo(p[0], p[1])); ctx.closePath() }
  else if (shape === 'cross') { crossPts(cx, cy, r).forEach((p, i) => i ? ctx.lineTo(p[0], p[1]) : ctx.moveTo(p[0], p[1])); ctx.closePath() }
  else ctx.arc(cx, cy, r, 0, Math.PI * 2)
  ctx.fillStyle = color || '#3b82f6'; ctx.fill()
  ctx.strokeStyle = '#ffffff'; ctx.lineWidth = stroke; ctx.stroke()
  const d = ctx.getImageData(0, 0, dim * dpr, dim * dpr)
  return { width: dim * dpr, height: dim * dpr, data: d.data, pixelRatio: dpr }
}

function buildPreviewStyle() {
  const style = {
    version: 8,
    glyphs: 'https://demotiles.maplibre.org/font/{fontstack}/{range}.pbf',
    sources: {
      basemap: {
        type: 'raster',
        tiles: [
          'https://a.basemaps.cartocdn.com/light_all/{z}/{x}/{y}@2x.png',
          'https://b.basemaps.cartocdn.com/light_all/{z}/{x}/{y}@2x.png',
        ],
        tileSize: 256,
        attribution: '© OpenStreetMap contributors © CARTO',
      },
    },
    layers: [{ id: 'basemap', type: 'raster', source: 'basemap' }],
  }

  // Merge every visible layer's bbox (skipping non-lon/lat bboxes, e.g. an old
  // projected raster) so "fit"/zoom-to-all covers all layers, not just the last one.
  let bounds = null
  const expandBounds = (b) => {
    const ok = Array.isArray(b) && b.length === 4 &&
      b[0] >= -180 && b[2] <= 180 && b[0] < b[2] && b[1] >= -90 && b[3] <= 90 && b[1] < b[3]
    if (!ok) return
    bounds = bounds
      ? [Math.min(bounds[0], b[0]), Math.min(bounds[1], b[1]), Math.max(bounds[2], b[2]), Math.max(bounds[3], b[3])]
      : b.slice()
  }

  // config[0] is the top of the list → draw it on top → build in reverse.
  for (const cfg of [...layerConfigs.value].reverse()) {
    if (cfg.visible === false) continue
    if (cfg.layer_type === 'vector') {
      const layer = dataStore.vectorLayers.find(l => l.id === cfg.layer_id)
      if (!layer || layer.status !== 'ready') continue

      const srcId = `vector_${layer.id}`
      let sourceLayer
      if (layer.storage_backend === 'geoparquet') {
        // File-backed (GeoParquet). PRIMARY display = a deck.gl overlay fed by the viewport query
        // (rendered outside this MapLibre style — see refreshDeck), so EXCLUDE the layer here and
        // just keep its bbox for zoom-to-all. FALLBACK: a layer explicitly tiled (ready PMTiles)
        // renders via the pmtiles:// vector source instead.
        if (!(layer.tile_status === 'ready' && layer.pmtiles_key)) { expandBounds(layer.bbox); continue }
        style.sources[srcId] = { type: 'vector', url: `pmtiles://${location.origin}/api/data/vector/${layer.id}/pmtiles` }
        sourceLayer = 'geodeploy'
      } else {
        style.sources[srcId] = {
          type: 'vector',
          tiles: [`${location.origin}/tiles/${layer.schema_name}.${layer.table_name}/{z}/{x}/{y}`],
          minzoom: 0, maxzoom: 22,
        }
        sourceLayer = `${layer.schema_name}.${layer.table_name}`
      }
      const color = cfg.style?.color || '#3b82f6'
      const opacity = cfg.opacity ?? 1.0
      const geom = (layer.geometry_type || '').toLowerCase()

      if (geom.includes('polygon')) {
        style.layers.push({
          id: srcId, type: 'fill', source: srcId, 'source-layer': sourceLayer,
          paint: {
            'fill-color': color,
            'fill-opacity': opacity * (cfg.style?.fill_opacity ?? 0.45),
            'fill-outline-color': cfg.style?.outline_color || '#1d4ed8',
          },
        })
      } else if (geom.includes('line')) {
        const linePaint = { 'line-color': color, 'line-width': cfg.style?.line_width ?? 2, 'line-opacity': opacity }
        if (cfg.style?.lineType === 'dashed') linePaint['line-dasharray'] = [2, 1.5]
        else if (cfg.style?.lineType === 'dotted') linePaint['line-dasharray'] = [0.4, 1.8]
        style.layers.push({
          id: srcId, type: 'line', source: srcId, 'source-layer': sourceLayer, paint: linePaint,
        })
      } else {
        // Points render as a symbol layer with a runtime-generated icon (so shapes
        // work on raster basemaps). Icon id encodes the style so it refreshes on change.
        const shape = cfg.style?.marker || 'circle'
        const mSize = cfg.style?.radius ?? 5
        const iconId = `gd-pt-${layer.id}-${shape}-${String(color).replace('#', '')}-${mSize}`
        markerSpecs[iconId] = { shape, color, size: mSize }
        style.layers.push({
          id: srcId, type: 'symbol', source: srcId, 'source-layer': sourceLayer,
          layout: { 'icon-image': iconId, 'icon-allow-overlap': true, 'icon-ignore-placement': true },
          paint: { 'icon-opacity': opacity },
        })
      }

      expandBounds(layer.bbox)

    } else if (cfg.layer_type === 'raster') {
      const layer = dataStore.rasterLayers.find(l => l.id === cfg.layer_id)
      if (!layer || layer.status !== 'ready' || !layer.tile_url) continue

      const srcId = `raster_${layer.id}`
      const absTileUrl = rasterTilesUrl(layer.tile_url, cfg.style)
      style.sources[srcId] = { type: 'raster', tiles: [absTileUrl], tileSize: 256 }
      style.layers.push({
        id: srcId, type: 'raster', source: srcId,
        paint: { 'raster-opacity': cfg.opacity ?? 1.0 },
      })
      expandBounds(layer.bbox)

    } else if (cfg.layer_type === 'external') {
      const src = dataStore.externalSources.find(s => s.id === cfg.layer_id)
      if (!src) continue
      const srcId = `ext_${src.id}`
      const abs = (u) => (u && u.startsWith('/')) ? location.origin + u : u
      const op = cfg.opacity ?? 1.0
      if (src.kind === 'raster') {
        if (!src.tile_url) continue
        style.sources[srcId] = { type: 'raster', tiles: [abs(src.tile_url)], tileSize: 256 }
        if (src.attribution) style.sources[srcId].attribution = src.attribution
        style.layers.push({ id: `external-${src.id}`, type: 'raster', source: srcId, paint: { 'raster-opacity': op } })
      } else {
        if (!src.data_url) continue
        style.sources[srcId] = { type: 'geojson', data: abs(src.data_url) }
        if (src.attribution) style.sources[srcId].attribution = src.attribution
        const geom = src.geometry_type || 'polygon'
        const color = cfg.style?.color || '#3b82f6'
        if (geom === 'polygon') {
          style.layers.push({ id: `external-${src.id}`, type: 'fill', source: srcId,
            paint: { 'fill-color': color, 'fill-opacity': op * (cfg.style?.fill_opacity ?? 0.45), 'fill-outline-color': cfg.style?.outline_color || '#1d4ed8' } })
        } else if (geom === 'line') {
          style.layers.push({ id: `external-${src.id}`, type: 'line', source: srcId,
            paint: { 'line-color': color, 'line-width': cfg.style?.line_width ?? 2, 'line-opacity': op } })
        } else {
          style.layers.push({ id: `external-${src.id}`, type: 'circle', source: srcId,
            paint: { 'circle-color': color, 'circle-radius': cfg.style?.radius ?? 5, 'circle-opacity': op, 'circle-stroke-color': '#fff', 'circle-stroke-width': 1 } })
        }
      }
      expandBounds(src.bbox)
    }
  }

  return { style, bounds }
}

// Build a raster tile URL from the layer's base URL + the configured raster style.
function rasterTilesUrl(baseTileUrl, style) {
  const base = (baseTileUrl || '').split('&')[0]  // s3 key has no '&', so this keeps ?url=...
  const params = []
  const bands = Array.isArray(style?.bidx) ? style.bidx.filter(b => b != null) : []
  bands.forEach(b => params.push(`bidx=${b}`))
  if (style?.rescale) params.push(`rescale=${style.rescale}`)
  if (style?.algorithm) {
    params.push(`algorithm=${style.algorithm}`)
    if (style.algorithm === 'hillshade' && style.zfactor && Number(style.zfactor) !== 1) {
      params.push(`expression=b1*${style.zfactor}`)
    }
  } else if (style?.colormap && bands.length !== 3) {
    params.push(`colormap_name=${style.colormap}`)
  }
  const url = base + (params.length ? '&' + params.join('&') : '')
  return url.startsWith('/') ? location.origin + url : url
}

const availableLayers = computed(() => [
  ...dataStore.vectorLayers.filter(l => l.status === 'ready').map(l => ({ ...l, type: 'vector' })),
  ...dataStore.rasterLayers.filter(l => l.status === 'ready').map(l => ({ ...l, type: 'raster' })),
  ...dataStore.externalSources.map(s => ({ ...s, type: 'external' })),
].filter(l => !layerConfigs.value.some(c => c.layer_id === l.id && c.layer_type === l.type)))

const LAYER_COLORS = [
  '#3b82f6', '#ef4444', '#10b981', '#f59e0b', '#8b5cf6',
  '#ec4899', '#06b6d4', '#84cc16', '#f97316', '#6366f1',
]

function nextColor() {
  const used = layerConfigs.value.map(c => c.style?.color).filter(Boolean)
  return LAYER_COLORS.find(c => !used.includes(c)) || LAYER_COLORS[layerConfigs.value.length % LAYER_COLORS.length]
}

async function addLayer(layer) {
  const ds = layer.default_style
  let style
  if (layer.type === 'vector') {
    style = ds?.style ?? { color: nextColor() }
  } else if (layer.type === 'external') {
    // External vector (WFS) gets a colour; external raster (WMS/XYZ) has no style.
    style = layer.kind === 'vector' ? { color: nextColor() } : {}
  } else {
    style = {}
    if (ds?.colormap) style.colormap = ds.colormap
    if (ds?.rescale) style.rescale = ds.rescale
    if (ds?.algorithm) style.algorithm = ds.algorithm
    if (ds?.zfactor != null) style.zfactor = ds.zfactor
    if (Array.isArray(ds?.bidx) && ds.bidx.length) style.bidx = ds.bidx.slice()
  }
  // Add to the top of the list (and the top of the map).
  layerConfigs.value.unshift({
    layer_id: layer.id,
    layer_type: layer.type,
    visible: true,
    opacity: ds?.opacity ?? 1.0,
    style,
    popup_fields: ds?.popup_fields ?? [],
  })
  lastAddedKey.value = `${layer.type}-${layer.id}`
  showAddLayer.value = false

  // Auto-stretch a freshly added raster that has no stretch yet
  if (layer.type === 'raster' && !style.rescale) {
    try {
      const { data } = await getRasterStats(layer.id)
      if (data?.rescale) {
        const idx = layerConfigs.value.findIndex(c => c.layer_id === layer.id && c.layer_type === 'raster')
        if (idx !== -1) {
          layerConfigs.value[idx] = {
            ...layerConfigs.value[idx],
            style: { ...layerConfigs.value[idx].style, rescale: data.rescale },
          }
        }
      }
    } catch { /* leave unstretched */ }
  }
}

function zoomToLayer(cfg) {
  const list = cfg.layer_type === 'external' ? dataStore.externalSources
    : cfg.layer_type === 'vector' ? dataStore.vectorLayers : dataStore.rasterLayers
  const layer = list.find(l => l.id === cfg.layer_id)
  if (layer?.bbox) fitToBbox(layer.bbox)
}

// Fit the preview to the merged extent of all (visible) layers.
function zoomToAll() {
  const { bounds } = buildPreviewStyle()
  if (bounds) fitToBbox(bounds)
}

// The map's current center/zoom — persisted so the published portal opens here.
function currentView() {
  const m = map.value
  if (!m) return null
  const c = m.getCenter()
  return {
    center: [c.lng, c.lat],
    zoom: m.getZoom(),
    bearing: m.getBearing(),
    pitch: m.getPitch(),
  }
}

async function save() {
  if (!portal.value) return
  busy.value = true
  saveMsg.value = null
  // Free any connections held by in-flight viewport fetches so the save request isn't queued
  // behind them (the "sometimes it never saves" symptom on heavy GeoParquet layers).
  abortDeckFetches()
  try {
    const view = currentView()
    if (view) savedView.value = view
    const payload = {
      layer_configs: layerConfigs.value,
      template_id: selectedTemplate.value,
      access_type: accessType.value,
      initial_view: view,
      description: description.value,
    }
    if (accessType.value === 'password' && accessPassword.value) {
      payload.access_password = accessPassword.value
    }
    const updated = await portalsStore.update(portal.value.id, payload)
    portal.value = updated
    accessPassword.value = ''
    saveMsg.value = { type: 'ok', text: 'Saved' }
    setTimeout(() => { saveMsg.value = null }, 3000)
  } catch (err) {
    saveMsg.value = { type: 'err', text: err.response?.data?.detail || err.message }
  } finally {
    busy.value = false
  }
}

async function handlePublish() {
  await save()
  if (saveMsg.value?.type === 'err') return
  busy.value = true
  try {
    const updated = await portalsStore.publish(portal.value.id)
    portal.value = updated
  } catch (err) {
    saveMsg.value = { type: 'err', text: err.response?.data?.detail || err.message }
  } finally {
    busy.value = false
  }
}
</script>

<style scoped>
/* TipTap editing surface — Tailwind preflight strips heading/list styling, restore it so the
   editor reads like the published About page. */
.gd-tiptap :deep(.ProseMirror) { min-height: 340px; padding: 14px 16px; outline: none; font-size: .875rem; line-height: 1.6; }
.gd-tiptap :deep(h1) { font-size: 1.2rem; font-weight: 700; margin: .7rem 0 .35rem; }
.gd-tiptap :deep(h2) { font-size: 1.05rem; font-weight: 600; margin: .6rem 0 .3rem; }
.gd-tiptap :deep(h3) { font-size: .95rem; font-weight: 600; margin: .5rem 0 .25rem; }
.gd-tiptap :deep(p) { margin: .3rem 0; }
.gd-tiptap :deep(ul) { list-style: disc; margin: .3rem 0 .3rem 1.2rem; }
.gd-tiptap :deep(ol) { list-style: decimal; margin: .3rem 0 .3rem 1.2rem; }
.gd-tiptap :deep(blockquote) { border-left: 3px solid hsl(var(--border)); padding-left: .8rem; color: hsl(var(--muted-foreground)); margin: .4rem 0; }
.gd-tiptap :deep(a) { color: hsl(var(--primary)); text-decoration: underline; }
.gd-tiptap :deep(code) { font-size: .8rem; background: hsl(var(--muted)); border: 1px solid hsl(var(--border)); border-radius: 4px; padding: 0 3px; }
.gd-tiptap :deep(img) { max-width: 100%; border-radius: 8px; border: 1px solid hsl(var(--border)); margin: .5rem 0; }
.gd-tiptap :deep(img.ProseMirror-selectednode) { outline: 2px solid hsl(var(--primary)); }
</style>
