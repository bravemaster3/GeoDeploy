import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { getSetupStatus } from '@/api'

const routes = [
  { path: '/setup', component: () => import('@/views/SetupWizard.vue'), meta: { public: true } },
  { path: '/login', component: () => import('@/views/Login.vue'), meta: { public: true } },
  {
    path: '/',
    component: () => import('@/views/Layout.vue'),
    children: [
      { path: '', redirect: '/data' },
      { path: 'data', component: () => import('@/views/DataManager.vue') },
      { path: 'portals', component: () => import('@/views/PortalBuilder.vue') },
      { path: 'portals/:id/edit', component: () => import('@/views/PortalEditor.vue') },
      { path: 'templates', component: () => import('@/views/TemplateGallery.vue') },
      { path: 'settings', component: () => import('@/views/Settings.vue') },
    ],
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

router.beforeEach(async (to) => {
  if (to.meta.public) return true

  // Check setup before anything else so the 401 interceptor can't race ahead
  try {
    const { data } = await getSetupStatus()
    if (!data.completed) return '/setup'
  } catch {
    // API unreachable — fall through
  }

  const auth = useAuthStore()
  if (!auth.user) {
    try {
      await auth.fetchMe()
    } catch {
      return '/login'
    }
  }
  return true
})

export default router
