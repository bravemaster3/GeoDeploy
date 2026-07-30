<template>
  <!-- One place for every service, Coolify-style: pick a service on the LEFT, everything about it
       is a tab on the right. This replaces three separate cards (a health list, a logs card, a
       danger-zone terminal) that each made you re-choose the service you were already looking at. -->
  <section class="card overflow-hidden">
    <header class="flex flex-wrap items-center gap-3 px-5 py-3.5 border-b border-border/60">
      <span class="w-9 h-9 rounded-lg bg-indigo-500/15 text-indigo-400 flex items-center justify-center flex-shrink-0">
        <ServerIcon class="w-5 h-5" />
      </span>
      <div class="min-w-0">
        <h2 class="font-semibold">Infrastructure</h2>
        <p class="text-xs text-muted-foreground">Services, logs, deployments and terminal.</p>
      </div>
      <button @click="refreshAll" class="ml-auto btn-secondary text-xs px-3 py-1.5">
        <RefreshIcon class="w-3.5 h-3.5 inline -mt-0.5 mr-1" />Refresh
      </button>
    </header>

    <div class="flex flex-col md:flex-row">
      <!-- ── Service rail ─────────────────────────────────────────────────────────────── -->
      <nav class="md:w-52 flex-shrink-0 border-b md:border-b-0 md:border-r border-border/60
                  flex md:flex-col overflow-x-auto md:overflow-visible">
        <button v-for="s in services" :key="s.name" @click="select(s.name)"
          class="flex items-center gap-2 px-4 py-2.5 text-sm text-left whitespace-nowrap transition-colors"
          :class="s.name === active
            ? 'bg-muted/70 text-foreground font-medium md:border-r-2 md:border-primary'
            : 'text-muted-foreground hover:bg-muted/40'">
          <span class="w-2 h-2 rounded-full flex-shrink-0" :class="dot(s.status)" :title="s.status"></span>
          <span class="truncate">{{ s.name }}</span>
        </button>
      </nav>

      <!-- ── Detail ───────────────────────────────────────────────────────────────────── -->
      <div class="flex-1 min-w-0">
        <div class="flex flex-wrap items-center gap-1 px-4 py-2 border-b border-border/60">
          <button v-for="t in TABS" :key="t.id" @click="tab = t.id"
            class="text-xs px-3 py-1.5 rounded-md transition-colors"
            :class="tab === t.id ? 'bg-muted text-foreground font-medium' : 'text-muted-foreground hover:text-foreground'">
            {{ t.label }}
          </button>
          <!-- Per-service actions, mirroring Coolify's Restart/Stop position -->
          <div class="ml-auto flex items-center gap-1.5">
            <span class="text-[11px] text-muted-foreground mr-1">{{ current?.status || '—' }}</span>
            <!-- Martin's recovery hook: it can end up with an empty/stale config, and this
                 regenerates it from every ready PostGIS layer. Lives here because it is an action
                 ON a service, not a page-level setting. -->
            <button v-if="active === 'martin'" @click="doReloadMartin" :disabled="martinBusy"
              class="text-xs px-2.5 py-1.5 rounded-md border border-border hover:border-primary
                     text-muted-foreground hover:text-foreground disabled:opacity-40"
              title="Regenerate Martin's config from all ready PostGIS layers">
              {{ martinBusy ? 'Reloading…' : 'Reload config' }}
            </button>
            <button v-for="a in ACTIONS" :key="a.id" @click="act(a.id)"
              :disabled="busy || !current?.controllable"
              class="text-xs px-2.5 py-1.5 rounded-md border border-border hover:border-primary
                     text-muted-foreground hover:text-foreground disabled:opacity-40 disabled:hover:border-border"
              :title="current?.controllable ? a.label : 'This service cannot be controlled from here'">
              {{ a.label }}
            </button>
          </div>
        </div>

        <p v-if="martinMsg" class="text-xs px-4 pt-3" :class="martinMsg.ok ? 'text-emerald-400' : 'text-red-400'">
          {{ martinMsg.text }}
        </p>

        <!-- Logs -->
        <div v-if="tab === 'logs'" class="p-4 space-y-3">
          <div class="flex flex-wrap items-end gap-3">
            <div>
              <label class="text-[11px] text-muted-foreground block mb-1">Lines</label>
              <select v-model.number="logLines" @change="loadLogs" class="input text-sm py-1.5">
                <option v-for="n in [100, 200, 500, 1000, 2000]" :key="n" :value="n">{{ n }}</option>
              </select>
            </div>
            <label class="flex items-center gap-2 text-xs text-muted-foreground pb-2">
              <input type="checkbox" v-model="logTimestamps" @change="loadLogs" class="w-3.5 h-3.5" />
              Timestamps
            </label>
            <label class="flex items-center gap-2 text-xs text-muted-foreground pb-2">
              <input type="checkbox" v-model="streaming" class="w-3.5 h-3.5" />
              Stream
            </label>
            <button @click="loadLogs" class="btn-secondary text-xs px-3 py-1.5 mb-0.5">Refresh</button>
            <span v-if="logsLoading" class="text-[11px] text-muted-foreground pb-2">Loading…</span>
          </div>
          <pre ref="logBox" class="text-[11px] leading-relaxed font-mono bg-muted/50 rounded-lg p-3
                     h-96 overflow-auto whitespace-pre-wrap break-all">{{ logs || 'No output.' }}</pre>
        </div>

        <!-- Terminal -->
        <div v-else-if="tab === 'terminal'" class="p-4 space-y-3">
          <p v-if="!termAllowed" class="text-xs text-amber-300/90 bg-amber-500/10 border border-amber-400/30 rounded-lg p-3">
            The terminal is not available for <strong>{{ active }}</strong>. Containers that mount the
            Docker socket (api, celery) are excluded on purpose — a shell there is a host escape.
          </p>
          <template v-else>
            <p class="text-xs text-muted-foreground">
              Runs one command inside the <strong>{{ active }}</strong> container and returns its
              output. 30-second limit, and every command is recorded in Activity.
            </p>
            <div class="flex gap-2">
              <input v-model="cmd" @keyup.enter="runCmd" placeholder="e.g. ls -la /"
                class="input flex-1 text-sm font-mono" spellcheck="false" />
              <button @click="runCmd" :disabled="cmdBusy || !cmd.trim()"
                class="btn-primary text-sm px-4 disabled:opacity-60">
                {{ cmdBusy ? 'Running…' : 'Run' }}
              </button>
            </div>
            <pre v-if="cmdOut !== null" class="text-[11px] leading-relaxed font-mono bg-muted/50
                       rounded-lg p-3 max-h-96 overflow-auto whitespace-pre-wrap break-all">{{ cmdOut }}</pre>
          </template>
        </div>

        <!-- Deployments -->
        <div v-else class="p-4">
          <div v-if="!deployments.length" class="py-8 text-center text-sm text-muted-foreground/70">
            No deployments recorded yet.
          </div>
          <div v-else class="space-y-2">
            <div v-for="d in deployments" :key="d.id"
              class="border-l-2 rounded-r-lg bg-muted/30 px-4 py-3"
              :class="d.status === 'success' ? 'border-emerald-500'
                : d.status === 'running' ? 'border-amber-500' : 'border-red-500'">
              <div class="flex items-center gap-2 flex-wrap">
                <span class="text-[11px] font-semibold px-2 py-0.5 rounded"
                  :class="d.status === 'success' ? 'bg-emerald-500/15 text-emerald-400'
                    : d.status === 'running' ? 'bg-amber-500/15 text-amber-400' : 'bg-red-500/15 text-red-400'">
                  {{ d.status }}
                </span>
                <span class="text-xs text-muted-foreground">{{ new Date(d.started_at).toLocaleString() }}</span>
                <span v-if="duration(d)" class="text-xs text-muted-foreground">· {{ duration(d) }}</span>
                <span class="text-xs text-muted-foreground">· {{ d.trigger }}</span>
                <span v-if="d.actor_name" class="text-xs text-muted-foreground">· {{ d.actor_name }}</span>
              </div>
              <p class="text-xs text-muted-foreground mt-1.5 font-mono">
                <span v-if="d.from_sha">{{ d.from_sha.slice(0, 7) }}</span>
                <span v-if="d.from_sha && d.to_sha"> → </span>
                <span v-if="d.to_sha">{{ d.to_sha.slice(0, 7) }}</span>
              </p>
              <p v-if="d.message" class="text-xs text-muted-foreground/80 mt-1">{{ d.message }}</p>
            </div>
          </div>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, nextTick } from 'vue'
