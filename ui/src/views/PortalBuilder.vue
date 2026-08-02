<template>
  <div class="p-6 space-y-6">
    <div class="flex items-center justify-between">
      <h1 class="text-xl font-semibold">Portals</h1>
      <div class="flex items-center gap-2">
        <!-- Creator filter (shared workspace) — admins review a member's portals in bulk -->
        <select v-if="creators.length > 1" v-model="creatorFilter"
          class="text-xs bg-background text-foreground border border-border rounded-lg px-2.5 py-2 focus:outline-none focus:ring-1 focus:ring-primary/60">
          <option value="">Everyone</option>
          <option v-for="c in creators" :key="c" :value="c">{{ c }}</option>
        </select>
        <button v-if="auth.canEdit" @click="showCreate = true" class="btn-primary">
          <PlusIcon class="w-4 h-4" /> New portal
        </button>
      </div>
    </div>

    <div v-if="!portalsStore.portals.length" class="card p-12 text-center">
      <GlobeIcon class="w-10 h-10 text-muted-foreground/40 mx-auto mb-3" />
      <p class="text-muted-foreground text-sm">No portals yet.</p>
      <button v-if="auth.canEdit" @click="showCreate = true" class="btn-primary mt-4 mx-auto">Create your first portal</button>
    </div>

    <div v-else class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
      <PortalCard
        v-for="portal in filteredPortals"
        :key="portal.id"
        :portal="portal"
        @edit="$router.push(`/portals/${portal.id}/edit`)"
        :capturing="capturingId === portal.id"
        @publish="handlePublish(portal)"
        @recapture="handleRecapture(portal)"
        @unpublish="portalsStore.unpublish(portal.id)"
        @delete="portalsStore.remove(portal.id)"
      />
    </div>

    <CreatePortalModal v-if="showCreate" @close="showCreate = false" @created="showCreate = false" />
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useAuthStore } from '@/stores/auth'
import { usePortalsStore } from '@/stores/portals'
import { PlusIcon, GlobeIcon } from './icons'
import PortalCard from '@/components/portal/PortalCard.vue'
import CreatePortalModal from '@/components/portal/CreatePortalModal.vue'
import { capturePortalThumbnail } from '@/composables/portalThumbnail'

const auth = useAuthStore()
const portalsStore = usePortalsStore()
const showCreate = ref(false)
const capturingId = ref(null)   // portal whose card image is being taken

const creatorFilter = ref('')
const creators = computed(() =>
  [...new Set(portalsStore.portals.map((p) => p.created_by).filter(Boolean))].sort())
const filteredPortals = computed(() =>
  portalsStore.portals.filter((p) => !creatorFilter.value || p.created_by === creatorFilter.value))

onMounted(() => portalsStore.refresh())

// Shared by publish-from-list and the explicit "refresh card image" button.
async function grabThumbnail(portal) {
  capturingId.value = portal.id
  try {
    const { url, error } = await capturePortalThumbnail(
      portal.id, { hasExisting: !!portal.thumbnail_url })
    if (url) {
      const list = portalsStore.portals
      const idx = list.findIndex(p => p.id === portal.id)
      // Cache-busting is unnecessary: the endpoint mints a NEW filename per capture, precisely so a
      // replaced thumbnail is never served from a stale cache entry.
      if (idx !== -1) list[idx] = { ...list[idx], thumbnail_url: url }
    } else {
      // The REASON, verbatim. "Could not capture a preview" told the operator nothing they could
      // act on and sent them to the editor, where the same failure was waiting.
      alert(['Could not capture a preview for this portal:', '', error].join('\n'))
    }
  } finally {
    capturingId.value = null
  }
}

async function handleRecapture(portal) {
  await grabThumbnail(portal)
}

async function handlePublish(portal) {
  try {
    await portalsStore.publish(portal.id)
  } catch (err) {
    alert(err.response?.data?.detail || err.message)
    return
  }
  // Capture the card image here too. The EDITOR captures from its live preview iframe, but a portal
  // published from this list never passed through the editor, so it kept the gradient placeholder
  // forever — indistinguishable from thumbnails being broken. Not awaited into the publish result:
  // publishing is done, and a picture is decoration.
  await grabThumbnail(portal)
}
</script>
