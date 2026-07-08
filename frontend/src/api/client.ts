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
    return Promise.reject(error)
  }
)

export default client
