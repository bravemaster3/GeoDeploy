<template>
  <Teleport to="body">
  <div class="fixed inset-0 bg-gray-900/50 backdrop-blur-sm flex items-center justify-center z-50 p-4">
    <div class="card w-full max-w-md p-6 space-y-4 shadow-2xl max-h-[90vh] overflow-y-auto">
      <div class="flex items-start gap-3">
        <span class="w-9 h-9 rounded-lg bg-red-500/15 text-red-400 flex items-center justify-center flex-shrink-0">
          <TrashIcon class="w-5 h-5" />
        </span>
        <div class="min-w-0">
          <h2 class="text-base font-semibold text-foreground">Delete “{{ name }}”?</h2>
          <p class="text-xs text-muted-foreground mt-0.5">This is permanent and cannot be undone.</p>
        </div>
      </div>

      <div v-if="loadingUsage" class="text-xs text-muted-foreground/70">Checking where it's used…</div>
      <div v-else-if="usage.length" class="text-xs bg-amber-500/10 rounded-lg p-3 space-y-1">
        <p class="font-medium text-amber-400">
          Used in {{ usage.length }} portal{{ usage.length > 1 ? 's' : '' }}:
        </p>
        <ul class="list-disc list-inside space-y-0.5 text-amber-300/90">
          <li v-for="p in usage" :key="p.id">
            {{ p.title }}<span v-if="p.published" class="text-amber-400/80"> · published</span>
          </li>
        </ul>
        <p class="text-amber-300/70 pt-0.5">
          It will be removed from these portals; published ones are re-published automatically.
        </p>
      </div>

      <div class="flex justify-end gap-2 pt-1">
        <button @click="$emit('cancel')" :disabled="busy" class="btn-secondary text-sm">Cancel</button>
        <button @click="$emit('confirm')" :disabled="busy"
          class="text-sm px-3 py-1.5 rounded-lg bg-red-500 text-white hover:bg-red-600 transition-colors disabled:opacity-50">
          {{ busy ? 'Deleting…' : 'Delete' }}
        </button>
      </div>
    </div>
  </div>
  </Teleport>
</template>

<script setup>
import { TrashIcon } from '@/views/icons'

defineProps({
  name: { type: String, default: '' },
  usage: { type: Array, default: () => [] },
  loadingUsage: { type: Boolean, default: false },
  busy: { type: Boolean, default: false },
})
defineEmits(['confirm', 'cancel'])
</script>
