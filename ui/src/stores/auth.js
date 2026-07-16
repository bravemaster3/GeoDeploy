import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { login as apiLogin, getMe } from '@/api'

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
  }

  async function loginUser(email, password) {
    const { data } = await apiLogin(email, password)
    localStorage.setItem('geodeploy_token', data.access_token)
    await fetchMe()
  }

  function logout() {
    localStorage.removeItem('geodeploy_token')
    user.value = null
  }

  return { user, role, isAdmin, isOwner, canEdit, fetchMe, loginUser, logout }
})
