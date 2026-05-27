import { ref, onMounted, onUnmounted } from 'vue'
import maplibregl from 'maplibre-gl'

export function useMaplibre(containerId, initialStyle = null) {
  const map = ref(null)
  const loaded = ref(false)

  const defaultStyle = {
    version: 8,
    sources: {
      osm: {
        type: 'raster',
        tiles: ['https://tile.openstreetmap.org/{z}/{x}/{y}.png'],
        tileSize: 256,
        attribution: '© OpenStreetMap contributors',
      },
    },
    layers: [{ id: 'osm', type: 'raster', source: 'osm' }],
  }

  onMounted(() => {
    map.value = new maplibregl.Map({
      container: containerId,
      style: initialStyle || defaultStyle,
      center: [0, 20],
      zoom: 2,
    })

    map.value.addControl(new maplibregl.NavigationControl(), 'top-right')
    map.value.on('load', () => (loaded.value = true))
  })

  onUnmounted(() => {
    map.value?.remove()
  })

  function applyStyle(style) {
    if (!map.value || !loaded.value) return
    map.value.setStyle(style)
  }

  function fitToBbox(bbox) {
    if (!map.value || !bbox) return
    map.value.fitBounds([[bbox[0], bbox[1]], [bbox[2], bbox[3]]], { padding: 40 })
  }

  return { map, loaded, applyStyle, fitToBbox }
}
