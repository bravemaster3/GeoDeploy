<template>
  <div class="flex items-center gap-1.5">
    <template v-if="status === 'ready'">
      <span class="w-2 h-2 rounded-full bg-green-500 flex-shrink-0" />
      <span class="text-xs text-green-400 font-medium">Ready</span>
    </template>
    <template v-else-if="status === 'processing'">
      <span class="w-2 h-2 rounded-full bg-amber-400 animate-pulse flex-shrink-0" />
      <span class="text-xs text-amber-400">{{ step || 'Processing' }}{{ progress ? ` ${progress}%` : '' }}</span>
    </template>
    <template v-else-if="status === 'error'">
      <span class="w-2 h-2 rounded-full bg-red-500 flex-shrink-0" />
      <span class="text-xs text-red-400 font-medium">Error</span>
    </template>
    <template v-else>
      <span class="w-2 h-2 rounded-full bg-gray-300 flex-shrink-0" />
      <span class="text-xs text-muted-foreground">{{ status }}</span>
    </template>
  </div>
</template>

<script setup>
defineProps({ status: String, progress: Number, step: String })
</script>
