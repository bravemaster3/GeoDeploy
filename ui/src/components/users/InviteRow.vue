<template>
  <div class="px-4 py-3 hover:bg-muted/60 group">
    <div class="flex items-center gap-4">
      <div class="w-9 h-9 rounded-full bg-muted text-muted-foreground flex items-center justify-center flex-shrink-0">
        <UsersIcon class="w-4 h-4" />
      </div>
      <div class="flex-1 min-w-0">
        <div class="flex items-center gap-2 min-w-0">
          <span class="text-sm font-medium text-foreground truncate">{{ invite.email }}</span>
          <span class="text-[10px] px-1.5 py-0.5 rounded font-medium flex-shrink-0" :class="roleBadge">
            {{ $t(`users.role_${invite.role}`) }}
          </span>
        </div>
        <div class="text-xs text-muted-foreground">
          {{ $t('users.expires') }} {{ expires }}
        </div>
      </div>
      <div class="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity flex-shrink-0">
        <button @click="onRegenerate" class="btn-secondary text-xs px-2 py-1">{{ $t('users.regenerate') }}</button>
        <button @click="onRevoke" :title="$t('users.revoke')"
          class="w-7 h-7 rounded-md flex items-center justify-center text-muted-foreground/70 hover:text-red-400 hover:bg-muted">
          <TrashIcon class="w-3.5 h-3.5" />
        </button>
      </div>
    </div>
    <div v-if="url" class="mt-2">
      <CopyLink :url="url" :hint="$t('users.link_once')" />
    </div>
    <div v-if="error" class="mt-2 text-xs text-red-400">{{ error }}</div>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { useUsersStore } from '@/stores/users'
import { TrashIcon, UsersIcon } from '@/views/icons'
import CopyLink from './CopyLink.vue'

const props = defineProps({ invite: { type: Object, required: true } })

const store = useUsersStore()
const url = ref('')
const error = ref('')

const expires = computed(() => new Date(props.invite.expires_at).toLocaleDateString())

const roleBadge = computed(() => ({
  admin: 'bg-violet-500/15 text-violet-400',
  editor: 'bg-blue-500/15 text-blue-400',
  viewer: 'bg-muted text-muted-foreground',
}[props.invite.role] || 'bg-muted text-muted-foreground'))

async function onRegenerate() {
  error.value = ''
  try {
    const inv = await store.regenerate(props.invite.id)
    url.value = `${window.location.origin}/accept-invite?token=${inv.token}`
  } catch (err) {
    error.value = err.response?.data?.detail || err.message
  }
}

async function onRevoke() {
  error.value = ''
  try { await store.revoke(props.invite.id) }
  catch (err) { error.value = err.response?.data?.detail || err.message }
}
</script>
