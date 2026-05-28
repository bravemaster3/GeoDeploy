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
export const getVectorJobStatus = (jobId) => api.get(`/data/vector/jobs/${jobId}`)
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
