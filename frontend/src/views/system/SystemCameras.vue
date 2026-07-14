<template>
  <ModuleStatusCard
    title="摄像头模块"
    :status="cameraStatus"
    :metrics="cameraMetrics"
  >
    <template #header-right>
      <el-button size="small" :icon="Refresh" circle @click="fetchStatus" />
    </template>

    <ControlSwitch
      label="自动重连"
      description="摄像头断线后自动重连（指数退避）"
      :model-value="controls['camera.auto_reconnect'] ?? true"
      api-key="camera.auto_reconnect"
      @update:model-value="val => controls['camera.auto_reconnect'] = val"
    />
    <ControlSwitch
      label="WebSocket 推送"
      description="实时视频帧推送到前端"
      :model-value="controls['websocket.enabled'] ?? true"
      api-key="websocket.enabled"
      @update:model-value="val => controls['websocket.enabled'] = val"
    />
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
const controls = reactive<Record<string, boolean>>({})

const cameraStatus = computed(() => {
  const h = systemStore.health
  if (!h) return 'warning'
  if (h.active_cameras === 0 && h.total_cameras > 0) return 'error'
  if (h.active_cameras < h.total_cameras) return 'warning'
  return 'ok'
})

const cameraMetrics = computed(() => {
  const h = systemStore.health
  return [
    { label: '在线', value: h?.active_cameras ?? 0, color: 'var(--va-success)' },
    { label: '离线', value: (h?.total_cameras ?? 0) - (h?.active_cameras ?? 0), color: 'var(--va-danger)' },
    { label: '总路数', value: h?.total_cameras ?? 0, color: 'var(--va-primary)' },
    { label: '队列积压', value: h?.queue_depth ?? 0, color: (h?.queue_depth ?? 0) > 50 ? 'var(--va-warning)' : 'var(--va-success)' },
  ]
})

async function fetchStatus() {
  try {
    const { data } = await client.get('/api/system/controls')
    Object.entries(data.controls || {}).forEach(([key, info]: [string, any]) => {
      if (key.startsWith('camera.') || key.startsWith('websocket.')) controls[key] = info.value
    })
    await systemStore.fetchHealth()
  } catch { /* ignore */ }
}

onMounted(fetchStatus)
</script>
