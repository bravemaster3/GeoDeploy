<template>
  <div class="min-h-screen flex items-center justify-center bg-background text-foreground p-4">
    <div class="w-full max-w-sm card p-6 space-y-4 shadow-2xl">
      <div v-if="loading" class="text-sm text-muted-foreground text-center py-6">Checking…</div>

      <template v-else-if="mode === 'password'">
        <div>
          <h1 class="text-lg font-semibold">{{ title || 'Protected portal' }}</h1>
          <p class="text-xs text-muted-foreground mt-1">Enter the password to view this portal.</p>
        </div>
        <form @submit.prevent="submit" class="space-y-3">
          <input v-model="password" type="password" autofocus placeholder="Password"
            class="input w-full" :disabled="busy" />
          <p v-if="error" class="text-xs text-red-400">{{ error }}</p>
          <button type="submit" :disabled="busy || !password"
            class="btn-primary w-full justify-center disabled:opacity-60">
            {{ busy ? 'Unlocking…' : 'Unlock' }}
          </button>
        </form>
      </template>

      <template v-else-if="mode === 'error'">
        <h1 class="text-lg font-semibold">Portal unavailable</h1>
        <p class="text-sm text-muted-foreground">{{ error || 'This portal could not be found.' }}</p>
        <a href="/" class="btn-secondary w-full justify-center">Go to GeoDeploy</a>
      </template>
    </div>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

const route = useRoute()
const router = useRouter()

const loading = ref(true)
const mode = ref('')          // 'password' | 'error'
const title = ref('')
const password = ref('')
const busy = ref(false)
const error = ref('')

// The portal the visitor was trying to reach (nginx forwards it as ?next=/portals/{slug}/…).
// Only same-origin /portals/ paths are honored (no open redirect).
const next = typeof route.query.next === 'string' && route.query.next.startsWith('/portals/')
  ? route.query.next : ''
const slug = next ? next.split('/').filter(Boolean)[1] : ''

onMounted(async () => {
  if (!slug) { mode.value = 'error'; loading.value = false; return }
  try {
    const r = await fetch(`/api/portals/${encodeURIComponent(slug)}/gate`)
    if (!r.ok) { mode.value = 'error'; loading.value = false; return }
    const info = await r.json()
    title.value = info.title || ''
    if (info.access_type === 'password') {
      mode.value = 'password'
    } else {
      // Organization / owner tiers need a signed-in user → hand off to login, which returns here.
      router.replace({ path: '/login', query: { next } })
      return
    }
  } catch {
    mode.value = 'error'
  } finally {
    loading.value = false
  }
})

async function submit() {
  if (!password.value || busy.value) return
  busy.value = true
  error.value = ''
  try {
    const r = await fetch(`/api/portals/${encodeURIComponent(slug)}/unlock`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ password: password.value }),
    })
    if (r.status === 204) {
      window.location.assign(next)   // full navigation — the portal is a static bundle, now unlocked
    } else if (r.status === 401) {
      error.value = 'Incorrect password.'
    } else {
      error.value = 'Could not unlock — try again.'
    }
  } catch {
    error.value = 'Could not unlock — try again.'
  } finally {
    busy.value = false
  }
}
</script>
