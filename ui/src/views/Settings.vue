<template>
  <div class="p-6 space-y-6">
    <h1 class="text-xl font-semibold">Settings</h1>

    <!-- Service health -->
    <section class="card p-5 space-y-3">
      <h2 class="font-semibold text-sm text-gray-700">Infrastructure health</h2>
      <div v-if="!systemStore.health.length" class="text-sm text-gray-400">Loading…</div>
      <div v-for="svc in systemStore.health" :key="svc.name" class="flex items-center justify-between">
        <span class="text-sm text-gray-700 capitalize">{{ svc.name }}</span>
        <span class="text-xs font-medium flex items-center gap-1.5"
          :class="{
            'text-green-600': svc.status === 'running' || svc.status === 'healthy',
            'text-red-600': svc.status === 'unhealthy' || svc.status === 'stopped',
            'text-gray-400': !['running','healthy','unhealthy','stopped'].includes(svc.status),
          }"
        >
          <span class="w-2 h-2 rounded-full"
            :class="{
              'bg-green-500': svc.status === 'running' || svc.status === 'healthy',
              'bg-red-500': svc.status === 'unhealthy' || svc.status === 'stopped',
              'bg-gray-300': !['running','healthy','unhealthy','stopped'].includes(svc.status),
            }"
          />
          {{ svc.status }}
        </span>
      </div>
      <button @click="systemStore.refreshHealth()" class="btn-secondary text-xs mt-1">Refresh</button>
    </section>

    <!-- Storage stats -->
    <section v-if="systemStore.stats" class="card p-5 space-y-3">
      <h2 class="font-semibold text-sm text-gray-700">Storage</h2>
      <StorageBar :used="systemStore.stats.used_bytes" :total="systemStore.stats.total_bytes" />
      <div class="grid grid-cols-3 gap-3 mt-2">
        <div class="text-center">
          <div class="text-2xl font-bold text-gray-900">{{ systemStore.stats.vector_layers }}</div>
          <div class="text-xs text-gray-500">Vector layers</div>
        </div>
        <div class="text-center">
          <div class="text-2xl font-bold text-gray-900">{{ systemStore.stats.raster_layers }}</div>
          <div class="text-xs text-gray-500">Raster files</div>
        </div>
        <div class="text-center">
          <div class="text-2xl font-bold text-gray-900">{{ systemStore.stats.portals }}</div>
          <div class="text-xs text-gray-500">Portals</div>
        </div>
      </div>
    </section>

    <!-- Account -->
    <section class="card p-5 space-y-2">
      <h2 class="font-semibold text-sm text-gray-700">Account</h2>
      <div class="text-sm text-gray-600">{{ auth.user?.name }}</div>
      <div class="text-sm text-gray-400">{{ auth.user?.email }}</div>
    </section>
  </div>
</template>

<script setup>
import { onMounted } from 'vue'
import { useSystemStore } from '@/stores/system'
import { useAuthStore } from '@/stores/auth'
import StorageBar from '@/components/shared/StorageBar.vue'

const systemStore = useSystemStore()
const auth = useAuthStore()

onMounted(() => {
  systemStore.refreshHealth()
  systemStore.refreshStats()
})
</script>
