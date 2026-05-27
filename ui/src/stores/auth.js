import { defineStore } from 'pinia'
import { ref } from 'vue'
import { login as apiLogin, getMe } from '@/api'

export const useAuthStore = defineStore('auth', () => {
  const user = ref(null)

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

  return { user, fetchMe, loginUser, logout }
})
