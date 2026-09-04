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
    // `visualizePitch` turns the compass into a TILT control: dragging it pitches the map,
    // and it shows the current pitch rather than only the bearing. Without it a 3D layer —
    // an extrusion, a 2.5D style — could only be seen flat unless the viewer happened to
    // know that right-drag tilts, which is not a thing anyone knows.
    navCtrl = new maplibregl.NavigationControl({ visualizePitch: true })
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
    navCtrl = new maplibregl.NavigationControl({ visualizePitch: true })
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


  /** MapLibre's own fullscreen button. The element it expands is the map's CONTAINER, so the
   *  legend and any overlay inside it come along — expanding the canvas alone would leave the
   *  legend behind on the page. */
  function addFullscreen() {
    if (!map.value || !maplibregl.FullscreenControl) return
    const el = document.getElementById(containerId)
    map.value.addControl(
      new maplibregl.FullscreenControl(el ? { container: el.parentElement || el } : {}),
      'top-right')
  }

  /** A "zoom to the data" button. MapLibre has no such control, and it is the one thing a viewer
   *  wants most on a layer they have panned away from — or on one that never came into view,
   *  where the map is otherwise empty with no clue whether the data is missing or elsewhere. */
  function addZoomToExtent(getBbox, title = 'Zoom to the layer') {
    if (!map.value) return
    const control = {
      onAdd() {
        const wrap = document.createElement('div')
        wrap.className = 'maplibregl-ctrl maplibregl-ctrl-group'
        const btn = document.createElement('button')
        btn.type = 'button'
        btn.title = title
        btn.setAttribute('aria-label', title)
        // An inline SVG rather than a glyph: the control group's own font is not ours to rely on,
        // and a missing glyph renders as a blank button that looks broken rather than absent.
        btn.innerHTML = '<span class="maplibregl-ctrl-icon" aria-hidden="true" '
          + 'style="display:flex;align-items:center;justify-content:center">'
          + '<svg width="15" height="15" viewBox="0 0 18 18" fill="none" stroke="currentColor" '
          + 'stroke-width="1.6" stroke-linecap="round">'
          + '<path d="M2 6V2h4M16 6V2h-4M2 12v4h4M16 12v4h-4"/>'
          + '<circle cx="9" cy="9" r="2.5"/></svg></span>'
        btn.addEventListener('click', () => fitToBbox(getBbox()))
        wrap.appendChild(btn)
        this._wrap = wrap
        return wrap
      },
      onRemove() { this._wrap?.parentNode?.removeChild(this._wrap) },
    }
    map.value.addControl(control, 'top-right')
  }

  return { map, loaded, applyStyle, fitToBbox, jumpTo, addTopRightControlFirst,
           addFullscreen, addZoomToExtent }
}
