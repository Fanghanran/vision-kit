<template>
  <div class="dashboard">
    <!-- 统计卡片 -->
    <el-row :gutter="16" class="stat-row">
      <el-col :span="6" v-for="card in statCards" :key="card.label">
        <el-card shadow="hover" class="stat-card">
          <div class="stat-label">{{ card.label }}</div>
          <div class="stat-value" :style="{ color: card.color }">{{ card.value }}</div>
        </el-card>
      </el-col>
    </el-row>

    <el-row :gutter="16" style="margin-top: 16px">
      <!-- 告警趋势图 -->
      <el-col :span="14">
        <el-card shadow="hover">
          <template #header>告警趋势（最近 7 天）</template>
          <div ref="trendChartRef" style="height: 300px"></div>
        </el-card>
      </el-col>

      <!-- 实时告警流 -->
      <el-col :span="10">
        <el-card shadow="hover">
          <template #header>实时告警</template>
          <div class="alert-stream">
            <div
              v-for="alert in realtimeAlerts"
              :key="alert.alert_id"
              class="alert-item"
              @click="router.push(`/alerts/${alert.alert_id}`)"
            >
              <el-tag :type="severityTagType(alert.severity)" size="small" effect="dark">
                {{ severityLabel(alert.severity) }}
              </el-tag>
              <span class="alert-type">{{ eventTypeLabel(alert.event_type) }}</span>
              <span class="alert-camera">{{ alert.camera_name }}</span>
              <span class="alert-time">{{ formatTime(alert.created_at) }}</span>
            </div>
            <el-empty v-if="!realtimeAlerts.length" description="暂无告警" :image-size="60" />
          </div>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { useSystemStore } from '@/stores/system'
import { useAlertsStore } from '@/stores/alerts'
import { useCamerasStore } from '@/stores/cameras'
import * as echarts from 'echarts'

const router = useRouter()
const systemStore = useSystemStore()
const alertsStore = useAlertsStore()
const camerasStore = useCamerasStore()

const trendChartRef = ref<HTMLElement>()
let trendChart: echarts.ECharts | null = null

const realtimeAlerts = computed(() => alertsStore.realtimeAlerts)

const statCards = computed(() => [
  { label: '今日告警', value: systemStore.health?.today_alerts ?? '-', color: 'var(--va-danger)' },
  { label: '在线摄像头', value: `${systemStore.health?.active_cameras ?? 0}/${systemStore.health?.total_cameras ?? 0}`, color: 'var(--va-success)' },
  { label: 'GPU 使用率', value: `${Math.round((systemStore.health?.gpu_utilization ?? 0) * 100)}%`, color: 'var(--va-primary)' },
  { label: '推理延迟 P50', value: `${systemStore.health?.inference_latency_p50_ms?.toFixed(0) ?? '-'}ms`, color: 'var(--va-warning)' },
])

// 定时刷新
let refreshTimer: ReturnType<typeof setInterval>
onMounted(async () => {
  await Promise.all([
    systemStore.fetchHealth(),
    systemStore.fetchStats('7d'),
    camerasStore.fetchCameras(),
  ])
  initTrendChart()
  refreshTimer = setInterval(() => systemStore.fetchHealth(), 10000)
})

onUnmounted(() => {
  clearInterval(refreshTimer)
  trendChart?.dispose()
})

function initTrendChart() {
  if (!trendChartRef.value) return
  trendChart = echarts.init(trendChartRef.value)
  // 使用模拟数据（后端 stats API 返回的数据）
  trendChart.setOption({
    tooltip: { trigger: 'axis' },
    xAxis: { type: 'category', data: ['周一', '周二', '周三', '周四', '周五', '周六', '周日'] },
    yAxis: { type: 'value', name: '告警数' },
    series: [{ type: 'line', data: [5, 8, 3, 12, 7, 2, 6], smooth: true, areaStyle: { opacity: 0.1 } }],
    grid: { left: 50, right: 20, top: 20, bottom: 30 },
  })
}

function severityTagType(s: string) {
  return { critical: 'danger', warning: 'warning', info: 'info' }[s] || 'info'
}
function severityLabel(s: string) {
  return { critical: '紧急', warning: '警告', info: '信息' }[s] || s
}
function eventTypeLabel(t: string) {
  const map: Record<string, string> = {
    intrusion: '闯入', absence: '离岗', crowd: '聚集',
    abandoned_object: '遗留', counting: '统计',
  }
  return map[t] || t
}
function formatTime(ts: number) {
  if (!ts) return ''
  return new Date(ts * 1000).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
}
</script>

<style lang="scss" scoped>
.stat-card {
  text-align: center;
  .stat-label { font-size: 14px; color: var(--va-text-secondary); margin-bottom: 8px; }
  .stat-value { font-size: 32px; font-weight: 700; }
}

.alert-stream {
  max-height: 300px;
  overflow-y: auto;
}

.alert-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 0;
  border-bottom: 1px solid var(--va-border);
  cursor: pointer;
  &:hover { background: rgba(24, 144, 255, 0.05); }
}

.alert-type { font-weight: 500; }
.alert-camera { color: var(--va-text-secondary); flex: 1; text-align: right; }
.alert-time { color: var(--va-text-secondary); font-size: 12px; }
</style>
