import axios from 'axios'

const client = axios.create({
  baseURL: '',
  timeout: 15000,
  headers: { 'Content-Type': 'application/json' },
})

// 响应拦截器：统一错误处理
client.interceptors.response.use(
  (response) => response,
  (error) => {
    console.error('[api] error:', error.response?.status, error.message)
    if (error.response?.status === 401) {
      localStorage.removeItem('va-token')
      // 延迟跳转避免多个请求同时触发
      const current = window.location.pathname
      if (current !== '/login') {
        window.location.replace('/login')
      }
    }
    return Promise.reject(error)
  }
)

export default client
