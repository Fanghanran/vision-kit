<template>
  <div class="cameras-page">
    <div class="page-header">
      <h2>摄像头管理</h2>
      <el-button type="primary" :icon="Plus" @click="openAdd">添加摄像头</el-button>
    </div>

    <!-- 统计卡片 -->
    <el-row :gutter="16" class="stats-row">
      <el-col :span="6">
        <el-card shadow="hover" :body-style="{ padding: '20px' }">
          <div class="stat-card">
            <div class="stat-icon" style="background:#e6f4ff"><el-icon :size="24" color="#1677ff"><VideoCamera /></el-icon></div>
            <div class="stat-body">
              <div class="stat-value">{{ store.stats.total }}</div>
              <div class="stat-label">总设备</div>
            </div>
          </div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="hover" :body-style="{ padding: '20px' }">
          <div class="stat-card">
            <div class="stat-icon" style="background:#f6ffed"><el-icon :size="24" color="#389e0d"><CircleCheck /></el-icon></div>
            <div class="stat-body">
              <div class="stat-value">{{ store.stats.online }}</div>
              <div class="stat-label">在线</div>
            </div>
          </div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="hover" :body-style="{ padding: '20px' }">
          <div class="stat-card">
            <div class="stat-icon" style="background:#fff1f0"><el-icon :size="24" color="#cf1322"><CircleClose /></el-icon></div>
            <div class="stat-body">
              <div class="stat-value">{{ store.stats.offline }}</div>
              <div class="stat-label">离线</div>
            </div>
          </div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="hover" :body-style="{ padding: '20px' }">
          <div class="stat-card">
            <div class="stat-icon" style="background:#fffbe6"><el-icon :size="24" color="#d48806"><Warning /></el-icon></div>
            <div class="stat-body">
              <div class="stat-value">{{ store.stats.alerting }}</div>
              <div class="stat-label">告警中</div>
            </div>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <!-- 搜索筛选 + 卡片网格 -->
    <el-card shadow="hover">
      <div class="toolbar">
        <el-input v-model="search" placeholder="搜索名称或ID..." clearable style="width:240px" :prefix-icon="Search" />
        <div class="toolbar-right">
          <el-select v-model="filterStatus" placeholder="状态" clearable style="width:110px">
            <el-option label="在线" value="connected" />
            <el-option label="离线" value="disconnected" />
            <el-option label="错误" value="error" />
          </el-select>
          <el-select v-model="filterSource" placeholder="来源" clearable style="width:100px;margin-left:8px">
            <el-option label="RTSP" value="rtsp" />
            <el-option label="视频" value="video" />
            <el-option label="测试" value="test" />
          </el-select>
          <el-button :icon="Refresh" circle size="small" style="margin-left:8px" @click="refresh" />
        </div>
      </div>

      <el-row :gutter="16">
        <el-col :span="8" v-for="cam in filteredCameras" :key="cam.camera_id">
          <el-card shadow="hover" class="camera-card" :class="cam.status">
            <div class="cam-header">
              <span class="cam-dot" :class="cam.status" />
              <span class="cam-name">{{ cam.camera_id }}</span>
              <el-tag :type="sourceTagType(cam)" size="small" effect="plain">{{ sourceLabel(cam) }}</el-tag>
            </div>
            <div class="cam-status-row">
              <el-tag :type="statusTagType(cam.status)" effect="dark" size="small">
                {{ statusLabel(cam.status) }}
              </el-tag>
              <span class="cam-fps">{{ cam.current_fps?.toFixed(0) || 0 }} fps</span>
            </div>
            <div class="cam-stats">
              <div class="cam-stat-item">
                <div class="stat-num">{{ cam.queue_size }}</div>
                <div class="stat-lbl">队列</div>
              </div>
              <div class="cam-stat-item">
                <div class="stat-num">{{ cam.gpu_latency_ms?.toFixed(0) || '-' }}</div>
                <div class="stat-lbl">延迟ms</div>
              </div>
              <div class="cam-stat-item">
                <div class="stat-num">{{ cam.total_alerts }}</div>
                <div class="stat-lbl">告警</div>
              </div>
              <div class="cam-stat-item">
                <div class="stat-num">{{ fmtUptime(cam.uptime_seconds) }}</div>
                <div class="stat-lbl">运行</div>
              </div>
            </div>
            <div class="cam-actions">
              <el-button size="small" type="primary" @click="openDetail(cam.camera_id)">详情</el-button>
              <el-button size="small" :type="cam.status === 'connected' ? 'warning' : 'success'" @click="store.toggleCamera(cam.camera_id)">
                {{ cam.status === 'connected' ? '停止' : '启动' }}
              </el-button>
              <el-popconfirm title="确定删除此摄像头？" @confirm="store.deleteCamera(cam.camera_id)">
                <template #reference>
                  <el-button size="small" type="danger" :icon="Delete" />
                </template>
              </el-popconfirm>
            </div>
          </el-card>
        </el-col>
      </el-row>

      <el-empty v-if="!filteredCameras.length && !store.loading" description="暂无摄像头" />
    </el-card>

    <!-- 右侧详情抽屉 -->
    <el-drawer v-model="drawerVisible" title="摄像头详情" size="420px" direction="rtl" destroy-on-close @open="onDrawerOpen">
      <template v-if="detail">
        <div class="drawer-status">
          <el-tag :type="statusTagType(detail.status)" effect="dark" size="large">
            {{ statusIcon(detail.status) }} {{ statusLabel(detail.status) }}
          </el-tag>
        </div>

        <el-descriptions :column="1" border size="small" style="margin-top:16px" title="基本信息">
          <el-descriptions-item label="ID">{{ detail.camera_id }}</el-descriptions-item>
          <el-descriptions-item label="名称">{{ detail.camera_name || '-' }}</el-descriptions-item>
          <el-descriptions-item label="来源">{{ sourceTypeLabel(detail.source_type) }}</el-descriptions-item>
        </el-descriptions>

        <el-divider />

        <div class="drawer-section">
          <h4>运行指标</h4>
          <el-descriptions :column="2" border size="small">
            <el-descriptions-item label="FPS">{{ detail.fps?.toFixed(1) || '-' }}</el-descriptions-item>
            <el-descriptions-item label="队列深度">{{ detail.queue_size }}</el-descriptions-item>
            <el-descriptions-item label="总检测数">{{ detail.total_detections }}</el-descriptions-item>
            <el-descriptions-item label="今日告警">{{ detail.total_alerts }}</el-descriptions-item>
            <el-descriptions-item label="运行时长" :span="2">{{ fmtUptime(detail.uptime_seconds) }}</el-descriptions-item>
          </el-descriptions>
        </div>

        <el-divider />

        <div class="drawer-section">
          <h4>连接配置</h4>
          <el-descriptions :column="1" border size="small">
            <el-descriptions-item label="分辨率">{{ detail.resolution?.[0] || '-' }} × {{ detail.resolution?.[1] || '-' }}</el-descriptions-item>
            <el-descriptions-item label="RTSP 地址">
              <code style="font-size:12px">{{ maskRtsp(detail.rtsp_url) || '-' }}</code>
            </el-descriptions-item>
          </el-descriptions>
        </div>

        <div v-if="detail.error_message" class="drawer-error">
          <el-alert :title="detail.error_message" type="error" show-icon :closable="false" />
        </div>

        <div class="drawer-actions" style="margin-top:24px">
          <el-button :type="detail.status === 'connected' ? 'warning' : 'success'" @click="store.toggleCamera(detail.camera_id); refreshDetail()">
            {{ detail.status === 'connected' ? '停止' : '启动' }}
          </el-button>
          <el-button type="primary" @click="openEditFromDetail">编辑</el-button>
          <el-popconfirm title="确定删除？" @confirm="store.deleteCamera(detail.camera_id); drawerVisible = false">
            <template #reference>
              <el-button type="danger">删除</el-button>
            </template>
          </el-popconfirm>
        </div>
      </template>
    </el-drawer>

    <!-- 添加弹窗 -->
    <el-dialog v-model="addVisible" title="添加摄像头" width="480px" destroy-on-close>
      <el-form :model="addForm" label-width="100px">
        <el-form-item label="摄像头 ID" required>
          <el-input v-model="addForm.id" placeholder="cam_02" />
        </el-form-item>
        <el-form-item label="名称">
          <el-input v-model="addForm.name" placeholder="与 ID 相同时可留空" />
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
          <el-input-number v-model="addForm.fps" :min="0" :max="60" />
          <span class="form-tip">0 = 自动检测</span>
        </el-form-item>
        <el-form-item label="分辨率">
          <el-input-number v-model="addForm.width" :min="320" :max="4096" /> ×
          <el-input-number v-model="addForm.height" :min="240" :max="4096" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="addVisible = false">取消</el-button>
        <el-button type="primary" :disabled="!addForm.id" :loading="addLoading" @click="doAdd">添加</el-button>
      </template>
    </el-dialog>

    <!-- 编辑弹窗 -->
    <el-dialog v-model="editVisible" title="编辑摄像头" width="480px" destroy-on-close>
      <el-form :model="editForm" label-width="100px">
        <el-form-item label="名称">
          <el-input v-model="editForm.name" />
        </el-form-item>
        <el-form-item label="来源类型">
          <el-select v-model="editForm.source_type">
            <el-option label="RTSP 流" value="rtsp" />
            <el-option label="视频文件" value="video" />
            <el-option label="测试图案" value="test" />
          </el-select>
        </el-form-item>
        <el-form-item v-if="editForm.source_type === 'rtsp'" label="RTSP 地址">
          <el-input v-model="editForm.rtsp_url" placeholder="rtsp://..." />
        </el-form-item>
        <el-form-item v-if="editForm.source_type === 'video'" label="视频路径">
          <el-input v-model="editForm.video_path" placeholder="data/video.mp4" />
        </el-form-item>
        <el-form-item label="帧率">
          <el-input-number v-model="editForm.fps" :min="0" :max="60" />
          <span class="form-tip">0 = 自动检测</span>
        </el-form-item>
        <el-form-item label="分辨率">
          <el-input-number v-model="editForm.width" :min="320" :max="4096" /> ×
          <el-input-number v-model="editForm.height" :min="240" :max="4096" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editVisible = false">取消</el-button>
        <el-button type="primary" :loading="editLoading" @click="doEdit">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted, onUnmounted } from 'vue'
