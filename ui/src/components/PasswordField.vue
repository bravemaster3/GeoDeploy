<template>
  <!-- A password box you can read back. Typing a long generated secret into a field of dots and
       getting "authentication failed" tells you nothing about WHICH character went wrong, and the
       usual recovery is to retype it blind a second time. The reveal is per-field and per-press:
       nothing is remembered, so a revealed field cannot surprise you on the next page. -->
  <div class="relative">
    <input
      :id="id"
      :type="shown ? 'text' : 'password'"
      :value="modelValue"
      :name="name"
      :autocomplete="autocomplete"
      :placeholder="placeholder"
      :minlength="minlength"
      :aria-invalid="invalid || undefined"
      spellcheck="false"
      autocapitalize="off"
      class="input w-full pr-10"
      :class="invalid ? 'border-red-500/60' : ''"
      @input="$emit('update:modelValue', $event.target.value)"
      @keydown.enter="$emit('enter')"
    />
    <button
      type="button"
      tabindex="-1"
      class="absolute inset-y-0 right-0 flex items-center px-3 text-muted-foreground/70
             hover:text-foreground focus:outline-none"
      :aria-label="shown ? 'Hide password' : 'Show password'"
      :aria-pressed="shown"
      @click="shown = !shown"
    >
      <!-- Eye / eye-off, inline so the component carries no icon dependency. -->
      <svg v-if="!shown" class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor"
           stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
        <path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7z" /><circle cx="12" cy="12" r="3" />
      </svg>
      <svg v-else class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor"
           stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
        <path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-6.5 0-10-8-10-8a18.45 18.45 0 0 1 5.06-5.94" />
        <path d="M9.9 4.24A9.12 9.12 0 0 1 12 4c6.5 0 10 8 10 8a18.5 18.5 0 0 1-2.16 3.19" />
        <path d="M1 1l22 22" />
      </svg>
    </button>
  </div>
</template>

<script setup>
import { ref } from 'vue'

defineProps({
  modelValue: { type: String, default: '' },
  id: { type: String, default: undefined },
  name: { type: String, default: undefined },
  // Defaults to `new-password`, which is what every field in this app except the sign-in box is.
  // It stops the browser substituting a saved credential for the host — the dots look identical, so
  // the operator submits someone else's value and reads a failure for input they can see is right.
  autocomplete: { type: String, default: 'new-password' },
  placeholder: { type: String, default: undefined },
  minlength: { type: [String, Number], default: undefined },
  invalid: { type: Boolean, default: false },
})
defineEmits(['update:modelValue', 'enter'])

// Starts hidden on every mount: a field that remembers being revealed shows a secret to whoever
// walks past next.
const shown = ref(false)
</script>
