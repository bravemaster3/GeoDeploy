<script setup>
//: A way back to the source, on the DEMO instance only.
//:
//: Someone trying the demo is deciding whether to run this themselves, and the repository is the
//: next thing they want. On a real install it would be noise: the operator already has the source,
//: and a permanent link out of their own admin panel to someone else's project page is clutter at
//: best. Same test the demo banner uses, and the same posture — anything other than a clear
//: `{demo: true}` means "do not render".
import { ref, onMounted } from 'vue'
import { getDemoInfo } from '@/api'

const REPO = 'bravemaster3/GeoDeploy'
const URL = `https://github.com/${REPO}`
//: A day. The count is a signal of scale, not a live figure, and nobody returns to a demo to watch
//: it tick. The cache matters because GitHub allows 60 unauthenticated calls an hour PER IP — and a
//: demo behind one address would spend that on a busy afternoon, so every visitor asking on every
//: page load is the one thing that guarantees the number stops arriving.
const TTL = 24 * 60 * 60 * 1000
const CACHE = 'gd-gh-stars'

const props = defineProps({ collapsed: { type: Boolean, default: false } })

const show = ref(false)
const stars = ref(null)

function fmt(n) {
  return n >= 1000 ? (n / 1000).toFixed(n >= 10000 ? 0 : 1).replace(/\.0$/, '') + 'k' : String(n)
}

async function loadStars() {
  try {
    const raw = localStorage.getItem(CACHE)
    if (raw) {
      const c = JSON.parse(raw)
      if (c && Date.now() - c.at < TTL && typeof c.n === 'number') { stars.value = c.n; return }
    }
  } catch { /* private mode throws on read; ask GitHub instead */ }
  try {
    const res = await fetch(`https://api.github.com/repos/${REPO}`, {
      headers: { Accept: 'application/vnd.github+json' },
    })
    if (!res.ok) return                       // rate-limited or offline: the LINK still works
    const data = await res.json()
    if (typeof data.stargazers_count !== 'number') return
    stars.value = data.stargazers_count
    try { localStorage.setItem(CACHE, JSON.stringify({ n: stars.value, at: Date.now() })) } catch { /* see above */ }
  } catch { /* the count is an ornament; its absence must not be an error */ }
}

onMounted(async () => {
  try {
    const { data } = await getDemoInfo()
    show.value = !!data.demo
  } catch {
    show.value = false
  }
  if (show.value) loadStars()
})
</script>

<template>
  <a v-if="show" :href="URL" target="_blank" rel="noopener noreferrer"
    :title="`GeoDeploy on GitHub${stars != null ? ` — ${stars.toLocaleString()} stars` : ''}`"
    class="flex items-center rounded-md text-muted-foreground/80 hover:text-foreground hover:bg-muted/60 transition-colors"
    :class="collapsed ? 'justify-center w-9 h-9 mx-auto' : 'gap-2.5 px-3 py-2 text-sm'">
    <svg class="w-4 h-4 flex-shrink-0" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
      <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38
        0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01
        1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95
        0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82a7.42 7.42 0 0 1 2-.27c.68 0
        1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0
        3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01
        8.01 0 0 0 16 8c0-4.42-3.58-8-8-8z"/>
    </svg>
    <template v-if="!collapsed">
      <span class="truncate">View on GitHub</span>
      <!-- The count only when it arrived. A star pill reading "—" says the link is broken when what
           actually happened is that GitHub declined to answer this minute. -->
      <span v-if="stars != null"
        class="ml-auto flex items-center gap-1 text-[11px] text-muted-foreground/70 tabular-nums">
        <svg class="w-3 h-3" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
          <path d="M12 2l2.9 6.3 6.9.8-5.1 4.7 1.4 6.8L12 17.3 5.9 20.6l1.4-6.8L2.2 9.1l6.9-.8z"/>
        </svg>
        {{ fmt(stars) }}
      </span>
    </template>
  </a>
</template>