import { Plus, Refresh, Search, Delete, VideoCamera, CircleCheck, CircleClose, Warning } from '@element-plus/icons-vue'
import { useCamerasStore } from '@/stores/cameras'
import type { CameraDetail } from '@/api/cameras'
import type { CreateCameraPayload } from '@/api/cameras'
import { ElMessage } from 'element-plus'

const store = useCamerasStore()

// 轮询
let timer: ReturnType<typeof setInterval>
onMounted(() => { refresh(); timer = setInterval(refresh, 5000) })
onUnmounted(() => clearInterval(timer))
function refresh() { store.fetchCameras(); store.fetchStats() }

// 搜索筛选
const search = ref('')
const filterStatus = ref('')
const filterSource = ref('')

const filteredCameras = computed(() => {
  return store.cameras.filter(c => {
    if (search.value) {
      const q = search.value.toLowerCase()
      if (!c.camera_id.toLowerCase().includes(q)) return false
    }
    if (filterStatus.value && c.status !== filterStatus.value) return false
    // source_type not in CameraState, skip filter for now
    return true
  })
})

// 详情抽屉
const drawerVisible = ref(false)
const detail = ref<CameraDetail | null>(null)
const detailCameraId = ref('')

async function openDetail(cid: string) {
  detailCameraId.value = cid
  drawerVisible.value = true
}

