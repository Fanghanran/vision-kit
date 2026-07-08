import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import client from '@/api/client'
import router from '@/router'

interface UserInfo {
  id: number
  username: string
  email: string
  role: 'admin' | 'operator' | 'viewer'
  status: number
  avatar_bg: string
  created_at: number
  updated_at: number
}

export const useAuthStore = defineStore('auth', () => {
  const token = ref(localStorage.getItem('va-token') || '')
  const user = ref<UserInfo | null>(null)
  const loading = ref(false)

  const isLoggedIn = computed(() => !!token.value && !!user.value)
  const isAdmin = computed(() => user.value?.role === 'admin')

  if (token.value) {
    client.defaults.headers.common['Authorization'] = `Bearer ${token.value}`
  }

  async function login(username: string, password: string) {
    loading.value = true
    try {
      const { data } = await client.post('/api/auth/login', { username, password })
      token.value = data.token
      user.value = data.user
      localStorage.setItem('va-token', data.token)
      client.defaults.headers.common['Authorization'] = `Bearer ${data.token}`
      return true
    } catch (e: any) {
      const msg = e?.response?.data?.detail || '登录失败'
      throw new Error(msg)
    } finally {
      loading.value = false
    }
  }

  async function fetchMe() {
    if (!token.value) return
    try {
      const { data } = await client.get('/api/auth/me')
      user.value = data
    } catch {
      logout()
    }
  }

  async function updateProfile(payload: { email?: string; avatar_bg?: string }) {
    const { data } = await client.put('/api/auth/profile', payload)
    user.value = data
  }

  async function logout() {
    if (token.value) {
      try { await client.post('/api/auth/logout') } catch { /* ignore */ }
    }
    token.value = ''
    user.value = null
    localStorage.removeItem('va-token')
    delete client.defaults.headers.common['Authorization']
    router.replace({ name: 'Login' })
  }

  async function listUsers(): Promise<UserInfo[]> {
    const { data } = await client.get('/api/users')
    return data
  }

  async function createUser(username: string, password: string, role: string, email = '') {
    const { data } = await client.post('/api/users', { username, password, role, email })
    return data
  }

  async function updateUser(username: string, payload: Record<string, any>) {
    const { data } = await client.put(`/api/users/${username}`, payload)
    return data
  }

  async function deleteUser(username: string) {
    await client.delete(`/api/users/${username}`)
  }

  async function changePassword(oldPassword: string, newPassword: string) {
    const { data } = await client.post('/api/auth/change-password', {
      old_password: oldPassword,
      new_password: newPassword,
    })
    return data
  }

  return { token, user, loading, isLoggedIn, isAdmin,
    login, fetchMe, updateProfile, logout,
    listUsers, createUser, updateUser, deleteUser, changePassword }
})
