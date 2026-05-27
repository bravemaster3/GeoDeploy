import { defineStore } from 'pinia'
import { ref } from 'vue'
import { getServiceHealth, getStorageStats } from '@/api'

export const useSystemStore = defineStore('system', () => {
  const health = ref([])
  const stats = ref(null)

  async function refreshHealth() {
    const { data } = await getServiceHealth()
    health.value = data
  }

  async function refreshStats() {
    const { data } = await getStorageStats()
    stats.value = data
  }

  return { health, stats, refreshHealth, refreshStats }
})
