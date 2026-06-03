<template>
  <div class="p-6 lg:p-8">
    <div class="max-w-4xl mx-auto space-y-6">
      <div>
        <h1 class="text-2xl font-semibold tracking-tight text-gray-900">Settings</h1>
        <p class="text-sm text-gray-500 mt-1">Manage infrastructure, storage, and your account.</p>
      </div>

      <!-- Infrastructure health -->
      <section class="card overflow-hidden">
        <header class="flex items-center gap-3 px-5 py-3.5 border-b border-gray-100">
          <span class="w-9 h-9 rounded-lg bg-indigo-50 text-indigo-600 flex items-center justify-center flex-shrink-0">
            <ServerIcon class="w-5 h-5" />
          </span>
          <div class="flex-1 min-w-0">
            <h2 class="text-sm font-semibold text-gray-900">Infrastructure</h2>
            <p class="text-xs text-gray-400">Container health &amp; controls</p>
          </div>
          <button @click="systemStore.refreshHealth()" class="btn-secondary text-xs px-3 py-1.5">
            <RefreshIcon class="w-3.5 h-3.5" /> Refresh
          </button>
          <button @click="reloadMartin" :disabled="martinBusy" class="btn-secondary text-xs px-3 py-1.5">
            {{ martinBusy ? 'Reloading…' : 'Reload Martin' }}
          </button>
        </header>
        <div class="p-2">
          <div v-if="!systemStore.health.length" class="px-3 py-6 text-sm text-gray-400 text-center">Loading…</div>
          <div v-for="svc in systemStore.health" :key="svc.name"
            class="flex items-center gap-3 px-3 py-2 rounded-lg hover:bg-gray-50">
            <span class="w-2 h-2 rounded-full flex-shrink-0" :class="dotClass(svc.status)" />
            <span class="text-sm font-medium text-gray-700 capitalize flex-1 min-w-0 truncate">{{ svc.name }}</span>
            <span class="inline-flex items-center text-[11px] font-medium px-2 py-0.5 rounded-full" :class="pillClass(svc.status)">
              {{ svc.status }}
            </span>
            <div v-if="svc.controllable" class="flex items-center gap-1 w-16 justify-end">
              <button v-if="!['running','healthy'].includes(svc.status)"
                @click="svcAction(svc.name, 'start')" :disabled="busySvc === svc.name"
                class="svc-btn text-green-600" title="Start">▶</button>
              <button v-else @click="svcAction(svc.name, 'stop')" :disabled="busySvc === svc.name"
                class="svc-btn text-red-500" title="Stop">■</button>
              <button @click="svcAction(svc.name, 'restart')" :disabled="busySvc === svc.name"
                class="svc-btn text-gray-500" title="Restart">↻</button>
            </div>
            <div v-else class="w-16" />
          </div>
          <p v-if="martinMsg" class="px-3 pt-1 text-xs" :class="martinMsg.ok ? 'text-green-600' : 'text-red-600'">
            {{ martinMsg.text }}
          </p>
        </div>
      </section>

      <!-- Storage -->
      <section v-if="systemStore.stats" class="card overflow-hidden">
        <header class="flex items-center gap-3 px-5 py-3.5 border-b border-gray-100">
          <span class="w-9 h-9 rounded-lg bg-amber-50 text-amber-600 flex items-center justify-center flex-shrink-0">
            <HardDriveIcon class="w-5 h-5" />
          </span>
          <div class="flex-1 min-w-0">
            <h2 class="text-sm font-semibold text-gray-900">Storage</h2>
            <p class="text-xs text-gray-400">Usage across your data</p>
          </div>
        </header>
        <div class="p-5 space-y-4">
          <StorageBar :used="systemStore.stats.used_bytes" :total="systemStore.stats.total_bytes" />
          <div class="grid grid-cols-3 gap-3">
            <div v-for="tile in statTiles" :key="tile.label" class="rounded-lg border border-gray-100 bg-gray-50 p-4 text-center">
              <div class="text-2xl font-bold text-gray-900">{{ tile.value }}</div>
              <div class="text-xs text-gray-500 mt-0.5">{{ tile.label }}</div>
            </div>
          </div>
        </div>
      </section>

      <!-- Account -->
      <section class="card overflow-hidden">
        <header class="flex items-center gap-3 px-5 py-3.5 border-b border-gray-100">
          <span class="w-9 h-9 rounded-lg bg-brand-50 text-brand-600 flex items-center justify-center flex-shrink-0">
            <UserIcon class="w-5 h-5" />
          </span>
          <h2 class="text-sm font-semibold text-gray-900">Account</h2>
        </header>
        <div class="p-5 flex items-center gap-4">
          <span class="w-12 h-12 rounded-full bg-brand-50 text-brand-700 flex items-center justify-center font-semibold flex-shrink-0">
            {{ initials }}
          </span>
          <div class="flex-1 min-w-0">
            <div class="text-sm font-medium text-gray-900 truncate">{{ auth.user?.name }}</div>
            <div class="text-sm text-gray-400 truncate">{{ auth.user?.email }}</div>
          </div>
          <button @click="signOut" class="btn-secondary text-xs px-3 py-1.5">Sign out</button>
        </div>
      </section>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useSystemStore } from '@/stores/system'
import { useAuthStore } from '@/stores/auth'
import StorageBar from '@/components/shared/StorageBar.vue'
import { ServerIcon, HardDriveIcon, UserIcon, RefreshIcon } from './icons'
import api, { controlService } from '@/api'

const systemStore = useSystemStore()
const auth = useAuthStore()
const router = useRouter()
const martinBusy = ref(false)
const martinMsg = ref(null)
const busySvc = ref(null)

const initials = computed(() =>
  (auth.user?.name || '?').split(' ').map(w => w[0]).filter(Boolean).slice(0, 2).join('').toUpperCase())

const statTiles = computed(() => [
  { label: 'Vector layers', value: systemStore.stats?.vector_layers ?? 0 },
  { label: 'Raster files', value: systemStore.stats?.raster_layers ?? 0 },
  { label: 'Portals', value: systemStore.stats?.portals ?? 0 },
])

function dotClass(s) {
  if (['running', 'healthy'].includes(s)) return 'bg-green-500'
  if (['unhealthy', 'stopped', 'exited'].includes(s)) return 'bg-red-500'
  return 'bg-gray-300'
}
function pillClass(s) {
  if (['running', 'healthy'].includes(s)) return 'bg-green-50 text-green-700'
  if (['unhealthy', 'stopped', 'exited'].includes(s)) return 'bg-red-50 text-red-600'
  return 'bg-gray-100 text-gray-500'
}

function signOut() {
  auth.logout()
  router.push('/login')
}

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
