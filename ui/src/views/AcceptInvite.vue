<template>
  <div class="min-h-screen bg-muted/40 flex items-center justify-center p-4">
    <div class="w-full max-w-sm">
      <div class="text-center mb-8">
        <h1 class="text-2xl font-bold text-foreground">GeoDeploy</h1>
      </div>

      <!-- Invalid / expired link -->
      <div v-if="linkError" class="card p-6 space-y-3 text-center">
        <p class="text-sm text-red-400">{{ linkError }}</p>
        <p class="text-xs text-muted-foreground">Ask a workspace admin for a new invitation link.</p>
      </div>

      <!-- Accept form -->
      <div v-else-if="invite" class="card p-6 space-y-4">
        <div class="text-center">
          <p class="text-sm text-foreground/85">You've been invited to join this workspace</p>
          <p class="text-xs text-muted-foreground mt-1">
            {{ invite.email }} ·
            <span class="text-[10px] px-1.5 py-0.5 rounded font-medium bg-primary/10 text-primary">{{ invite.role }}</span>
          </p>
        </div>
        <div>
          <label class="label">Your name</label>
          <input v-model="name" type="text" class="input" @keydown.enter="submit" />
        </div>
        <div>
          <label class="label">Password</label>
          <input v-model="password" type="password" class="input" @keydown.enter="submit" />
        </div>
        <div>
          <label class="label">Confirm password</label>
          <input v-model="confirm" type="password" class="input" @keydown.enter="submit" />
        </div>
        <div v-if="error" class="text-sm text-red-400">{{ error }}</div>
        <button @click="submit" :disabled="!canSubmit || busy" class="btn-primary w-full justify-center">
          <span v-if="busy" class="animate-spin">⟳</span>
          Create account
        </button>
      </div>

      <div v-else class="text-center text-sm text-muted-foreground">Checking your invitation…</div>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { acceptInvitation, getInvitation } from '@/api'
import { useAuthStore } from '@/stores/auth'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

const token = route.query.token || ''
const invite = ref(null)
const linkError = ref('')
const name = ref('')
const password = ref('')
const confirm = ref('')
const error = ref('')
const busy = ref(false)

const canSubmit = computed(() =>
  name.value.trim() && password.value.length >= 8 && password.value === confirm.value)

onMounted(async () => {
  if (!token) { linkError.value = 'This invitation link is incomplete.'; return }
  try {
    const { data } = await getInvitation(token)
    if (data.purpose !== 'invite') { linkError.value = 'This link is not an invitation.'; return }
    invite.value = data
  } catch (err) {
    linkError.value = err.response?.status === 410
      ? 'This invitation has expired or was already used.'
      : 'This invitation link is invalid.'
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
    const { data } = await acceptInvitation(token, { name: name.value.trim(), password: password.value })
    localStorage.setItem('geodeploy_token', data.access_token)
    await auth.fetchMe()
    router.push('/data')
  } catch (err) {
    error.value = err.response?.data?.detail || 'Could not create the account.'
  } finally {
    busy.value = false
  }
}
</script>
