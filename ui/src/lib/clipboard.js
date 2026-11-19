// Copy text to the clipboard, on http as well as https.
//
// navigator.clipboard exists only in a SECURE context, and a fresh instance is reached at
// http://<server-ip> until its domain and certificate are set up — exactly when the Deployment
// panel's DNS record is being copied. Without the fallback the button silently did nothing and the
// operator pasted whatever was on the clipboard before (reported: the full domain instead of the
// record's Name). Returns whether the copy happened, so a button only says "Copied" when it did.
export async function copyText(text) {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text)
      return true
    }
  } catch { /* permission denied — try the fallback */ }
  const ta = document.createElement('textarea')
  ta.value = text
  ta.setAttribute('readonly', '')
  ta.style.position = 'fixed'
  ta.style.opacity = '0'
  document.body.appendChild(ta)
  ta.select()
  let ok = false
  try { ok = document.execCommand('copy') } catch { ok = false }
  ta.remove()
  return ok
}
