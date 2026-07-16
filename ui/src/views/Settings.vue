<template>
  <div class="p-6 lg:p-8">
    <div class="max-w-4xl mx-auto space-y-6">
      <div>
        <h1 class="text-2xl font-semibold tracking-tight text-foreground">Settings</h1>
        <p class="text-sm text-muted-foreground mt-1">Manage infrastructure, storage, and your account.</p>
      </div>

      <!-- Infrastructure health (admin/owner — service control is require_admin server-side) -->
      <section v-if="auth.isAdmin" class="card overflow-hidden">
        <header class="flex items-center gap-3 px-5 py-3.5 border-b border-border/60">
          <span class="w-9 h-9 rounded-lg bg-indigo-500/15 text-indigo-400 flex items-center justify-center flex-shrink-0">
            <ServerIcon class="w-5 h-5" />
          </span>
          <div class="flex-1 min-w-0">
            <h2 class="text-sm font-semibold text-foreground">Infrastructure</h2>
            <p class="text-xs text-muted-foreground/70">Container health &amp; controls</p>
          </div>
          <button @click="systemStore.refreshHealth()" class="btn-secondary text-xs px-3 py-1.5">
            <RefreshIcon class="w-3.5 h-3.5" /> Refresh
          </button>
          <button @click="reloadMartin" :disabled="martinBusy" class="btn-secondary text-xs px-3 py-1.5">
            {{ martinBusy ? 'Reloading…' : 'Reload Martin' }}
          </button>
        </header>
        <div class="p-2">
          <div v-if="!systemStore.health.length" class="px-3 py-6 text-sm text-muted-foreground/70 text-center">Loading…</div>
          <div v-for="svc in systemStore.health" :key="svc.name"
            class="flex items-center gap-3 px-3 py-2 rounded-lg hover:bg-muted/60">
            <span class="w-2 h-2 rounded-full flex-shrink-0" :class="dotClass(svc.status)" />
            <span class="text-sm font-medium text-foreground/85 capitalize flex-1 min-w-0 truncate">{{ svc.name }}</span>
            <span class="inline-flex items-center text-[11px] font-medium px-2 py-0.5 rounded-full" :class="pillClass(svc.status)">
              {{ svc.status }}
            </span>
            <div v-if="svc.controllable" class="flex items-center gap-1 w-16 justify-end">
              <button v-if="!['running','healthy'].includes(svc.status)"
                @click="svcAction(svc.name, 'start')" :disabled="busySvc === svc.name"
                class="svc-btn text-green-400" title="Start">▶</button>
              <button v-else @click="svcAction(svc.name, 'stop')" :disabled="busySvc === svc.name"
                class="svc-btn text-red-500" title="Stop">■</button>
              <button @click="svcAction(svc.name, 'restart')" :disabled="busySvc === svc.name"
                class="svc-btn text-muted-foreground" title="Restart">↻</button>
            </div>
            <div v-else class="w-16" />
          </div>
          <p v-if="martinMsg" class="px-3 pt-1 text-xs" :class="martinMsg.ok ? 'text-green-400' : 'text-red-400'">
            {{ martinMsg.text }}
          </p>
        </div>
      </section>

      <!-- Storage (admin/owner — storage-stats is require_admin server-side) -->
      <section v-if="auth.isAdmin && systemStore.stats" class="card overflow-hidden">
        <header class="flex items-center gap-3 px-5 py-3.5 border-b border-border/60">
          <span class="w-9 h-9 rounded-lg bg-amber-500/15 text-amber-400 flex items-center justify-center flex-shrink-0">
            <HardDriveIcon class="w-5 h-5" />
          </span>
          <div class="flex-1 min-w-0">
            <h2 class="text-sm font-semibold text-foreground">Storage</h2>
            <p class="text-xs text-muted-foreground/70">Usage across your data</p>
          </div>
        </header>
        <div class="p-5 space-y-4">
          <StorageBar :used="systemStore.stats.used_bytes" :total="systemStore.stats.total_bytes" />
          <div class="grid grid-cols-3 gap-3">
            <div v-for="tile in statTiles" :key="tile.label" class="rounded-lg border border-border/60 bg-muted/40 p-4 text-center">
              <div class="text-2xl font-bold text-foreground">{{ tile.value }}</div>
              <div class="text-xs text-muted-foreground mt-0.5">{{ tile.label }}</div>
            </div>
          </div>
        </div>
      </section>

      <!-- Outgoing email (generic SMTP — admin/owner) -->
      <section v-if="auth.isAdmin" class="card overflow-hidden">
        <header class="flex items-center gap-3 px-5 py-3.5 border-b border-border/60">
          <span class="w-9 h-9 rounded-lg bg-sky-500/15 text-sky-400 flex items-center justify-center flex-shrink-0">
            <MailIcon class="w-5 h-5" />
          </span>
          <div class="flex-1 min-w-0">
            <h2 class="text-sm font-semibold text-foreground">Email</h2>
            <p class="text-xs text-muted-foreground/70">Invites, password resets — any SMTP provider</p>
          </div>
          <span v-if="emailForm" class="text-[11px] font-medium px-2 py-0.5 rounded-full"
            :class="emailConfigured ? 'bg-green-500/15 text-green-400' : 'bg-muted text-muted-foreground'">
            {{ emailConfigured ? 'configured' : 'not configured' }}
          </span>
        </header>
        <div v-if="emailForm" class="p-5 space-y-3 max-w-lg">
          <p class="text-xs text-muted-foreground">
            Optional — without it, invite and reset links are copy-and-send. Works with any provider:
            <span class="font-medium text-foreground/80">Resend</span> (host <code class="font-mono">smtp.resend.com</code>,
            port 465 TLS, user <code class="font-mono">resend</code>, password = API key) ·
            <span class="font-medium text-foreground/80">Brevo</span> (host <code class="font-mono">smtp-relay.brevo.com</code>,
            port 587 STARTTLS, ~300 free emails/day) · or your organisation's mail server.
          </p>
          <div class="grid grid-cols-3 gap-3">
            <div class="col-span-2">
              <label class="text-xs text-muted-foreground block mb-1">SMTP host</label>
              <input v-model="emailForm.smtp_host" type="text" placeholder="smtp.example.com" class="input w-full text-sm font-mono" />
            </div>
            <div>
              <label class="text-xs text-muted-foreground block mb-1">Port</label>
              <input v-model.number="emailForm.smtp_port" type="number" class="input w-full text-sm" />
            </div>
          </div>
          <div>
            <label class="text-xs text-muted-foreground block mb-1">Security</label>
            <div class="grid grid-cols-3 gap-2">
              <button v-for="s in ['starttls', 'tls', 'none']" :key="s" type="button"
                class="p-2 rounded-lg border text-xs font-medium transition-colors"
                :class="emailForm.smtp_security === s ? 'border-primary bg-primary/10 text-primary' : 'border-border hover:border-muted-foreground/40 text-foreground/85'"
                @click="emailForm.smtp_security = s">{{ s === 'tls' ? 'TLS (465)' : s === 'starttls' ? 'STARTTLS (587)' : 'None' }}</button>
            </div>
          </div>
          <div class="grid grid-cols-2 gap-3">
            <div>
              <label class="text-xs text-muted-foreground block mb-1">Username</label>
              <input v-model="emailForm.smtp_username" type="text" class="input w-full text-sm font-mono" />
            </div>
            <div>
              <label class="text-xs text-muted-foreground block mb-1">
                Password {{ emailHasPassword ? '(saved — blank keeps it)' : '' }}
              </label>
              <input v-model="emailForm.smtp_password" type="password" autocomplete="new-password" class="input w-full text-sm" />
            </div>
          </div>
          <div>
            <label class="text-xs text-muted-foreground block mb-1">From address</label>
            <input v-model="emailForm.email_from" type="email" placeholder="geodeploy@your-domain.org" class="input w-full text-sm" />
          </div>
          <p v-if="emailMsg" class="text-xs" :class="emailMsg.ok ? 'text-green-400' : 'text-red-400'">{{ emailMsg.text }}</p>
          <div class="flex gap-2">
            <button @click="saveEmail" :disabled="emailBusy" class="btn-primary text-xs px-3 py-1.5">
              {{ emailBusy === 'save' ? 'Saving…' : 'Save' }}
            </button>
            <button @click="testEmail" :disabled="emailBusy || !emailConfigured" class="btn-secondary text-xs px-3 py-1.5"
              title="Sends a test email to your own address">
              {{ emailBusy === 'test' ? 'Sending…' : 'Send test email' }}
            </button>
          </div>
        </div>
      </section>

      <!-- Account -->
      <section class="card overflow-hidden">
        <header class="flex items-center gap-3 px-5 py-3.5 border-b border-border/60">
          <span class="w-9 h-9 rounded-lg bg-primary/10 text-primary flex items-center justify-center flex-shrink-0">
            <UserIcon class="w-5 h-5" />
          </span>
          <h2 class="text-sm font-semibold text-foreground">Account</h2>
        </header>
        <div class="p-5 flex items-center gap-4">
          <span class="w-12 h-12 rounded-full bg-primary/10 text-primary flex items-center justify-center font-semibold flex-shrink-0">
            {{ initials }}
          </span>
          <div class="flex-1 min-w-0">
            <div class="flex items-center gap-2 min-w-0">
              <span class="text-sm font-medium text-foreground truncate">{{ auth.user?.name }}</span>
              <span v-if="auth.role" class="text-[10px] px-1.5 py-0.5 rounded font-medium flex-shrink-0" :class="roleBadge">
                {{ auth.role }}
              </span>
            </div>
            <div class="text-sm text-muted-foreground/70 truncate">{{ auth.user?.email }}</div>
          </div>
          <button @click="showPwForm = !showPwForm" class="btn-secondary text-xs px-3 py-1.5">Change password</button>
          <button @click="signOut" class="btn-secondary text-xs px-3 py-1.5">Sign out</button>
        </div>
        <!-- Change password (any role) -->
        <div v-if="showPwForm" class="px-5 pb-5 border-t border-border/60 pt-4 space-y-3 max-w-sm">
          <div>
            <label class="text-xs text-muted-foreground block mb-1">Current password</label>
            <input v-model="pwCurrent" type="password" class="input w-full text-sm" />
          </div>
          <div>
            <label class="text-xs text-muted-foreground block mb-1">New password (min 8 characters)</label>
            <input v-model="pwNew" type="password" class="input w-full text-sm" />
          </div>
          <div>
            <label class="text-xs text-muted-foreground block mb-1">Confirm new password</label>
            <input v-model="pwConfirm" type="password" class="input w-full text-sm" @keydown.enter="submitPassword" />
          </div>
          <p v-if="pwMsg" class="text-xs" :class="pwMsg.ok ? 'text-green-400' : 'text-red-400'">{{ pwMsg.text }}</p>
          <button @click="submitPassword" :disabled="!pwCanSubmit || pwBusy" class="btn-primary text-xs px-3 py-1.5">
            {{ pwBusy ? 'Saving…' : 'Save password' }}
          </button>
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
import { ServerIcon, HardDriveIcon, UserIcon, RefreshIcon, MailIcon } from './icons'
import api, { changePassword, controlService, getEmailSettings, sendTestEmail, updateEmailSettings } from '@/api'

