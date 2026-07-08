import { createRouter, createWebHistory } from 'vue-router'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/',
      name: 'Dashboard',
      component: () => import('@/views/Dashboard.vue'),
    },
    {
      path: '/alerts',
      name: 'AlertList',
      component: () => import('@/views/AlertList.vue'),
    },
    {
      path: '/alerts/:id',
      name: 'AlertDetail',
      component: () => import('@/views/AlertDetail.vue'),
      props: true,
    },
    {
      path: '/cameras',
      name: 'Cameras',
      component: () => import('@/views/Cameras.vue'),
    },
    {
      path: '/system',
      name: 'System',
      component: () => import('@/views/System.vue'),
    },
  ],
})

export default router
