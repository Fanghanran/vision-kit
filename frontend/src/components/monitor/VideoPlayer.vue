<template>
  <div class="video-player" :class="{ offline: !connected }">
    <!-- 视频画面 -->
    <img
      v-if="frameUrl"
      :src="frameUrl"
      class="video-frame"
      alt="video stream"
    />

    <!-- 离线占位 -->
    <div v-else class="offline-placeholder">
      <el-icon :size="48"><VideoCamera /></el-icon>
      <span>{{ connected ? '等待画面...' : '连接中...' }}</span>
    </div>

    <!-- 信息栏 -->
    <div class="info-bar">
      <span class="cam-id">{{ cameraId }}</span>
      <span class="cam-fps" :class="{ active: fps > 0 }">{{ fps }} fps</span>
      <span class="cam-time">{{ currentTime }}</span>
    </div>

    <!-- 连接状态指示 -->
    <div class="status-badge" :class="connected ? 'online' : 'offline'">
      {{ connected ? '●' : '○' }}
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, onUnmounted, watch } from 'vue'
import { VideoCamera } from '@element-plus/icons-vue'
import { useVideoStream } from '@/composables/useVideoStream'

const props = defineProps<{
  cameraId: string
  showOverlay?: boolean
}>()

const { frameUrl, connected, fps, connect, disconnect } = useVideoStream(props.cameraId)

const currentTime = ref('')
let timeTimer = 0

function updateTime() {
  currentTime.value = new Date().toLocaleTimeString('zh-CN', { hour12: false })
}

onMounted(() => {
  connect()
  updateTime()
  timeTimer = window.setInterval(updateTime, 1000)
})

onUnmounted(() => {
  disconnect()
  if (timeTimer) clearInterval(timeTimer)
})

// 摄像头 ID 变化时重连
watch(
  () => props.cameraId,
  (newId, oldId) => {
    if (newId !== oldId) {
      disconnect()
      connect()
    }
  }
)
</script>

<style lang="scss" scoped>
.video-player {
  position: relative;
  width: 100%;
  height: 100%;
  background: #000;
  display: flex;
  align-items: center;
  justify-content: center;
}

.video-frame {
  width: 100%;
  height: 100%;
  object-fit: contain;
}

.offline-placeholder {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  color: #666;
  font-size: 14px;
}

.info-bar {
  position: absolute;
  bottom: 0;
  left: 0;
  right: 0;
  display: flex;
  justify-content: space-between;
  padding: 4px 8px;
  background: rgba(0, 0, 0, 0.6);
  font-size: 12px;
  color: #ccc;
  font-family: monospace;

  .cam-id {
    font-weight: 600;
    color: #fff;
  }

  .cam-fps {
    color: #999;

    &.active {
      color: #52c41a;
    }
  }
}

.status-badge {
  position: absolute;
  top: 6px;
  right: 6px;
  font-size: 12px;

  &.online {
    color: #52c41a;
  }

  &.offline {
    color: #ff4d4f;
  }
}
</style>
