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
        @publish="handlePublish(portal)"
        @unpublish="portalsStore.unpublish(portal.id)"
        @delete="portalsStore.remove(portal.id)"
        @visibility="portalsStore.update(portal.id, { visibility: $event })"
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

const auth = useAuthStore()
const portalsStore = usePortalsStore()
const showCreate = ref(false)

const creatorFilter = ref('')
const creators = computed(() =>
  [...new Set(portalsStore.portals.map((p) => p.created_by).filter(Boolean))].sort())
const filteredPortals = computed(() =>
  portalsStore.portals.filter((p) => !creatorFilter.value || p.created_by === creatorFilter.value))

onMounted(() => portalsStore.refresh())

async function handlePublish(portal) {
  try {
    await portalsStore.publish(portal.id)
  } catch (err) {
    alert(err.response?.data?.detail || err.message)
  }
}
</script>
