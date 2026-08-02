<template>
  <!-- Renders NOTHING unless the server says this instance is a demo. A normal install never sees
       this component's markup at all. -->
  <div v-if="isDemo && !dismissed"
    class="relative z-[60] flex items-start gap-3 px-4 py-2.5 bg-amber-500/15 border-b border-amber-500/40">
    <svg class="w-4 h-4 mt-0.5 flex-shrink-0 text-amber-400" viewBox="0 0 24 24" fill="none"
      stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z" />
      <path d="M12 9v4M12 17h.01" />
    </svg>
    <p class="flex-1 min-w-0 text-xs leading-relaxed text-amber-100">
      <strong class="font-semibold">This is a demo.</strong>
      Everything here — layers, portals, accounts — is
      <strong>deleted every hour, on the hour</strong><span v-if="countdown"> — next wipe in
      <strong>{{ countdown }}</strong></span>. Don't build a portal here that you intend to keep,
      and don't put anything private here: everyone trying the demo shares this workspace, and can
      see and change what you make.
      <span class="text-amber-200/80">
        Uploads are capped at {{ maxUploadMb }} MB in the demo only — on your own server, the limit
        is your server.
      </span>
    </p>
    <button @click="dismissed = true" title="Hide until the page reloads" aria-label="Hide"
      class="flex-shrink-0 w-5 h-5 leading-none text-amber-300/70 hover:text-amber-100">&times;</button>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import { getDemoInfo } from '@/api'

// `dismissed` is plain component state on PURPOSE — no localStorage, no sessionStorage. The brief was
// that closing it hides it, and a refresh brings it back: someone who has been clicking around for
// twenty minutes needs reminding again that their work is about to be wiped.
const dismissed = ref(false)
const isDemo = ref(false)
const maxUploadMb = ref(500)
const countdown = ref('')

// The SERVER says when the next reset is, and the offset between its clock and the browser's is
// measured once. A visitor with a wrong system clock — or in any timezone — then sees the same
// countdown the reset actually obeys, instead of one their own machine invented.
let resetAt = null
let skew = 0
let timer = null

function tickCountdown() {
  if (!resetAt) return
  const left = resetAt - (Date.now() + skew)
  if (left <= 0) {
    // The wipe is happening. Say so rather than showing a stale 0:00; a reload lands on the fresh
    // instance and picks up the next hour.
    countdown.value = 'moments'
    return
  }
  const m = Math.floor(left / 60000)
  const sec = Math.floor((left % 60000) / 1000)
  countdown.value = m >= 1 ? `${m} min` : `${sec}s`
}

onMounted(async () => {
  try {
    const { data } = await getDemoInfo()
    isDemo.value = !!data.demo
    if (data.max_upload_mb) maxUploadMb.value = data.max_upload_mb
    if (data.next_reset) {
      resetAt = new Date(data.next_reset).getTime()
      if (data.server_time) skew = new Date(data.server_time).getTime() - Date.now()
      tickCountdown()
      // Every 15s: precise enough for a minutes-level countdown without waking the tab each second.
      timer = setInterval(tickCountdown, 15000)
    }
  } catch {
    // A normal install answers {demo:false}; anything else (offline, error) must also mean "no
    // banner" rather than a broken bar across the top of the app.
    isDemo.value = false
  }
})

onUnmounted(() => clearInterval(timer))
</script>
