<template>
  <div class="min-h-screen bg-muted/40 flex items-center justify-center p-4">
    <div class="text-sm text-muted-foreground flex items-center gap-2">
      <span class="animate-spin">⟳</span> Signing you in…
    </div>
  </div>
</template>

<script setup>
import { onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { sessionToken } from '@/api'

const router = useRouter()
const auth = useAuthStore()

// The OIDC callback set the HttpOnly session cookie and redirected here; a top-level redirect can't
// populate localStorage, so pull the JWT from the cookie (via the server) into localStorage, then go.
onMounted(async () => {
  try {
    const { data } = await sessionToken()
    auth.setToken(data.access_token)
    await auth.fetchMe()
    router.replace('/data')
  } catch {
    router.replace('/login?sso_error=' + encodeURIComponent('Single sign-on failed. Please try again.'))
  }
})
</script>
