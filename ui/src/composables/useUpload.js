import { ref } from 'vue'
import { uploadVectorFile, uploadRasterFile } from '@/api'
import { useDataStore } from '@/stores/data'

export function useUpload() {
  const uploading = ref(false)
  const uploadProgress = ref(0)
  const error = ref(null)
  const dataStore = useDataStore()

  async function uploadFile(file, type) {
    uploading.value = true
    uploadProgress.value = 0
    error.value = null

    try {
      const uploadFn = type === 'vector' ? uploadVectorFile : uploadRasterFile
      const { data: job } = await uploadFn(file, (p) => (uploadProgress.value = p))

      // Add an optimistic "processing" entry to the store
      const layer = { id: job.layer_id, name: file.name, status: 'processing', _job: job }
      if (type === 'vector') dataStore.vectorLayers.unshift(layer)
      else dataStore.rasterLayers.unshift(layer)

      // Poll in background — don't await
      dataStore.pollJob(job.id, type, job.layer_id).catch(() => {})
      return job
    } catch (err) {
      error.value = err.response?.data?.detail || err.message
      throw err
    } finally {
      uploading.value = false
    }
  }

  return { uploading, uploadProgress, error, uploadFile }
}
