import { defineStore } from 'pinia'
import { ref } from 'vue'
import * as alertsApi from '@/api/alerts'
import type { Alert, AlertListResponse, AlertFilters } from '@/api/types'

export const useAlertsStore = defineStore('alerts', () => {
  const alerts = ref<Alert[]>([])
  const total = ref(0)
  const loading = ref(false)
  const realtimeAlerts = ref<Alert[]>([]) // WebSocket 推送的实时告警

  async function fetchAlerts(filters: AlertFilters = {}, page = 1, pageSize = 20) {
    loading.value = true
    try {
      const data = await alertsApi.getAlerts(filters, page, pageSize)
      alerts.value = data.items
      total.value = data.total
    } catch (e) {
      console.error('fetchAlerts failed:', e)
    } finally {
      loading.value = false
    }
  }

  async function getAlert(id: string): Promise<Alert | null> {
    try {
      return await alertsApi.getAlert(id)
    } catch (e) {
      console.error('getAlert failed:', e)
      return null
    }
  }

  async function updateAlertStatus(id: string, status: string, by = '') {
    try {
      const updated = await alertsApi.updateAlertStatus(id, status, by)
      // 更新列表中的对应项
      const idx = alerts.value.findIndex((a) => a.alert_id === id)
      if (idx >= 0) alerts.value[idx] = updated
      return updated
    } catch (e) {
      console.error('updateAlertStatus failed:', e)
      throw e
    }
  }

  function addRealtimeAlert(raw: any) {
    // 规范化：兼容嵌套（event.*）和扁平两种格式
    const alert: Alert = {
      alert_id: raw.alert_id || '',
      event_type: raw.event_type || raw.event?.event_type || '',
      camera_id: raw.camera_id || raw.event?.camera_id || '',
      camera_name: raw.camera_name || raw.event?.camera_name || '',
      severity: raw.severity || raw.event?.severity || 'info',
      status: raw.status || 'pending',
      risk_level: raw.risk_level,
      created_at: raw.created_at || Date.now() / 1000,
    }
    realtimeAlerts.value.unshift(alert)
    if (realtimeAlerts.value.length > 50) {
      realtimeAlerts.value = realtimeAlerts.value.slice(0, 50)
    }
  }

  function updateAlertStatusById(id: string, newStatus: string) {
    const idx = alerts.value.findIndex((a) => a.alert_id === id)
    if (idx >= 0) alerts.value[idx].status = newStatus as any
    const rIdx = realtimeAlerts.value.findIndex((a) => a.alert_id === id)
    if (rIdx >= 0) realtimeAlerts.value[rIdx].status = newStatus as any
  }

  return {
    alerts, total, loading, realtimeAlerts,
    fetchAlerts, getAlert, updateAlertStatus,
    addRealtimeAlert, updateAlertStatusById,
  }
})
