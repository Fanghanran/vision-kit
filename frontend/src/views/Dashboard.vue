<template>
  <div class="dashboard">
    <!-- 统计卡片（带数字滚动动画） -->
    <el-row :gutter="16" class="stat-row">
      <el-col :span="6" v-for="card in statCards" :key="card.label">
        <el-card shadow="hover" class="stat-card" @click="card.onClick?.()">
          <div class="stat-label">{{ card.label }}</div>
          <div class="stat-value" :style="{ color: card.color }">
            <span class="stat-number">{{ card.displayValue }}</span>
            <span v-if="card.trend" class="stat-trend" :class="card.trend > 0 ? 'up' : 'down'">
              {{ card.trend > 0 ? '↑' : '↓' }}{{ Math.abs(card.trend) }}%
            </span>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <el-row :gutter="16" style="margin-top: 16px">
      <!-- 左侧：告警趋势图 -->
      <el-col :span="14">
        <el-card shadow="hover">
          <template #header>
            <div class="card-header">
              <span>告警趋势（最近 7 天）</span>
              <el-radio-group v-model="trendPeriod" size="small" @change="fetchTrendData">
                <el-radio-button label="7d">7天</el-radio-button>
                <el-radio-button label="30d">30天</el-radio-button>
              </el-radio-group>
            </div>
          </template>
          <div ref="trendChartRef" style="height: 300px"></div>
        </el-card>
      </el-col>

      <!-- 右侧：实时告警流 -->
      <el-col :span="10">
        <el-card shadow="hover">
          <template #header>
            <div class="card-header">
              <span>实时告警</span>
              <el-badge :value="realtimeAlerts.length" :max="99" class="alert-badge" />
            </div>
          </template>
          <div class="alert-stream">
            <div
              v-for="alert in realtimeAlerts"
              :key="alert.alert_id"
              class="alert-item"
              :class="{ 'alert-new': isNewAlert(alert) }"
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

    <el-row :gutter="16" style="margin-top: 16px">
      <!-- 告警类型分布饼图 -->
      <el-col :span="10">
        <el-card shadow="hover">
          <template #header>告警类型分布（今日）</template>
          <div ref="pieChartRef" style="height: 280px"></div>
        </el-card>
      </el-col>

      <!-- 摄像头告警排行 -->
      <el-col :span="14">
        <el-card shadow="hover">
          <template #header>摄像头告警排行</template>
          <div ref="barChartRef" style="height: 280px"></div>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, watch, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import { useSystemStore } from '@/stores/system'
import { useAlertsStore } from '@/stores/alerts'
import { useCamerasStore } from '@/stores/cameras'
import * as echarts from 'echarts'

const router = useRouter()
const systemStore = useSystemStore()
const alertsStore = useAlertsStore()
const camerasStore = useCamerasStore()

// 图表引用
const trendChartRef = ref<HTMLElement>()
const pieChartRef = ref<HTMLElement>()
const barChartRef = ref<HTMLElement>()
let trendChart: echarts.ECharts | null = null
let pieChart: echarts.ECharts | null = null
let barChart: echarts.ECharts | null = null

const trendPeriod = ref('7d')
const realtimeAlerts = computed(() => alertsStore.realtimeAlerts)
const newAlertIds = ref(new Set<string>())

// 新告警自动加入闪烁集合，3 秒后移除
watch(realtimeAlerts, (newVal, oldVal) => {
  const added = newVal.filter((a: any) => !oldVal?.some((o: any) => o.alert_id === a.alert_id))
  for (const alert of added) {
    newAlertIds.value.add(alert.alert_id)
    setTimeout(() => newAlertIds.value.delete(alert.alert_id), 3000)
  }
})

// 统计卡片
const statCards = computed(() => {
  const h = systemStore.health
  return [
    {
      label: '今日告警',
      value: h?.today_alerts ?? 0,
      displayValue: String(h?.today_alerts ?? '-'),
      color: 'var(--va-danger)',
      onClick: () => router.push('/alerts'),
    },
    {
      label: '在线摄像头',
      value: h?.active_cameras ?? 0,
      displayValue: `${h?.active_cameras ?? 0}/${h?.total_cameras ?? 0}`,
      color: 'var(--va-success)',
      onClick: () => router.push('/cameras'),
    },
    {
      label: 'GPU 使用率',
      value: Math.round((h?.gpu_utilization ?? 0) * 100),
      displayValue: `${Math.round((h?.gpu_utilization ?? 0) * 100)}%`,
      color: (h?.gpu_utilization ?? 0) > 0.8 ? 'var(--va-danger)' : 'var(--va-primary)',
    },
    {
      label: '推理延迟 P50',
      value: h?.inference_latency_p50_ms ?? 0,
      displayValue: `${h?.inference_latency_p50_ms?.toFixed(0) ?? '-'}ms`,
      color: (h?.inference_latency_p50_ms ?? 0) > 50 ? 'var(--va-warning)' : 'var(--va-success)',
    },
  ]
})

// 定时刷新
let refreshTimer: ReturnType<typeof setInterval>
onMounted(async () => {
  await Promise.all([
    systemStore.fetchHealth(),
    systemStore.fetchStats('today'),
    systemStore.fetchStats('7d'),
    camerasStore.fetchCameras(),
  ])
  await nextTick()
  initCharts()
  refreshTimer = setInterval(() => {
    systemStore.fetchHealth()
    systemStore.fetchStats('today')
  }, 10000)
})

