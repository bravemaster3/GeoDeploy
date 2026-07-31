<template>
  <div class="p-6 lg:p-8">
    <div class="max-w-4xl mx-auto space-y-6">
      <div class="flex items-center justify-between">
        <div>
          <h1 class="text-xl font-bold text-foreground">{{ $t('users.title') }}</h1>
          <p class="text-sm text-muted-foreground mt-0.5">{{ $t('users.subtitle') }}</p>
        </div>
        <button @click="showInvite = true" class="btn-primary text-sm flex items-center gap-2">
          <PlusIcon class="w-4 h-4" />
          {{ $t('users.invite') }}
        </button>
      </div>

      <!-- Members -->
      <section class="card overflow-hidden">
        <header class="flex items-center gap-3 px-5 py-3.5 border-b border-border/60">
          <div class="w-9 h-9 rounded-lg bg-violet-500/15 text-violet-400 flex items-center justify-center flex-shrink-0">
            <UsersIcon class="w-4 h-4" />
          </div>
          <div>
            <h2 class="text-sm font-semibold text-foreground">{{ $t('users.members') }}</h2>
            <p class="text-xs text-muted-foreground">{{ store.users.length }}</p>
          </div>
        </header>
        <div class="divide-y divide-border/60">
          <UserRow v-for="u in store.users" :key="u.id" :user="u" />
        </div>
      </section>

      <!-- Pending invitations -->
      <section v-if="store.invites.length" class="card overflow-hidden">
        <header class="flex items-center gap-3 px-5 py-3.5 border-b border-border/60">
          <div class="w-9 h-9 rounded-lg bg-amber-500/15 text-amber-400 flex items-center justify-center flex-shrink-0">
            <KeyIcon class="w-4 h-4" />
          </div>
          <div>
            <h2 class="text-sm font-semibold text-foreground">{{ $t('users.pending') }}</h2>
            <p class="text-xs text-muted-foreground">{{ store.invites.length }}</p>
          </div>
        </header>
        <div class="divide-y divide-border/60">
          <InviteRow v-for="i in store.invites" :key="i.id" :invite="i" />
        </div>
      </section>
    </div>

    <InviteModal v-if="showInvite" @close="showInvite = false" />
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { useUsersStore } from '@/stores/users'
import { KeyIcon, PlusIcon, UsersIcon } from './icons'
import UserRow from '@/components/users/UserRow.vue'
import InviteRow from '@/components/users/InviteRow.vue'
import InviteModal from '@/components/users/InviteModal.vue'

const store = useUsersStore()
const showInvite = ref(false)

onMounted(store.fetchAll)
</script>