const systemStore = useSystemStore()
const auth = useAuthStore()
const router = useRouter()
const martinBusy = ref(false)
const martinMsg = ref(null)
const busySvc = ref(null)

const initials = computed(() =>
  (auth.user?.name || '?').split(' ').map(w => w[0]).filter(Boolean).slice(0, 2).join('').toUpperCase())

const roleBadge = computed(() => ({
  owner: 'bg-amber-500/15 text-amber-400',
  admin: 'bg-violet-500/15 text-violet-400',
  editor: 'bg-blue-500/15 text-blue-400',
  viewer: 'bg-muted text-muted-foreground',
}[auth.role] || 'bg-muted text-muted-foreground'))

// Change password — martinMsg-style transient feedback (no toast system by convention)
const showPwForm = ref(false)
const pwCurrent = ref('')
const pwNew = ref('')
const pwConfirm = ref('')
const pwBusy = ref(false)
const pwMsg = ref(null)
const pwCanSubmit = computed(() =>
  pwCurrent.value && pwNew.value.length >= 8 && pwNew.value === pwConfirm.value)

async function submitPassword() {
  if (!pwCanSubmit.value || pwBusy.value) {
    if (pwNew.value && pwNew.value.length < 8) pwMsg.value = { ok: false, text: 'New password must be at least 8 characters.' }
    else if (pwConfirm.value && pwNew.value !== pwConfirm.value) pwMsg.value = { ok: false, text: 'Passwords do not match.' }
    return
  }
  pwBusy.value = true
  pwMsg.value = null
  try {
    await changePassword({ current_password: pwCurrent.value, new_password: pwNew.value })
    pwMsg.value = { ok: true, text: 'Password updated.' }
    pwCurrent.value = pwNew.value = pwConfirm.value = ''
    setTimeout(() => { pwMsg.value = null; showPwForm.value = false }, 2500)
  } catch (err) {
    pwMsg.value = { ok: false, text: err.response?.data?.detail || err.message }
  } finally {
    pwBusy.value = false
  }
}

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
  if (['running', 'healthy'].includes(s)) return 'bg-green-500/15 text-green-400'
  if (['unhealthy', 'stopped', 'exited'].includes(s)) return 'bg-red-500/15 text-red-400'
  return 'bg-muted text-muted-foreground'
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
  // Health/stats/email endpoints are admin-only server-side — don't fire doomed requests as editor/viewer.
  if (auth.isAdmin) {
    systemStore.refreshHealth()
    systemStore.refreshStats()
    loadEmail()
  }
})

