import { ref } from 'vue'
import { useAlertsStore } from '@/stores/alerts'
import { useCamerasStore } from '@/stores/cameras'
import { useSystemStore } from '@/stores/system'
import type { WSMessage } from '@/api/types'

const wsStatus = ref<'connected' | 'disconnected' | 'reconnecting'>('disconnected')
let ws: WebSocket | null = null
let pendingMessages: WSMessage[] = []
let rafId: number | null = null

// ─── 重连状态 ──────────────────────────────────────────────
let reconnectAttempts = 0
let reconnectDelay = 3000
let reconnectTimer: ReturnType<typeof setTimeout> | null = null
const MAX_RECONNECT_ATTEMPTS = 10
const MAX_RECONNECT_DELAY = 30000 // 30s 上限

// ─── 心跳状态 ──────────────────────────────────────────────
let lastPingTime = Date.now()
let heartbeatTimer: ReturnType<typeof setInterval> | null = null
const HEARTBEAT_INTERVAL = 45000 // 45s 检查一次
const HEARTBEAT_TIMEOUT = 60000  // 60s 无 ping 认为断开

const BATCH_INTERVAL = 100

export function useWebSocket() {
  function connect() {
    if (ws && ws.readyState === WebSocket.OPEN) return

    // 如果已有重连定时器在跑，不重复触发
    if (reconnectTimer) return

    const token = localStorage.getItem('va-token') || ''
    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
    const url = `${protocol}//${location.host}/ws?token=${encodeURIComponent(token)}`

    try {
      ws = new WebSocket(url)
    } catch {
      scheduleReconnect()
      return
    }

    ws.onopen = () => {
      wsStatus.value = 'connected'
      reconnectAttempts = 0
      reconnectDelay = 3000
      lastPingTime = Date.now()
      startHeartbeat()
      console.log('[ws] connected')
    }

    ws.onmessage = (event) => {
      try {
        const msg: WSMessage = JSON.parse(event.data)

        // 服务端 ping → 回 pong
        if (msg.type === 'ping') {
          lastPingTime = Date.now()
          ws?.send(JSON.stringify({ type: 'pong' }))
          return
        }

        pendingMessages.push(msg)
        scheduleBatchProcess()
      } catch (e) {
        console.warn('[ws] parse error:', e)
      }
    }

    ws.onclose = (event) => {
      console.warn('[ws] closed code=', event.code, 'reason=', event.reason)
      ws = null
      stopHeartbeat()

      // 认证失败（4001）→ 停止重连，触发重新登录
      if (event.code === 4001) {
        wsStatus.value = 'disconnected'
        console.warn('[ws] auth failed, stop reconnecting')
        // 清除本地 token，触发登录页跳转
        localStorage.removeItem('va-token')
        window.location.href = '/login'
        return
      }

      scheduleReconnect()
    }

    ws.onerror = () => {
      ws?.close()
    }
  }

  function scheduleReconnect() {
    if (reconnectTimer) return

    if (reconnectAttempts >= MAX_RECONNECT_ATTEMPTS) {
      wsStatus.value = 'disconnected'
      console.warn('[ws] max reconnect attempts reached, giving up')
      return
    }

    wsStatus.value = 'reconnecting'
    reconnectAttempts++
    console.log(`[ws] reconnecting in ${reconnectDelay}ms... (attempt ${reconnectAttempts}/${MAX_RECONNECT_ATTEMPTS})`)

    reconnectTimer = setTimeout(() => {
      reconnectTimer = null
      reconnectDelay = Math.min(reconnectDelay * 2, MAX_RECONNECT_DELAY)
      connect()
    }, reconnectDelay)
  }

  function startHeartbeat() {
    stopHeartbeat()
    heartbeatTimer = setInterval(() => {
      if (!ws || ws.readyState !== WebSocket.OPEN) return
      const elapsed = Date.now() - lastPingTime
      if (elapsed > HEARTBEAT_TIMEOUT) {
        console.warn(`[ws] heartbeat timeout (${elapsed}ms), closing connection`)
        ws.close()
      }
    }, HEARTBEAT_INTERVAL)
  }

  function stopHeartbeat() {
    if (heartbeatTimer) {
      clearInterval(heartbeatTimer)
      heartbeatTimer = null
    }
  }

  function disconnect() {
    // 标记为 intentional close，不触发重连
    if (reconnectTimer) {
      clearTimeout(reconnectTimer)
      reconnectTimer = null
    }
    stopHeartbeat()
    if (ws) {
      ws.onclose = null // 移除 onclose 避免触发重连
      ws.close()
      ws = null
    }
    wsStatus.value = 'disconnected'
    pendingMessages = []
    if (rafId !== null) {
      cancelAnimationFrame(rafId)
      rafId = null
    }
  }

  function scheduleBatchProcess() {
    if (rafId !== null) return
    rafId = requestAnimationFrame(() => {
      processBatch(pendingMessages)
      pendingMessages = []
      rafId = null
    })
  }

  function processBatch(messages: WSMessage[]) {
    const alertsStore = useAlertsStore()
    const camerasStore = useCamerasStore()
    const systemStore = useSystemStore()

    const deduped = new Map<string, WSMessage>()
    for (const msg of messages) {
      const key = msg.type + '_' + (msg.alert_id || msg.camera_id || '')
      deduped.set(key, msg)
    }

    for (const msg of deduped.values()) {
      switch (msg.type) {
        case 'new_alert':
          alertsStore.addRealtimeAlert(msg.alert || msg)
          break
        case 'alert_status':
          if (msg.alert_id && msg.new_status) {
            alertsStore.updateAlertStatusById(msg.alert_id, msg.new_status)
          }
          break
        case 'system_status':
          systemStore.updateHealth(msg as unknown as any)
          break
        case 'camera_status':
          if (msg.camera_id && msg.new_status) {
            camerasStore.updateCameraStatus(msg.camera_id, msg.new_status)
          }
          break
      }
    }
  }

  return { wsStatus, connect, disconnect }
}
