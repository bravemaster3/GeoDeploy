import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { getSetupStatus } from '@/api'

const routes = [
  { path: '/setup', component: () => import('@/views/SetupWizard.vue'), meta: { public: true } },
  { path: '/login', component: () => import('@/views/Login.vue'), meta: { public: true } },
  { path: '/accept-invite', component: () => import('@/views/AcceptInvite.vue'), meta: { public: true } },
  { path: '/reset-password', component: () => import('@/views/ResetPassword.vue'), meta: { public: true } },
  { path: '/portal-gate', component: () => import('@/views/PortalGate.vue'), meta: { public: true } },
  { path: '/sso-callback', component: () => import('@/views/SsoCallback.vue'), meta: { public: true } },
  {
    path: '/',
    component: () => import('@/views/Layout.vue'),
    children: [
      { path: '', redirect: '/data' },
      { path: 'data', component: () => import('@/views/DataManager.vue') },
      { path: 'portals', component: () => import('@/views/PortalBuilder.vue') },
      { path: 'portals/:id/edit', component: () => import('@/views/PortalEditor.vue'), meta: { requiresEditor: true } },
      { path: 'templates', component: () => import('@/views/TemplateGallery.vue') },
      { path: 'settings', component: () => import('@/views/Settings.vue') },
      { path: 'users', component: () => import('@/views/Users.vue'), meta: { requiresAdmin: true } },
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
  // Role-gated routes (backend enforces regardless — this only avoids showing a dead screen).
  if (to.meta.requiresAdmin && !auth.isAdmin) return '/data'
  if (to.meta.requiresEditor && !auth.canEdit) return '/portals'
  return true
})

export default router
