<script setup>
//: An ⓘ next to a label, opening the explanation that used to sit under it as a paragraph.
//:
//: Why a component and not `title=""`, which is what the rest of the app uses: a native tooltip
//: cannot be opened on a touch device, appears after a delay the reader has usually given up on,
//: renders unstyled, and collapses multi-sentence text on some platforms. These hints are two or
//: three sentences of real explanation, which is exactly what `title` is worst at.
//:
//: TELEPORTED to <body> and positioned `fixed`. The inspector is a narrow scrolling column, so a
//: popover positioned inside it would be clipped by the scroll container the moment it opened near
//: an edge — the same bug the symbology popover had. Measuring the button and clamping to the
//: viewport is the fix that does not depend on where in the panel the hint sits.
import { ref, nextTick, onBeforeUnmount } from 'vue'

defineProps({
  //: Screen-reader name. The visible ⓘ carries no text, so without this the control is announced
  //: as an unlabelled button.
  label: { type: String, default: 'More information' },
})

const open = ref(false)
const btn = ref(null)
const pop = ref(null)
const pos = ref({ top: 0, left: 0 })

const GAP = 6          // between the button and the popover
const MARGIN = 8       // never closer than this to a viewport edge
const WIDTH = 248      // matches the width set in the template

async function place() {
  const b = btn.value?.getBoundingClientRect()
  if (!b) return
  // Prefer the side with more room, so a hint in the right-hand inspector opens leftwards rather
  // than off-screen. Both sides are then clamped, because "more room" can still be too little.
  const roomRight = window.innerWidth - b.right
  const left = roomRight >= WIDTH + GAP + MARGIN ? b.right + GAP : b.left - WIDTH - GAP
  await nextTick()
  const h = pop.value?.offsetHeight || 0
  pos.value = {
    left: Math.min(Math.max(left, MARGIN), window.innerWidth - WIDTH - MARGIN),
    // Vertically centred on the button, then clamped — a hint near the foot of a long panel would
    // otherwise open below the fold, which is where the old symbology popover put its Save button.
    top: Math.min(Math.max(b.top + b.height / 2 - h / 2, MARGIN), window.innerHeight - h - MARGIN),
  }
}

function onDocDown(e) {
  if (btn.value?.contains(e.target) || pop.value?.contains(e.target)) return
  close()
}
function onKey(e) { if (e.key === 'Escape') close() }

function toggle() { open.value ? close() : show() }
async function show() {
  open.value = true
  await place()
  document.addEventListener('pointerdown', onDocDown, true)
  document.addEventListener('keydown', onKey)
  // A hint left hanging over a panel the reader has scrolled away from is worse than one that
  // simply closes; `true` catches the inspector's own scroll, which does not bubble.
  window.addEventListener('scroll', close, true)
  window.addEventListener('resize', close)
}
function close() {
  if (!open.value) return
  open.value = false
  document.removeEventListener('pointerdown', onDocDown, true)
  document.removeEventListener('keydown', onKey)
  window.removeEventListener('scroll', close, true)
  window.removeEventListener('resize', close)
}
onBeforeUnmount(close)
</script>

<template>
  <button ref="btn" type="button" :aria-label="label" :aria-expanded="open"
    @click.stop.prevent="toggle"
    class="inline-flex items-center justify-center w-3.5 h-3.5 shrink-0 rounded-full align-[1px]
           border border-muted-foreground/40 text-muted-foreground/70 text-[8px] font-semibold
           leading-none hover:text-foreground hover:border-foreground/60 focus:outline-none
           focus:ring-1 focus:ring-primary/60"
    :class="open ? 'text-foreground border-foreground/60' : ''">i</button>

  <Teleport to="body">
    <div v-if="open" ref="pop" role="tooltip"
      :style="{ top: pos.top + 'px', left: pos.left + 'px', width: WIDTH + 'px' }"
      class="fixed z-[100] rounded-md border border-border bg-background shadow-lg
             px-2.5 py-2 text-[11px] leading-snug text-muted-foreground">
      <slot />
    </div>
  </Teleport>
</template>
