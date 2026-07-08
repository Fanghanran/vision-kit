import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import * as systemApi from '@/api/system'
import type { HealthResponse, SystemStats } from '@/api/types'

export const useSystemStore = defineStore('system', () => {
  const health = ref<HealthResponse | null>(null)
  const statsByPeriod = ref<Record<string, SystemStats>>({})
  const config = ref<Record<string, any> | null>(null)

  // 向后兼容：默认返回 today 的 stats
  const stats = computed(() => statsByPeriod.value['today'] || null)

  async function fetchHealth() {
    try {
      health.value = await systemApi.getHealth()
    } catch (e) {
      console.error('fetchHealth failed:', e)
    }
  }

  async function fetchStats(period = 'today') {
    try {
      const data = await systemApi.getStats(period)
      statsByPeriod.value[period] = data
    } catch (e) {
      console.error('fetchStats failed:', e)
    }
  }

  function getStats(period: string): SystemStats | null {
    return statsByPeriod.value[period] || null
  }

  async function fetchConfig() {
    try {
      config.value = await systemApi.getConfig()
    } catch (e) {
      console.error('fetchConfig failed:', e)
    }
  }

  function updateHealth(h: HealthResponse) {
    health.value = h
  }

  return {
    health, stats, statsByPeriod, config,
    fetchHealth, fetchStats, getStats, fetchConfig, updateHealth,
  }
})
