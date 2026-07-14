<template>
  <ModuleStatusCard
    title="录制模块"
    :status="(controls['recording.enabled'] ?? true) ? 'ok' : 'warning'"
    :metrics="recordingMetrics"
  >
    <template #header-right>
      <el-button size="small" :icon="Refresh" circle @click="fetchStatus" />
    </template>

    <ControlSwitch
      label="启用告警录制"
      description="关闭后不再录制告警视频片段"
      :model-value="controls['recording.enabled'] ?? true"
      api-key="recording.enabled"
      @update:model-value="val => controls['recording.enabled'] = val"
    />

    <el-descriptions :column="2" border style="margin-top: 16px" size="small">
      <el-descriptions-item label="缓冲时长">{{ config?.recording?.buffer_duration || 30 }}s</el-descriptions-item>
      <el-descriptions-item label="告警前截取">{{ config?.recording?.default_before || 15 }}s</el-descriptions-item>
      <el-descriptions-item label="告警后截取">{{ config?.recording?.default_after || 15 }}s</el-descriptions-item>
      <el-descriptions-item label="视频保留">{{ config?.recording?.retention_days || 7 }} 天</el-descriptions-item>
      <el-descriptions-item label="截图保留">{{ config?.recording?.snapshot_retention_days || 30 }} 天</el-descriptions-item>
      <el-descriptions-item label="磁盘上限">{{ config?.recording?.max_disk_gb || 50 }} GB</el-descriptions-item>
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

const recordingMetrics = computed(() => [
  { label: '今日片段', value: status.value?.today_clips ?? '-', color: 'var(--va-primary)' },
  { label: '截图数', value: status.value?.today_snapshots ?? '-', color: 'var(--va-primary)' },
  { label: '磁盘占用', value: status.value?.disk_usage_gb != null ? `${status.value.disk_usage_gb.toFixed(1)} GB` : '-', color: 'var(--va-primary)' },
  { label: '缓冲区', value: status.value?.buffer_status ?? '正常', color: 'var(--va-success)' },
])

async function fetchStatus() {
  try {
    const { data } = await client.get('/api/system/controls')
    Object.entries(data.controls || {}).forEach(([key, info]: [string, any]) => {
      if (key.startsWith('recording.')) controls[key] = info.value
    })
  } catch { /* ignore */ }
}

onMounted(fetchStatus)
</script>
