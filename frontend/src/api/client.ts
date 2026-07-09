import axios from 'axios'
import { extractError } from '@/utils/error'

const client = axios.create({
  baseURL: '',
  timeout: 15000,
  headers: { 'Content-Type': 'application/json' },
})

// 响应拦截器：统一错误处理 + 401 自动跳转
client.interceptors.response.use(
  (response) => response,
  (error) => {
    const detail = extractError(error)
    console.error(`[api] ${detail.status || 'NET'} ${error.config?.url || ''} — ${detail.message}`)

    if (error.response?.status === 401) {
      localStorage.removeItem('va-token')
      const current = window.location.pathname
      if (current !== '/login') {
        window.location.replace('/login')
      }
    }
    return Promise.reject(error)
  }
)

export default client
