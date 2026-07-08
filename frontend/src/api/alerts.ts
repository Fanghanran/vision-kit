import client from './client'
import type { Alert, AlertListResponse, AlertFilters } from './types'

export async function getAlerts(
  filters: AlertFilters = {},
  page = 1,
  pageSize = 20
): Promise<AlertListResponse> {
  const params = { page, page_size: pageSize, ...filters }
  const { data } = await client.get('/api/alerts', { params })
  return data
}

export async function getAlert(id: string): Promise<Alert> {
  const { data } = await client.get(`/api/alerts/${id}`)
  return data
}

export async function updateAlertStatus(
  id: string,
  status: string,
  acknowledgedBy = ''
): Promise<Alert> {
  const { data } = await client.put(`/api/alerts/${id}/status`, {
    status,
    acknowledged_by: acknowledgedBy,
  })
  return data
}
