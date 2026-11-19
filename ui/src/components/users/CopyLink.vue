<template>
  <div class="space-y-1.5">
    <p class="text-[11px] text-amber-400/90">{{ hint }}</p>
    <div class="flex gap-2">
      <input :value="url" readonly class="input flex-1 text-xs font-mono" @focus="$event.target.select()" />
      <button @click="copy" class="btn-secondary text-xs px-3 flex items-center gap-1.5 flex-shrink-0">
        <CopyIcon class="w-3.5 h-3.5" />
        {{ copied ? $t('users.copied') : $t('users.copy') }}
      </button>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { CopyIcon } from '@/views/icons'
import { copyText } from '@/lib/clipboard'

const props = defineProps({
  url: { type: String, required: true },
  hint: { type: String, default: '' },
})

const copied = ref(false)

async function copy() {
  if (!(await copyText(props.url))) return   // blocked — the link is on screen and selectable
  copied.value = true
  setTimeout(() => (copied.value = false), 2000)
}
</script>
