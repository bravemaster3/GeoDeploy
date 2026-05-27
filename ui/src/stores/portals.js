import { defineStore } from 'pinia'
import { ref } from 'vue'
import { listPortals, createPortal, updatePortal, publishPortal, unpublishPortal, deletePortal } from '@/api'

export const usePortalsStore = defineStore('portals', () => {
  const portals = ref([])
  const loading = ref(false)

  async function refresh() {
    loading.value = true
    try {
      const { data } = await listPortals()
      portals.value = data
    } finally {
      loading.value = false
    }
  }

  async function create(payload) {
    const { data } = await createPortal(payload)
    portals.value.unshift(data)
    return data
  }

  async function update(id, payload) {
    const { data } = await updatePortal(id, payload)
    const idx = portals.value.findIndex(p => p.id === id)
    if (idx !== -1) portals.value[idx] = data
    return data
  }

  async function publish(id) {
    const { data } = await publishPortal(id)
    const idx = portals.value.findIndex(p => p.id === id)
    if (idx !== -1) portals.value[idx] = data
    return data
  }

  async function unpublish(id) {
    const { data } = await unpublishPortal(id)
    const idx = portals.value.findIndex(p => p.id === id)
    if (idx !== -1) portals.value[idx] = data
    return data
  }

  async function remove(id) {
    await deletePortal(id)
    portals.value = portals.value.filter(p => p.id !== id)
  }

  return { portals, loading, refresh, create, update, publish, unpublish, remove }
})
