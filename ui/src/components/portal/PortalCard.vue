<template>
  <div class="card overflow-hidden group">
    <!-- A snapshot of the actual published map, captured in the editor at publish time. The gradient
         stays as the fallback for portals published before this existed, drafts, and captures that
         failed — the card must always render something. -->
    <div class="h-28 bg-gradient-to-br from-brand-700 to-brand-500 relative overflow-hidden">
      <img v-if="portal.thumbnail_url" :src="portal.thumbnail_url" alt=""
        class="absolute inset-0 w-full h-full object-cover" loading="lazy" @error="thumbFailed = true"
        v-show="!thumbFailed" />
      <div v-if="!portal.thumbnail_url || thumbFailed" class="absolute inset-0 flex items-center justify-center">
        <GlobeIcon class="w-10 h-10 text-white/40" />
      </div>
      <!-- Keeps the "Live" pill legible over an arbitrary map image. -->
      <div v-if="portal.thumbnail_url && !thumbFailed"
        class="absolute inset-x-0 top-0 h-10 bg-gradient-to-b from-black/45 to-transparent"></div>
      <!-- The capture mounts an off-screen preview and waits for the map to settle, which takes a
           few seconds. Saying so beats a card that looks like it failed and then changes by itself. -->
      <div v-if="capturing"
        class="absolute inset-0 flex items-center justify-center gap-2 bg-black/45 text-white text-xs">
        <span class="animate-spin">⟳</span> Capturing preview…
      </div>
      <div v-if="portal.published" class="absolute top-2 right-2">
        <span class="text-xs bg-green-500 text-white px-2 py-0.5 rounded-full font-medium">Live</span>
      </div>
    </div>

    <div class="p-4 space-y-3">
      <div>
        <h3 class="font-semibold text-foreground truncate">{{ portal.title }}</h3>
        <p v-if="summary" class="text-xs text-muted-foreground mt-0.5 line-clamp-2">{{ summary }}</p>
      </div>

      <div class="text-xs text-muted-foreground/70 flex gap-3 flex-wrap items-center">
        <span>{{ portal.layer_configs?.length || 0 }} layer{{ portal.layer_configs?.length !== 1 ? 's' : '' }}</span>
        <span>{{ portal.template_id }}</span>
        <span v-if="portal.created_by">by {{ portal.created_by }}</span>
      </div>

      <!-- Published access tier (who can VIEW the live portal), read-only — changed in the editor. -->
      <div class="flex items-center">
        <span class="inline-flex items-center gap-1.5 text-[11px] px-2 py-0.5 rounded-full" :class="access.class">
          <component :is="access.icon" class="w-3 h-3" />
          {{ access.label }}
        </span>
      </div>

      <div class="flex gap-2 pt-1">
        <button v-if="auth.canEdit" @click="$emit('edit')" class="btn-secondary flex-1 justify-center text-xs py-1.5">Edit</button>
        <button v-if="!portal.published && auth.canEdit" @click="$emit('publish')" class="btn-primary flex-1 justify-center text-xs py-1.5">Publish</button>
        <template v-if="portal.published">
          <a :href="`/portals/${portal.slug}/`" target="_blank" class="btn-primary flex-1 justify-center text-xs py-1.5 no-underline">
            <ExternalLinkIcon class="w-3 h-3" /> View
          </a>
          <button v-if="auth.canEdit" @click="$emit('unpublish')" class="btn-secondary px-2 text-xs py-1.5 text-muted-foreground" title="Unpublish">⊘</button>
        </template>
        <button v-if="auth.canEdit" @click="$emit('delete')" class="px-2 text-muted-foreground/70 hover:text-red-500 transition-colors" title="Delete">
          <TrashIcon class="w-4 h-4" />
        </button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { GlobeIcon, TrashIcon, ExternalLinkIcon, KeyIcon, UsersIcon, UserIcon } from '@/views/icons'
import { useAuthStore } from '@/stores/auth'

const props = defineProps({ portal: Object, capturing: Boolean })
defineEmits(['edit', 'publish', 'unpublish', 'delete'])

const auth = useAuthStore()

// A thumbnail can 404 (asset pruned, portal restored from a backup without its assets). Fall back to
// the gradient rather than leaving a broken image in the card.
const thumbFailed = ref(false)

// The portal description is MARKDOWN (written in the About editor), so printing it raw put things
// like "![clean-black-world-map…](…)" on the card. Reduced to a plain-text summary: images are
// dropped entirely (their alt text is not a description), links keep their label, and the remaining
// syntax markers are stripped. Not a full markdown parser on purpose — this is a two-line teaser,
// and rendering real markdown in a fixed-height card invites broken layout.
const summary = computed(() => {
  const md = props.portal?.description
  if (!md) return ''
  return String(md)
    .replace(/!\[[^\]]*\]\([^)]*\)/g, ' ')      // images — drop, alt text is not prose
    .replace(/\[([^\]]*)\]\([^)]*\)/g, '$1')    // links — keep the label
    .replace(/`{1,3}[^`]*`{1,3}/g, ' ')          // code spans/fences
    .replace(/^\s{0,3}#{1,6}\s+/gm, '')           // headings
    .replace(/^\s{0,3}>\s?/gm, '')                // blockquotes
    .replace(/^\s{0,3}([-*+]|\d+\.)\s+/gm, '')   // list markers
    .replace(/[*_~]{1,3}/g, '')                  // emphasis
    .replace(/\s+/g, ' ')
    .trim()
})

// Published access tier → badge. 'private' is the legacy value for organization (members-only).
const ACCESS = {
  public: { label: 'Public', icon: GlobeIcon, class: 'bg-emerald-500/15 text-emerald-400' },
  password: { label: 'Password', icon: KeyIcon, class: 'bg-violet-500/15 text-violet-400' },
  organization: { label: 'Organization', icon: UsersIcon, class: 'bg-sky-500/15 text-sky-400' },
  private: { label: 'Organization', icon: UsersIcon, class: 'bg-sky-500/15 text-sky-400' },
  owner: { label: 'Private', icon: UserIcon, class: 'bg-amber-500/15 text-amber-400' },
}
const access = computed(() => ACCESS[props.portal.access_type] || ACCESS.public)
</script>