import api, { controlService, listDeployments } from '@/api'
import { useSystemStore } from '@/stores/system'
import { ServerIcon, RefreshIcon } from '@/views/icons'

// Mirrors admin.py TERMINAL_ALLOWED. Kept in sync by hand: the SERVER is the enforcement point
// (this only decides whether to show the box), so drifting is a cosmetic bug, not a hole.
const TERMINAL_ALLOWED = ['postgres', 'redis', 'martin', 'titiler', 'minio', 'nginx', 'ui']
const TABS = [
  { id: 'logs', label: 'Logs' },
  { id: 'terminal', label: 'Terminal' },
  { id: 'deployments', label: 'Deployments' },
]
const ACTIONS = [
  { id: 'restart', label: 'Restart' },
  { id: 'stop', label: 'Stop' },
  { id: 'start', label: 'Start' },
]

const systemStore = useSystemStore()
const active = ref('api')
const tab = ref('logs')
const busy = ref(false)

const logs = ref('')
const logLines = ref(200)
const logTimestamps = ref(true)
const logsLoading = ref(false)
const streaming = ref(false)
const logBox = ref(null)
let streamTimer = null

const cmd = ref('')
const cmdOut = ref(null)
const cmdBusy = ref(false)

const deployments = ref([])
const martinBusy = ref(false)
const martinMsg = ref(null)

