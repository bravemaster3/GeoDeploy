<template>
  <!-- An ⓘ that works with a mouse AND with a thumb. `title=` does neither on a phone: touch devices
       have no hover, so a native tooltip is simply invisible there — which is the whole reason this
       is a component rather than an attribute. Hover opens it on a pointer device, tap toggles it
       everywhere, Escape and an outside tap close it. -->
  <span class="relative inline-block align-middle" @mouseenter="hover(true)" @mouseleave="hover(false)">
    <button type="button"
            class="w-4 h-4 rounded-full border border-border/70 text-[9px] leading-none
                   text-muted-foreground/70 hover:text-foreground hover:border-border
                   focus:outline-none focus:ring-1 focus:ring-sky-500/60 align-middle"
            :aria-expanded="open" :aria-label="label" @click.stop="open = !open" @keydown.esc="open = false">
      i
    </button>
    <span v-if="open" role="tooltip"
          class="absolute z-30 left-1/2 -translate-x-1/2 top-5 w-64 max-w-[80vw] rounded-lg border
                 border-border bg-background shadow-lg px-3 py-2 text-[11px] leading-relaxed
                 text-muted-foreground/90 font-sans normal-case text-left">
      {{ text }}
    </span>
  </span>
</template>

<script setup>
import { onBeforeUnmount, onMounted, ref } from 'vue'

const props = defineProps({
  text: { type: String, required: true },
  label: { type: String, default: 'More information' },
})

const open = ref(false)

// Hover is a convenience for pointer devices only. A touch "hover" fires alongside the tap on some
// browsers and would toggle twice, so it is gated on the device actually having a hover-capable
// pointer rather than on guessing from the event.
const canHover = () => window.matchMedia?.('(hover: hover)')?.matches ?? false
function hover(state) {
  if (canHover()) open.value = state
}

function closeOnOutside(event) {
  if (open.value && !event.target.closest?.('[role="tooltip"], button[aria-expanded]')) open.value = false
}
function closeOnEscape(event) {
  if (event.key === 'Escape') open.value = false
}

onMounted(() => {
  document.addEventListener('click', closeOnOutside)
  document.addEventListener('keydown', closeOnEscape)
})
onBeforeUnmount(() => {
  document.removeEventListener('click', closeOnOutside)
  document.removeEventListener('keydown', closeOnEscape)
})
</script>
