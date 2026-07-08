import { createRouter, createWebHistory } from 'vue-router'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/login',
      name: 'Login',
      component: () => import('@/views/Login.vue'),
      meta: { guest: true },
    },
    {
      path: '/',
      name: 'Dashboard',
      component: () => import('@/views/Dashboard.vue'),
      meta: { requiresAuth: true },
    },
    {
      path: '/alerts',
      name: 'AlertList',
      component: () => import('@/views/AlertList.vue'),
      meta: { requiresAuth: true },
    },
    {
      path: '/alerts/:id',
      name: 'AlertDetail',
      component: () => import('@/views/AlertDetail.vue'),
      props: true,
      meta: { requiresAuth: true },
    },
    {
      path: '/cameras',
      name: 'Cameras',
      component: () => import('@/views/Cameras.vue'),
      meta: { requiresAuth: true },
    },
    {
      path: '/monitor',
      name: 'Monitor',
      component: () => import('@/views/Monitor.vue'),
      meta: { requiresAuth: true },
    },
    {
      path: '/system',
      name: 'System',
      component: () => import('@/views/System.vue'),
      meta: { requiresAuth: true },
    },
    {
      path: '/profile',
      name: 'Profile',
      component: () => import('@/views/Profile.vue'),
      meta: { requiresAuth: true },
    },
    {
      path: '/users',
      name: 'Users',
      component: () => import('@/views/Users.vue'),
      meta: { requiresAuth: true, requireAdmin: true },
    },
  ],
})

// 路由守卫
router.beforeEach(async (to, _from, next) => {
  const token = localStorage.getItem('va-token')
  if (to.meta.guest) {
    next()
    return
  }
  if (!token && to.meta.requiresAuth) {
    next({ name: 'Login' })
    return
  }
  // 管理员路由检查
  if (to.meta.requireAdmin) {
    const { useAuthStore } = await import('@/stores/auth')
    const authStore = useAuthStore()
    if (!authStore.isAdmin) {
      next({ name: 'Dashboard' })
      return
    }
  }
  next()
})

export default router
