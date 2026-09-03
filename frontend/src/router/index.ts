import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/login', name: 'login', component: () => import('@/views/Login.vue') },
    {
      path: '/',
      component: () => import('@/layouts/MainLayout.vue'),
      redirect: '/dashboard',
      children: [
        { path: 'dashboard', name: 'dashboard', component: () => import('@/views/Dashboard.vue') },
        { path: 'keys', name: 'keys', component: () => import('@/views/Keys.vue') },
        { path: 'providers', name: 'providers', component: () => import('@/views/Providers.vue') },
        { path: 'channels', name: 'channels', component: () => import('@/views/Channels.vue') },
        { path: 'playground', name: 'playground', component: () => import('@/views/Playground.vue') },
        { path: 'logs', name: 'logs', component: () => import('@/views/Logs.vue') },
        { path: 'usage', name: 'usage', component: () => import('@/views/Usage.vue') },
        { path: 'pricing', name: 'pricing', component: () => import('@/views/Pricing.vue') },
        { path: 'alerts', name: 'alerts', component: () => import('@/views/Alerts.vue') },
        { path: 'settings', name: 'settings', component: () => import('@/views/Settings.vue') },
      ],
    },
  ],
})

router.beforeEach((to) => {
  const auth = useAuthStore()
  if (!auth.token && to.name !== 'login') {
    return { name: 'login' }
  }
})

export default router
