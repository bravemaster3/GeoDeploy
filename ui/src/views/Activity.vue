<template>
  <div class="p-6 lg:p-8">
    <div class="max-w-5xl mx-auto space-y-4">
      <div class="flex items-end justify-between gap-3 flex-wrap">
        <div>
          <h1 class="text-2xl font-semibold tracking-tight text-foreground">Activity</h1>
          <p class="text-sm text-muted-foreground mt-1">Who did what, when — across the workspace.</p>
        </div>
        <div class="flex items-center gap-2">
          <select v-model="filterType" @change="load" class="input text-sm">
            <option value="">All resources</option>
            <option value="user">Users</option>
            <option value="portal">Portals</option>
            <option value="vector">Vector</option>
            <option value="raster">Raster</option>
            <option value="source">Sources</option>
            <option value="token">Tokens</option>
          </select>
          <button @click="load" class="btn-secondary text-xs px-3 py-1.5">Refresh</button>
        </div>
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
      <p v-if="entries.length >= limit" class="text-[11px] text-muted-foreground/70 text-center">
        Showing the {{ limit }} most recent entries.
      </p>
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
import { ref, computed, onMounted } from 'vue'
import { listAudit } from '@/api'
import { useUsersStore } from '@/stores/users'

const entries = ref([])
const loading = ref(true)
const filterType = ref('')
const limit = 200

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
    const params = { limit }
    if (filterType.value) params.resource_type = filterType.value
    entries.value = (await listAudit(params)).data
  } catch {
    entries.value = []
  } finally {
    loading.value = false
  }
}
onMounted(load)

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
