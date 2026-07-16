<template>
  <div class="flex h-screen overflow-hidden bg-background">
    <!-- Sidebar -->
    <aside
      class="flex-shrink-0 bg-card border-r border-border flex flex-col transition-[width] duration-200 ease-out"
      :class="collapsed ? 'w-[60px]' : 'w-56'"
    >
      <!-- Brand + collapse toggle -->
      <div class="h-[57px] border-b border-border flex items-center"
        :class="collapsed ? 'justify-center px-0' : 'px-5 gap-2.5'">
        <span class="w-2.5 h-2.5 rounded-full bg-primary shadow-[0_0_10px_hsl(var(--primary)/.6)] flex-shrink-0" />
        <span v-if="!collapsed" class="font-bold text-foreground text-[15px] tracking-tight">GeoDeploy</span>
        <button v-if="!collapsed" @click="toggle" title="Collapse sidebar"
          class="ml-auto flex-shrink-0 w-6 h-6 rounded-md flex items-center justify-center text-muted-foreground/70 hover:text-foreground hover:bg-muted/60">
          <svg class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 18l-6-6 6-6"/></svg>
        </button>
      </div>

      <!-- Expand button (collapsed only) -->
      <button v-if="collapsed" @click="toggle" title="Expand sidebar"
        class="mx-auto mt-3 mb-1 w-8 h-8 rounded-lg flex items-center justify-center text-muted-foreground/70 hover:text-foreground hover:bg-muted/60">
        <svg class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 18l6-6-6-6"/></svg>
      </button>

      <nav class="flex-1 py-3 space-y-1" :class="collapsed ? 'px-2' : 'px-3'">
        <RouterLink v-for="item in nav" :key="item.to" :to="item.to"
          :title="collapsed ? $t(item.label) : ''"
          class="flex items-center rounded-lg text-[13px] font-medium transition-colors"
          :class="[
            collapsed ? 'justify-center h-10 w-10 mx-auto' : 'gap-3 px-3 py-2',
            isActive(item.to)
              ? 'bg-primary/10 text-primary'
              : 'text-muted-foreground hover:text-foreground hover:bg-muted/60'
          ]"
        >
          <component :is="item.icon" class="w-4 h-4 flex-shrink-0" />
          <span v-if="!collapsed">{{ $t(item.label) }}</span>
        </RouterLink>
      </nav>

      <!-- Footer: user + theme + logout -->
      <div class="border-t border-border text-xs text-muted-foreground"
        :class="collapsed ? 'py-3 flex flex-col items-center gap-2' : 'px-4 py-3 flex items-center gap-2 min-w-0'">
        <span v-if="!collapsed" class="truncate">{{ auth.user?.name }}</span>
        <button @click="toggleTheme" :title="isDark ? 'Switch to light mode' : 'Switch to dark mode'"
          class="flex-shrink-0 w-7 h-7 rounded-md flex items-center justify-center text-muted-foreground/70 hover:text-foreground hover:bg-muted/60"
          :class="collapsed ? '' : 'ml-auto'">
          <svg v-if="isDark" class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>
          <svg v-else class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/></svg>
        </button>
        <button @click="auth.logout(); $router.push('/login')" :title="$t('nav.logout')"
          class="flex-shrink-0 flex items-center justify-center text-muted-foreground/70 hover:text-foreground"
          :class="collapsed ? 'w-7 h-7 rounded-md hover:bg-muted/60' : ''">
          <svg v-if="collapsed" class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><path d="M16 17l5-5-5-5M21 12H9"/></svg>
          <span v-else>{{ $t('nav.logout') }}</span>
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
import { ref, computed, watch } from 'vue'
import { useRoute, RouterLink, RouterView } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { DatabaseIcon, GlobeIcon, LayoutIcon, SettingsIcon, UsersIcon } from './icons'

const auth = useAuthStore()
const route = useRoute()

// Theme toggle — the boot default (dark) + saved preference are applied in main.js.
const isDark = ref(document.documentElement.classList.contains('dark'))
function toggleTheme() {
  isDark.value = !isDark.value
  document.documentElement.classList.toggle('dark', isDark.value)
  localStorage.setItem('gd-theme', isDark.value ? 'dark' : 'light')
}

// Collapsible sidebar. The user's preference is remembered across sessions, but the PORTAL EDITOR
// always opens collapsed so the map/settings get the full width — freeing ~200px for the editor.
// Toggling while in the editor is a temporary override that doesn't overwrite the saved preference.
const STORAGE_KEY = 'gd-sidebar-collapsed'
const userPref = ref(localStorage.getItem(STORAGE_KEY) === '1')
const isEditor = computed(() => /^\/portals\/[^/]+\/edit/.test(route.path))
const collapsed = ref(isEditor.value || userPref.value)

watch(isEditor, (nowEditor) => {
  // Entering the editor forces collapse; leaving restores the saved preference.
  collapsed.value = nowEditor ? true : userPref.value
})

function toggle() {
  collapsed.value = !collapsed.value
  if (!isEditor.value) {
    userPref.value = collapsed.value
    localStorage.setItem(STORAGE_KEY, collapsed.value ? '1' : '0')
  }
}

const nav = computed(() => [
  { to: '/data', label: 'nav.data', icon: DatabaseIcon },
  { to: '/portals', label: 'nav.portals', icon: GlobeIcon },
  { to: '/templates', label: 'nav.templates', icon: LayoutIcon },
  // Users management is admin/owner only — hidden (not disabled) for editors/viewers.
  ...(auth.isAdmin ? [{ to: '/users', label: 'nav.users', icon: UsersIcon }] : []),
  { to: '/settings', label: 'nav.settings', icon: SettingsIcon },
])

const isActive = (to) => route.path.startsWith(to)
</script>
