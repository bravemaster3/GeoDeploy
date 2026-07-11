import { ref } from 'vue'
import {
  uploadVectorFile, uploadRasterFile,
  presignGeoParquet, completeGeoParquet, putFileToUrl,
  presignLargeVector, completeLargeVector,
} from '@/api'
import { useDataStore } from '@/stores/data'

// Files at or above this size can't be POSTed through the API (its multipart cap); they upload
// direct-to-storage and convert to GeoParquet in the background. Matches the API's MAX_FILE_SIZE.
export const LARGE_UPLOAD_THRESHOLD = 2 * 1024 * 1024 * 1024  // 2 GB

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

  // GeoParquet: presign → PUT straight to storage → register + queue inspection. The file never
  // passes through the API, so multi-GB uploads don't tie up the API process or its disk.
  async function uploadGeoParquet(file, name) {
    uploading.value = true
    uploadProgress.value = 0
    error.value = null
    try {
      const { data: pre } = await presignGeoParquet({ filename: file.name, name, file_size: file.size })
      await putFileToUrl(pre.upload_url, file, (p) => (uploadProgress.value = p))
      const { data: job } = await completeGeoParquet({ s3_key: pre.s3_key, name, file_size: file.size })

      dataStore.vectorLayers.unshift({ id: job.layer_id, name: name || file.name, status: 'processing', _job: job })
      dataStore.pollJob(job.id, 'vector', job.layer_id).catch(() => {})
      return job
    } catch (err) {
      error.value = err.response?.data?.detail || err.message
      throw err
    } finally {
      uploading.value = false
    }
  }

  // Large vector file (over the API cap): presign → PUT straight to storage → register + queue the
  // background GeoParquet conversion. `csvOpts` (x/y or wkt column + srid + delimiter) is only used
  // for CSV. Same no-API-passthrough benefit as GeoParquet uploads.
  async function uploadLargeVector(file, name, csvOpts) {
    uploading.value = true
    uploadProgress.value = 0
    error.value = null
    try {
      const { data: pre } = await presignLargeVector({ filename: file.name, name, file_size: file.size })
      await putFileToUrl(pre.upload_url, file, (p) => (uploadProgress.value = p))
      const { data: job } = await completeLargeVector({
        s3_key: pre.s3_key, name, file_size: file.size, ...(csvOpts || {}),
      })
      dataStore.vectorLayers.unshift({ id: job.layer_id, name: name || file.name, status: 'processing', _job: job })
      dataStore.pollJob(job.id, 'vector', job.layer_id).catch(() => {})
      return job
    } catch (err) {
      error.value = err.response?.data?.detail || err.message
      throw err
    } finally {
      uploading.value = false
    }
  }

  return { uploading, uploadProgress, error, uploadFile, uploadGeoParquet, uploadLargeVector }
}
