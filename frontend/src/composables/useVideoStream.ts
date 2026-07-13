import { ref, onUnmounted } from 'vue'

/**
 * WebSocket JPEG 视频流 composable
 *
 * 协议（docs/frontend/MONITOR_PANEL.md 4.1 节）：
 * - 前 8 字节帧头：帧序号(uint32) + 时间戳(uint32)
 * - 后续字节：JPEG 数据
 */
export interface VideoFrame {
  frameSeq: number
  timestamp: number
  imageUrl: string
}

export function useVideoStream(cameraId: string) {
  const frameUrl = ref<string>('')
  const connected = ref(false)
  const frameSeq = ref(0)
  const fps = ref(0)

  let ws: WebSocket | null = null
  let fpsCounter = 0
  let fpsTimer = 0
  let currentBlobUrl: string | null = null
  let reconnectTimer = 0
  let reconnectDelay = 1000
  let intentionalClose = false

  function connect() {
    if (ws && ws.readyState === WebSocket.OPEN) return
    intentionalClose = false

    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
    const url = `${protocol}//${location.host}/ws/video/${cameraId}`

    try {
      ws = new WebSocket(url)
      ws.binaryType = 'arraybuffer'
    } catch (e) {
      console.warn('[video-ws] create failed:', e)
      connected.value = false
      scheduleReconnect()
      return
    }

    ws.onopen = () => {
      connected.value = true
      reconnectDelay = 1000 // 重置重连延迟
      fpsTimer = window.setInterval(() => {
        fps.value = fpsCounter
        fpsCounter = 0
      }, 1000)
    }

    ws.onmessage = (event: MessageEvent) => {
      // 跳过文本消息（心跳/JSON），只处理二进制帧
      if (typeof event.data === 'string' || !(event.data instanceof ArrayBuffer)) return
      const data = event.data as ArrayBuffer
      if (data.byteLength < 8) return

      // 解析帧头
      const header = new DataView(data, 0, 8)
      frameSeq.value = header.getUint32(0)

      // JPEG 数据
      const jpegData = data.slice(8)
      const blob = new Blob([jpegData], { type: 'image/jpeg' })

      // 释放旧的 blob URL
      if (currentBlobUrl) {
        URL.revokeObjectURL(currentBlobUrl)
      }
      currentBlobUrl = URL.createObjectURL(blob)
      frameUrl.value = currentBlobUrl
      fpsCounter++
    }

    ws.onclose = (event) => {
      console.warn('[video-ws] closed code=', event.code, 'reason=', event.reason)
      connected.value = false
      cleanupTimers()
      if (!intentionalClose) {
        scheduleReconnect()
      }
    }

    ws.onerror = (event) => {
      console.warn('[video-ws] error:', event)
      ws?.close()
    }
  }

  function scheduleReconnect() {
    if (reconnectTimer) return
    console.log(`[video-ws] reconnecting in ${reconnectDelay}ms...`)
    reconnectTimer = window.setTimeout(() => {
      reconnectTimer = 0
      reconnectDelay = Math.min(reconnectDelay * 2, 10000) // 指数退避，最大10s
      connect()
    }, reconnectDelay)
  }

  function disconnect() {
    intentionalClose = true
    if (reconnectTimer) {
      clearTimeout(reconnectTimer)
      reconnectTimer = 0
    }
    if (ws) {
      ws.close()
      ws = null
    }
    cleanupTimers()
  }

  function cleanupTimers() {
    if (fpsTimer) {
      clearInterval(fpsTimer)
      fpsTimer = 0
    }
    if (currentBlobUrl) {
      URL.revokeObjectURL(currentBlobUrl)
      currentBlobUrl = null
    }
    connected.value = false
    fps.value = 0
    fpsCounter = 0
  }

  // 组件卸载时自动断开
  onUnmounted(() => {
    disconnect()
  })

  return {
    frameUrl,
    connected,
    frameSeq,
    fps,
    connect,
    disconnect,
  }
}
