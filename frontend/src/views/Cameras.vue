<template>
  <div class="cameras">
    <el-card shadow="hover">
      <template #header>
        <div class="card-header">
          <span>摄像头状态</span>
          <div class="header-actions">
            <el-button type="primary" :icon="Plus" size="small" @click="openAddDialog">添加</el-button>
            <el-button :icon="Refresh" circle size="small" @click="camerasStore.fetchCameras()" />
          </div>
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
              <div><span class="label">告警</span> {{ cam.total_alerts }}</div>
              <div><span class="label">队列</span> {{ cam.queue_size }}</div>
            </div>
            <div class="camera-actions">
              <el-button
                size="small"
                :type="cam.status === 'connected' ? 'warning' : 'success'"
                @click="camerasStore.toggleCamera(cam.camera_id)"
              >
                {{ cam.status === 'connected' ? '停止' : '启动' }}
              </el-button>
              <el-popconfirm
                title="确定删除此摄像头？"
                confirm-button-text="删除"
                cancel-button-text="取消"
                @confirm="camerasStore.deleteCamera(cam.camera_id)"
              >
                <template #reference>
                  <el-button size="small" type="danger" :icon="Delete" />
                </template>
              </el-popconfirm>
            </div>
            <div v-if="cam.error_message" class="error-msg">{{ cam.error_message }}</div>
          </el-card>
        </el-col>
      </el-row>

      <el-empty v-if="!cameras.length" description="暂无摄像头配置" />
    </el-card>

    <!-- 添加摄像头弹窗 -->
    <el-dialog v-model="addDialogVisible" title="添加摄像头" width="480px" append-to-body>
      <el-form :model="addForm" label-width="100px">
        <el-form-item label="摄像头 ID" required>
          <el-input v-model="addForm.id" placeholder="cam_02" />
        </el-form-item>
        <el-form-item label="名称">
          <el-input v-model="addForm.name" placeholder="摄像头名称" />
        </el-form-item>
        <el-form-item label="来源类型">
          <el-select v-model="addForm.source_type">
            <el-option label="RTSP 流" value="rtsp" />
            <el-option label="视频文件" value="video" />
            <el-option label="测试图案" value="test" />
          </el-select>
        </el-form-item>
        <el-form-item v-if="addForm.source_type === 'rtsp'" label="RTSP 地址">
          <el-input v-model="addForm.rtsp_url" placeholder="rtsp://..." />
        </el-form-item>
        <el-form-item v-if="addForm.source_type === 'video'" label="视频路径">
          <el-input v-model="addForm.video_path" placeholder="data/video.mp4" />
        </el-form-item>
        <el-form-item label="帧率">
          <el-input-number v-model="addForm.fps" :min="0" :max="60" placeholder="0=自动" />
          <span class="form-tip">0=自动检测</span>
        </el-form-item>
        <el-form-item label="分辨率">
          <el-input-number v-model="addForm.width" :min="320" :max="4096" /> ×
          <el-input-number v-model="addForm.height" :min="240" :max="4096" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="addDialogVisible = false">取消</el-button>
        <el-button type="primary" @click="doAdd" :disabled="!addForm.id" :loading="addLoading">添加</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { Refresh, Plus, Delete } from '@element-plus/icons-vue'
import { useCamerasStore } from '@/stores/cameras'
import type { CreateCameraPayload } from '@/api/cameras'

const camerasStore = useCamerasStore()
const cameras = computed(() => camerasStore.cameras)

let refreshTimer: ReturnType<typeof setInterval>
onMounted(() => {
  camerasStore.fetchCameras()
  refreshTimer = setInterval(() => camerasStore.fetchCameras(), 5000)
})
onUnmounted(() => clearInterval(refreshTimer))

// 添加弹窗
const addDialogVisible = ref(false)
type SourceType = 'rtsp' | 'video' | 'test'
const addForm = reactive({
  id: '',
  name: '',
  source_type: 'test' as SourceType,
  rtsp_url: '',
  video_path: '',
  fps: 0,
  width: 640,
  height: 640,
})

function openAddDialog() {
  addForm.id = ''
  addForm.name = ''
  addForm.source_type = 'test'
  addForm.rtsp_url = ''
  addForm.video_path = ''
  addForm.fps = 0
  addForm.width = 640
  addForm.height = 640
  addDialogVisible.value = true
}

const addLoading = ref(false)

async function doAdd() {
  const payload: CreateCameraPayload = {
    id: addForm.id,
    name: addForm.name || addForm.id,
    source_type: addForm.source_type,
    fps: addForm.fps,
    resolution: [addForm.width, addForm.height],
  }
  if (addForm.source_type === 'rtsp') {
    payload.rtsp_url = addForm.rtsp_url
  }
  if (addForm.source_type === 'video') {
    payload.video_path = addForm.video_path
  }
  addLoading.value = true
  try {
    await camerasStore.createCamera(payload)
    addDialogVisible.value = false
  } finally {
    addLoading.value = false
  }
}

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

.header-actions {
  display: flex;
  align-items: center;
  gap: 8px;
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

.camera-actions {
  margin-top: 12px;
  display: flex;
  justify-content: center;
  gap: 8px;
}

.error-msg {
  margin-top: 8px;
  font-size: 12px;
  color: var(--va-danger);
}

.form-tip {
  margin-left: 8px;
  font-size: 12px;
  color: var(--va-text-secondary);
}
</style>
