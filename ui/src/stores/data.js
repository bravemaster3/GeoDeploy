import { defineStore } from 'pinia'
import { ref } from 'vue'
import {
  listVectorLayers, listRasterLayers, listExternalSources,
  deleteVectorLayer, deleteRasterLayer, deleteExternalSource,
  getVectorJobStatus, getRasterJobStatus,
} from '@/api'

export const useDataStore = defineStore('data', () => {
  const vectorLayers = ref([])
  const rasterLayers = ref([])
  const externalSources = ref([])
  const loading = ref(false)

  async function refresh() {
    loading.value = true
    try {
      const [v, r, e] = await Promise.all([listVectorLayers(), listRasterLayers(), listExternalSources()])
      vectorLayers.value = v.data
      rasterLayers.value = r.data
      externalSources.value = e.data
    } finally {
      loading.value = false
    }
    // Safety net so the list advances processing → ready on its own even if a per-job poll never
    // ran or died (page reloaded, transient API blip during a long convert/prep job, etc.).
    watchProcessing()
  }

  const isBusy = (l) => l.status === 'processing' || l.status === 'queued'
  let processingTimer = null

  // While any layer is still processing, re-fetch the whole list periodically. This is independent
  // of pollJob (which tracks a single upload's %-progress) — it guarantees the UI reflects the real
  // server state without a manual page refresh, and it resumes automatically after a reload.
  function watchProcessing() {
    if (processingTimer) return
    const anyBusy = () => vectorLayers.value.some(isBusy) || rasterLayers.value.some(isBusy)
    if (!anyBusy()) return
    processingTimer = setInterval(async () => {
      try { await refresh() } catch { /* keep trying — transient errors shouldn't stop the watch */ }
      if (!anyBusy()) { clearInterval(processingTimer); processingTimer = null }
    }, 3000)
  }

  async function pollJob(jobId, type, layerId) {
    const poll = type === 'vector' ? getVectorJobStatus : getRasterJobStatus
    return new Promise((resolve, reject) => {
      let fails = 0
      const interval = setInterval(async () => {
        let job
        try {
          job = (await poll(jobId)).data
          fails = 0
        } catch (err) {
          // Tolerate transient failures (a deploy, a network blip): a multi-minute convert/prep
          // job must not freeze its progress bar permanently because of one failed poll. Give up
          // only after several consecutive failures.
          if (++fails < 8) return
          clearInterval(interval)
          reject(err)
          return
        }
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

  function addExternal(source) {
    externalSources.value = [source, ...externalSources.value]
  }

  async function removeExternal(id) {
    await deleteExternalSource(id)
    externalSources.value = externalSources.value.filter(s => s.id !== id)
  }

  return {
    vectorLayers, rasterLayers, externalSources, loading,
    refresh, pollJob, removeVector, removeRaster, addExternal, removeExternal,
  }
})
