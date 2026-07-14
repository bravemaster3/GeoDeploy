import { ref, onMounted, onUnmounted } from 'vue'
import maplibregl from 'maplibre-gl'
import { Protocol } from 'pmtiles'

// Register the pmtiles:// protocol once so MapLibre can read PMTiles archives (GeoParquet display).
// addProtocol is global on the maplibregl module, so a single registration covers every map.
if (!maplibregl.__pmtilesRegistered) {
  maplibregl.addProtocol('pmtiles', new Protocol().tile)
  maplibregl.__pmtilesRegistered = true
}

export function useMaplibre(containerId, initialStyle = null) {
  const map = ref(null)
  const loaded = ref(false)
  let globeCtrl = null
  let navCtrl = null

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

    if (maplibregl.GlobeControl) { globeCtrl = new maplibregl.GlobeControl(); map.value.addControl(globeCtrl, 'top-right') }
    navCtrl = new maplibregl.NavigationControl()
    map.value.addControl(navCtrl, 'top-right')
    map.value.on('load', () => (loaded.value = true))
  })

  // Add a control to the TOP of the top-right stack (above the globe/zoom controls). MapLibre only
  // appends within a corner, so we remove the globe/nav controls and re-add them after `control`.
  function addTopRightControlFirst(control) {
    if (!map.value) return
    if (globeCtrl) { map.value.removeControl(globeCtrl); globeCtrl = null }
    if (navCtrl) { map.value.removeControl(navCtrl); navCtrl = null }
    map.value.addControl(control, 'top-right')
    if (maplibregl.GlobeControl) { globeCtrl = new maplibregl.GlobeControl(); map.value.addControl(globeCtrl, 'top-right') }
    navCtrl = new maplibregl.NavigationControl()
    map.value.addControl(navCtrl, 'top-right')
  }

  onUnmounted(() => {
    map.value?.remove()
  })

  function applyStyle(style) {
    if (!map.value || !loaded.value) return
    map.value.setStyle(style)
  }

  function jumpTo(view) {
    if (!map.value || !view || !Array.isArray(view.center)) return
    try {
      map.value.jumpTo({
        center: view.center,
        zoom: view.zoom != null ? view.zoom : 2,
        bearing: view.bearing || 0,
        pitch: view.pitch || 0,
      })
    } catch { /* keep current view */ }
  }

  function fitToBbox(bbox) {
    if (!map.value || !bbox) return
    // Guard against non-lon/lat bboxes (e.g. a projected raster bbox) so a bad
    // value can't throw "Invalid LngLat" and break the preview.
    const valid = Array.isArray(bbox) && bbox.length === 4 &&
      bbox[0] >= -180 && bbox[2] <= 180 && bbox[0] < bbox[2] &&
      bbox[1] >= -90 && bbox[3] <= 90 && bbox[1] < bbox[3]
    if (!valid) return
    try {
      map.value.fitBounds([[bbox[0], bbox[1]], [bbox[2], bbox[3]]], { padding: 40 })
    } catch { /* keep current view */ }
  }

  return { map, loaded, applyStyle, fitToBbox, jumpTo, addTopRightControlFirst }
}