async function onDrawerOpen() {
  detail.value = await store.fetchDetail(detailCameraId.value)
}

async function refreshDetail() {
  detail.value = await store.fetchDetail(detailCameraId.value)
}

function openEditFromDetail() {
  if (!detail.value) return
  drawerVisible.value = false
  openEditFromDetailData(detail.value)
}

function openEditFromDetailData(d: CameraDetail) {
  editingCamId.value = d.camera_id
  editForm.name = d.camera_name
  editForm.source_type = d.source_type as any
  editForm.rtsp_url = d.rtsp_url || ''
  editForm.video_path = ''
  editForm.fps = Math.round(d.fps)
  editForm.width = d.resolution?.[0] || 640
  editForm.height = d.resolution?.[1] || 640
  editVisible.value = true
}

// 添加
const addVisible = ref(false)
const addForm = reactive({ id: '', name: '', source_type: 'test' as 'rtsp'|'video'|'test', rtsp_url: '', video_path: '', fps: 0, width: 640, height: 640 })
const addLoading = ref(false)

function openAdd() {
  addForm.id = ''; addForm.name = ''; addForm.source_type = 'test'
  addForm.rtsp_url = ''; addForm.video_path = ''
  addForm.fps = 0; addForm.width = 640; addForm.height = 640
  addVisible.value = true
}

