import client from './client'
import type { HealthResponse, SystemStats } from './types'

export async function getHealth(): Promise<HealthResponse> {
  const { data } = await client.get('/health')
  return data
}

export async function getStats(period = 'today'): Promise<SystemStats> {
  const { data } = await client.get('/api/stats', { params: { period } })
  return data
}

export async function getConfig(): Promise<Record<string, any>> {
  const { data } = await client.get('/api/config')
  return data
}
