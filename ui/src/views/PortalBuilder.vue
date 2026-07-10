<template>
  <div class="p-6 space-y-6">
    <div class="flex items-center justify-between">
      <h1 class="text-xl font-semibold">Portals</h1>
      <button @click="showCreate = true" class="btn-primary">
        <PlusIcon class="w-4 h-4" /> New portal
      </button>
    </div>

    <div v-if="!portalsStore.portals.length" class="card p-12 text-center">
      <GlobeIcon class="w-10 h-10 text-muted-foreground/40 mx-auto mb-3" />
      <p class="text-muted-foreground text-sm">No portals yet.</p>
      <button @click="showCreate = true" class="btn-primary mt-4 mx-auto">Create your first portal</button>
    </div>

    <div v-else class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
      <PortalCard
        v-for="portal in portalsStore.portals"
        :key="portal.id"
        :portal="portal"
        @edit="$router.push(`/portals/${portal.id}/edit`)"
        @publish="handlePublish(portal)"
        @unpublish="portalsStore.unpublish(portal.id)"
        @delete="portalsStore.remove(portal.id)"
      />
    </div>

    <CreatePortalModal v-if="showCreate" @close="showCreate = false" @created="showCreate = false" />
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { usePortalsStore } from '@/stores/portals'
import { PlusIcon, GlobeIcon } from './icons'
import PortalCard from '@/components/portal/PortalCard.vue'
import CreatePortalModal from '@/components/portal/CreatePortalModal.vue'

const portalsStore = usePortalsStore()
const showCreate = ref(false)

onMounted(() => portalsStore.refresh())

async function handlePublish(portal) {
  try {
    await portalsStore.publish(portal.id)
  } catch (err) {
    alert(err.response?.data?.detail || err.message)
  }
}
</script>
