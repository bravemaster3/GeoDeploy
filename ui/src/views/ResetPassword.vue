<template>
  <div class="min-h-screen bg-muted/40 flex items-center justify-center p-4">
    <div class="w-full max-w-sm">
      <div class="text-center mb-8">
        <h1 class="text-2xl font-bold text-foreground">GeoDeploy</h1>
      </div>

      <div v-if="linkError" class="card p-6 space-y-3 text-center">
        <p class="text-sm text-red-400">{{ linkError }}</p>
        <p class="text-xs text-muted-foreground">Ask a workspace admin for a new reset link.</p>
      </div>

      <div v-else-if="info" class="card p-6 space-y-4">
        <p class="text-sm text-foreground/85 text-center">Set a new password for {{ info.email }}</p>
        <div>
          <label class="label">New password</label>
          <input v-model="password" type="password" class="input" @keydown.enter="submit" />
        </div>
        <div>
          <label class="label">Confirm password</label>
          <input v-model="confirm" type="password" class="input" @keydown.enter="submit" />
        </div>
        <div v-if="error" class="text-sm text-red-400">{{ error }}</div>
        <button @click="submit" :disabled="!canSubmit || busy" class="btn-primary w-full justify-center">
          <span v-if="busy" class="animate-spin">⟳</span>
          Set password
        </button>
      </div>

      <div v-else class="text-center text-sm text-muted-foreground">Checking your link…</div>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { getInvitation, resetPassword } from '@/api'

const route = useRoute()
const router = useRouter()

const token = route.query.token || ''
const info = ref(null)
const linkError = ref('')
const password = ref('')
const confirm = ref('')
const error = ref('')
const busy = ref(false)

const canSubmit = computed(() => password.value.length >= 8 && password.value === confirm.value)

onMounted(async () => {
  if (!token) { linkError.value = 'This reset link is incomplete.'; return }
  try {
    const { data } = await getInvitation(token)
    if (data.purpose !== 'password_reset') { linkError.value = 'This link is not a password reset.'; return }
    info.value = data
  } catch (err) {
    linkError.value = err.response?.status === 410
      ? 'This reset link has expired or was already used.'
      : 'This reset link is invalid.'
  }
})

async function submit() {
  if (!canSubmit.value || busy.value) {
    if (password.value && password.value.length < 8) error.value = 'Password must be at least 8 characters.'
    else if (confirm.value && password.value !== confirm.value) error.value = 'Passwords do not match.'
    return
  }
  busy.value = true
  error.value = ''
  try {
    await resetPassword(token, { password: password.value })
    router.push('/login')
  } catch (err) {
    error.value = err.response?.data?.detail || 'Could not reset the password.'
  } finally {
    busy.value = false
  }
}
</script>
