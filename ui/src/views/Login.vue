<template>
  <div class="min-h-screen bg-muted/40 flex items-center justify-center p-4">
    <div class="w-full max-w-sm">
      <div class="text-center mb-8">
        <h1 class="text-2xl font-bold text-foreground">GeoDeploy</h1>
      </div>

      <!-- Forgot-password (only offered when the instance has outgoing email configured) -->
      <div v-if="mode === 'forgot'" class="card p-6 space-y-4">
        <p class="text-sm text-foreground/85">Reset your password</p>
        <template v-if="!forgotDone">
          <p class="text-xs text-muted-foreground">
            Enter your account email — if it exists, we'll send a single-use reset link (valid 24 h).
          </p>
          <div>
            <label class="label">Email</label>
            <input v-model="email" type="email" class="input" @keydown.enter="submitForgot" />
          </div>
          <button @click="submitForgot" :disabled="busy" class="btn-primary w-full justify-center">
            <span v-if="busy" class="animate-spin">⟳</span>
            Send reset link
          </button>
        </template>
        <p v-else class="text-sm text-green-400">
          If that email belongs to an account, a reset link is on its way. Check your inbox.
        </p>
        <button @click="mode = 'login'; forgotDone = false" class="text-xs text-muted-foreground hover:text-foreground w-full text-center">
          ← Back to sign in
        </button>
      </div>

      <!-- Sign in -->
      <div v-else class="card p-6 space-y-4">
        <div>
          <label class="label">Email</label>
          <input v-model="email" type="email" class="input" @keydown.enter="submit" />
        </div>
        <div>
          <label class="label">Password</label>
          <input v-model="password" type="password" class="input" @keydown.enter="submit" />
        </div>
        <div v-if="error" class="text-sm text-red-400">{{ error }}</div>
        <button @click="submit" :disabled="busy" class="btn-primary w-full justify-center">
          <span v-if="busy" class="animate-spin">⟳</span>
          Sign in
        </button>
        <button v-if="emailEnabled" @click="mode = 'forgot'"
          class="text-xs text-muted-foreground hover:text-foreground w-full text-center">
          Forgot password?
        </button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { forgotPassword, getSetupStatus } from '@/api'

const router = useRouter()
const route = useRoute()
const auth = useAuthStore()
const email = ref('')
const password = ref('')
const error = ref('')
const busy = ref(false)
const mode = ref('login')
const forgotDone = ref(false)
const emailEnabled = ref(false)

onMounted(async () => {
  try {
    const { data } = await getSetupStatus()
    emailEnabled.value = !!data.email_enabled
  } catch { /* no link shown if the check fails */ }
})

async function submit() {
  error.value = ''
  busy.value = true
  try {
    await auth.loginUser(email.value, password.value)
    // A gated portal bounced the visitor here with ?next=/portals/…; send them back with a FULL
    // navigation (the portal is a static bundle, not an SPA route) now that the cookie is set.
    // Only same-origin /portals/ paths are honored (no open redirect).
    const next = route.query.next
    if (typeof next === 'string' && next.startsWith('/portals/')) {
      window.location.assign(next)
    } else {
      router.push('/data')
    }
  } catch {
    error.value = 'Invalid email or password.'
  } finally {
    busy.value = false
  }
}

async function submitForgot() {
  if (!/.+@.+\..+/.test(email.value.trim()) || busy.value) return
  busy.value = true
  try {
    await forgotPassword(email.value.trim().toLowerCase())
  } catch { /* always show the same outcome — the endpoint is anti-enumeration anyway */ }
  finally {
    busy.value = false
    forgotDone.value = true
  }
}
</script>
