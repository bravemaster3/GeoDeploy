<template>
  <div class="p-6 lg:p-8">
    <div class="max-w-5xl mx-auto space-y-4">
      <div class="flex items-end justify-between gap-3 flex-wrap">
        <div>
          <h1 class="text-2xl font-semibold tracking-tight text-foreground">Activity</h1>
          <p class="text-sm text-muted-foreground mt-1">Who did what, when — across the workspace.</p>
        </div>
        <button @click="load" class="btn-secondary text-xs px-3 py-1.5">Refresh</button>
      </div>

      <!-- Filters. All are applied SERVER-side and combine (AND); changing any resets to page 1.
           Never filter the loaded page locally — that would only search the current 20 rows. -->
      <div class="card p-3 flex flex-wrap items-end gap-2">
        <div class="flex-1 min-w-[180px]">
          <label class="text-[11px] text-muted-foreground block mb-1">Search</label>
          <input v-model="filters.q" @keyup.enter="apply" type="text" placeholder="name, id, detail…"
            class="input text-sm w-full" />
        </div>
        <div>
          <label class="text-[11px] text-muted-foreground block mb-1">Resource</label>
          <select v-model="filters.resource_type" @change="apply" class="input text-sm">
            <option value="">All resources</option>
            <option value="user">Users</option>
            <option value="portal">Portals</option>
            <option value="vector">Vector</option>
            <option value="raster">Raster</option>
            <option value="source">Sources</option>
            <option value="token">Tokens</option>
          </select>
        </div>
        <div>
          <label class="text-[11px] text-muted-foreground block mb-1">Action</label>
          <select v-model="filters.action" @change="apply" class="input text-sm">
            <option value="">All actions</option>
            <option v-for="a in actions" :key="a" :value="a">{{ a }}</option>
          </select>
        </div>
        <div>
          <label class="text-[11px] text-muted-foreground block mb-1">Who</label>
          <select v-model="filters.actor_id" @change="apply" class="input text-sm">
            <option value="">Anyone</option>
            <option v-for="u in usersStore.users" :key="u.id" :value="u.id">{{ u.name || u.email }}</option>
          </select>
        </div>
        <div>
          <label class="text-[11px] text-muted-foreground block mb-1">When</label>
          <select v-model="filters.period" @change="apply" class="input text-sm">
            <option v-for="p in PERIODS" :key="p.value" :value="p.value">{{ p.label }}</option>
          </select>
        </div>
        <div>
          <label class="text-[11px] text-muted-foreground block mb-1">Per page</label>
          <select v-model.number="limit" @change="apply" class="input text-sm">
            <option v-for="n in [20, 50, 100, 200]" :key="n" :value="n">{{ n }}</option>
          </select>
        </div>
        <button v-if="anyFilter" @click="reset" class="text-xs text-muted-foreground hover:text-foreground px-2 py-2">
          Clear
        </button>
      </div>

      <div class="card overflow-x-auto">
        <div v-if="loading" class="px-4 py-10 text-center text-sm text-muted-foreground/70">Loading…</div>
        <div v-else-if="!entries.length" class="px-4 py-10 text-center text-sm text-muted-foreground/70">
          No activity yet.
        </div>
        <table v-else class="w-full text-sm">
          <thead class="text-left text-[11px] uppercase tracking-wider text-muted-foreground/70 border-b border-border/60">
            <tr>
              <th class="px-4 py-2 font-medium">When</th>
              <th class="px-4 py-2 font-medium">Who</th>
              <th class="px-4 py-2 font-medium">Action</th>
              <th class="px-4 py-2 font-medium">Detail</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="e in entries" :key="e.id" class="border-b border-border/40 hover:bg-muted/40">
              <td class="px-4 py-2 text-xs text-muted-foreground whitespace-nowrap" :title="e.created_at">{{ fmt(e.created_at) }}</td>
              <td class="px-4 py-2 text-foreground/85 whitespace-nowrap">
                <button v-if="e.actor_id" @click="showUser(e)"
                  class="text-primary hover:underline underline-offset-2 decoration-primary/40">{{ e.actor_name || 'User #' + e.actor_id }}</button>
                <span v-else>{{ e.actor_name || '—' }}</span>
              </td>
              <td class="px-4 py-2 whitespace-nowrap">
                <span class="text-[11px] font-mono px-1.5 py-0.5 rounded" :class="badge(e.action)">{{ e.action }}</span>
              </td>
              <td class="px-4 py-2 text-xs text-muted-foreground">{{ summarize(e) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <div v-if="total" class="flex items-center justify-between gap-3 flex-wrap">
        <p class="text-[11px] text-muted-foreground/70">
          {{ rangeStart }}–{{ rangeEnd }} of {{ total.toLocaleString() }}{{ anyFilter ? ' matching' : '' }}
        </p>
        <div v-if="pageCount > 1" class="flex items-center gap-1.5">
          <button @click="go(0)" :disabled="page === 1" class="btn-secondary text-xs px-2 py-1 disabled:opacity-40">«</button>
          <button @click="go(offset - limit)" :disabled="page === 1" class="btn-secondary text-xs px-2 py-1 disabled:opacity-40">Prev</button>
          <span class="text-xs text-muted-foreground px-1">Page {{ page }} / {{ pageCount }}</span>
          <button @click="go(offset + limit)" :disabled="page === pageCount" class="btn-secondary text-xs px-2 py-1 disabled:opacity-40">Next</button>
          <button @click="go((pageCount - 1) * limit)" :disabled="page === pageCount" class="btn-secondary text-xs px-2 py-1 disabled:opacity-40">»</button>
        </div>
      </div>
    </div>

    <!-- User info popup (click a "Who" cell) -->
    <div v-if="userPopup" class="fixed inset-0 z-50 flex items-center justify-center p-4"
      @click.self="userPopup = null">
      <div class="absolute inset-0 bg-black/40"></div>
      <div class="relative card w-full max-w-sm p-5 space-y-4">
        <button @click="userPopup = null"
          class="absolute top-3 right-3 text-muted-foreground/60 hover:text-foreground text-lg leading-none">×</button>
        <div class="flex items-center gap-4">
          <div class="w-12 h-12 rounded-full bg-primary/10 text-primary flex items-center justify-center text-sm font-semibold flex-shrink-0">
            {{ popupInitials }}
          </div>
          <div class="min-w-0">
            <div class="flex items-center gap-2">
              <span class="text-base font-semibold text-foreground truncate">{{ userPopup.name }}</span>
              <span v-if="userPopup.role" class="text-[10px] px-1.5 py-0.5 rounded font-medium" :class="roleBadge(userPopup.role)">
                {{ userPopup.role }}
              </span>
            </div>
            <div class="text-xs text-muted-foreground truncate">{{ userPopup.email || '—' }}</div>
          </div>
        </div>
        <div v-if="userPopup.found" class="grid grid-cols-4 gap-2 text-center">
          <div v-for="s in popupStats" :key="s.label" class="rounded-lg bg-muted/50 py-2">
            <div class="text-sm font-semibold text-foreground">{{ s.value }}</div>
            <div class="text-[10px] text-muted-foreground uppercase tracking-wide">{{ s.label }}</div>
          </div>
        </div>
        <p v-else class="text-xs text-muted-foreground">
          This user is no longer in the workspace — showing the name recorded at the time of the action.
        </p>
        <router-link v-if="userPopup.found" to="/users" @click="userPopup = null"
          class="block text-center text-xs text-primary hover:underline underline-offset-2">Manage in Users →</router-link>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { listAudit, listAuditActions } from '@/api'
import { useUsersStore } from '@/stores/users'

// Date presets. Each returns the START instant in the VIEWER's local timezone — that is why the
// boundary is computed here and not on the server: only the browser knows where the user's day,
// week and year begin. The server just gets an absolute ISO instant.
const PERIODS = [
  { value: '', label: 'Any time' },
  { value: 'today', label: 'Today' },
  { value: 'week', label: 'This week' },
  { value: 'month', label: 'This month' },
  { value: 'quarter', label: 'Last 3 months' },
  { value: 'year', label: 'This year' },
]

function periodStart(value) {
  if (!value) return null
  const d = new Date()
  d.setHours(0, 0, 0, 0)
  if (value === 'today') return d
  if (value === 'week') {
    const dow = (d.getDay() + 6) % 7        // Monday-first
    d.setDate(d.getDate() - dow)
    return d
  }
  if (value === 'month') { d.setDate(1); return d }
  if (value === 'quarter') { d.setMonth(d.getMonth() - 3); return d }
  if (value === 'year') { d.setMonth(0, 1); return d }
  return null
}

const entries = ref([])
const actions = ref([])
const loading = ref(true)
const total = ref(0)
const limit = ref(20)
const offset = ref(0)
const filters = reactive({ q: '', resource_type: '', action: '', actor_id: '', period: '' })

const anyFilter = computed(() =>
  !!(filters.q || filters.resource_type || filters.action || filters.actor_id || filters.period))
const page = computed(() => Math.floor(offset.value / limit.value) + 1)
const pageCount = computed(() => Math.max(1, Math.ceil(total.value / limit.value)))
const rangeStart = computed(() => (total.value ? offset.value + 1 : 0))
const rangeEnd = computed(() => Math.min(offset.value + entries.value.length, total.value))

const usersStore = useUsersStore()
const userPopup = ref(null)

async function showUser(e) {
  // Reuse the (admin-gated) users store — same data as the Users tab. Fetch lazily on first click.
  if (!usersStore.users.length) { try { await usersStore.fetchAll() } catch { /* ignore */ } }
  const u = usersStore.users.find(x => x.id === e.actor_id)
  userPopup.value = u
    ? { found: true, id: u.id, name: u.name, email: u.email, role: u.role,
        vector_count: u.vector_count || 0, raster_count: u.raster_count || 0,
        portal_count: u.portal_count || 0, source_count: u.source_count || 0 }
    : { found: false, name: e.actor_name || ('User #' + e.actor_id), email: '', role: '' }
}

const popupInitials = computed(() =>
  (userPopup.value?.name || '?').split(/\s+/).map(w => w[0]).slice(0, 2).join('').toUpperCase())

const popupStats = computed(() => {
  const u = userPopup.value
  if (!u) return []
  return [
    { label: 'layers', value: (u.vector_count || 0) + (u.raster_count || 0) },
    { label: 'portals', value: u.portal_count || 0 },
    { label: 'sources', value: u.source_count || 0 },
    { label: 'rasters', value: u.raster_count || 0 },
  ]
})

function roleBadge(role) {
  return {
    owner: 'bg-amber-500/15 text-amber-400',
    admin: 'bg-violet-500/15 text-violet-400',
    editor: 'bg-blue-500/15 text-blue-400',
    viewer: 'bg-muted text-muted-foreground',
  }[role] || 'bg-muted text-muted-foreground'
}

async function load() {
  loading.value = true
  try {
    const params = { limit: limit.value, offset: offset.value }
    if (filters.q) params.q = filters.q.trim()
    if (filters.resource_type) params.resource_type = filters.resource_type
    if (filters.action) params.action = filters.action
    if (filters.actor_id) params.actor_id = filters.actor_id
    const start = periodStart(filters.period)
    if (start) params.since = start.toISOString()
    const { data } = await listAudit(params)
    entries.value = data.items || []
    total.value = data.total || 0
    // A filter change can leave us past the end (e.g. page 7 of a now-shorter result) — step back
    // to the last real page instead of showing an empty table.
    if (!entries.value.length && offset.value > 0 && total.value > 0) {
      offset.value = Math.max(0, (Math.ceil(total.value / limit.value) - 1) * limit.value)
      return load()
    }
  } catch {
    entries.value = []
    total.value = 0
  } finally {
    loading.value = false
  }
}

function apply() { offset.value = 0; load() }   // any filter change restarts at page 1
function go(next) {
  offset.value = Math.max(0, Math.min(next, (pageCount.value - 1) * limit.value))
  load()
}
function reset() {
  Object.assign(filters, { q: '', resource_type: '', action: '', actor_id: '', period: '' })
  apply()
}

onMounted(async () => {
  load()
  // The "Who" and "Action" pickers need the real option sets; both are admin-gated like this page.
  try { if (!usersStore.users.length) await usersStore.fetchAll() } catch { /* ignore */ }
  try { actions.value = (await listAuditActions()).data } catch { /* ignore */ }
})

function fmt(s) { return new Date(s).toLocaleString() }

function badge(action) {
  if (action.includes('delete')) return 'bg-red-500/15 text-red-400'
  if (action.includes('publish')) return 'bg-emerald-500/15 text-emerald-400'
  if (action.startsWith('auth')) return 'bg-sky-500/15 text-sky-400'
  if (action.includes('role') || action.includes('ownership') || action.includes('invite'))
    return 'bg-violet-500/15 text-violet-400'
  return 'bg-muted text-muted-foreground'
}

function summarize(e) {
  const d = e.detail || {}
  const bits = []
  if (d.name) bits.push(d.name)
  if (d.email) bits.push(d.email)
  if (d.title) bits.push(d.title)
  if (d.from && d.to) bits.push(`${d.from} → ${d.to}`)
  else if (d.to) bits.push(`→ ${d.to}`)
  if (d.visibility) bits.push(d.visibility)
  if (d.access) bits.push(d.access)
  if (d.method) bits.push(d.method)
  if (d.role) bits.push(d.role)
  if (Array.isArray(d.scopes)) bits.push(d.scopes.join(' '))
  const resource = e.resource_type ? `${e.resource_type}${e.resource_id ? ' #' + e.resource_id : ''}` : ''
  return [resource, bits.join(' · ')].filter(Boolean).join('  —  ')
}
</script>
