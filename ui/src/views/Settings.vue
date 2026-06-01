<template>
  <div class="p-6 space-y-6">
    <h1 class="text-xl font-semibold">Settings</h1>

    <!-- Service health -->
    <section class="card p-5 space-y-3">
      <h2 class="font-semibold text-sm text-gray-700">Infrastructure health</h2>
      <div v-if="!systemStore.health.length" class="text-sm text-gray-400">Loading…</div>
      <div v-for="svc in systemStore.health" :key="svc.name" class="flex items-center justify-between gap-3">
        <span class="text-sm text-gray-700 capitalize flex-1">{{ svc.name }}</span>
        <span class="text-xs font-medium flex items-center gap-1.5"
          :class="{
            'text-green-600': svc.status === 'running' || svc.status === 'healthy',
            'text-red-600': ['unhealthy','stopped','exited'].includes(svc.status),
            'text-gray-400': !['running','healthy','unhealthy','stopped','exited'].includes(svc.status),
          }"
        >
          <span class="w-2 h-2 rounded-full"
            :class="{
              'bg-green-500': svc.status === 'running' || svc.status === 'healthy',
              'bg-red-500': ['unhealthy','stopped','exited'].includes(svc.status),
              'bg-gray-300': !['running','healthy','unhealthy','stopped','exited'].includes(svc.status),
            }"
          />
          {{ svc.status }}
        </span>
        <div v-if="svc.controllable" class="flex items-center gap-1 w-20 justify-end">
          <button v-if="!['running','healthy'].includes(svc.status)"
            @click="svcAction(svc.name, 'start')" :disabled="busySvc === svc.name"
            class="svc-btn text-green-600" title="Start">▶</button>
          <button v-else @click="svcAction(svc.name, 'stop')" :disabled="busySvc === svc.name"
            class="svc-btn text-red-500" title="Stop">■</button>
          <button @click="svcAction(svc.name, 'restart')" :disabled="busySvc === svc.name"
            class="svc-btn text-gray-500" title="Restart">↻</button>
        </div>
        <div v-else class="w-20" />
      </div>
      <div class="flex gap-2 mt-1">
        <button @click="systemStore.refreshHealth()" class="btn-secondary text-xs">Refresh</button>
        <button @click="reloadMartin" :disabled="martinBusy" class="btn-secondary text-xs">
          {{ martinBusy ? 'Reloading…' : 'Reload Martin config' }}
        </button>
      </div>
      <p v-if="martinMsg" class="text-xs" :class="martinMsg.ok ? 'text-green-600' : 'text-red-600'">
        {{ martinMsg.text }}
      </p>
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
import { ref, onMounted } from 'vue'
import { useSystemStore } from '@/stores/system'
import { useAuthStore } from '@/stores/auth'
import StorageBar from '@/components/shared/StorageBar.vue'
import api, { controlService } from '@/api'

const systemStore = useSystemStore()
const auth = useAuthStore()
const martinBusy = ref(false)
const martinMsg = ref(null)
const busySvc = ref(null)

async function svcAction(name, action) {
  if (action === 'stop' &&
      !confirm(`Stop the "${name}" service? Features that depend on it will be unavailable until you start it again.`)) {
    return
  }
  busySvc.value = name
  try {
    await controlService(name, action)
  } catch (e) {
    // Restarting nginx drops the proxy mid-request, so a network error here is expected.
  } finally {
    setTimeout(async () => {
      try { await systemStore.refreshHealth() } catch {}
      busySvc.value = null
    }, 2500)
  }
}

onMounted(() => {
  systemStore.refreshHealth()
  systemStore.refreshStats()
})

async function reloadMartin() {
  martinBusy.value = true
  martinMsg.value = null
  try {
    const { data } = await api.post('/admin/reload-martin')
    martinMsg.value = { ok: true, text: `Config reloaded — ${data.tables} table(s) registered.` }
    setTimeout(() => systemStore.refreshHealth(), 2000)
  } catch (err) {
    martinMsg.value = { ok: false, text: err.response?.data?.detail || err.message }
  } finally {
    martinBusy.value = false
    setTimeout(() => { martinMsg.value = null }, 6000)
  }
}
</script>

<style scoped>
.svc-btn {
  width: 22px; height: 22px; border-radius: 5px; font-size: 11px; line-height: 1;
  display: inline-flex; align-items: center; justify-content: center;
  border: 1px solid #e5e7eb; background: #fff; cursor: pointer;
}
.svc-btn:hover:not(:disabled) { background: #f9fafb; }
.svc-btn:disabled { opacity: .4; cursor: default; }
</style>
