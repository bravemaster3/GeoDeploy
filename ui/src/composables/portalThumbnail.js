// Capture a portal card thumbnail WITHOUT an editor open.
//
// The editor already does this: its preview iframe IS the real published portal, so a picture of
// that canvas is a picture of the portal — no headless browser on the server, no second renderer to
// keep in sync with portal.js. But the Portals LIST also publishes (the Publish button on a card),
// and it has no preview to photograph, so a portal published from there never got a thumbnail and
// kept its gradient placeholder. It looked exactly like the thumbnail feature being broken.
//
// This mounts the same preview off-screen for as long as it takes to answer one snapshot request.
//
// `?edit=1` is REQUIRED, not incidental: portal.js only sets `preserveDrawingBuffer` in edit mode
// (it costs performance, so the published portal does not pay for it), and without it the WebGL
// canvas is already cleared by the time toDataURL runs and reads back blank.
import { syncSession, uploadPortalThumbnail } from '@/api'

const CAPTURE_TIMEOUT_MS = 15000   // generous: an off-screen frame is cold, and tiles must load
const MIN_BYTES = 2048             // a blank canvas serialises tiny; never overwrite a good one

export async function capturePortalThumbnail(portalId) {
  let frame = null
  let onMessage = null
  try {
    // The preview route is behind an nginx session gate; without the cookie the iframe loads the
    // login page and photographs that.
    await syncSession().catch(() => {})

    const requestId = Date.now() % 100000
    const dataUrl = await new Promise((resolve) => {
      let done = false
      const finish = (v) => { if (!done) { done = true; resolve(v) } }

      frame = document.createElement('iframe')
      // Off-screen rather than display:none or 0x0 — a hidden or zero-sized frame gives MapLibre a
      // canvas with no dimensions, and the snapshot comes back empty.
      Object.assign(frame.style, {
        position: 'fixed', left: '-10000px', top: '0', width: '1200px', height: '800px',
        border: '0', opacity: '0', pointerEvents: 'none',
      })
      frame.setAttribute('aria-hidden', 'true')
      frame.src = `/portals/_preview/${portalId}/?edit=1&t=${Date.now()}`

      onMessage = (e) => {
        if (e.origin !== location.origin) return
        const d = e.data
        if (!d || d.gd !== 1) return
        if (d.type === 'snapshot' && d.requestId === requestId) finish(d.dataUrl || null)
      }
      window.addEventListener('message', onMessage)

      frame.onload = () => {
        // The runtime answers when the map is idle; asking on load is the earliest it can hear us.
        try {
          frame.contentWindow.postMessage({ gd: 1, type: 'snapshot', requestId }, location.origin)
        } catch { finish(null) }
      }
      document.body.appendChild(frame)
      setTimeout(() => finish(null), CAPTURE_TIMEOUT_MS)
    })

    if (!dataUrl || !dataUrl.startsWith('data:image/')) return null
    const blob = await (await fetch(dataUrl)).blob()
    if (blob.size < MIN_BYTES) return null
    const { data } = await uploadPortalThumbnail(portalId, blob)
    return data?.url || null
  } catch (err) {
    // A thumbnail is decoration. Publishing must never fail because a picture could not be taken.
    console.warn('[geodeploy] thumbnail capture failed', err)
    return null
  } finally {
    if (onMessage) window.removeEventListener('message', onMessage)
    if (frame && frame.parentNode) frame.parentNode.removeChild(frame)
  }
}
