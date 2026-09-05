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
  let fullscreenCleanup = null
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
    // The fullscreen listener is on `document`, so it outlives this component unless it is removed
    // — and it closes over a map that is about to be destroyed.
    if (fullscreenCleanup) { fullscreenCleanup(); fullscreenCleanup = null }
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


  /** MapLibre's own fullscreen button.
   *
   *  The element it expands is the map's CONTAINER, so the legend and any overlay inside it come
   *  along — expanding the canvas alone would leave the legend behind on the page.
   *
   *  AND THE CONTAINER HAS TO GROW. The map's height is a fixed `52vh` from its class, which the
   *  browser keeps honouring in fullscreen: the page went black around a map that stayed exactly
   *  the height it had been, which reads as fullscreen being broken rather than as CSS winning.
   *  Nothing in MapLibre resizes it — the fullscreen element is the app's, so the sizing is the
   *  app's problem. Forced inline while fullscreen and released on the way out, and `resize()` is
   *  called after each because MapLibre reads the canvas size once. */
  function addFullscreen() {
    if (!map.value || !maplibregl.FullscreenControl) return
    const el = document.getElementById(containerId)
    const target = el ? (el.parentElement || el) : null
    map.value.addControl(new maplibregl.FullscreenControl(target ? { container: target } : {}),
                         'top-right')
    if (!target || !el) return
    const onChange = () => {
      const full = document.fullscreenElement === target
      el.style.height = full ? '100vh' : ''
      el.style.maxHeight = full ? '100vh' : ''
      target.style.height = full ? '100vh' : ''
      // A tick later: the element is resized by the time MapLibre measures it.
      requestAnimationFrame(() => map.value && map.value.resize())
    }
    document.addEventListener('fullscreenchange', onChange)
    fullscreenCleanup = () => document.removeEventListener('fullscreenchange', onChange)
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
        // THE PORTAL'S OWN ZOOM-TO-ALL ICON, copied path for path from
        // `templates/shared/portal.js::zoomAllIcon`. A control that does the same thing on two
        // maps in the same product must not be drawn two different ways — a reader learns an icon
        // once. (That runtime is a standalone bundle the app cannot import, so this is matched by
        // hand, like `LegendSwatch` matches `legendSwatch`: change one, change the other.)
        btn.innerHTML = '<span class="maplibregl-ctrl-icon" aria-hidden="true" '
          + 'style="display:flex;align-items:center;justify-content:center">'
          + '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
          + 'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
          + '<path d="M3 8V5a2 2 0 0 1 2-2h3M16 3h3a2 2 0 0 1 2 2v3'
          + 'M21 16v3a2 2 0 0 1-2 2h-3M8 21H5a2 2 0 0 1-2-2v-3"/></svg></span>'
        btn.addEventListener('click', () => fitToBbox(getBbox()))
        wrap.appendChild(btn)
        this._wrap = wrap
        return wrap
      },
      onRemove() { this._wrap?.parentNode?.removeChild(this._wrap) },
    }
    map.value.addControl(control, 'top-right')
  }


  /** A one-click way into and out of the tilted view.
   *
   *  `visualizePitch` on the navigation control was NOT this. It makes the compass show pitch and
   *  lets you drag it to change one — but there is no tilt BUTTON, and right-drag, ctrl-drag and
   *  compass-drag are none of them things a reader knows to try. So a map with an extrusion, a
   *  2.5D style or a raised terrain on it still presented no visible way to look at it from the
   *  side, which reads as the 3D being broken rather than as the camera being flat.
   *
   *  Copied from `templates/shared/portal.js` — same icon, same 60 degrees, same toggle, and the
   *  same reflection on `pitchend` so the button never contradicts a pitch reached some other way.
   *  A control that does one job in two places must not be two different controls. The on-state
   *  class is `gd-active` rather than the portal's `active`: this stylesheet is shared with a whole
   *  app, where `active` is a word half the CSS already uses.
   */
  function addTilt() {
    if (!map.value) return
    const TILT_PITCH = 60      // enough for sides to be visible, short of maxPitch (75)
    const TILT_ON_AT = 5       // degrees below which the map counts as flat
    let button = null
    const sync = () => {
      if (button && map.value) button.classList.toggle('gd-active', map.value.getPitch() >= TILT_ON_AT)
    }
    const control = {
      onAdd: () => {
        const wrap = document.createElement('div')
        wrap.className = 'maplibregl-ctrl maplibregl-ctrl-group'
        button = document.createElement('button')
        button.type = 'button'
        button.title = 'Tilt the map (3D view)'
        button.setAttribute('aria-label', 'Tilt the map (3D view)')
        button.innerHTML = '<span class="maplibregl-ctrl-icon" aria-hidden="true" '
          + 'style="display:flex;align-items:center;justify-content:center">'
          + '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
          + 'stroke-width="1.7" stroke-linejoin="round" stroke-linecap="round">'
          + '<path d="M2 17l10 4 10-4-10-4-10 4z"/>'
          + '<path d="M9 15.8V9h6v4.4"/><path d="M9 9l3-2 3 2"/></svg></span>'
        button.addEventListener('click', () => {
          if (!map.value) return
          const flat = map.value.getPitch() < TILT_ON_AT
          map.value.easeTo({ pitch: flat ? TILT_PITCH : 0, duration: 550 })
        })
        wrap.appendChild(button)
        map.value.on('pitchend', sync)
        sync()
        return wrap
      },
      onRemove: () => { if (map.value) map.value.off('pitchend', sync) },
    }
    map.value.addControl(control, 'top-right')
  }

  return { map, loaded, applyStyle, fitToBbox, jumpTo, addTopRightControlFirst,
           addFullscreen, addZoomToExtent, addTilt }
}
