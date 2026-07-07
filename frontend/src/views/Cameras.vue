<template>
  <div class="cameras">
    <el-card shadow="hover">
      <template #header>
        <div class="card-header">
          <span>摄像头状态</span>
          <el-button :icon="Refresh" circle @click="camerasStore.fetchCameras()" />
        </div>
      </template>

      <el-row :gutter="16">
        <el-col :span="6" v-for="cam in cameras" :key="cam.camera_id">
          <el-card shadow="hover" class="camera-card" :class="cam.status">
            <div class="camera-id">{{ cam.camera_id }}</div>
            <el-tag :type="statusTagType(cam.status)" effect="dark" size="large">
              {{ statusIcon(cam.status) }} {{ statusLabel(cam.status) }}
            </el-tag>
            <div class="camera-stats">
              <div><span class="label">FPS</span> {{ cam.current_fps }}</div>
              <div><span class="label">总帧数</span> {{ cam.total_frames.toLocaleString() }}</div>
              <div><span class="label">告警</span> {{ cam.total_alerts }}</div>
              <div><span class="label">队列</span> {{ cam.queue_size }}</div>
            </div>
            <div v-if="cam.error_message" class="error-msg">{{ cam.error_message }}</div>
          </el-card>
        </el-col>
      </el-row>

      <el-empty v-if="!cameras.length" description="暂无摄像头配置" />
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted } from 'vue'
import { Refresh } from '@element-plus/icons-vue'
import { useCamerasStore } from '@/stores/cameras'

const camerasStore = useCamerasStore()
const cameras = computed(() => camerasStore.cameras)

let refreshTimer: ReturnType<typeof setInterval>
onMounted(() => {
  camerasStore.fetchCameras()
  refreshTimer = setInterval(() => camerasStore.fetchCameras(), 5000)
})
onUnmounted(() => clearInterval(refreshTimer))

function statusTagType(s: string) {
  return { connected: 'success', connecting: 'warning', disconnected: 'danger', error: 'danger' }[s] || 'info'
}
function statusLabel(s: string) {
  return { connected: '在线', connecting: '连接中', disconnected: '离线', error: '错误' }[s] || s
}
function statusIcon(s: string) {
  return { connected: '🟢', connecting: '🟡', disconnected: '🔴', error: '🔴' }[s] || '⚪'
}
</script>

<style lang="scss" scoped>
.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.camera-card {
  margin-bottom: 16px;
  text-align: center;
  &.disconnected, &.error { border-color: var(--va-danger); }
  &.connecting { border-color: var(--va-warning); }
}

.camera-id {
  font-size: 16px;
  font-weight: 600;
  margin-bottom: 8px;
}

.camera-stats {
  margin-top: 12px;
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px;
  font-size: 14px;
  .label { color: var(--va-text-secondary); }
}

.error-msg {
  margin-top: 8px;
  font-size: 12px;
  color: var(--va-danger);
}
</style>
