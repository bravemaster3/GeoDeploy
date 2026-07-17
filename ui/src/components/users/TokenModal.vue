<template>
  <Teleport to="body">
  <div class="fixed inset-0 bg-gray-900/50 backdrop-blur-sm flex items-center justify-center z-50 p-4">
    <div class="card w-full max-w-md p-6 space-y-4 shadow-2xl">
      <div class="flex items-center justify-between">
        <h2 class="text-lg font-semibold">Create API token</h2>
        <button @click="$emit('close')" class="text-muted-foreground/70 hover:text-foreground text-xl leading-none">&times;</button>
      </div>

      <!-- Success state: the raw secret, shown once -->
      <template v-if="created">
        <p class="text-sm text-foreground/85">
          Token <span class="font-mono">{{ created.prefix }}…</span> created.
        </p>
        <CopyLink :url="created.token" hint="Copy it now — it will not be shown again." />
        <p class="text-[11px] text-muted-foreground">
          Expires in {{ form.expires_in_days }} days. Send it as
          <code class="font-mono">Authorization: Bearer &lt;token&gt;</code>.
        </p>
        <div class="flex justify-end pt-1">
          <button @click="$emit('close')" class="btn-primary text-sm">Done</button>
        </div>
      </template>

      <!-- Form state -->
      <template v-else>
        <p class="text-xs text-muted-foreground">
          A scoped token for scripts and the GeoLibre/QGIS plugins. It acts as you, limited to the
          scopes you pick (never above your role).
        </p>

        <div>
          <label class="text-xs text-muted-foreground block mb-1">Name</label>
          <input v-model="form.name" placeholder="QGIS plugin — my laptop"
            class="input w-full text-sm" @keydown.enter="submit" />
        </div>

        <div>
          <label class="text-xs text-muted-foreground block mb-1">Quick preset</label>
          <div class="grid grid-cols-3 gap-2">
            <button v-for="p in presets" :key="p.label" type="button"
              class="p-2 rounded-lg border text-xs font-medium transition-colors"
              :class="isPreset(p) ? 'border-primary bg-primary/10 text-primary' : 'border-border hover:border-muted-foreground/40 text-foreground/85'"
              @click="applyPreset(p)">{{ p.label }}</button>
          </div>
        </div>

        <div>
          <label class="text-xs text-muted-foreground block mb-1">Scopes</label>
          <div class="space-y-1">
            <label v-for="s in visibleScopes" :key="s.id"
              class="flex items-start gap-2.5 p-2 rounded-lg border cursor-pointer transition-colors"
              :class="form.scopes.includes(s.id) ? 'border-primary bg-primary/10' : 'border-border hover:border-muted-foreground/40'">
              <input type="checkbox" :value="s.id" v-model="form.scopes" class="mt-0.5 accent-primary flex-shrink-0" />
              <span class="min-w-0">
                <span class="block text-xs font-mono font-medium text-foreground/90">{{ s.id }}</span>
                <span class="block text-[10px] text-muted-foreground/70">{{ s.desc }}</span>
              </span>
            </label>
          </div>
        </div>

        <div>
          <label class="text-xs text-muted-foreground block mb-1">Expires</label>
          <select v-model.number="form.expires_in_days" class="input w-full text-sm">
            <option :value="30">30 days</option>
            <option :value="90">90 days</option>
            <option :value="365">365 days</option>
          </select>
        </div>

        <div v-if="error" class="text-sm text-red-400 bg-red-500/15 p-3 rounded-lg">{{ error }}</div>

        <div class="flex justify-end gap-2 pt-1">
          <button @click="$emit('close')" class="btn-secondary text-sm">Cancel</button>
          <button @click="submit" :disabled="!canSubmit || saving" class="btn-primary text-sm">
            {{ saving ? '…' : 'Create token' }}
          </button>
        </div>
      </template>
    </div>
  </div>
  </Teleport>
</template>

<script setup>
import { computed, ref } from 'vue'
import { createToken } from '@/api'
import { useAuthStore } from '@/stores/auth'
import CopyLink from './CopyLink.vue'

const emit = defineEmits(['close', 'created'])
const auth = useAuthStore()

// Mirrors deps.SCOPES on the backend. `users:admin` is only offered to admins/owner.
const ALL_SCOPES = [
  { id: 'data:read', desc: 'Read layers, sources and features' },
  { id: 'data:write', desc: 'Upload, prepare, edit and delete data' },
  { id: 'portal:read', desc: 'Read portals and their config' },
  { id: 'portal:write', desc: 'Create and edit portal drafts' },
  { id: 'portal:publish', desc: 'Publish and unpublish portals' },
  { id: 'users:admin', desc: 'Manage users and invitations', admin: true },
]
const visibleScopes = computed(() => ALL_SCOPES.filter(s => !s.admin || auth.isAdmin))

const presets = [
  { label: 'Read-only', scopes: ['data:read', 'portal:read'] },
  { label: 'Publish', scopes: ['data:read', 'portal:read', 'portal:publish'] },
  { label: 'Full editor', scopes: ['data:read', 'data:write', 'portal:read', 'portal:write', 'portal:publish'] },
]

const form = ref({ name: '', scopes: ['data:read', 'portal:read'], expires_in_days: 90 })
const saving = ref(false)
const error = ref('')
const created = ref(null)

const canSubmit = computed(() => form.value.name.trim() && form.value.scopes.length > 0)
const sameSet = (a, b) => a.length === b.length && a.every(x => b.includes(x))
const isPreset = (p) => sameSet(form.value.scopes, p.scopes)
function applyPreset(p) { form.value.scopes = [...p.scopes] }

async function submit() {
  if (!canSubmit.value || saving.value) return
  saving.value = true
  error.value = ''
  try {
    const { data } = await createToken({
      name: form.value.name.trim(),
      scopes: form.value.scopes,
      expires_in_days: form.value.expires_in_days,
    })
    created.value = data
    emit('created')
  } catch (err) {
    error.value = err.response?.data?.detail || err.message
  } finally {
    saving.value = false
  }
}
</script>
