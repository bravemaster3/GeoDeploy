<template>
  <div class="min-h-screen bg-muted/40 flex items-center justify-center p-4">
    <div class="w-full max-w-sm">
      <div class="text-center mb-8">
        <img :src="logoDark" alt="" class="w-14 h-14 mx-auto mb-3" />
        <h1 class="text-2xl font-bold text-foreground">GeoDeploy</h1>
      </div>

      <!-- DEMO: join with a name. Rendered only when the server says this instance is a demo, so a
           normal install's login page is byte-identical to what it was. Placed first and styled as
           the primary action because on a demo it is the ONLY thing a visitor can usefully do —
           they have no account to sign in with. -->
      <!-- Placeholder while the demo check is in flight. Same height as a card, so the layout does
           not jump when the real one arrives — a skeleton is honest about not knowing yet, whereas
           rendering the sign-in form was a guess that was wrong half the time. -->
      <div v-if="!demoChecked" class="card p-6 h-40 animate-pulse opacity-40" aria-hidden="true"></div>

      <div v-else-if="isDemo && mode === 'login'" class="card p-6 space-y-4 mb-4 border-primary/40">
        <div>
          <p class="text-sm font-medium text-foreground">Try GeoDeploy</p>
          <p class="text-xs text-muted-foreground mt-1">
            No sign-up, no email. Pick a name and you are in.
          </p>
        </div>
        <div>
          <label class="label">Your name</label>
          <input v-model="demoName" class="input" placeholder="e.g. Ada" maxlength="60"
            @keydown.enter="joinDemo" />
        </div>
        <div v-if="demoError" class="text-sm text-red-400">{{ demoError }}</div>
        <button @click="joinDemo" :disabled="demoBusy || !demoName.trim()"
          class="btn-primary w-full justify-center disabled:opacity-50">
          <span v-if="demoBusy" class="animate-spin">⟳</span>
          Start exploring
        </button>
        <p class="text-[11px] text-muted-foreground/70 leading-snug">
          Everything in this demo is wiped about once an hour, and everyone shares one workspace —
          treat it as a sandbox, not storage.
        </p>
      </div>

      <!-- Forgot-password (only offered when the instance has outgoing email configured) -->
      <div v-if="demoChecked && mode === 'forgot'" class="card p-6 space-y-4">
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

      <!-- Sign in.
           On a DEMO this is collapsed behind a quiet link. A visitor's only useful action is "pick a
           name and go", and a half-visible email/password form directly beneath that reads as a
           required second step — several people scrolled into it and stalled. The operator still
           needs it on the same page, so it is one click away rather than gone. -->
      <div v-else-if="demoChecked && (!isDemo || showSignIn)" class="card p-6 space-y-4">
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

        <!-- SSO (A-04) — shown only when an OIDC provider is configured -->
        <template v-if="ssoEnabled">
          <div class="relative py-1 text-center">
            <span class="absolute inset-x-0 top-1/2 border-t border-border" />
            <span class="relative z-10 text-[11px] text-muted-foreground/70 bg-card px-2">or</span>
          </div>
          <button @click="ssoLogin" class="btn-secondary w-full justify-center">{{ ssoLabel }}</button>
        </template>
        <button v-if="isDemo" @click="showSignIn = false"
          class="text-xs text-muted-foreground hover:text-foreground w-full text-center">
          ← Back to the demo
        </button>
      </div>

      <!-- Demo: the way back to a real sign-in. Pushed a full screen DOWN, not merely styled small —
           a visitor should never see it, and anything on the first screen gets read as part of the
           flow. `85vh` inline rather than a Tailwind arbitrary value so it does not depend on the
           JIT scanning this file. The page grows past the viewport, so the outer `items-center` stops
           having any effect and the join card sits at the top where it belongs. -->
      <p v-else-if="demoChecked && isDemo && mode === 'login'" class="text-center" style="margin-top: 85vh">
        <button @click="showSignIn = true"
          class="text-[11px] text-muted-foreground/50 hover:text-muted-foreground">
          Instance owner? Sign in with an account
        </button>
      </p>
    </div>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import logoDark from '@/assets/logo-dark.svg'
import { forgotPassword, getSetupStatus, oidcStatus, getDemoInfo, demoJoin } from '@/api'

const router = useRouter()
const route = useRoute()
const auth = useAuthStore()
const email = ref('')
const password = ref('')
const error = ref('')
const busy = ref(false)
const mode = ref('login')

// ── Demo join ──────────────────────────────────────────────────────────────────────────────
// isDemo stays false unless the SERVER says otherwise, so nothing below renders on a normal install.
const isDemo = ref(false)
// Whether the demo CHECK has answered — distinct from its answer. `isDemo` alone cannot express
// "we do not know yet", so the page rendered the normal sign-in card during the round trip and then
// swapped it for the join card: a visible flash of the wrong page on every demo load.
const demoChecked = ref(false)
// Demo only: the email/password card starts hidden and is revealed by the link below it.
const showSignIn = ref(false)
const demoName = ref('')
const demoBusy = ref(false)
const demoError = ref('')

async function joinDemo() {
  const name = demoName.value.trim()
  if (!name) return
  demoBusy.value = true
  demoError.value = ''
  try {
    const { data } = await demoJoin(name)
    auth.setToken(data.access_token)
    await auth.fetchMe()
    router.push('/data')
  } catch (e) {
    demoError.value = e.response?.data?.detail || 'Could not start the demo. Try again.'
  } finally {
    demoBusy.value = false
  }
}
const forgotDone = ref(false)
const emailEnabled = ref(false)
const ssoEnabled = ref(false)
const ssoLabel = ref('Single sign-on')

onMounted(async () => {
  // Failure means "not a demo" — a normal install answers {demo:false}, and an error must not turn
  // the login page into a join form.
  try { isDemo.value = !!(await getDemoInfo()).data.demo } catch { isDemo.value = false }
  finally { demoChecked.value = true }
  // An SSO refusal (unknown account, blocked domain, provider error) bounces here with ?sso_error=.
  if (typeof route.query.sso_error === 'string') error.value = route.query.sso_error
  try {
    const { data } = await getSetupStatus()
    emailEnabled.value = !!data.email_enabled
  } catch { /* no link shown if the check fails */ }
  try {
    const { data } = await oidcStatus()
    ssoEnabled.value = !!data.enabled
    ssoLabel.value = data.label || 'Single sign-on'
  } catch { /* no SSO button if the check fails */ }
})

function ssoLogin() {
  window.location.assign('/api/auth/oidc/login')  // top-level nav → provider → /auth/oidc/callback
}

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
