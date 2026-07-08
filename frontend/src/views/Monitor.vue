<template>
  <div class="monitor">
    <!-- 顶栏 -->
    <el-card shadow="hover" class="toolbar">
      <div class="toolbar-inner">
        <div class="toolbar-left">
          <span class="toolbar-title">视频监控</span>
          <el-radio-group v-model="layout" size="small" @change="onLayoutChange">
            <el-radio-button :value="1">1×1</el-radio-button>
            <el-radio-button :value="2">2×2</el-radio-button>
            <el-radio-button :value="3">3×3</el-radio-button>
            <el-radio-button :value="4">4×4</el-radio-button>
          </el-radio-group>
        </div>
        <div class="toolbar-right">
          <el-switch v-model="showOverlay" active-text="叠加" inactive-text="" size="small" />
          <span class="time-display">{{ currentTime }}</span>
        </div>
      </div>
    </el-card>

    <!-- 视频网格 -->
    <div
      class="video-grid"
      :style="{
        gridTemplateColumns: `repeat(${layout}, 1fr)`,
        gridTemplateRows: `repeat(${layout}, 1fr)`,
      }"
    >
      <div
        v-for="(cell, idx) in cells"
        :key="idx"
        class="video-cell"
        :class="{ active: cell.cameraId, selected: selectedCell === idx }"
        @click="selectedCell = idx"
        @dblclick="toggleFullscreen(idx)"
        @contextmenu.prevent="openContextMenu($event, idx)"
      >
        <!-- 有摄像头 -->
        <VideoPlayer
          v-if="cell.cameraId"
          :camera-id="cell.cameraId"
          :show-overlay="showOverlay"
        />

        <!-- 空位 -->
        <div v-else class="empty-cell" @click="openCameraSelector(idx)">
          <el-icon :size="32"><Plus /></el-icon>
          <span>点击选择摄像头</span>
        </div>
      </div>
    </div>

    <!-- 摄像头选择弹窗 -->
    <el-dialog v-model="selectorVisible" title="选择摄像头" width="360px" append-to-body>
      <el-radio-group v-model="selectedCameraId" class="camera-radio-group">
        <el-radio
          v-for="cam in availableCameras"
          :key="cam.camera_id"
          :value="cam.camera_id"
          :disabled="cam.status === 'disconnected' || cam.status === 'error'"
        >
          <span :class="['status-dot', cam.status]"></span>
          {{ cam.camera_id }} ({{ cam.status === 'connected' ? '在线' : '离线' }})
        </el-radio>
      </el-radio-group>
      <template #footer>
        <el-button @click="selectorVisible = false">取消</el-button>
        <el-button type="primary" @click="assignCamera" :disabled="!selectedCameraId">
          确定
        </el-button>
      </template>
    </el-dialog>

    <!-- 右键菜单 -->
    <div
      v-if="contextMenu.visible"
      class="context-menu"
      :style="{ left: contextMenu.x + 'px', top: contextMenu.y + 'px' }"
    >
      <div class="context-item" @click="openCameraSelector(contextMenu.cellIdx)">
        <el-icon><Switch /></el-icon> 替换
      </div>
      <div class="context-item" @click="removeCamera(contextMenu.cellIdx)">
        <el-icon><Close /></el-icon> 移除
      </div>
      <div class="context-item" @click="toggleFullscreen(contextMenu.cellIdx)">
        <el-icon><FullScreen /></el-icon> 全屏
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { Plus, Switch, Close, FullScreen } from '@element-plus/icons-vue'
import { useCamerasStore } from '@/stores/cameras'
import VideoPlayer from '@/components/monitor/VideoPlayer.vue'

interface Cell {
  cameraId: string | null
}

const camerasStore = useCamerasStore()
const cameras = computed(() => camerasStore.cameras)

// 布局
const layout = ref(2)
const cells = ref<Cell[]>([])
const selectedCell = ref<number | null>(null)
const showOverlay = ref(true)

// 摄像头选择
const selectorVisible = ref(false)
const selectedCameraId = ref<string | null>(null)
const pendingCellIdx = ref<number | null>(null)

// 右键菜单
const contextMenu = ref({ visible: false, x: 0, y: 0, cellIdx: 0 })

// 时间显示
const currentTime = ref('')
let timeTimer = 0

// 初始化格子
function initCells(count: number) {
  const old = cells.value
  const newCells: Cell[] = []
  for (let i = 0; i < count; i++) {
    newCells.push(i < old.length ? old[i] : { cameraId: null })
  }
  cells.value = newCells
}

function onLayoutChange(val: number) {
  initCells(val * val)
}

