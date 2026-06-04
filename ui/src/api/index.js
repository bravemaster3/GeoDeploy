import axios from 'axios'

const api = axios.create({ baseURL: '/api' })

api.interceptors.request.use((config) => {
  const token = localStorage.getItem('geodeploy_token')
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

api.interceptors.response.use(
  (r) => r,
  (err) => {
    if (err.response?.status === 401 && window.location.pathname !== '/login') {
      localStorage.removeItem('geodeploy_token')
      window.location.href = '/login'
    }
    return Promise.reject(err)
  }
)

export default api

// Setup
export const getSetupStatus = () => api.get('/setup/status')
export const configureDB = (data) => api.post('/setup/configure-db', data)
export const configureStorage = (data) => api.post('/setup/configure-storage', data)
export const createAdmin = (data) => api.post('/setup/create-admin', data)

// Auth
export const login = (email, password) =>
  api.post('/auth/login', new URLSearchParams({ username: email, password }), {
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
  })
export const getMe = () => api.get('/auth/me')

// Vector layers
export const listVectorLayers = () => api.get('/data/vector')
export const uploadVectorFile = (file, onProgress) => {
  const form = new FormData()
  form.append('file', file)
  return api.post('/data/vector/upload', form, {
    onUploadProgress: (e) => onProgress?.(Math.round((e.loaded * 100) / e.total)),
  })
}
export const uploadCsvFile = (file, params, onProgress) => {
  const form = new FormData()
  form.append('file', file)
  form.append('x_column', params.x_column)
  form.append('y_column', params.y_column)
  form.append('srid', params.srid ?? 4326)
  form.append('delimiter', params.delimiter ?? 'comma')
  if (params.name) form.append('name', params.name)
  return api.post('/data/vector/upload-csv', form, {
    onUploadProgress: (e) => onProgress?.(Math.round((e.loaded * 100) / e.total)),
  })
}
// GeoParquet: presigned DIRECT-to-storage upload (browser → MinIO), then register + inspect.
export const presignGeoParquet = (data) => api.post('/data/vector/geoparquet/presign', data)
export const completeGeoParquet = (data) => api.post('/data/vector/geoparquet/complete', data)
// Raw axios (NOT the `api` instance): no /api baseURL and no JWT header, which would otherwise
// clash with the presigned request's own auth. The URL is same-origin (/s3/...) for local MinIO.
export const putFileToUrl = (url, file, onProgress) =>
  axios.put(url, file, {
    headers: { 'Content-Type': 'application/octet-stream' },
    onUploadProgress: (e) => onProgress?.(Math.round((e.loaded * 100) / (e.total || e.loaded || 1))),
  })

export const getVectorJobStatus = (jobId) => api.get(`/data/vector/jobs/${jobId}`)
// Viewport query for a GeoParquet (file-backed) layer → GeoJSON in EPSG:4326 (rendered by deck.gl).
export const getVectorFeatures = (id, bbox, limit = 50000) =>
  api.get(`/data/vector/${id}/features`, { params: { bbox, limit } })
export const saveVectorDefaultStyle = (id, style) => api.put(`/data/vector/${id}/default-style`, style)
export const deleteVectorLayer = (id) => api.delete(`/data/vector/${id}`)

// Raster layers
export const listRasterLayers = () => api.get('/data/raster')
export const uploadRasterFile = (file, onProgress) => {
  const form = new FormData()
  form.append('file', file)
  return api.post('/data/raster/upload', form, {
    onUploadProgress: (e) => onProgress?.(Math.round((e.loaded * 100) / e.total)),
  })
}
export const getRasterJobStatus = (jobId) => api.get(`/data/raster/jobs/${jobId}`)
export const saveRasterDefaultStyle = (id, style) => api.put(`/data/raster/${id}/default-style`, style)
export const deleteRasterLayer = (id) => api.delete(`/data/raster/${id}`)
export const listColormaps = () => api.get('/data/raster/colormaps')
export const getRasterStats = (id) => api.get(`/data/raster/${id}/stats`)

// Discover + import data already in the connected DB / storage (registers catalog entries, no copy)
export const discoverDatabase = () => api.get('/data/discover/database')
export const importDatabase = (tables) => api.post('/data/discover/database', { tables })
export const discoverStorage = () => api.get('/data/discover/storage')
export const importStorage = (items) => api.post('/data/discover/storage', { items })
export const getCsvColumns = (key, delimiter = 'comma') => api.get('/data/discover/storage/csv-columns', { params: { key, delimiter } })
export const importCsv = (data) => api.post('/data/discover/storage/csv', data)

// External sources (WMS / XYZ raster, WFS vector — displayed without ingesting)
export const listExternalSources = () => api.get('/data/sources')
export const createExternalSource = (data) => api.post('/data/sources', data)
export const deleteExternalSource = (id) => api.delete(`/data/sources/${id}`)

// Portals
export const listPortals = () => api.get('/portals')
export const createPortal = (data) => api.post('/portals', data)
export const updatePortal = (id, data) => api.put(`/portals/${id}`, data)
export const publishPortal = (id) => api.post(`/portals/${id}/publish`)
export const unpublishPortal = (id) => api.post(`/portals/${id}/unpublish`)
export const deletePortal = (id) => api.delete(`/portals/${id}`)

// Templates
export const listTemplates = () => api.get('/templates')

// Admin
export const getServiceHealth = () => api.get('/admin/health')
export const getStorageStats = () => api.get('/admin/storage-stats')
export const controlService = (name, action) => api.post(`/admin/services/${name}/${action}`)
