import { ref } from 'vue'
import { useAlertsStore } from '@/stores/alerts'
import { useCamerasStore } from '@/stores/cameras'
import { useSystemStore } from '@/stores/system'
import type { WSMessage } from '@/api/types'

const wsStatus = ref<'connected' | 'disconnected'>('disconnected')
let ws: WebSocket | null = null

export function useWebSocket() {
  function connect() {
    if (ws && ws.readyState === WebSocket.OPEN) return

    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
    const url = `${protocol}//${location.host}/ws`

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
        handleMessage(msg)
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
  }

  function handleMessage(msg: WSMessage) {
    const alertsStore = useAlertsStore()
    const camerasStore = useCamerasStore()
    const systemStore = useSystemStore()

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
        systemStore.updateHealth(msg)
        break
      case 'camera_status':
        if (msg.camera_id && msg.new_status) {
          camerasStore.updateCameraStatus(msg.camera_id, msg.new_status)
        }
        break
    }
  }

  return { wsStatus, connect, disconnect }
}
