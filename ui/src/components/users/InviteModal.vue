<template>
  <Teleport to="body">
  <div class="fixed inset-0 bg-gray-900/50 backdrop-blur-sm flex items-center justify-center z-50 p-4">
    <div class="card w-full max-w-md p-6 space-y-4 shadow-2xl">
      <div class="flex items-center justify-between">
        <h2 class="text-lg font-semibold">{{ $t('users.invite') }}</h2>
        <button @click="$emit('close')" class="text-muted-foreground/70 hover:text-foreground text-xl leading-none">&times;</button>
      </div>

      <!-- Success state: the copyable link (shown once) -->
      <template v-if="createdUrl">
        <p class="text-sm text-foreground/85">{{ $t('users.link_ready') }} — {{ form.email }}</p>
        <p v-if="emailSent" class="text-xs text-green-400">{{ $t('users.email_sent', { email: form.email }) }}</p>
        <CopyLink :url="createdUrl" :hint="$t('users.link_once')" />
        <div class="flex justify-end pt-1">
          <button @click="$emit('close')" class="btn-primary text-sm">{{ $t('users.done') }}</button>
        </div>
      </template>

      <!-- Form state -->
      <template v-else>
        <p class="text-xs text-muted-foreground">{{ $t('users.invite_hint') }}</p>

        <div>
          <label class="text-xs text-muted-foreground block mb-1">{{ $t('users.email') }}</label>
          <input v-model="form.email" type="email" placeholder="colleague@example.org"
            class="input w-full text-sm" @keydown.enter="submit" />
        </div>

        <div>
          <label class="text-xs text-muted-foreground block mb-1">{{ $t('users.role') }}</label>
          <div class="grid grid-cols-3 gap-2">
            <button v-for="r in roles" :key="r" type="button"
              class="p-2 rounded-lg border text-xs font-medium transition-colors"
              :class="form.role === r ? 'border-primary bg-primary/10 text-primary' : 'border-border hover:border-muted-foreground/40 text-foreground/85'"
              @click="form.role = r">{{ $t(`users.role_${r}`) }}</button>
          </div>
          <p class="text-[10px] text-muted-foreground/70 mt-1">{{ $t(`users.role_${form.role}_hint`) }}</p>
        </div>

        <div v-if="error" class="text-sm text-red-400 bg-red-500/15 p-3 rounded-lg">{{ error }}</div>

        <div class="flex justify-end gap-2 pt-1">
          <button @click="$emit('close')" class="btn-secondary text-sm">{{ $t('users.cancel') }}</button>
          <button @click="submit" :disabled="!canSubmit || saving" class="btn-primary text-sm">
            {{ saving ? '…' : $t('users.send') }}
          </button>
        </div>
      </template>
    </div>
  </div>
  </Teleport>
</template>

<script setup>
import { computed, ref } from 'vue'
import { useUsersStore } from '@/stores/users'
import CopyLink from './CopyLink.vue'

const emit = defineEmits(['close'])
const store = useUsersStore()

const roles = ['viewer', 'editor', 'admin']
const form = ref({ email: '', role: 'editor' })
const saving = ref(false)
const error = ref('')
const createdUrl = ref('')
const emailSent = ref(false)

const canSubmit = computed(() => /.+@.+\..+/.test(form.value.email.trim()))

async function submit() {
  if (!canSubmit.value || saving.value) return
  saving.value = true
  error.value = ''
  try {
    const inv = await store.invite(form.value.email.trim().toLowerCase(), form.value.role)
    createdUrl.value = `${window.location.origin}/accept-invite?token=${inv.token}`
    emailSent.value = !!inv.email_sent
  } catch (err) {
    error.value = err.response?.data?.detail || err.message
  } finally {
    saving.value = false
  }
}
</script>
