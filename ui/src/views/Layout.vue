<template>
  <div class="flex h-screen overflow-hidden bg-gray-50">
    <!-- Sidebar -->
    <aside class="w-56 flex-shrink-0 bg-gray-900 text-gray-100 flex flex-col">
      <div class="px-5 py-4 border-b border-gray-700">
        <span class="font-bold text-white text-lg tracking-tight">GeoDeploy</span>
      </div>

      <nav class="flex-1 py-4 space-y-1 px-3">
        <RouterLink v-for="item in nav" :key="item.to" :to="item.to"
          class="flex items-center gap-3 px-3 py-2 rounded-lg text-sm font-medium transition-colors"
          :class="isActive(item.to) ? 'bg-brand-600 text-white' : 'text-gray-400 hover:text-white hover:bg-gray-800'"
        >
          <component :is="item.icon" class="w-4 h-4" />
          {{ $t(item.label) }}
        </RouterLink>
      </nav>

      <div class="px-4 py-3 border-t border-gray-700 text-xs text-gray-500">
        {{ auth.user?.name }}
        <button @click="auth.logout(); $router.push('/login')" class="ml-2 text-gray-400 hover:text-white">
          {{ $t('nav.logout') }}
        </button>
      </div>
    </aside>

    <!-- Main -->
    <main class="flex-1 overflow-auto">
      <RouterView />
    </main>
  </div>
</template>

<script setup>
import { useRoute, RouterLink, RouterView } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { DatabaseIcon, GlobeIcon, LayoutIcon, SettingsIcon } from './icons'

const auth = useAuthStore()
const route = useRoute()

const nav = [
  { to: '/data', label: 'nav.data', icon: DatabaseIcon },
  { to: '/portals', label: 'nav.portals', icon: GlobeIcon },
  { to: '/templates', label: 'nav.templates', icon: LayoutIcon },
  { to: '/settings', label: 'nav.settings', icon: SettingsIcon },
]

const isActive = (to) => route.path.startsWith(to)
</script>
