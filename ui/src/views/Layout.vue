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
        <button @click="toggleTheme" :title="isDark ? 'Switch to light mode' : 'Switch to dark mode'"
          class="ml-auto flex-shrink-0 w-6 h-6 rounded-md flex items-center justify-center text-muted-foreground/70 hover:text-foreground hover:bg-muted/60">
          <!-- moon / sun -->
          <svg v-if="isDark" class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>
          <svg v-else class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/></svg>
        </button>
        <button @click="auth.logout(); $router.push('/login')"
          class="flex-shrink-0 text-muted-foreground/70 hover:text-foreground">
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
import { ref } from 'vue'
import { useRoute, RouterLink, RouterView } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { DatabaseIcon, GlobeIcon, LayoutIcon, SettingsIcon } from './icons'

const auth = useAuthStore()
const route = useRoute()

// Theme toggle — the boot default (dark) + saved preference are applied in main.js.
const isDark = ref(document.documentElement.classList.contains('dark'))
function toggleTheme() {
  isDark.value = !isDark.value
  document.documentElement.classList.toggle('dark', isDark.value)
  localStorage.setItem('gd-theme', isDark.value ? 'dark' : 'light')
}

const nav = [
  { to: '/data', label: 'nav.data', icon: DatabaseIcon },
  { to: '/portals', label: 'nav.portals', icon: GlobeIcon },
  { to: '/templates', label: 'nav.templates', icon: LayoutIcon },
  { to: '/settings', label: 'nav.settings', icon: SettingsIcon },
]

const isActive = (to) => route.path.startsWith(to)
</script>