onUnmounted(() => {
  clearInterval(refreshTimer)
  clearTimeout(newAlertTimer)
  window.removeEventListener('resize', handleResize)
  trendChart?.dispose()
  pieChart?.dispose()
  barChart?.dispose()
})

function handleResize() {
  trendChart?.resize()
  pieChart?.resize()
  barChart?.resize()
}
window.addEventListener('resize', handleResize)

function initCharts() {
  initTrendChart()
  initPieChart()
  initBarChart()
}

function initTrendChart() {
  if (!trendChartRef.value) return
  trendChart = echarts.init(trendChartRef.value)
  updateTrendChart()
}

function updateTrendChart() {
  if (!trendChart) return
  const stats = systemStore.getStats(trendPeriod.value)
  // 从 stats 构造趋势数据（后端返回的 groups 按天聚合）
  const groups = stats?.groups || []
  const dates = groups.map((g: any) => g.group_key)
  const values = groups.map((g: any) => g.count)

  // 如果没有真实数据，用占位
  if (dates.length === 0) {
    const today = new Date()
    for (let i = 6; i >= 0; i--) {
      const d = new Date(today)
      d.setDate(d.getDate() - i)
      dates.push(`${d.getMonth() + 1}/${d.getDate()}`)
      values.push(0)
    }
  }

  trendChart.setOption({
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    xAxis: { type: 'category', data: dates, axisLabel: { color: '#8c8c8c' } },
    yAxis: { type: 'value', name: '告警数', axisLabel: { color: '#8c8c8c' } },
    series: [{
      type: 'line',
      data: values,
      smooth: true,
      areaStyle: { color: 'rgba(24, 144, 255, 0.15)' },
      lineStyle: { color: '#1890FF', width: 2 },
      itemStyle: { color: '#1890FF' },
    }],
    grid: { left: 50, right: 20, top: 30, bottom: 30 },
  })
}

function initPieChart() {
  if (!pieChartRef.value) return
  pieChart = echarts.init(pieChartRef.value)

  const stats = systemStore.getStats('today')
  const byType = stats?.alerts_by_type || {}
  const data = Object.entries(byType).map(([name, value]) => ({
    name: eventTypeLabel(name),
    value: value as number,
  }))

  // 如果没有数据，显示空状态
  if (data.length === 0) {
    data.push({ name: '暂无数据', value: 0 })
  }

  pieChart.setOption({
    tooltip: { trigger: 'item', formatter: '{b}: {c} ({d}%)' },
    legend: { bottom: 0, textStyle: { color: '#8c8c8c' } },
    color: ['#D32029', '#D89614', '#1890FF', '#49AA19', '#8C8C8C'],
    series: [{
      type: 'pie',
      radius: ['40%', '65%'],
      center: ['50%', '45%'],
      data,
      label: { show: true, formatter: '{b}\n{d}%', fontSize: 12 },
      emphasis: { itemStyle: { shadowBlur: 10, shadowOffsetX: 0, shadowColor: 'rgba(0,0,0,0.3)' } },
    }],
  })
}

function initBarChart() {
  if (!barChartRef.value) return
  barChart = echarts.init(barChartRef.value)

  const stats = systemStore.getStats('today')
  const byCamera = stats?.alerts_by_camera || {}
  const sorted = Object.entries(byCamera).sort((a, b) => (b[1] as number) - (a[1] as number))
  const cameras = sorted.map(([id]) => id)
  const values = sorted.map(([, v]) => v as number)

  if (cameras.length === 0) {
    cameras.push('暂无数据')
    values.push(0)
  }

  barChart.setOption({
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    xAxis: { type: 'value', name: '告警数' },
    yAxis: { type: 'category', data: cameras.reverse(), axisLabel: { color: '#8c8c8c' } },
    series: [{
      type: 'bar',
      data: values.reverse(),
      itemStyle: {
        color: new echarts.graphic.LinearGradient(0, 0, 1, 0, [
          { offset: 0, color: '#1890FF' },
          { offset: 1, color: '#40A9FF' },
        ]),
        borderRadius: [0, 4, 4, 0],
      },
      barWidth: 20,
    }],
    grid: { left: 80, right: 30, top: 10, bottom: 20 },
  })
}

async function fetchTrendData() {
  await systemStore.fetchStats(trendPeriod.value)
  updateTrendChart()
}

function isNewAlert(alert: any) {
  return newAlertIds.value.has(alert.alert_id)
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
  cursor: pointer;
  transition: transform 0.2s;
  &:hover { transform: translateY(-2px); }
  .stat-label { font-size: 14px; color: var(--va-text-secondary); margin-bottom: 8px; }
  .stat-value {
    font-size: 32px;
    font-weight: 700;
    display: flex;
    align-items: baseline;
    justify-content: center;
    gap: 8px;
  }
  .stat-trend {
    font-size: 14px;
    font-weight: 500;
    &.up { color: var(--va-danger); }
    &.down { color: var(--va-success); }
  }
}

.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.alert-stream {
  max-height: 300px;
  overflow-y: auto;
}

.alert-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px;
  margin-bottom: 4px;
  border-radius: 4px;
  cursor: pointer;
  transition: background 0.2s;
  &:hover { background: rgba(24, 144, 255, 0.05); }
}

.alert-new {
  animation: alert-flash 1s ease-in-out 3;
}

@keyframes alert-flash {
  0%, 100% { background: rgba(24, 144, 255, 0.15); }
  50% { background: transparent; }
}

.alert-type { font-weight: 500; min-width: 50px; }
.alert-camera { color: var(--va-text-secondary); flex: 1; text-align: right; }
.alert-time { color: var(--va-text-secondary); font-size: 12px; }
</style>
