/**
 * 告警通知 composable — 浏览器通知 + 提示音
 *
 * 提示音使用 Web Audio API 生成，无需外部音频文件
 * 设置存 localStorage:
 *   - va-sound-enabled: bool
 *   - va-desktop-notify-enabled: bool
 */

import type { Alert } from '@/api/types'

let audioCtx: AudioContext | null = null

function ensureAudioContext(): AudioContext {
  if (!audioCtx) {
    audioCtx = new (window.AudioContext || (window as any).webkitAudioContext)()
  }
  return audioCtx
}

/**
 * 生成提示音（beep）
 * @param severity 严重级别，影响音高和时长
 */
export function playAlertSound(severity: string) {
  const enabled = localStorage.getItem('va-sound-enabled') !== 'false'
  if (!enabled) return

  try {
    const ctx = ensureAudioContext()
    if (ctx.state === 'suspended') ctx.resume()
    const osc = ctx.createOscillator()
    const gain = ctx.createGain()

    osc.connect(gain)
    gain.connect(ctx.destination)

    // 根据严重级别调整音高和时长
    const isCritical = severity === 'critical'
    osc.type = isCritical ? 'square' : 'sine'
    osc.frequency.value = isCritical ? 880 : 440

    const now = ctx.currentTime
    gain.gain.setValueAtTime(0.3, now)
    gain.gain.exponentialRampToValueAtTime(0.01, now + (isCritical ? 0.4 : 0.2))

    osc.start(now)
    osc.stop(now + (isCritical ? 0.4 : 0.2))

    // critical 时连响两声
    if (isCritical) {
      const osc2 = ctx.createOscillator()
      const gain2 = ctx.createGain()
      osc2.connect(gain2)
      gain2.connect(ctx.destination)
      osc2.type = 'square'
      osc2.frequency.value = 880
      gain2.gain.setValueAtTime(0.3, now + 0.25)
      gain2.gain.exponentialRampToValueAtTime(0.01, now + 0.6)
      osc2.start(now + 0.25)
      osc2.stop(now + 0.6)
    }
  } catch {
    // Web Audio 不支持时静默失败
  }
}

/**
 * 请求浏览器通知权限
 */
export async function requestNotificationPermission(): Promise<boolean> {
  if (!('Notification' in window)) return false
  if (Notification.permission === 'granted') return true
  if (Notification.permission === 'denied') return false
  const result = await Notification.requestPermission()
  return result === 'granted'
}

/**
 * 显示浏览器通知
 */
export function showBrowserNotification(alert: Alert) {
  const enabled = localStorage.getItem('va-desktop-notify-enabled') !== 'false'
  if (!enabled) return
  if (!('Notification' in window)) return
  if (Notification.permission !== 'granted') return

  const severityMap: Record<string, string> = {
    critical: '紧急',
    warning: '警告',
    info: '信息',
  }
  const eventTypeMap: Record<string, string> = {
    intrusion: '闯入',
    absence: '离岗',
    crowd: '聚集',
    abandoned_object: '遗留物',
    counting: '人数统计',
  }

  const title = `SentinelMind ${severityMap[alert.severity] || '告警'}`
  const body = `[${eventTypeMap[alert.event_type] || alert.event_type}] ${alert.camera_name}`

  try {
    const n = new Notification(title, {
      body,
      icon: '/favicon.ico',
      tag: alert.alert_id,
      requireInteraction: alert.severity === 'critical',
    })
    n.onclick = () => {
      window.focus()
      window.location.href = `/alerts/${alert.alert_id}`
      n.close()
    }
  } catch {
    // 通知失败静默处理
  }
}

/**
 * 统一通知入口
 */
export function notifyAlert(alert: Alert) {
  playAlertSound(alert.severity)
  showBrowserNotification(alert)
}

/**
 * 获取当前通知设置
 */
export function getNotificationSettings() {
  return {
    soundEnabled: localStorage.getItem('va-sound-enabled') !== 'false',
    desktopEnabled: localStorage.getItem('va-desktop-notify-enabled') !== 'false',
  }
}

/**
 * 更新通知设置
 */
export function setNotificationSettings(settings: { soundEnabled?: boolean; desktopEnabled?: boolean }) {
  if (settings.soundEnabled !== undefined) {
    localStorage.setItem('va-sound-enabled', String(settings.soundEnabled))
  }
  if (settings.desktopEnabled !== undefined) {
    localStorage.setItem('va-desktop-notify-enabled', String(settings.desktopEnabled))
  }
}
