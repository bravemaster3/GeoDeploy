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
import { previewPortal, syncSession, uploadPortalThumbnail } from '@/api'

const CAPTURE_TIMEOUT_MS = 15000   // generous: an off-screen frame is cold, and tiles must load

// Size floors, and why there are two.
//
// A failed capture serialises to almost nothing, and storing it would replace a good picture with a
// grey rectangle — hence a floor. But the floor was a flat 2 KB, and a SPARSE portal (plain basemap,
// one small layer, zoomed out) legitimately compresses below that in WebP at q=0.75. Those captures
// were discarded silently, so portals that most needed a picture were the ones that never got one.
//
// So the strict floor applies only when there is an existing thumbnail worth protecting. With no
// thumbnail, anything that decoded at all beats the gradient placeholder.
const MIN_BYTES_REPLACE = 2048
const MIN_BYTES_FIRST = 256

// Returns { url } on success, or { error } naming what went wrong. NOT a bare null: four different
// failures used to collapse into "no image", and telling them apart meant asking the operator to
// read a browser console. The caller shows `error` verbatim.
export async function capturePortalThumbnail(portalId, { hasExisting = false } = {}) {
  let frame = null
  let onMessage = null
  try {
    // The preview route is behind an nginx session gate; without the cookie the iframe loads the
    // login page and photographs that.
    await syncSession().catch(() => {})

    // BUILD the preview bundle first. `/portals/_preview/{id}/` is not a live route — it serves a
    // bundle that `POST /portals/{id}/preview` writes to disk, and only the EDITOR was ever calling
    // that. From the Portals list the directory frequently did not exist at all, so the iframe
    // loaded a 404 page, nothing answered, and the capture timed out with no explanation. That is
    // why publishing from a card produced no thumbnail while publishing from the editor did.
    //
    // An empty body is deliberate: every field falls back to the portal's SAVED state, which is
    // exactly what a card image should depict. It also re-bakes the bundle from the current
    // templates/shared/portal.js, so a portal published before a runtime change is photographed by
    // the runtime running now.
    try {
      await previewPortal(portalId, {})
    } catch (err) {
      return { error: 'could not build a preview of this portal ('
                    + (err?.response?.data?.detail || err?.message || err) + ')' }
    }

    const requestId = Date.now() % 100000
    const reply = await new Promise((resolve) => {
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
        if (d.type === 'snapshot' && d.requestId === requestId) {
          finish({ dataUrl: d.dataUrl || null, error: d.error || null })
        }
      }
      window.addEventListener('message', onMessage)

      frame.onload = () => {
        // The runtime answers when the map is idle; asking on load is the earliest it can hear us.
        try {
          frame.contentWindow.postMessage({ gd: 1, type: 'snapshot', requestId }, location.origin)
        } catch (e) { finish({ dataUrl: null, error: 'could not reach the preview frame' }) }
      }
      document.body.appendChild(frame)
      setTimeout(() => finish({
        dataUrl: null,
        error: `the preview did not answer within ${CAPTURE_TIMEOUT_MS / 1000}s `
             + '(it may have failed to load — check that the portal opens normally)',
      }), CAPTURE_TIMEOUT_MS)
    })

    if (!reply.dataUrl || !reply.dataUrl.startsWith('data:image/')) {
      return { error: reply.error || 'the map produced no image' }
    }
    const blob = await (await fetch(reply.dataUrl)).blob()
    const floor = hasExisting ? MIN_BYTES_REPLACE : MIN_BYTES_FIRST
    if (blob.size < floor) {
      return { error: `the captured image was only ${blob.size} bytes (minimum ${floor}) — `
                    + 'the map was probably still blank when it was taken' }
    }
    const { data } = await uploadPortalThumbnail(portalId, blob)
    return data?.url ? { url: data.url } : { error: 'the server stored no URL for the image' }
  } catch (err) {
    // A thumbnail is decoration. Publishing must never fail because a picture could not be taken.
    console.warn('[geodeploy] thumbnail capture failed', err)
    return { error: err?.response?.data?.detail || err?.message || String(err) }
  } finally {
    if (onMessage) window.removeEventListener('message', onMessage)
    if (frame && frame.parentNode) frame.parentNode.removeChild(frame)
  }
}
