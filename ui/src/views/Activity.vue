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
              <td class="px-4 py-2 text-foreground/85 whitespace-nowrap">{{ e.actor_name || '—' }}</td>
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
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { listAudit } from '@/api'

const entries = ref([])
const loading = ref(true)
const filterType = ref('')
const limit = 200

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
