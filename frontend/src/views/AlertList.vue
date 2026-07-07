<template>
  <div class="alert-list">
    <el-card shadow="hover">
      <template #header>
        <div class="card-header">
          <span>告警列表</span>
          <el-button :icon="Refresh" circle @click="loadData" />
        </div>
      </template>

      <!-- 筛选栏 -->
      <el-form :inline="true" class="filter-form">
        <el-form-item label="状态">
          <el-select v-model="filters.status" clearable placeholder="全部" @change="loadData">
            <el-option label="待处理" value="pending" />
            <el-option label="已确认" value="acknowledged" />
            <el-option label="误报" value="rejected" />
            <el-option label="已解决" value="resolved" />
          </el-select>
        </el-form-item>
        <el-form-item label="级别">
          <el-select v-model="filters.severity" clearable placeholder="全部" @change="loadData">
            <el-option label="紧急" value="critical" />
            <el-option label="警告" value="warning" />
            <el-option label="信息" value="info" />
          </el-select>
        </el-form-item>
        <el-form-item label="摄像头">
          <el-select v-model="filters.camera_id" clearable placeholder="全部" @change="loadData">
            <el-option v-for="cam in cameras" :key="cam.camera_id" :label="cam.camera_id" :value="cam.camera_id" />
          </el-select>
        </el-form-item>
      </el-form>

      <!-- 告警表格 -->
      <el-table :data="alerts" v-loading="loading" stripe @row-click="goDetail" style="cursor: pointer" :row-class-name="rowClassName">
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="statusTagType(row.status)" size="small">
              {{ statusLabel(row.status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="event_type" label="类型" width="100">
          <template #default="{ row }">{{ eventTypeLabel(row.event_type) }}</template>
        </el-table-column>
        <el-table-column prop="camera_name" label="摄像头" width="120" />
        <el-table-column prop="risk_level" label="风险" width="80" />
        <el-table-column label="级别" width="80">
          <template #default="{ row }">
            <el-tag :type="severityTagType(row.severity)" size="small" effect="dark">
              {{ severityLabel(row.severity) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="时间" width="160">
          <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="100">
          <template #default="{ row }">
            <el-button size="small" type="primary" link @click.stop="goDetail(row)">详情</el-button>
          </template>
        </el-table-column>
      </el-table>

      <!-- 分页 -->
      <el-pagination
        v-model:current-page="page"
        :page-size="pageSize"
        :total="total"
        layout="prev, pager, next, total"
        @current-change="loadData"
        style="margin-top: 16px; justify-content: flex-end"
      />
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { Refresh } from '@element-plus/icons-vue'
import { useAlertsStore } from '@/stores/alerts'
import { useCamerasStore } from '@/stores/cameras'

const router = useRouter()
const alertsStore = useAlertsStore()
const camerasStore = useCamerasStore()

const alerts = ref<any[]>([])
const total = ref(0)
const loading = ref(false)
const page = ref(1)
const pageSize = 20
const filters = reactive({ status: '', severity: '', camera_id: '' })
const cameras = computed(() => camerasStore.cameras)

onMounted(() => {
  loadData()
  camerasStore.fetchCameras()
})

async function loadData() {
  loading.value = true
  try {
    await alertsStore.fetchAlerts(filters, page.value, pageSize)
    alerts.value = alertsStore.alerts
    total.value = alertsStore.total
  } finally {
    loading.value = false
  }
}

function goDetail(row: any) {
  router.push(`/alerts/${row.alert_id}`)
}

function rowClassName({ row }: { row: any }) {
  // 新告警（最近 30 秒内）添加闪烁动画
  if (row.created_at && Date.now() / 1000 - row.created_at < 30) {
    return 'alert-new-row'
  }
  return ''
}

function statusTagType(s: string) {
  return { pending: 'warning', acknowledged: '', rejected: 'info', resolved: 'success' }[s] || 'info'
}
function statusLabel(s: string) {
  return { pending: '待处理', acknowledged: '已确认', rejected: '误报', resolved: '已解决' }[s] || s
}
function severityTagType(s: string) {
  return { critical: 'danger', warning: 'warning', info: 'info' }[s] || 'info'
}
function severityLabel(s: string) {
  return { critical: '紧急', warning: '警告', info: '信息' }[s] || s
}
function eventTypeLabel(t: string) {
  const map: Record<string, string> = { intrusion: '闯入', absence: '离岗', crowd: '聚集', abandoned_object: '遗留', counting: '统计' }
  return map[t] || t
}
function formatTime(ts: number) {
  if (!ts) return ''
  return new Date(ts * 1000).toLocaleString('zh-CN')
}
</script>

<style lang="scss" scoped>
.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.filter-form {
  margin-bottom: 16px;
}

:deep(.alert-new-row) {
  animation: alert-flash 1s ease-in-out 3;
}

@keyframes alert-flash {
  0%, 100% { background: rgba(24, 144, 255, 0.12) !important; }
  50% { background: transparent !important; }
}
</style>