// 可用摄像头（排除已分配的）
const assignedIds = computed(() =>
  new Set(cells.value.map((c) => c.cameraId).filter(Boolean))
)
const availableCameras = computed(() =>
  cameras.value.filter((c) => !assignedIds.value.has(c.camera_id))
)

// 打开摄像头选择
function openCameraSelector(idx: number) {
  pendingCellIdx.value = idx
  selectedCameraId.value = cells.value[idx]?.cameraId || null
  selectorVisible.value = true
  contextMenu.value.visible = false
}

// 分配摄像头
function assignCamera() {
  if (pendingCellIdx.value !== null && selectedCameraId.value) {
    cells.value[pendingCellIdx.value] = { cameraId: selectedCameraId.value }
  }
  selectorVisible.value = false
}

// 移除摄像头
function removeCamera(idx: number) {
  cells.value[idx] = { cameraId: null }
  contextMenu.value.visible = false
}

// 右键菜单
function openContextMenu(e: MouseEvent, idx: number) {
  if (!cells.value[idx]?.cameraId) return
  contextMenu.value = { visible: true, x: e.clientX, y: e.clientY, cellIdx: idx }
}

// 全屏
function toggleFullscreen(idx: number) {
  const el = document.querySelectorAll('.video-cell')[idx]
  if (el && document.fullscreenElement !== el) {
    el.requestFullscreen?.()
  } else {
    document.exitFullscreen?.()
  }
}

// 点击其他区域关闭右键菜单
function closeContextMenu() {
  contextMenu.value.visible = false
}

// 时间更新
function updateTime() {
  const now = new Date()
  currentTime.value = now.toLocaleTimeString('zh-CN', { hour12: false })
}

onMounted(() => {
  camerasStore.fetchCameras()
  initCells(layout.value * layout.value)
  updateTime()
  timeTimer = window.setInterval(updateTime, 1000)
  document.addEventListener('click', closeContextMenu)
})

onUnmounted(() => {
  if (timeTimer) clearInterval(timeTimer)
  document.removeEventListener('click', closeContextMenu)
})

// 自动分配：摄像头列表加载后，自动填入前 N 个
watch(
  () => cameras.value,
  (cams) => {
    if (cams.length === 0) return
    const emptyIdx = cells.value.findIndex((c) => !c.cameraId)
    if (emptyIdx === -1) return
    // 只自动填第一个空位
    const unassigned = cams.find((c) => !assignedIds.value.has(c.camera_id))
    if (unassigned) {
      cells.value[emptyIdx] = { cameraId: unassigned.camera_id }
    }
  },
  { immediate: true }
)
</script>

<style lang="scss" scoped>
.monitor {
  display: flex;
  flex-direction: column;
  height: calc(100vh - 56px - 32px - 48px);
  gap: 12px;
}

.toolbar {
  flex-shrink: 0;

  .toolbar-inner {
    display: flex;
    align-items: center;
    justify-content: space-between;
  }

  .toolbar-left {
    display: flex;
    align-items: center;
    gap: 16px;
  }

  .toolbar-title {
    font-size: 16px;
    font-weight: 600;
  }

  .toolbar-right {
    display: flex;
    align-items: center;
    gap: 16px;
  }

  .time-display {
    font-family: monospace;
    font-size: 14px;
    color: var(--va-text-secondary);
  }
}

.video-grid {
  flex: 1;
  display: grid;
  gap: 4px;
  min-height: 0;
}

.video-cell {
  position: relative;
  background: #000;
  border-radius: 4px;
  overflow: hidden;
  cursor: pointer;
  border: 2px solid transparent;
  transition: border-color 0.2s;

  &.active {
    border-color: #333;
  }

  &.selected {
    border-color: var(--el-color-primary);
  }
}

.empty-cell {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100%;
  color: #666;
  gap: 8px;
  font-size: 14px;

  &:hover {
    color: #999;
    background: rgba(255, 255, 255, 0.05);
  }
}

.camera-radio-group {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.status-dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  margin-right: 4px;

  &.connected { background: #52c41a; }
  &.connecting { background: #faad14; }
  &.disconnected, &.error { background: #ff4d4f; }
}

.context-menu {
  position: fixed;
  z-index: 9999;
  background: var(--el-bg-color, #fff);
  border-radius: 4px;
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.15);
  padding: 4px 0;
  min-width: 120px;

  .context-item {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 8px 16px;
    font-size: 14px;
    cursor: pointer;

    &:hover {
      background: var(--el-fill-color-light, #f5f5f5);
    }
  }
}
</style>
