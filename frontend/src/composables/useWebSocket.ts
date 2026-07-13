import { ref } from 'vue'
import { useAlertsStore } from '@/stores/alerts'
import { useCamerasStore } from '@/stores/cameras'
import { useSystemStore } from '@/stores/system'
import type { WSMessage } from '@/api/types'

const wsStatus = ref<'connected' | 'disconnected'>('disconnected')
let ws: WebSocket | null = null
let pendingMessages: WSMessage[] = []
let rafId: number | null = null

const BATCH_INTERVAL = 100 // 每 100ms 合并处理一次消息

export function useWebSocket() {
  function connect() {
    if (ws && ws.readyState === WebSocket.OPEN) return

    const token = localStorage.getItem('va-token') || ''
    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
    const url = `${protocol}//${location.host}/ws?token=${encodeURIComponent(token)}`

    try {
      ws = new WebSocket(url)
    } catch {
      wsStatus.value = 'disconnected'
      return
    }

    ws.onopen = () => {
      wsStatus.value = 'connected'
      console.log('[ws] connected')
    }

    ws.onmessage = (event) => {
      try {
        const msg: WSMessage = JSON.parse(event.data)
        pendingMessages.push(msg)
        scheduleBatchProcess()
      } catch (e) {
        console.warn('[ws] parse error:', e)
      }
    }

    ws.onclose = () => {
      wsStatus.value = 'disconnected'
      ws = null
    }

    ws.onerror = () => {
      ws?.close()
    }
  }

  function disconnect() {
    if (ws) {
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

    // 去重：相同类型+相同 ID 只保留最新的
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
