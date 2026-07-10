<template>
  <div class="min-h-screen bg-muted/40 flex items-center justify-center p-4">
    <div class="w-full max-w-sm">
      <div class="text-center mb-8">
        <h1 class="text-2xl font-bold text-foreground">GeoDeploy</h1>
      </div>
      <div class="card p-6 space-y-4">
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
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const auth = useAuthStore()
const email = ref('')
const password = ref('')
const error = ref('')
const busy = ref(false)

async function submit() {
  error.value = ''
  busy.value = true
  try {
    await auth.loginUser(email.value, password.value)
    router.push('/data')
  } catch {
    error.value = 'Invalid email or password.'
  } finally {
    busy.value = false
  }
}
</script>
