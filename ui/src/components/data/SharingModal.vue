<template>
  <Teleport to="body">
  <div class="fixed inset-0 bg-gray-900/50 backdrop-blur-sm flex items-center justify-center z-50 p-4">
    <div class="card w-full max-w-md p-6 space-y-4 shadow-2xl max-h-[90vh] overflow-y-auto">
      <div class="flex items-center justify-between">
        <h2 class="text-lg font-semibold">Sharing</h2>
        <button @click="$emit('close')" class="text-muted-foreground/70 hover:text-foreground text-xl leading-none">&times;</button>
      </div>

      <p class="text-xs text-muted-foreground">
        Who can see this layer. <span class="font-medium text-foreground">Organization</span> is the default —
        every member of your workspace. <span class="font-medium text-foreground">Private</span> keeps it to you
        and workspace admins. <span class="font-medium text-foreground">Public</span> also lists it in this
        instance's data catalog
        (<a :href="stacUrl" target="_blank" class="text-primary hover:underline font-mono">/api/stac</a>)
        with ready-to-use URLs — anyone can find and load it in QGIS, DuckDB, or scripts.
      </p>

      <!-- Visibility picker -->
      <div class="space-y-2">
        <label v-for="t in tiers" :key="t.value"
          class="flex items-start gap-3 p-3 rounded-lg border cursor-pointer transition-colors"
          :class="form.visibility === t.value ? t.activeClass : 'border-border hover:bg-muted/50'">
          <input type="radio" name="gd-visibility" :value="t.value" v-model="form.visibility"
            class="mt-0.5 w-4 h-4" :class="t.accent" />
          <component :is="t.icon" class="w-4 h-4 mt-0.5 flex-shrink-0" :class="t.color" />
          <span class="flex-1 min-w-0">
            <span class="block text-sm font-medium">{{ t.label }}</span>
            <span class="block text-xs text-muted-foreground">{{ t.hint }}</span>
          </span>
        </label>
      </div>

      <!-- Catalog metadata — only relevant for a PUBLIC layer (this is what consumers see in /api/stac) -->
      <div v-if="form.visibility === 'public'" class="space-y-3 pt-1 border-t border-border/60">
        <p class="text-[11px] text-muted-foreground pt-2">Catalog metadata — shown to anyone who finds this layer.</p>
        <div>
          <label class="text-xs text-muted-foreground block mb-1">Abstract</label>
          <textarea id="gd-share-abstract" name="gd-share-abstract" v-model="form.abstract" rows="3" class="input w-full text-sm"
            placeholder="What is this dataset? Coverage, vintage, method…"></textarea>
        </div>
        <div>
          <label class="text-xs text-muted-foreground block mb-1">Keywords <span class="text-muted-foreground/70">(comma-separated)</span></label>
          <input id="gd-share-keywords" name="gd-share-keywords" v-model="form.keywords" type="text" class="input w-full text-sm" placeholder="landcover, france, 2018" />
        </div>
        <div class="grid grid-cols-2 gap-3">
          <div>
            <label class="text-xs text-muted-foreground block mb-1">License</label>
            <input id="gd-share-license" name="gd-share-license" v-model="form.license" type="text" list="gd-licenses" class="input w-full text-sm" placeholder="CC-BY-4.0" />
            <datalist id="gd-licenses">
              <option value="CC-BY-4.0" /><option value="CC-BY-SA-4.0" /><option value="CC0-1.0" />
              <option value="ODbL-1.0" /><option value="proprietary" />
            </datalist>
          </div>
          <div>
            <label class="text-xs text-muted-foreground block mb-1">Attribution</label>
            <input id="gd-share-attribution" name="gd-share-attribution" v-model="form.attribution" type="text" class="input w-full text-sm" placeholder="© Provider" />
          </div>
        </div>
      </div>

      <div class="flex items-center justify-end gap-3 pt-1">
        <span v-if="error" class="text-xs text-red-400 mr-auto">{{ error }}</span>
        <button @click="$emit('close')" class="text-sm text-muted-foreground hover:text-foreground px-3 py-2">Cancel</button>
        <button @click="save" :disabled="saving"
          class="text-sm font-semibold text-white bg-brand-600 hover:bg-brand-700 disabled:opacity-60 rounded-lg px-4 py-2">
          {{ saving ? 'Saving…' : 'Save' }}
        </button>
      </div>
    </div>
  </div>
  </Teleport>
</template>

<script setup>
import { reactive, ref, computed } from 'vue'
import { setVectorSharing, setRasterSharing } from '@/api'
import { UserIcon, UsersIcon, GlobeIcon } from '@/views/icons'

const props = defineProps({
  layer: Object,
  layerType: { type: String, default: 'vector' },  // 'vector' | 'raster'
})
const emit = defineEmits(['close'])

const tiers = [
  { value: 'private', label: 'Private', icon: UserIcon, color: 'text-amber-400',
    accent: 'accent-amber-500', activeClass: 'border-amber-300 bg-amber-500/15',
    hint: 'Only you and workspace admins' },
  { value: 'organization', label: 'Organization', icon: UsersIcon, color: 'text-sky-400',
    accent: 'accent-sky-500', activeClass: 'border-sky-300 bg-sky-500/15',
    hint: 'Everyone in your workspace (default)' },
  { value: 'public', label: 'Public', icon: GlobeIcon, color: 'text-emerald-400',
    accent: 'accent-emerald-600', activeClass: 'border-emerald-300 bg-emerald-500/15',
    hint: 'Your workspace + listed in the public data catalog' },
]

const form = reactive({
  // Fall back from the legacy is_public flag if an older row lacks visibility.
  visibility: props.layer.visibility || (props.layer.is_public ? 'public' : 'organization'),
  abstract: props.layer.abstract || '',
  keywords: props.layer.keywords || '',
  license: props.layer.license || '',
  attribution: props.layer.attribution || '',
})
const saving = ref(false)
const error = ref('')
const stacUrl = computed(() => `${location.origin}/api/stac`)

async function save() {
  saving.value = true
  error.value = ''
  try {
    const fn = props.layerType === 'raster' ? setRasterSharing : setVectorSharing
    const { data } = await fn(props.layer.id, {
      visibility: form.visibility,
      abstract: form.abstract || null,
      keywords: form.keywords || null,
      license: form.license || null,
      attribution: form.attribution || null,
    })
    Object.assign(props.layer, data)
    emit('close')
  } catch (e) {
    error.value = 'Could not save — try again.'
  } finally {
    saving.value = false
  }
}
</script>
