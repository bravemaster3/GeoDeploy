import { defineStore } from 'pinia'
import { ref } from 'vue'
import {
  createInvite, createPasswordResetLink, deleteUser, listInvites, listUsers,
  regenerateInvite, revokeInvite, transferOwnership, updateUserRole,
} from '@/api'

export const useUsersStore = defineStore('users', () => {
  const users = ref([])
  const invites = ref([])
  const loading = ref(false)

  async function fetchAll() {
    loading.value = true
    try {
      const [u, i] = await Promise.all([listUsers(), listInvites()])
      users.value = u.data
      invites.value = i.data
    } finally {
      loading.value = false
    }
  }

  // Thin wrappers: mutate then refresh; callers surface errors inline (err.response.data.detail).
  async function invite(email, role) {
    const { data } = await createInvite({ email, role })
    await fetchAll()
    return data // carries the raw token ONCE — build the copyable link from it now
  }

  async function regenerate(id) {
    const { data } = await regenerateInvite(id)
    await fetchAll()
    return data
  }

  async function revoke(id) {
    await revokeInvite(id)
    invites.value = invites.value.filter((i) => i.id !== id)
  }

  async function setRole(id, role) {
    await updateUserRole(id, role)
    await fetchAll()
  }

  async function transferTo(id) {
    await transferOwnership(id)
    await fetchAll()
  }

  async function remove(id) {
    await deleteUser(id)
    await fetchAll()
  }

  async function resetLink(id) {
    const { data } = await createPasswordResetLink(id)
    return data // raw token, shown once
  }

  return { users, invites, loading, fetchAll, invite, regenerate, revoke, setRole, transferTo, remove, resetLink }
})
