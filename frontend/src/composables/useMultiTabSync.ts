/**
 * 多标签页同步 composable
 *
 * 使用 BroadcastChannel API 实现标签页间认证状态同步：
 * - token 过期时广播，其他标签页 3 秒后同步登出
 * - 正常登出时广播，所有标签页立即同步
 * - Token 刷新时广播，其他标签页同步新 token
 */

import { ElMessage } from 'element-plus'

type AuthMessage =
  | { type: 'token_expired' }
  | { type: 'logout' }
  | { type: 'token_refreshed'; token: string }

let channel: BroadcastChannel | null = null
let isInitialized = false

function getChannel(): BroadcastChannel {
  if (!channel) {
    channel = new BroadcastChannel('sentinelmind-auth')
  }
  return channel
}

/**
 * 初始化多标签页同步监听
 * 应在应用启动时调用一次
 */
export function initMultiTabSync() {
  if (isInitialized) return
  isInitialized = true

  const ch = getChannel()

  ch.onmessage = (event: MessageEvent<AuthMessage>) => {
    const msg = event.data

    switch (msg.type) {
      case 'token_expired': {
        // 其他标签页通知：token 已过期
        ElMessage.warning('检测到其他标签页登出，3 秒后同步登出...')
        setTimeout(() => {
          clearAuthAndRedirect()
        }, 3000)
        break
      }

      case 'logout': {
        // 其他标签页通知：正常登出
        clearAuthAndRedirect()
        break
      }

      case 'token_refreshed': {
        // 其他标签页通知：token 已刷新
        localStorage.setItem('va-token', msg.token)
        break
      }
    }
  }
}

/**
 * 广播 token 过期消息
 */
export function broadcastTokenExpired() {
  getChannel().postMessage({ type: 'token_expired' })
}

/**
 * 广播正常登出消息
 */
export function broadcastLogout() {
  getChannel().postMessage({ type: 'logout' })
}

/**
 * 广播 token 刷新消息
 */
export function broadcastTokenRefreshed(token: string) {
  getChannel().postMessage({ type: 'token_refreshed', token })
}

/**
 * 清除认证状态并跳转登录页
 */
function clearAuthAndRedirect() {
  localStorage.removeItem('va-token')
  localStorage.removeItem('va-refresh-token')
  if (window.location.pathname !== '/login') {
    window.location.replace('/login')
  }
}
