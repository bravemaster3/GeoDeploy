<template>
  <div class="relative" ref="root">
    <button type="button" @click="open = !open" :disabled="disabled"
      class="inline-flex items-center gap-1.5 text-xs px-2 py-1 rounded-md border border-border hover:bg-muted transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
      :title="current.hint">
      <component :is="current.icon" class="w-3.5 h-3.5" :class="current.color" />
      <span class="font-medium">{{ current.label }}</span>
      <svg class="w-3 h-3 opacity-60" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>
    </button>

    <div v-if="open"
      class="absolute right-0 z-30 mt-1 w-60 rounded-lg border border-border bg-card shadow-xl py-1">
      <button v-for="t in tiers" :key="t.value" type="button" @click="choose(t.value)"
        class="w-full flex items-start gap-2 px-3 py-2 text-left hover:bg-muted transition-colors"
        :class="t.value === modelValue ? 'bg-muted/50' : ''">
        <component :is="t.icon" class="w-4 h-4 mt-0.5 flex-shrink-0" :class="t.color" />
        <span class="flex-1 min-w-0">
          <span class="block text-xs font-medium">{{ t.label }}</span>
          <span class="block text-[11px] text-muted-foreground leading-snug">{{ t.hint }}</span>
        </span>
        <CheckIcon v-if="t.value === modelValue" class="w-3.5 h-3.5 text-primary mt-0.5 flex-shrink-0" />
      </button>
    </div>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { UserIcon, UsersIcon, GlobeIcon, CheckIcon } from '@/views/icons'

const props = defineProps({
  modelValue: { type: String, default: 'organization' },
  // Layers have a public (internet catalog) tier; sources & portals do not.
  allowPublic: { type: Boolean, default: false },
  disabled: { type: Boolean, default: false },
})
const emit = defineEmits(['update:modelValue', 'change'])

const ALL = {
  private: { value: 'private', label: 'Private', icon: UserIcon, color: 'text-amber-400',
             hint: 'Only you and workspace admins' },
  organization: { value: 'organization', label: 'Organization', icon: UsersIcon, color: 'text-sky-400',
                  hint: 'Everyone in your workspace' },
  public: { value: 'public', label: 'Public', icon: GlobeIcon, color: 'text-emerald-400',
            hint: 'Your workspace + published to the internet catalog' },
}

const tiers = computed(() =>
  props.allowPublic ? [ALL.private, ALL.organization, ALL.public] : [ALL.private, ALL.organization])
const current = computed(() => ALL[props.modelValue] || ALL.organization)

const open = ref(false)
const root = ref(null)

function choose(v) {
  open.value = false
  if (v === props.modelValue) return
  emit('update:modelValue', v)
  emit('change', v)
}

function onDocClick(e) {
  if (open.value && root.value && !root.value.contains(e.target)) open.value = false
}
onMounted(() => document.addEventListener('click', onDocClick, true))
onBeforeUnmount(() => document.removeEventListener('click', onDocClick, true))
</script>
