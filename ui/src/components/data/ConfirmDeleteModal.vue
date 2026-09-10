<template>
  <Teleport to="body">
  <div class="fixed inset-0 bg-gray-900/50 backdrop-blur-sm flex items-center justify-center z-50 p-4">
    <div class="card w-full max-w-md p-6 space-y-4 shadow-2xl max-h-[90vh] overflow-y-auto">
      <div class="flex items-start gap-3">
        <span class="w-9 h-9 rounded-lg bg-red-500/15 text-red-400 flex items-center justify-center flex-shrink-0">
          <TrashIcon class="w-5 h-5" />
        </span>
        <div class="min-w-0">
          <h2 class="text-base font-semibold text-foreground">
            <template v-if="items.length > 1">Delete {{ items.length }} items?</template>
            <template v-else>Delete “{{ items.length ? items[0].name : name }}”?</template>
          </h2>
          <p class="text-xs text-muted-foreground mt-0.5">This is permanent and cannot be undone.</p>
        </div>
      </div>

      <!-- EVERYTHING THAT WILL GO, BY NAME. A count is not a confirmation: "delete 12 items" is
           impossible to check against what you meant to tick, and this is the last moment anyone
           can catch a wrong one. Scrolls rather than growing without limit. -->
      <ul v-if="items.length > 1"
        class="text-xs bg-muted/50 rounded-lg p-3 space-y-1 max-h-40 overflow-y-auto">
        <li v-for="item in items" :key="item.type + ':' + item.id"
          class="flex items-center gap-2 min-w-0">
          <span class="uppercase text-[10px] font-semibold text-muted-foreground/70 w-12 flex-shrink-0">
            {{ item.type === 'source' ? 'source' : item.type }}
          </span>
          <span class="truncate text-foreground/90">{{ item.name }}</span>
        </li>
      </ul>

      <div v-if="loadingUsage" class="text-xs text-muted-foreground/70">Checking where it's used…</div>
      <div v-else-if="usage.length" class="text-xs bg-amber-500/10 rounded-lg p-3 space-y-1">
        <p class="font-medium text-amber-400">
          <template v-if="items.length > 1">Used by {{ usage.length }} portal{{ usage.length > 1 ? 's' : '' }}:</template>
          <template v-else>Used in {{ usage.length }} portal{{ usage.length > 1 ? 's' : '' }}:</template>
        </p>
        <ul class="list-disc list-inside space-y-0.5 text-amber-300/90">
          <li v-for="p in usage" :key="p.id">
            {{ p.title }}<span v-if="p.published" class="text-amber-400/80"> · published</span>
          </li>
        </ul>
        <p class="text-amber-300/70 pt-0.5">
          Deleting {{ items.length > 1 ? 'them' : 'it' }} removes {{ items.length > 1 ? 'them' : 'it' }}
          from these portals. The ones marked <span class="font-medium">published</span>
          are re-published immediately, so {{ items.length > 1 ? 'they' : 'it' }} also
          disappear{{ items.length > 1 ? '' : 's' }} from their <span class="font-medium">live</span> maps.
        </p>
      </div>

      <!-- WHAT WENT WRONG, PER ITEM. A batch that half-succeeds is the normal failure here, and
           "delete failed" would say nothing about which four of twelve are still there. -->
      <div v-if="failures.length" class="text-xs bg-red-500/10 rounded-lg p-3 space-y-1">
        <p class="font-medium text-red-400">
          {{ failures.length }} could not be deleted — the rest were:
        </p>
        <ul class="list-disc list-inside space-y-0.5 text-red-300/90">
          <li v-for="f in failures" :key="f.name">{{ f.name }} — {{ f.error }}</li>
        </ul>
      </div>

      <div class="flex justify-end gap-2 pt-1">
        <button @click="$emit('cancel')" :disabled="busy" class="btn-secondary text-sm">
          {{ failures.length ? 'Close' : 'Cancel' }}
        </button>
        <button v-if="!failures.length" @click="$emit('confirm')" :disabled="busy"
          class="text-sm px-3 py-1.5 rounded-lg bg-red-500 text-white hover:bg-red-600 transition-colors disabled:opacity-50">
          <template v-if="busy">
            <!-- A count only when there is something to count off. "Deleting 1 of 1" is noise. -->
            {{ progress && progress.total > 1
                ? `Deleting ${progress.done} of ${progress.total}…` : 'Deleting…' }}
          </template>
          <template v-else>Delete{{ items.length > 1 ? ` ${items.length}` : '' }}</template>
        </button>
      </div>
    </div>
  </div>
  </Teleport>
</template>

<script setup>
import { TrashIcon } from '@/views/icons'

defineProps({
  // ONE ITEM OR MANY, through one component. `name` is the single-item form the row menus have
  // always used; `items` is the list form. Keeping both means the single delete keeps its exact
  // wording — "Delete “Roads”?" reads better than "Delete 1 item?" — without a second modal to
  // keep in step with this one.
  name: { type: String, default: '' },
  items: { type: Array, default: () => [] },
  // The union of the portals every selected item appears in, de-duplicated by the caller.
  usage: { type: Array, default: () => [] },
  loadingUsage: { type: Boolean, default: false },
  busy: { type: Boolean, default: false },
  // `{ done, total }` while a batch runs, so a long delete is not a frozen button.
  progress: { type: Object, default: null },
  // `[{ name, error }]` — what failed, once the batch has finished.
  failures: { type: Array, default: () => [] },
})
defineEmits(['confirm', 'cancel'])
</script>
