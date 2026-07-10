<template>
  <div class="space-y-1">
    <div class="flex justify-between text-xs text-muted-foreground">
      <span>Storage</span>
      <span>{{ formatBytes(used) }}{{ total ? ` / ${formatBytes(total)}` : '' }}</span>
    </div>
    <div class="h-1.5 bg-muted rounded-full overflow-hidden">
      <div class="h-full bg-primary/100 rounded-full transition-all" :style="{ width: pct + '%' }" />
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
const props = defineProps({ used: Number, total: Number })
const pct = computed(() => props.total ? Math.min((props.used / props.total) * 100, 100) : 0)
const formatBytes = (b) => !b ? '0 B' : b > 1e9 ? `${(b/1e9).toFixed(1)} GB` : b > 1e6 ? `${(b/1e6).toFixed(1)} MB` : `${(b/1e3).toFixed(0)} KB`
</script>
