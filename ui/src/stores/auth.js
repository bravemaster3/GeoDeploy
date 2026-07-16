import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { login as apiLogin, getMe, syncSession, logoutSession } from '@/api'

export const useAuthStore = defineStore('auth', () => {
  const user = ref(null)

  // RBAC (A-01): single source of truth for role-aware UI affordances.
  // The backend enforces everything — these only decide what to SHOW.
  const role = computed(() => user.value?.role ?? null)
  const isAdmin = computed(() => ['admin', 'owner'].includes(role.value))
  const isOwner = computed(() => role.value === 'owner')
  const canEdit = computed(() => ['editor', 'admin', 'owner'].includes(role.value))

  async function fetchMe() {
    const { data } = await getMe()
    user.value = data
    // Ensure the portal-access session cookie exists (covers a login AND an existing localStorage
    // session restored on boot). Fire-and-forget — the SPA doesn't depend on it, portals do.
    syncSession().catch(() => {})
  }

  async function loginUser(email, password) {
    const { data } = await apiLogin(email, password)   // also sets the session cookie server-side
    localStorage.setItem('geodeploy_token', data.access_token)
    await fetchMe()
  }

  function logout() {
    logoutSession().catch(() => {})   // clear the HttpOnly cookie too
    localStorage.removeItem('geodeploy_token')
    user.value = null
  }

  return { user, role, isAdmin, isOwner, canEdit, fetchMe, loginUser, logout }
})
