<template>
  <div class="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4" @click.self="$emit('close')">
    <div class="bg-card border border-border rounded-lg shadow-xl w-full max-w-md max-h-[90vh] flex flex-col">
      <div class="flex items-center justify-between gap-2 px-4 py-3 border-b border-border/60">
        <div class="min-w-0">
          <h3 class="text-sm font-semibold truncate">{{ layer.name }}</h3>
          <p class="text-[11px] text-muted-foreground">
            Default style — how this layer looks wherever it is added next.
          </p>
        </div>
        <button @click="$emit('close')"
          class="text-muted-foreground/70 hover:text-foreground text-xl leading-none flex-shrink-0">&times;</button>
      </div>

      <div class="px-4 py-3 overflow-y-auto">
        <!-- The SAME control the portal editor uses, rendered without its layer row. A second
             styling UI would be a second definition of the symbology vocabulary. -->
        <LayerPanel :config="config" standalone @update="apply" />
      </div>

      <div class="flex items-center gap-2 px-4 py-3 border-t border-border/60">
        <p v-if="error" class="text-xs text-red-400 flex-1">{{ error }}</p>
        <p v-else class="text-[11px] text-muted-foreground flex-1">
          Portals already using this layer keep their own styling.
        </p>
        <button @click="$emit('close')" class="btn-secondary text-xs px-3 py-1.5">Cancel</button>
        <button @click="save" :disabled="saving" class="btn-primary text-xs px-3 py-1.5">
          {{ saving ? 'Saving…' : 'Save' }}
        </button>
      </div>
    </div>
  </div>
</template>

<script setup>
/**
 * Style a layer from My Data (issue #23).
 *
 * Until now the symbology popover existed only inside the portal editor, and its "Save as default"
 * button was the ONLY writer of a layer's default style in the whole UI — so a layer had no style
 * of its own until someone added it to a portal, styled it there, and remembered to press that
 * button. Anything that asks "what does this layer look like" without naming a portal (the legend
 * endpoint, the QGIS plugin, a catalog view) got the fallback instead.
 *
 * This is a HOST, not a second editor: it builds the config LayerPanel expects, collects patches,
 * and persists them to `PUT /data/{kind}/{id}/default-style`.
 */
import { ref } from 'vue'
import LayerPanel from '@/components/portal/LayerPanel.vue'
import { saveVectorDefaultStyle, saveRasterDefaultStyle } from '@/api'
import { useDataStore } from '@/stores/data'

const props = defineProps({ layer: Object, layerType: String })
const emit = defineEmits(['close'])
const dataStore = useDataStore()

const saving = ref(false)
const error = ref('')

// LayerPanel speaks `layer_config` — the shape a portal stores. A layer's default_style is the same
// vocabulary in a slightly different wrapper, so translate once here rather than teaching the panel
// about a second one. Rasters keep their keys at the top level, as the API stores them.
const ds = props.layer.default_style || {}
const config = ref({
  layer_id: props.layer.id,
  layer_type: props.layerType,
  visible: true,
  opacity: ds.opacity ?? 1.0,
  style: props.layerType === 'vector'
    ? { ...(ds.style || {}) }
    : {
        colormap: ds.colormap || null,
        rescale: ds.rescale || null,
        algorithm: ds.algorithm || null,
        zfactor: ds.zfactor ?? null,
        bidx: ds.bidx || null,
      },
  popup_fields: ds.popup_fields || [],
})

function apply(patch) {
  config.value = { ...config.value, ...patch }
}

async function save() {
  saving.value = true
  error.value = ''
  try {
    const c = config.value
    const body = props.layerType === 'vector'
      ? { opacity: c.opacity, style: c.style, popup_fields: c.popup_fields }
      : {
          opacity: c.opacity,
          colormap: c.style?.colormap || null,
          rescale: c.style?.rescale || null,
          algorithm: c.style?.algorithm || null,
          zfactor: c.style?.zfactor ?? null,
          bidx: c.style?.bidx || null,
        }
    const fn = props.layerType === 'vector' ? saveVectorDefaultStyle : saveRasterDefaultStyle
    const { data: updated } = await fn(props.layer.id, body)
    // Keep the store in step so the row re-renders without a refetch.
    const list = props.layerType === 'vector' ? dataStore.vectorLayers : dataStore.rasterLayers
    const idx = list.findIndex(l => l.id === props.layer.id)
    if (idx !== -1) list[idx] = updated
    emit('close')
  } catch (e) {
    error.value = e?.response?.data?.detail || 'Could not save the style.'
  } finally {
    saving.value = false
  }
}
</script>
