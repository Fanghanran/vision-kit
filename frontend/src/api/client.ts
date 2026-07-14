import axios from 'axios'
import { extractError } from '@/utils/error'
import { broadcastTokenExpired, broadcastTokenRefreshed } from '@/composables/useMultiTabSync'

const client = axios.create({
  baseURL: '',
  timeout: 15000,
  headers: { 'Content-Type': 'application/json' },
})

// refresh 锁（防止并发刷新）
let isRefreshing = false
let refreshSubscribers: Array<(token: string) => void> = []

function subscribeTokenRefresh(cb: (token: string) => void) {
  refreshSubscribers.push(cb)
}

function onRefreshed(token: string) {
  refreshSubscribers.forEach((cb) => cb(token))
  refreshSubscribers = []
}

// 响应拦截器：401 时尝试 refresh token
client.interceptors.response.use(
  (response) => response,
  async (error) => {
    const detail = extractError(error)
    console.error(`[api] ${detail.status || 'NET'} ${error.config?.url || ''} — ${detail.message}`)

    if (error.response?.status === 401) {
      // 非 refresh 接口本身才尝试刷新
      if (error.config?.url?.includes('/api/auth/refresh')) {
        // refresh 接口本身返回 401，说明 refresh token 也失效了
        broadcastTokenExpired()
        localStorage.removeItem('va-token')
        localStorage.removeItem('va-refresh-token')
        if (window.location.pathname !== '/login') {
          window.location.replace('/login')
        }
        return Promise.reject(error)
      }

      const refreshToken = localStorage.getItem('va-refresh-token')

      if (refreshToken) {
        // 已在刷新中，等待完成后重试（必须在 isRefreshing 检查之前）
        if (isRefreshing) {
          return new Promise((resolve) => {
            subscribeTokenRefresh((token) => {
              error.config.headers['Authorization'] = `Bearer ${token}`
              resolve(client(error.config))
            })
          })
        }

        // 发起 refresh（只有一个请求能走到这里）
        isRefreshing = true
        try {
          const { data } = await axios.post('/api/auth/refresh', { refresh_token: refreshToken })
          const newToken = data.token
          const newRefresh = data.refresh_token

          localStorage.setItem('va-token', newToken)
          localStorage.setItem('va-refresh-token', newRefresh)
          client.defaults.headers.common['Authorization'] = `Bearer ${newToken}`

          // 广播 token 刷新，通知其他标签页
          broadcastTokenRefreshed(newToken)

          // 通知所有等待中的请求
          onRefreshed(newToken)
          isRefreshing = false

          // 重试原请求
          error.config.headers['Authorization'] = `Bearer ${newToken}`
          return client(error.config)
        } catch {
          isRefreshing = false
          refreshSubscribers = []
          broadcastTokenExpired()
          localStorage.removeItem('va-token')
          localStorage.removeItem('va-refresh-token')
          if (window.location.pathname !== '/login') {
            window.location.replace('/login')
          }
          return Promise.reject(error)
        }
      }

      // 无 refresh_token，广播过期并跳转登录
      broadcastTokenExpired()
      localStorage.removeItem('va-token')
      if (window.location.pathname !== '/login') {
        window.location.replace('/login')
      }
    }
    return Promise.reject(error)
  }
)

export default client
