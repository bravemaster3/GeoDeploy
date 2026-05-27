import { defineStore } from 'pinia'
import { ref } from 'vue'
import {
  listVectorLayers, listRasterLayers,
  deleteVectorLayer, deleteRasterLayer,
  getVectorJobStatus, getRasterJobStatus,
} from '@/api'

export const useDataStore = defineStore('data', () => {
  const vectorLayers = ref([])
  const rasterLayers = ref([])
  const loading = ref(false)

  async function refresh() {
    loading.value = true
    try {
      const [v, r] = await Promise.all([listVectorLayers(), listRasterLayers()])
      vectorLayers.value = v.data
      rasterLayers.value = r.data
    } finally {
      loading.value = false
    }
  }

  async function pollJob(jobId, type, layerId) {
    const poll = type === 'vector' ? getVectorJobStatus : getRasterJobStatus
    return new Promise((resolve, reject) => {
      const interval = setInterval(async () => {
        try {
          const { data: job } = await poll(jobId)
          // Update status in the layer list
          const list = type === 'vector' ? vectorLayers : rasterLayers
          const layer = list.value.find(l => l.id === layerId)
          if (layer) {
            layer.status = job.status
            layer._job = job
          }
          if (job.status === 'ready') {
            clearInterval(interval)
            await refresh()
            resolve(job)
          } else if (job.status === 'error') {
            clearInterval(interval)
            reject(new Error(job.error_message))
          }
        } catch (err) {
          clearInterval(interval)
          reject(err)
        }
      }, 2000)
    })
  }

  async function removeVector(id) {
    await deleteVectorLayer(id)
    vectorLayers.value = vectorLayers.value.filter(l => l.id !== id)
  }

  async function removeRaster(id) {
    await deleteRasterLayer(id)
    rasterLayers.value = rasterLayers.value.filter(l => l.id !== id)
  }

  return { vectorLayers, rasterLayers, loading, refresh, pollJob, removeVector, removeRaster }
})
