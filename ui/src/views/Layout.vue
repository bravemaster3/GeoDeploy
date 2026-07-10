<template>
  <div class="flex h-screen overflow-hidden bg-background">
    <!-- Sidebar -->
    <aside class="w-56 flex-shrink-0 bg-card border-r border-border flex flex-col">
      <div class="px-5 py-4 border-b border-border flex items-center gap-2.5">
        <span class="w-2.5 h-2.5 rounded-full bg-primary shadow-[0_0_10px_hsl(var(--primary)/.6)]" />
        <span class="font-bold text-foreground text-[15px] tracking-tight">GeoDeploy</span>
      </div>

      <nav class="flex-1 py-4 space-y-0.5 px-3">
        <RouterLink v-for="item in nav" :key="item.to" :to="item.to"
          class="flex items-center gap-3 px-3 py-2 rounded-lg text-[13px] font-medium transition-colors"
          :class="isActive(item.to)
            ? 'bg-primary/10 text-primary'
            : 'text-muted-foreground hover:text-foreground hover:bg-muted/60'"
        >
          <component :is="item.icon" class="w-4 h-4" />
          {{ $t(item.label) }}
        </RouterLink>
      </nav>

      <div class="px-4 py-3 border-t border-border text-xs text-muted-foreground flex items-center gap-2 min-w-0">
        <span class="truncate">{{ auth.user?.name }}</span>
        <button @click="auth.logout(); $router.push('/login')"
          class="ml-auto flex-shrink-0 text-muted-foreground/70 hover:text-foreground">
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
