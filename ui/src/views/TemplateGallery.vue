<template>
  <div class="p-6 space-y-6">
    <h1 class="text-xl font-semibold">Template Gallery</h1>

    <div v-if="loading" class="text-sm text-muted-foreground/70">Loading templates…</div>
    <div v-else-if="!templates.length" class="text-sm text-muted-foreground/70">No templates found.</div>

    <div v-else class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-5">
      <div v-for="t in templates" :key="t.id" class="card overflow-hidden">
        <!-- A template's preview is a MOCK drawn from its own palette + archetype, not a screenshot:
             it can never go stale when the theme changes, costs no image to ship, and shows the two
             things that actually differ between templates — the colours and the layout. A real
             preview image is used instead when a template ships one. -->
        <div class="h-36 relative overflow-hidden">
          <img v-if="t.preview_url" :src="t.preview_url" class="w-full h-full object-cover" loading="lazy" />
          <div v-else class="w-full h-full flex flex-col p-2.5 gap-1.5"
            :style="{ background: t.bg || '#0b1220' }">
            <!-- header bar -->
            <div class="h-3.5 rounded-sm flex items-center px-1.5 gap-1 flex-shrink-0"
              :style="{ background: t.accent || '#38bdf8' }">
              <span class="w-1 h-1 rounded-full bg-white/80"></span>
              <span class="h-1 w-8 rounded-full bg-white/60"></span>
            </div>
            <div class="flex-1 flex gap-1.5 min-h-0" :class="archetypeOf(t) === 'storymap' ? 'flex-row' : ''">
              <!-- side column: layer list, facet rail, or narrative column -->
              <div class="rounded-sm flex flex-col gap-1 p-1 flex-shrink-0"
                :class="archetypeOf(t) === 'storymap' ? 'w-1/3' : 'w-1/4'"
                :style="{ background: mix(t) }">
                <span v-for="n in 4" :key="n" class="h-1 rounded-full"
                  :style="{ background: t.accent || '#38bdf8', opacity: .55, width: (90 - n * 12) + '%' }"></span>
              </div>
              <!-- map area, with a hint of data on it -->
              <div class="flex-1 rounded-sm relative overflow-hidden" :style="{ background: mix(t, .55) }">
                <span class="absolute rounded-full" :style="{ background: t.accent || '#38bdf8',
                  opacity: .8, width: '8px', height: '8px', left: '22%', top: '38%' }"></span>
                <span class="absolute rounded-full" :style="{ background: t.accent || '#38bdf8',
                  opacity: .5, width: '14px', height: '14px', left: '52%', top: '55%' }"></span>
                <span class="absolute rounded-full" :style="{ background: t.accent || '#38bdf8',
                  opacity: .65, width: '6px', height: '6px', left: '72%', top: '28%' }"></span>
              </div>
            </div>
          </div>
          <span v-if="archetypeOf(t) !== 'webmap'"
            class="absolute bottom-1.5 right-1.5 text-[9px] uppercase tracking-wider px-1.5 py-0.5 rounded bg-black/55 text-white/90">
            {{ archetypeOf(t) }}
          </span>
        </div>
        <div class="p-4 space-y-2">
          <div class="flex items-start justify-between gap-2">
            <h3 class="font-semibold text-sm">{{ t.name }}</h3>
            <span v-if="t.is_official" class="text-xs bg-primary/15 text-primary px-1.5 py-0.5 rounded font-medium flex-shrink-0">Official</span>
          </div>
          <p class="text-xs text-muted-foreground line-clamp-2">{{ t.description }}</p>
          <div class="flex flex-wrap gap-1">
            <span v-for="tag in t.tags" :key="tag" class="text-xs bg-muted text-muted-foreground px-1.5 py-0.5 rounded">{{ tag }}</span>
          </div>
          <div class="text-xs text-muted-foreground/70 flex gap-2">
            <span>by {{ t.author }}</span>
            <span>· {{ t.language.toUpperCase() }}</span>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { listTemplates } from '@/api'

// The mock needs a surface tone BETWEEN the template's background and its accent — computed rather
// than hard-coded so a light template (cream paper) and a dark one (satellite) both read correctly.
function mix(t, amount = 0.35) {
  const bg = t.bg || '#0b1220'
  const accent = t.accent || '#38bdf8'
  return `color-mix(in srgb, ${accent} ${Math.round(amount * 22)}%, ${bg})`
}
// Templates predating the archetype field are plain web maps.
const archetypeOf = (t) => t.archetype || 'webmap'

const templates = ref([])
const loading = ref(true)

onMounted(async () => {
  const { data } = await listTemplates()
  templates.value = data
  loading.value = false
})
</script>
