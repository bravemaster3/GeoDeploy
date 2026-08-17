<template>
  <Teleport to="body">
  <div class="fixed inset-0 bg-gray-900/50 backdrop-blur-sm flex items-center justify-center z-50 p-4">
    <div class="card w-full max-w-2xl p-6 space-y-4 shadow-2xl max-h-[90vh] overflow-y-auto">
      <div class="flex items-start justify-between gap-4">
        <div>
          <h2 class="text-lg font-semibold">Share links</h2>
          <p class="text-xs text-muted-foreground mt-0.5 truncate">{{ layer.name }}</p>
        </div>
        <button @click="$emit('close')" class="text-muted-foreground/70 hover:text-foreground text-xl leading-none">&times;</button>
      </div>

      <p class="text-xs text-muted-foreground">
        Ready-to-paste URLs for using this layer in other tools — QGIS, GeoLibre, MapLibre, GDAL, DuckDB.
        Nothing is copied or exported: these serve the same data live.
      </p>

      <!-- Not public yet: the URLs exist but 404 for anyone outside. -->
      <div v-if="loaded && !isPublic"
        class="flex items-start gap-2 rounded-lg border border-amber-400/40 bg-amber-500/10 p-3 text-xs">
        <AlertIcon class="w-4 h-4 text-amber-400 flex-shrink-0 mt-0.5" />
        <p class="text-amber-200/90">
          This layer is not <span class="font-medium">Public</span> yet, so these URLs return
          <span class="font-mono">404</span> to anyone outside this workspace. Set sharing to Public to
          activate them (and list the layer in the data catalog).
        </p>
      </div>

      <div v-if="loading" class="text-sm text-muted-foreground py-6 text-center">Loading links…</div>
      <div v-else-if="error" class="text-sm text-red-400 py-6 text-center">{{ error }}</div>

      <div v-else class="space-y-2">
        <div v-for="l in links" :key="l.id"
          class="rounded-lg border p-3 space-y-2"
          :class="l.primary ? 'border-brand-500/50 bg-brand-500/5' : 'border-border'">
          <div class="flex items-center gap-2 flex-wrap">
            <span class="text-sm font-medium">{{ l.label }}</span>
            <span v-if="l.primary"
              class="px-1.5 py-0.5 rounded bg-brand-500/20 text-brand-300 text-[10px] font-semibold uppercase tracking-wide">Recommended</span>
            <span class="text-[10px] px-1.5 py-0.5 rounded bg-muted text-muted-foreground">{{ l.format }}</span>
            <span v-for="t in l.tools" :key="t" class="text-[10px] text-muted-foreground/80">{{ t }}</span>
          </div>
          <div class="flex items-center gap-2">
            <code class="flex-1 min-w-0 truncate text-[11px] font-mono bg-muted/60 rounded px-2 py-1.5"
              :title="l.url">{{ l.url }}</code>
            <button @click="copy(l)" class="p-1.5 rounded hover:bg-muted transition-colors flex-shrink-0"
              :title="copied === l.id ? 'Copied' : 'Copy URL'">
              <CheckIcon v-if="copied === l.id" class="w-4 h-4 text-emerald-400" />
              <CopyIcon v-else class="w-4 h-4 text-muted-foreground" />
            </button>
            <!-- pmtiles:// and /vsicurl/ aren't browser-navigable; only offer Open for real http URLs -->
            <a v-if="l.url.startsWith('http')" :href="l.url" target="_blank" rel="noopener"
              class="p-1.5 rounded hover:bg-muted transition-colors flex-shrink-0" title="Open in a new tab">
              <ExternalLinkIcon class="w-4 h-4 text-muted-foreground" />
            </a>
          </div>
          <p class="text-[11px] text-muted-foreground">{{ l.hint }}</p>
        </div>
      </div>

      <div class="flex items-center justify-between pt-1 border-t border-border/60">
        <a v-if="catalog" :href="catalog" target="_blank" rel="noopener"
          class="text-xs text-primary hover:underline font-mono">{{ catalog }}</a>
        <button @click="$emit('close')" class="text-sm text-muted-foreground hover:text-foreground px-3 py-2">Close</button>
      </div>
    </div>
  </div>
  </Teleport>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { getVectorLinks, getRasterLinks } from '@/api'
import { AlertIcon, CheckIcon, CopyIcon, ExternalLinkIcon } from '@/views/icons'

const props = defineProps({
  layer: Object,
  layerType: { type: String, default: 'vector' },  // 'vector' | 'raster'
})
defineEmits(['close'])

const links = ref([])
const catalog = ref('')
const isPublic = ref(true)
const loading = ref(true)
const loaded = ref(false)
const error = ref('')
const copied = ref('')

onMounted(async () => {
  // ALREADY HAVE THEM? Use them. The public layer page loads a layer from `/api/public/layers/...`,
  // which carries the same `links` list — built by the same `services/share_links.py`. The fetch
  // below needs `data:read`, so on that page it answered 401 and a signed-out visitor got "Could
  // not load the links" for a layer whose links were already on screen's worth of data away.
  if ((props.layer.links || []).length) {
    links.value = props.layer.links
    // A layer reachable through the public endpoint is public by definition, so the panel does not
    // prompt to share it first.
    isPublic.value = true
    loaded.value = true
    loading.value = false
    return
  }
  try {
    const fn = props.layerType === 'raster' ? getRasterLinks : getVectorLinks
    const { data } = await fn(props.layer.id)
    links.value = data.links || []
    catalog.value = data.catalog || ''
    isPublic.value = !!data.public
    loaded.value = true
  } catch (e) {
    error.value = e.response?.data?.detail || 'Could not load the links for this layer.'
  } finally {
    loading.value = false
  }
})

async function copy(l) {
  try {
    await navigator.clipboard.writeText(l.url)
  } catch {
    // clipboard API needs a secure context — fall back to a hidden textarea
    const ta = document.createElement('textarea')
    ta.value = l.url
    document.body.appendChild(ta)
    ta.select()
    document.execCommand('copy')
    ta.remove()
  }
  copied.value = l.id
  setTimeout(() => { if (copied.value === l.id) copied.value = '' }, 1500)
}
</script>