async function doAdd() {
  const payload: CreateCameraPayload = { id: addForm.id, name: addForm.name || addForm.id, source_type: addForm.source_type, fps: addForm.fps, resolution: [addForm.width, addForm.height] }
  if (addForm.source_type === 'rtsp') payload.rtsp_url = addForm.rtsp_url
  if (addForm.source_type === 'video') payload.video_path = addForm.video_path
  addLoading.value = true
  try { await store.createCamera(payload); addVisible.value = false } finally { addLoading.value = false }
}

// 编辑
const editVisible = ref(false)
const editingCamId = ref('')
const editForm = reactive({ name: '', source_type: 'test' as 'rtsp'|'video'|'test', rtsp_url: '', video_path: '', fps: 0, width: 640, height: 640 })
const editLoading = ref(false)

async function doEdit() {
  editLoading.value = true
  try {
    await store.updateCamera(editingCamId.value, {
      name: editForm.name, source_type: editForm.source_type, fps: editForm.fps,
      resolution: [editForm.width, editForm.height],
      ...(editForm.source_type === 'rtsp' ? { rtsp_url: editForm.rtsp_url } : {}),
      ...(editForm.source_type === 'video' ? { video_path: editForm.video_path } : {}),
    })
    editVisible.value = false
  } finally { editLoading.value = false }
}

// 标签/格式化
function statusTagType(s: string) { return { connected: 'success', connecting: 'warning', disconnected: 'info', error: 'danger' }[s] || 'info' }
function statusLabel(s: string) { return { connected: '在线', connecting: '连接中', disconnected: '离线', error: '错误' }[s] || s }
function statusIcon(s: string) { return { connected: '🟢', connecting: '🟡', disconnected: '🔴', error: '🔴' }[s] || '⚪' }
function sourceTagType(c: any) { return { rtsp: '', video: 'info', test: 'warning' }[c.source_type] || '' as any }
function sourceLabel(c: any) { return { rtsp: 'RTSP', video: '视频', test: '测试' }[c.source_type] || '' }
function sourceTypeLabel(s: string) { return { rtsp: 'RTSP 流', video: '本地视频', test: '测试图案' }[s] || s }
function fmtUptime(sec: number) {
  if (!sec || sec <= 0) return '-'
  const h = Math.floor(sec / 3600), m = Math.floor(sec % 3600 / 60)
  return h > 0 ? `${h}h ${m}m` : `${m}m`
}
function maskRtsp(url: string) {
  if (!url) return ''
  return url.replace(/(rtsp:\/\/)([^:]+):([^@]+)@/, '$1$2:***@')
}
</script>

<style lang="scss" scoped>
.cameras-page { padding: 20px; }
.page-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; h2 { margin: 0; font-size: 20px; } }
.stats-row { margin-bottom: 16px; }
.stat-card { display: flex; align-items: center; gap: 16px; }
.stat-icon { width: 48px; height: 48px; border-radius: 8px; display: flex; align-items: center; justify-content: center; }
.stat-value { font-size: 24px; font-weight: 700; line-height: 1.2; }
.stat-label { font-size: 13px; color: #999; margin-top: 2px; }
.toolbar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
.toolbar-right { display: flex; align-items: center; }

.camera-card {
  margin-bottom: 16px;
  &.disconnected, &.error { border-left: 3px solid #f5222d; }
  &.connecting { border-left: 3px solid #faad14; }
}
.cam-header { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
.cam-dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0;
  &.connected { background: #52c41a; }
  &.connecting { background: #faad14; }
  &.disconnected, &.error { background: #f5222d; }
}
.cam-name { font-size: 15px; font-weight: 600; flex: 1; }
.cam-status-row { display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; }
.cam-fps { font-size: 13px; color: #999; }
.cam-stats { display: grid; grid-template-columns: 1fr 1fr 1fr 1fr; gap: 4px; margin-bottom: 12px; }
.cam-stat-item { text-align: center; .stat-num { font-size: 16px; font-weight: 600; } .stat-lbl { font-size: 11px; color:#999; } }
.cam-actions { display: flex; justify-content: center; gap: 6px; }

.drawer-status { text-align: center; }
.drawer-section { h4 { margin: 0 0 8px; font-size: 14px; } }
.drawer-error { margin-top: 16px; }
.drawer-actions { display: flex; gap: 8px; justify-content: center; }

.form-tip { margin-left: 8px; font-size: 12px; color: #999; }
</style>
