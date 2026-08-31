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

const CAPTURE_TIMEOUT_MS = 25000   // an off-screen frame is cold: style, sources and tiles all
                                   // load from scratch before the runtime says `ready`

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

//: Is the picture actually empty, rather than merely small?
//:
//: Byte size was only ever a PROXY for "did the map render", and it is a poor one in the direction
//: that matters: a map zoomed in far enough to show no detail — a plain field, open water, a
//: single-colour basemap at z18 — is a legitimate picture that compresses to almost nothing, and it
//: was being discarded with the message "the map was probably still blank". It was not blank; it
//: was plain.
//:
//: So a small capture is now decoded and looked at. A blank one is uniform: every pixel the same
//: colour to within a rounding error. Anything with real variation is a real picture whatever it
//: weighs. Sampled at 32x32, because deciding "is there anything here" needs no more than a
//: thousand pixels and reading a full-size frame for it would be a waste on every publish.
async function looksBlank(dataUrl) {
  try {
    const img = await new Promise((resolve, reject) => {
      const i = new Image()
      i.onload = () => resolve(i)
      i.onerror = reject
      i.src = dataUrl
    })
    const N = 32
    const c = document.createElement('canvas')
    c.width = N; c.height = N
    const ctx = c.getContext('2d', { willReadFrequently: true })
    if (!ctx) return false
    ctx.drawImage(img, 0, 0, N, N)
    const d = ctx.getImageData(0, 0, N, N).data
    const min = [255, 255, 255], max = [0, 0, 0]
    for (let i = 0; i < d.length; i += 4) {
      // Fully transparent pixels are the blank case itself, not a colour to measure.
      if (d[i + 3] === 0) continue
      for (let k = 0; k < 3; k++) {
        if (d[i + k] < min[k]) min[k] = d[i + k]
        if (d[i + k] > max[k]) max[k] = d[i + k]
      }
    }
    // 8 of 255: below this is compression noise on a flat fill, not content.
    return Math.max(max[0] - min[0], max[1] - min[1], max[2] - min[2]) < 8
  } catch {
    // A picture that cannot be decoded here is not evidence of a blank one, and refusing to store
    // it on that basis would fail exactly the captures this check exists to rescue.
    return false
  }
}

// Returns { url } on success, or { error } naming what went wrong. NOT a bare null: four different
// failures used to collapse into "no image", and telling them apart meant asking the operator to
// read a browser console. The caller shows `error` verbatim.
export async function capturePortalThumbnail(portalId, { hasExisting = false } = {}) {
  let frame = null
  let onMessage = null
  // Hoisted so the outer `finally` can always clear it. A repeating timer that outlives its promise
  // keeps posting into a removed iframe for the life of the page.
  let retryTimer = null
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

      // The request is sent when the RUNTIME says it is listening, not when the iframe fires
      // `load`. portal.js installs its message listener inside setupEditMode(), which runs from
      // `map.on('load')` — i.e. after the style has loaded, seconds after the document did. A
      // request posted at iframe-onload therefore arrived before anything was listening and was
      // silently dropped, and the capture always timed out. It announces itself with `ready`.
      //
      // The repeat is not belt-and-braces: a portal whose bundle predates this handshake never
      // sends `ready` at all, and re-asking is the only thing that reaches it once it is up.
      let asked = 0
      const ask = () => {
        if (done || !frame || !frame.contentWindow) return
        try {
          frame.contentWindow.postMessage({ gd: 1, type: 'snapshot', requestId }, location.origin)
          asked += 1
        } catch { /* frame not ready yet; the interval will try again */ }
      }
      retryTimer = setInterval(ask, 2000)
      const stopRetry = () => clearInterval(retryTimer)

      onMessage = (e) => {
        if (e.origin !== location.origin) return
        // MUST be our frame. The editor keeps its own preview iframe on the page and it announces
        // `ready` too, so without this the handshake fires on someone else's frame. The snapshot
        // reply is already keyed by requestId, but `ready` carries no id at all.
        if (!frame || e.source !== frame.contentWindow) return
        const d = e.data
        if (!d || d.gd !== 1) return
        if (d.type === 'ready') return ask()          // listening now — ask immediately
        if (d.type === 'snapshot' && d.requestId === requestId) {
          stopRetry()
          finish({ dataUrl: d.dataUrl || null, error: d.error || null })
        }
      }
      window.addEventListener('message', onMessage)

      frame.onload = () => ask()
      document.body.appendChild(frame)
      setTimeout(() => {
        stopRetry()
        finish({
          dataUrl: null,
          error: `the preview did not answer within ${CAPTURE_TIMEOUT_MS / 1000}s`
               + (asked ? ` (asked ${asked}x)` : ' (the frame never loaded)')
               + ' — open the portal itself and check it renders',
        })
      }, CAPTURE_TIMEOUT_MS)
    })

    if (!reply.dataUrl || !reply.dataUrl.startsWith('data:image/')) {
      return { error: reply.error || 'the map produced no image' }
    }
    const blob = await (await fetch(reply.dataUrl)).blob()
    // SMALL is a reason to look, not a reason to refuse. Only a capture that is both small and
    // actually featureless is thrown away — otherwise a plain map, which is the case most likely to
    // produce a tiny file, was the one case guaranteed never to get a thumbnail.
    const floor = hasExisting ? MIN_BYTES_REPLACE : MIN_BYTES_FIRST
    if (blob.size < floor && await looksBlank(reply.dataUrl)) {
      return { error: `the captured image was ${blob.size} bytes and had no visible detail — `
                    + 'the map was probably still blank when it was taken' }
    }
    const { data } = await uploadPortalThumbnail(portalId, blob)
    return data?.url ? { url: data.url } : { error: 'the server stored no URL for the image' }
  } catch (err) {
    // A thumbnail is decoration. Publishing must never fail because a picture could not be taken.
    console.warn('[geodeploy] thumbnail capture failed', err)
    return { error: err?.response?.data?.detail || err?.message || String(err) }
  } finally {
    clearInterval(retryTimer)
    if (onMessage) window.removeEventListener('message', onMessage)
    if (frame && frame.parentNode) frame.parentNode.removeChild(frame)
  }
}