// Outgoing email (generic SMTP, C-08a)
const emailForm = ref(null)
const emailHasPassword = ref(false)
const emailConfigured = ref(false)
const emailBusy = ref(null)   // null | 'save' | 'test'
const emailMsg = ref(null)

async function loadEmail() {
  try {
    const { data } = await getEmailSettings()
    emailHasPassword.value = data.has_password
    emailConfigured.value = data.configured
    emailForm.value = {
      smtp_host: data.smtp_host || '',
      smtp_port: data.smtp_port || 587,
      smtp_security: data.smtp_security || 'starttls',
      smtp_username: data.smtp_username || '',
      smtp_password: '',   // blank = keep the stored secret
      email_from: data.email_from || '',
    }
  } catch { /* section simply stays in loading state */ }
}

async function saveEmail() {
  emailBusy.value = 'save'
  emailMsg.value = null
  try {
    const { data } = await updateEmailSettings(emailForm.value)
    emailHasPassword.value = data.has_password
    emailConfigured.value = data.configured
    emailForm.value.smtp_password = ''
    emailMsg.value = { ok: true, text: data.configured ? 'Saved — email is enabled.' : 'Saved (host + from address required to enable).' }
  } catch (err) {
    emailMsg.value = { ok: false, text: err.response?.data?.detail || err.message }
  } finally {
    emailBusy.value = null
    setTimeout(() => { emailMsg.value = null }, 6000)
  }
}

async function testEmail() {
  emailBusy.value = 'test'
  emailMsg.value = null
  try {
    const { data } = await sendTestEmail()
    emailMsg.value = { ok: true, text: `Test email sent to ${data.to} — check the inbox.` }
  } catch (err) {
    emailMsg.value = { ok: false, text: err.response?.data?.detail || err.message }
  } finally {
    emailBusy.value = null
  }
}

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
