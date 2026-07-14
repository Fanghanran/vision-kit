<template>
  <ModuleStatusCard
    title="LLM 模块"
    :status="llmStatus"
    :metrics="llmMetrics"
  >
    <template #header-right>
      <el-button size="small" :icon="Refresh" circle @click="fetchStatus" />
    </template>

    <ControlSwitch
      label="启用 LLM 分析"
      description="关闭后告警仅含规则引擎原始结果，不调用 LLM"
      :model-value="controls['llm.enabled'] ?? true"
      api-key="llm.enabled"
      @update:model-value="val => controls['llm.enabled'] = val"
    />
    <ControlSwitch
      label="响应缓存"
      description="相同输入的 LLM 响应缓存 1 小时"
      :model-value="controls['llm.cache_enabled'] ?? true"
      api-key="llm.cache_enabled"
      @update:model-value="val => controls['llm.cache_enabled'] = val"
    />

    <el-descriptions :column="2" border style="margin-top: 16px" size="small">
      <el-descriptions-item label="当前模型">{{ config?.llm?.model || '-' }}</el-descriptions-item>
      <el-descriptions-item label="API 地址">{{ config?.llm?.api_base || '-' }}</el-descriptions-item>
      <el-descriptions-item label="请求超时">{{ config?.llm?.timeout || 30 }}s</el-descriptions-item>
      <el-descriptions-item label="月度预算">${{ config?.llm?.daily_budget || 100 }}</el-descriptions-item>
    </el-descriptions>
  </ModuleStatusCard>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted } from 'vue'
import { Refresh } from '@element-plus/icons-vue'
import ModuleStatusCard from '@/components/system/ModuleStatusCard.vue'
import ControlSwitch from '@/components/system/ControlSwitch.vue'
import client from '@/api/client'
import { useSystemStore } from '@/stores/system'

const systemStore = useSystemStore()
const config = computed(() => systemStore.config)

const controls = reactive<Record<string, boolean>>({})
const status = ref<any>(null)

const llmStatus = computed(() => {
  if (!(controls['llm.enabled'] ?? true)) return 'warning'
  if (status.value?.circuit_breaker === 'open') return 'error'
  return 'ok'
})

const llmMetrics = computed(() => [
  { label: '今日调用', value: status.value?.today_calls ?? '-', color: 'var(--va-primary)' },
  { label: '成功率', value: status.value?.success_rate != null ? `${(status.value.success_rate * 100).toFixed(0)}%` : '-', color: (status.value?.success_rate ?? 0) > 0.9 ? 'var(--va-success)' : 'var(--va-warning)' },
  { label: '当月费用', value: status.value?.monthly_cost != null ? `$${status.value.monthly_cost.toFixed(2)}` : '-', color: 'var(--va-primary)' },
  { label: '断路器', value: status.value?.circuit_breaker === 'open' ? '打开' : status.value?.circuit_breaker === 'half_open' ? '半开' : '关闭', color: status.value?.circuit_breaker === 'open' ? 'var(--va-danger)' : 'var(--va-success)' },
])

async function fetchStatus() {
  try {
    const { data } = await client.get('/api/system/controls')
    Object.entries(data.controls || {}).forEach(([key, info]: [string, any]) => {
      if (key.startsWith('llm.')) controls[key] = info.value
    })
    const { data: s } = await client.get('/api/system/modules/llm/status').catch(() => ({ data: null }))
    status.value = s
  } catch { /* ignore */ }
}

onMounted(fetchStatus)
</script>
