<template>
  <ModuleStatusCard
    title="通知模块"
    :status="notifyStatus"
    :metrics="notifyMetrics"
  >
    <template #header-right>
      <el-button size="small" :icon="Refresh" circle @click="fetchStatus" />
    </template>

    <ControlSwitch
      label="Webhook 通知"
      description="企业微信/钉钉群机器人推送"
      :model-value="controls['notification.webhook.enabled'] ?? false"
      api-key="notification.webhook.enabled"
      @update:model-value="val => controls['notification.webhook.enabled'] = val"
    />
    <ControlSwitch
      label="邮件通知"
      description="SMTP 邮件告警"
      :model-value="controls['notification.email.enabled'] ?? false"
      api-key="notification.email.enabled"
      @update:model-value="val => controls['notification.email.enabled'] = val"
    />

    <el-descriptions :column="2" border style="margin-top: 16px" size="small">
      <el-descriptions-item label="Webhook 类型">{{ config?.notification?.webhook?.type || 'wechat' }}</el-descriptions-item>
      <el-descriptions-item label="SMTP 服务器">{{ config?.notification?.email?.smtp_host || '-' }}</el-descriptions-item>
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

const notifyStatus = computed(() => {
  const wh = controls['notification.webhook.enabled'] ?? false
  const em = controls['notification.email.enabled'] ?? false
  if (!wh && !em) return 'warning'
  return 'ok'
})

const notifyMetrics = computed(() => [
  { label: '今日发送', value: status.value?.today_sent ?? '-', color: 'var(--va-primary)' },
  { label: '成功率', value: status.value?.success_rate != null ? `${(status.value.success_rate * 100).toFixed(0)}%` : '-', color: (status.value?.success_rate ?? 0) > 0.9 ? 'var(--va-success)' : 'var(--va-warning)' },
  { label: 'Webhook', value: (controls['notification.webhook.enabled'] ?? false) ? '开启' : '关闭', color: (controls['notification.webhook.enabled'] ?? false) ? 'var(--va-success)' : 'var(--va-text-secondary)' },
  { label: '邮件', value: (controls['notification.email.enabled'] ?? false) ? '开启' : '关闭', color: (controls['notification.email.enabled'] ?? false) ? 'var(--va-success)' : 'var(--va-text-secondary)' },
])

async function fetchStatus() {
  try {
    const { data } = await client.get('/api/system/controls')
    Object.entries(data.controls || {}).forEach(([key, info]: [string, any]) => {
      if (key.startsWith('notification.')) controls[key] = info.value
    })
  } catch { /* ignore */ }
}

onMounted(fetchStatus)
</script>