const services = computed(() => systemStore.health?.services || [])
const current = computed(() => services.value.find(s => s.name === active.value))
const termAllowed = computed(() => TERMINAL_ALLOWED.includes(active.value))

function dot(status) {
  if (status === 'running' || status === 'healthy' || status === 'ok') return 'bg-emerald-400'
  if (status === 'missing' || status === 'stopped' || status === 'exited') return 'bg-muted-foreground/40'
  return 'bg-red-400'
}

function duration(d) {
  if (!d.finished_at) return null
  const ms = new Date(d.finished_at) - new Date(d.started_at)
  if (ms < 0) return null
  const s = Math.round(ms / 1000)
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${s % 60}s`
}

function select(name) {
  active.value = name
  cmdOut.value = null
  logs.value = ''
  if (tab.value === 'logs') loadLogs()
}

async function loadLogs() {
  logsLoading.value = true
  try {
    const { data } = await api.get(`/admin/services/${active.value}/logs`,
      { params: { tail: logLines.value, timestamps: logTimestamps.value } })
    logs.value = data.logs || ''
    // Follow the tail like a terminal would, but only when already pinned to the bottom, so a
    // refresh doesn't yank the view away from something being read further up.
    await nextTick()
    const el = logBox.value
    if (el && streaming.value) el.scrollTop = el.scrollHeight
  } catch (e) {
    logs.value = e.response?.data?.detail || 'Could not read logs.'
  } finally {
    logsLoading.value = false
  }
}

async function act(action) {
  busy.value = true
  try {
    await controlService(active.value, action)
    await systemStore.refreshHealth()
  } catch (e) {
    alert(e.response?.data?.detail || `Could not ${action} ${active.value}.`)
  } finally {
    busy.value = false
  }
}

async function runCmd() {
  if (!cmd.value.trim()) return
  cmdBusy.value = true
  cmdOut.value = null
  try {
    const { data } = await api.post(`/admin/services/${active.value}/exec`, { command: cmd.value })
    cmdOut.value = (data.output || '(no output)') + `\n\n[exit ${data.exit_code}]`
  } catch (e) {
    cmdOut.value = e.response?.data?.detail || 'Command failed.'
  } finally {
    cmdBusy.value = false
  }
}

async function doReloadMartin() {
  martinBusy.value = true
  martinMsg.value = null
  try {
    const { data } = await api.post('/admin/reload-martin')
    martinMsg.value = { ok: true, text: `Config reloaded — ${data.tables} table(s) registered.` }
  } catch (e) {
    martinMsg.value = { ok: false, text: e.response?.data?.detail || 'Reload failed.' }
  } finally {
    martinBusy.value = false
    setTimeout(() => { martinMsg.value = null }, 6000)
  }
}

async function loadDeployments() {
  try { deployments.value = (await listDeployments()).data } catch { deployments.value = [] }
}

async function refreshAll() {
  await systemStore.refreshHealth()
  if (tab.value === 'logs') loadLogs()
  if (tab.value === 'deployments') loadDeployments()
}

onMounted(async () => {
  if (!services.value.length) await systemStore.refreshHealth()
  loadLogs()
  loadDeployments()
  // Poll only while the Stream box is ticked AND the Logs tab is visible — an unattended tail
  // would otherwise hit the Docker socket every few seconds forever.
  streamTimer = setInterval(() => {
    if (streaming.value && tab.value === 'logs') loadLogs()
  }, 4000)
})
onUnmounted(() => clearInterval(streamTimer))
</script>
