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
      path: '/register',
      name: 'Register',
      component: () => import('@/views/Register.vue'),
      meta: { guest: true },
    },
    {
      path: '/change-password',
      name: 'ChangePassword',
      component: () => import('@/views/ChangePassword.vue'),
      meta: { requiresAuth: true },
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
      path: '/system/llm',
      name: 'SystemLLM',
      component: () => import('@/views/system/SystemLLM.vue'),
      meta: { requiresAuth: true, requireAdmin: true },
    },
    {
      path: '/system/notification',
      name: 'SystemNotify',
      component: () => import('@/views/system/SystemNotify.vue'),
      meta: { requiresAuth: true, requireAdmin: true },
    },
    {
      path: '/system/recording',
      name: 'SystemRecording',
      component: () => import('@/views/system/SystemRecording.vue'),
      meta: { requiresAuth: true, requireAdmin: true },
    },
    {
      path: '/system/rules',
      name: 'SystemRules',
      component: () => import('@/views/system/SystemRules.vue'),
      meta: { requiresAuth: true, requireAdmin: true },
    },
    {
      path: '/system/cameras',
      name: 'SystemCameras',
      component: () => import('@/views/system/SystemCameras.vue'),
      meta: { requiresAuth: true, requireAdmin: true },
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
    {
      path: '/rules',
      name: 'Rules',
      component: () => import('@/views/Rules.vue'),
      meta: { requiresAuth: true },
    },
    {
      path: '/audit',
      name: 'Audit',
      component: () => import('@/views/Audit.vue'),
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
  // 强制改密检查（已登录用户必须先改密）
  if (token && to.name !== 'ChangePassword') {
    const { useAuthStore } = await import('@/stores/auth')
    const authStore = useAuthStore()
    // 确保用户信息已加载
    if (!authStore.user) {
      await authStore.fetchMe()
    }
    if (authStore.user?.must_change_password) {
      next({ name: 'ChangePassword' })
      return
    }
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
