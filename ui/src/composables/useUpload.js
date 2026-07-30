import { ref } from 'vue'
import {
  uploadVectorFile, uploadRasterFile,
  presignGeoParquet, completeGeoParquet, putFileToUrl,
  presignLargeVector, completeLargeVector,
  multipartInitiate, multipartComplete, multipartAbort, putPartToUrl,
  rasterMultipartInitiate, rasterMultipartComplete, rasterMultipartAbort, completeLargeRaster,
} from '@/api'
import { useDataStore } from '@/stores/data'

// Files at or above this size bypass the API entirely (see the note on the constant); they upload
// direct-to-storage and convert to GeoParquet in the background. Matches the API's MAX_FILE_SIZE.
// 48 MB, NOT 2 GB. The old value was sized to uvicorn's multipart cap, but the binding limit is
// upstream: a reverse proxy/CDN in front of the instance caps the REQUEST BODY long before that —
// Cloudflare's free tier at 100 MB. A 300 MB GeoPackage POSTed through the API was therefore cut
// mid-body and surfaced as a bare "Network error" stuck at ~1%, with NOTHING in the API log
// because the request never arrived. (Reported 2026-07-30; it had been failing for a while.)
//
// Above this we go direct-to-storage in 48 MB presigned parts, so no single request ever
// approaches an edge limit, whatever the total file size. Matching CHUNK_THRESHOLD means every
// file over it takes the chunked path rather than one large PUT.
export const LARGE_UPLOAD_THRESHOLD = 48 * 1024 * 1024  // 48 MB

// Above this, the single-PUT direct upload gets reset behind a CDN (Cloudflare buffers/caps the
// body), so we switch to chunked multipart. Must stay <= the backend PART_SIZE (48 MB) so parts
// clear the ~100 MB CDN limit. Files under it keep the simpler single-PUT path.
const CHUNK_THRESHOLD = 48 * 1024 * 1024   // 48 MB
const CHUNK_CONCURRENCY = 4                // parts uploaded in parallel

// Upload a file as presigned multipart parts (initiate → PUT each part → complete). Returns the
// finished object's s3_key. `onProgress` gets a 0–100 percentage aggregated across all parts.
// `kind` selects BOTH the validation and the destination prefix: 'raster' routes to the raster
// endpoints (keys under rasters/), anything else to the vector ones (vectors/). Same part size and
// the same parallel-part loop either way — there is only one place that knows how to chunk.
async function uploadChunked(file, kind, onProgress) {
  const isRaster = kind === 'raster'
  const initiate = isRaster ? rasterMultipartInitiate : multipartInitiate
  const complete = isRaster ? rasterMultipartComplete : multipartComplete
  const abort = isRaster ? rasterMultipartAbort : multipartAbort
  const { data: init } = await initiate({ filename: file.name, file_size: file.size, kind })
  const { s3_key, upload_id, part_size, parts } = init
  const loaded = new Array(parts.length).fill(0)
  const results = new Array(parts.length)
  const report = () => onProgress?.(Math.round((loaded.reduce((a, b) => a + b, 0) * 100) / file.size))

  let next = 0
  async function worker() {
    while (next < parts.length) {
      const i = next++
      const { part_number, url } = parts[i]
      const start = (part_number - 1) * part_size
      const blob = file.slice(start, Math.min(start + part_size, file.size))
      const etag = await putPartToUrl(url, blob, (bytes) => { loaded[i] = bytes; report() })
      loaded[i] = blob.size; report()
      results[i] = { part_number, etag }
    }
  }
  try {
    await Promise.all(Array.from({ length: Math.min(CHUNK_CONCURRENCY, parts.length) }, worker))
    await complete({ s3_key, upload_id, parts: results })
    return s3_key
  } catch (err) {
    abort({ s3_key, upload_id }).catch(() => {})
    throw err
  }
}

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
      let s3Key
      if (file.size > CHUNK_THRESHOLD) {
        s3Key = await uploadChunked(file, 'geoparquet', (p) => (uploadProgress.value = p))
      } else {
        const { data: pre } = await presignGeoParquet({ filename: file.name, name, file_size: file.size })
        await putFileToUrl(pre.upload_url, file, (p) => (uploadProgress.value = p))
        s3Key = pre.s3_key
      }
      const { data: job } = await completeGeoParquet({ s3_key: s3Key, name, file_size: file.size })

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
  // Large GeoTIFF: chunked direct-to-storage, then register + queue the COG conversion. Without
  // this, anything over the CDN's request-body cap failed with a bare "Network error" and nothing
  // in the API log, because the upload never reached the API at all.
  async function uploadLargeRaster(file, name) {
    uploading.value = true
    uploadProgress.value = 0
    error.value = null
    try {
      const s3Key = await uploadChunked(file, 'raster', (p) => (uploadProgress.value = p))
      const { data: job } = await completeLargeRaster({
        s3_key: s3Key, name: name || file.name.replace(/\.[^.]+$/, ''), file_size: file.size,
      })
      dataStore.rasterLayers.unshift({
        id: job.layer_id, name: name || file.name, status: 'processing', _job: job })
      dataStore.pollJob(job.id, 'raster', job.layer_id).catch(() => {})
      return job
    } catch (err) {
      error.value = err.response?.data?.detail || err.message
      throw err
    } finally {
      uploading.value = false
    }
  }

  async function uploadLargeVector(file, name, csvOpts) {
    uploading.value = true
    uploadProgress.value = 0
    error.value = null
    try {
      let s3Key
      if (file.size > CHUNK_THRESHOLD) {
        s3Key = await uploadChunked(file, 'large', (p) => (uploadProgress.value = p))
      } else {
        const { data: pre } = await presignLargeVector({ filename: file.name, name, file_size: file.size })
        await putFileToUrl(pre.upload_url, file, (p) => (uploadProgress.value = p))
        s3Key = pre.s3_key
      }
      const { data: job } = await completeLargeVector({
        s3_key: s3Key, name, file_size: file.size, ...(csvOpts || {}),
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

  return { uploading, uploadProgress, error, uploadFile, uploadGeoParquet, uploadLargeVector, uploadLargeRaster }
}
