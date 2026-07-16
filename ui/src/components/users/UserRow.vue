<template>
  <div class="px-4 py-3 hover:bg-muted/60 group">
    <div class="flex items-center gap-4">
      <!-- Avatar -->
      <div class="w-9 h-9 rounded-full bg-primary/10 text-primary flex items-center justify-center text-xs font-semibold flex-shrink-0">
        {{ initials }}
      </div>

      <!-- Identity -->
      <div class="flex-1 min-w-0">
        <div class="flex items-center gap-2 min-w-0">
          <span class="text-sm font-medium text-foreground truncate">{{ user.name }}</span>
          <span v-if="isSelf" class="text-[10px] text-muted-foreground/70">({{ $t('users.you') }})</span>
          <span class="text-[10px] px-1.5 py-0.5 rounded font-medium flex-shrink-0" :class="roleBadge">
            {{ $t(`users.role_${user.role}`) }}
          </span>
        </div>
        <div class="text-xs text-muted-foreground truncate">
          {{ user.email }}
          <span v-if="itemCount" class="text-muted-foreground/70"> · {{ countLabel }}</span>
        </div>
      </div>

      <!-- Role select (not for the owner row, not for yourself) -->
      <select v-if="canChangeRole" :value="user.role" @change="onRole($event.target.value)"
        class="input text-xs py-1 px-2 w-24 flex-shrink-0">
        <option value="viewer">{{ $t('users.role_viewer') }}</option>
        <option value="editor">{{ $t('users.role_editor') }}</option>
        <option value="admin">{{ $t('users.role_admin') }}</option>
      </select>

      <!-- Hover actions -->
      <div class="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity flex-shrink-0">
        <button v-if="canReset" @click="onResetLink" :title="$t('users.reset_password')"
          class="w-7 h-7 rounded-md flex items-center justify-center text-muted-foreground/70 hover:text-foreground hover:bg-muted">
          <KeyIcon class="w-3.5 h-3.5" />
        </button>
        <button v-if="canTransfer" @click="confirmTransfer = true" :title="$t('users.transfer')"
          class="w-7 h-7 rounded-md flex items-center justify-center text-muted-foreground/70 hover:text-amber-400 hover:bg-muted">
          <UsersIcon class="w-3.5 h-3.5" />
        </button>
        <button v-if="canDelete" @click="confirmDelete = true" :title="$t('users.delete')"
          class="w-7 h-7 rounded-md flex items-center justify-center text-muted-foreground/70 hover:text-red-400 hover:bg-muted">
          <TrashIcon class="w-3.5 h-3.5" />
        </button>
      </div>
    </div>

    <!-- Inline two-step confirms + reset link + errors -->
    <div v-if="confirmDelete" class="mt-2 ml-13 flex items-center gap-2 text-xs">
      <span class="text-muted-foreground">{{ $t('users.delete_confirm', { count: itemCount }) }}</span>
      <button @click="onDelete" class="btn-danger text-xs px-2 py-1">{{ $t('users.delete') }}</button>
      <button @click="confirmDelete = false" class="btn-secondary text-xs px-2 py-1">{{ $t('users.cancel') }}</button>
    </div>
    <div v-if="confirmTransfer" class="mt-2 ml-13 flex items-center gap-2 text-xs">
      <span class="text-muted-foreground">{{ $t('users.transfer_confirm', { name: user.name }) }}</span>
      <button @click="onTransfer" class="btn-primary text-xs px-2 py-1">{{ $t('users.transfer') }}</button>
      <button @click="confirmTransfer = false" class="btn-secondary text-xs px-2 py-1">{{ $t('users.cancel') }}</button>
    </div>
    <div v-if="resetUrl" class="mt-2 space-y-1">
      <p v-if="resetEmailSent" class="text-xs text-green-400">{{ $t('users.email_sent', { email: user.email }) }}</p>
      <CopyLink :url="resetUrl" :hint="$t('users.reset_link_once')" />
    </div>
    <div v-if="error" class="mt-2 text-xs text-red-400">{{ error }}</div>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { useAuthStore } from '@/stores/auth'
import { useUsersStore } from '@/stores/users'
import { KeyIcon, TrashIcon, UsersIcon } from '@/views/icons'
import CopyLink from './CopyLink.vue'

const props = defineProps({ user: { type: Object, required: true } })

const auth = useAuthStore()
const store = useUsersStore()
const error = ref('')
const confirmDelete = ref(false)
const confirmTransfer = ref(false)
const resetUrl = ref('')
const resetEmailSent = ref(false)

const isSelf = computed(() => props.user.id === auth.user?.id)
const isOwnerRow = computed(() => props.user.role === 'owner')

// Backend rules mirrored for affordances only (it enforces them regardless):
// the owner row is untouchable (transfer is the one exception, owner-caller only);
// you can't change/delete yourself.
const canChangeRole = computed(() => !isOwnerRow.value && !isSelf.value)
const canDelete = computed(() => !isOwnerRow.value && !isSelf.value)
const canTransfer = computed(() => auth.isOwner && !isSelf.value)
const canReset = computed(() => !isSelf.value && (!isOwnerRow.value || auth.isOwner))

const initials = computed(() =>
  (props.user.name || '?').split(/\s+/).map(w => w[0]).slice(0, 2).join('').toUpperCase())

const itemCount = computed(() =>
  (props.user.vector_count || 0) + (props.user.raster_count || 0) +
  (props.user.portal_count || 0) + (props.user.source_count || 0))

const countLabel = computed(() => {
  const parts = []
  const n = (v, label) => v && parts.push(`${v} ${label}`)
  n((props.user.vector_count || 0) + (props.user.raster_count || 0), 'layers')
  n(props.user.portal_count, 'portals')
  n(props.user.source_count, 'sources')
  return parts.join(', ')
})

const roleBadge = computed(() => ({
  owner: 'bg-amber-500/15 text-amber-400',
  admin: 'bg-violet-500/15 text-violet-400',
  editor: 'bg-blue-500/15 text-blue-400',
  viewer: 'bg-muted text-muted-foreground',
}[props.user.role] || 'bg-muted text-muted-foreground'))

async function run(fn) {
  error.value = ''
  try { await fn() } catch (err) { error.value = err.response?.data?.detail || err.message }
}

const onRole = (role) => run(() => store.setRole(props.user.id, role))
const onDelete = () => run(async () => { await store.remove(props.user.id); confirmDelete.value = false })
const onTransfer = () => run(async () => { await store.transferTo(props.user.id); confirmTransfer.value = false })
const onResetLink = () => run(async () => {
  const inv = await store.resetLink(props.user.id)
  resetUrl.value = `${window.location.origin}/reset-password?token=${inv.token}`
  resetEmailSent.value = !!inv.email_sent
})
</script>
