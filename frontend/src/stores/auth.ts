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

interface UserPreferences {
  notify_alert: { enabled: boolean; channels: string[] }
  notify_system: { enabled: boolean; channels: string[] }
  notify_daily: { enabled: boolean; channels: string[] }
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
      const status = e?.response?.status
      const detail = e?.response?.data?.detail || ''
      if (status === 401) throw new Error(detail || '用户名或密码错误')
      throw new Error(detail || '登录失败')
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

  async function getUserStats() {
    const { data } = await client.get('/api/users/stats')
    return data as { total_users: number; by_role: Record<string,number>; active_count: number; disabled_count: number; online_count: number }
  }

  async function getUserSessions(username: string) {
    const { data } = await client.get(`/api/users/${username}/sessions`)
    return data as { username: string; ip: string; expires_at: number; remaining_seconds: number }[]
  }

  async function revokeSessions(username: string) {
    const { data } = await client.delete(`/api/users/${username}/sessions`)
    return data
  }

  async function getLoginHistory(username: string, limit = 20) {
    const { data } = await client.get(`/api/users/${username}/login-history`, { params: { limit } })
    return data as { id: number; username: string; ip: string; success: boolean; reason: string; created_at: number }[]
  }

  async function fetchDetail() {
    const { data } = await client.get('/api/auth/me/detail')
    return data as UserInfo & { last_login: { ip: string; time: number; success: boolean } | null; active_sessions: number; preferences: UserPreferences }
  }

  async function getPreferences() {
    const { data } = await client.get('/api/auth/me/preferences')
    return data as UserPreferences
  }

  async function updatePreferences(payload: Partial<UserPreferences>) {
    const { data } = await client.put('/api/auth/preferences', payload)
    return data as UserPreferences
  }

  return { token, user, loading, isLoggedIn, isAdmin,
    login, fetchMe, updateProfile, logout,
    listUsers, createUser, updateUser, deleteUser, changePassword,
    getUserStats, getUserSessions, revokeSessions, getLoginHistory,
    fetchDetail, getPreferences, updatePreferences }
})
