import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { resolve } from 'path'

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: { '@': resolve(__dirname, 'src') },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': { target: 'http://geodeploy-api:8000', changeOrigin: true },
      '/portals': { target: 'http://geodeploy-api:8000', changeOrigin: true },
      '/tiles': { target: 'http://martin:3000', changeOrigin: true, rewrite: (p) => p.replace(/^\/tiles/, '') },
      '/raster': { target: 'http://titiler:80', changeOrigin: true, rewrite: (p) => p.replace(/^\/raster/, '') },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
  },
})
