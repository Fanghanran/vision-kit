<template>
  <div class="system">
    <el-row :gutter="16">
      <!-- GPU 状态 -->
      <el-col :span="8">
        <el-card shadow="hover">
          <template #header>
            <div class="card-header">
              <span>GPU 状态</span>
              <el-tag :type="gpuPercent > 80 ? 'danger' : gpuPercent > 50 ? 'warning' : 'success'" size="small">
                {{ gpuPercent > 80 ? '高负载' : gpuPercent > 50 ? '中等' : '正常' }}
              </el-tag>
            </div>
          </template>
          <div class="gpu-gauge">
            <el-progress
              type="dashboard"
              :percentage="gpuPercent"
              :color="gpuColor"
              :width="140"
              :stroke-width="12"
            >
              <template #default>
                <div class="gauge-text">
                  <span class="gauge-value">{{ gpuPercent }}%</span>
                  <span class="gauge-label">GPU 使用率</span>
                </div>
              </template>
            </el-progress>
          </div>
          <el-descriptions :column="1" border style="margin-top: 16px">
            <el-descriptions-item label="显存使用">
              {{ health?.gpu_memory_used_mb?.toFixed(0) || 0 }} / {{ health?.gpu_memory_total_mb?.toFixed(0) || 0 }} MB
              <el-progress
                :percentage="memPercent"
                :stroke-width="6"
                :show-text="false"
                style="margin-top: 4px"
              />
            </el-descriptions-item>
          </el-descriptions>
        </el-card>
      </el-col>

      <!-- 运行状态 -->
      <el-col :span="8">
        <el-card shadow="hover">
          <template #header>
            <div class="card-header">
              <span>运行状态</span>
              <el-tag :type="health?.status === 'ok' ? 'success' : health?.status === 'degraded' ? 'warning' : 'danger'" size="small">
                {{ health?.status || '未知' }}
              </el-tag>
            </div>
          </template>
          <el-descriptions :column="1" border>
            <el-descriptions-item label="运行时间">
              <el-icon><Clock /></el-icon> {{ formatUptime(health?.uptime_seconds) }}
            </el-descriptions-item>
            <el-descriptions-item label="在线摄像头">
              <el-icon><VideoCamera /></el-icon> {{ health?.active_cameras || 0 }} / {{ health?.total_cameras || 0 }}
            </el-descriptions-item>
            <el-descriptions-item label="今日告警">
              <el-icon><Bell /></el-icon> {{ health?.today_alerts || 0 }}
            </el-descriptions-item>
            <el-descriptions-item label="帧队列积压">
              <el-icon><DataBoard /></el-icon> {{ health?.queue_depth || 0 }} 帧
            </el-descriptions-item>
          </el-descriptions>
        </el-card>
      </el-col>

      <!-- 推理性能 -->
      <el-col :span="8">
        <el-card shadow="hover">
          <template #header>推理性能</template>
          <el-descriptions :column="1" border>
            <el-descriptions-item label="延迟 P50">
              <el-tag :type="(health?.inference_latency_p50_ms ?? 0) > 50 ? 'warning' : 'success'" effect="plain">
                {{ health?.inference_latency_p50_ms?.toFixed(1) || '-' }} ms
              </el-tag>
            </el-descriptions-item>
            <el-descriptions-item label="延迟 P99">
              <el-tag :type="(health?.inference_latency_p99_ms ?? 0) > 100 ? 'danger' : 'success'" effect="plain">
                {{ health?.inference_latency_p99_ms?.toFixed(1) || '-' }} ms
              </el-tag>
            </el-descriptions-item>
            <el-descriptions-item label="LLM 成功率">
              <el-progress
                :percentage="Math.round((health?.llm_success_rate || 0) * 100)"
                :color="(health?.llm_success_rate || 0) > 0.9 ? '#67C23A' : '#E6A23C'"
                :stroke-width="8"
              />
            </el-descriptions-item>
          </el-descriptions>

          <!-- 延迟趋势图 -->
          <div ref="latencyChartRef" style="height: 150px; margin-top: 16px"></div>
        </el-card>
      </el-col>
    </el-row>

    <!-- 系统配置 -->
    <el-card shadow="hover" style="margin-top: 16px">
      <template #header>
        <div class="card-header">
          <span>系统配置（脱敏）</span>
          <el-button :icon="Refresh" circle size="small" @click="systemStore.fetchConfig()" />
        </div>
      </template>
      <el-row :gutter="16">
        <el-col :span="12">
          <el-descriptions :column="1" border>
            <el-descriptions-item label="检测模型">{{ config?.detector?.model_path || config?.detector?.model || '-' }}</el-descriptions-item>
            <el-descriptions-item label="置信度阈值">{{ config?.detector?.confidence || '-' }}</el-descriptions-item>
            <el-descriptions-item label="IoU 阈值">{{ config?.detector?.iou || config?.detector?.iou_threshold || '-' }}</el-descriptions-item>
            <el-descriptions-item label="追踪器">{{ config?.tracker?.type || 'botsort' }}</el-descriptions-item>
          </el-descriptions>
        </el-col>
        <el-col :span="12">
          <el-descriptions :column="1" border>
            <el-descriptions-item label="LLM 模型">
              <el-tag v-if="config?.llm?.enabled" type="success" size="small">{{ config?.llm?.model || '-' }}</el-tag>
              <el-tag v-else type="info" size="small">禁用</el-tag>
            </el-descriptions-item>
            <el-descriptions-item label="通知渠道">
              <el-tag v-if="config?.notification?.webhook?.enabled" size="small" style="margin-right: 4px">Webhook</el-tag>
              <el-tag v-if="config?.notification?.email?.enabled" size="small" style="margin-right: 4px">Email</el-tag>
              <span v-if="!config?.notification?.webhook?.enabled && !config?.notification?.email?.enabled" class="text-muted">无</span>
            </el-descriptions-item>
            <el-descriptions-item label="存储类型">{{ config?.storage?.type || 'sqlite' }}</el-descriptions-item>
            <el-descriptions-item label="数据目录">{{ config?.system?.data_dir || 'data' }}</el-descriptions-item>
          </el-descriptions>
        </el-col>
      </el-row>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useSystemStore } from '@/stores/system'
import { Refresh, Clock, VideoCamera, Bell, DataBoard } from '@element-plus/icons-vue'
import * as echarts from 'echarts'

const systemStore = useSystemStore()
const health = computed(() => systemStore.health)
const config = computed(() => systemStore.config)

const latencyChartRef = ref<HTMLElement>()
let latencyChart: echarts.ECharts | null = null
const latencyHistory = ref<Array<{ time: string; p50: number; p99: number }>>([])

const gpuPercent = computed(() => Math.round((health.value?.gpu_utilization || 0) * 100))
const gpuColor = computed(() => gpuPercent.value > 80 ? '#F56C6C' : gpuPercent.value > 50 ? '#E6A23C' : '#67C23A')
const memPercent = computed(() => {
  const used = health.value?.gpu_memory_used_mb || 0
  const total = health.value?.gpu_memory_total_mb || 1
  return Math.round((used / total) * 100)
})

let refreshTimer: ReturnType<typeof setInterval>

onMounted(async () => {
  await Promise.all([
    systemStore.fetchHealth(),
    systemStore.fetchConfig(),
  ])
  initLatencyChart()
  refreshTimer = setInterval(() => {
    systemStore.fetchHealth()
    updateLatencyChart()
  }, 5000)
})

onUnmounted(() => {
  clearInterval(refreshTimer)
  latencyChart?.dispose()
})

function initLatencyChart() {
  if (!latencyChartRef.value) return
  latencyChart = echarts.init(latencyChartRef.value)
  updateLatencyChart()
}

function updateLatencyChart() {
  if (!latencyChart || !health.value) return

  const now = new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
  latencyHistory.value.push({
    time: now,
    p50: health.value.inference_latency_p50_ms || 0,
    p99: health.value.inference_latency_p99_ms || 0,
  })
  // 保留最近 20 个数据点
  if (latencyHistory.value.length > 20) latencyHistory.value.shift()

  latencyChart.setOption({
    tooltip: { trigger: 'axis' },
    legend: { data: ['P50', 'P99'], bottom: 0, textStyle: { fontSize: 10 } },
    xAxis: { type: 'category', data: latencyHistory.value.map(d => d.time), show: false },
    yAxis: { type: 'value', name: 'ms', axisLabel: { fontSize: 10 } },
    series: [
      { name: 'P50', type: 'line', data: latencyHistory.value.map(d => d.p50), smooth: true, lineStyle: { width: 2 }, itemStyle: { color: '#67C23A' } },
      { name: 'P99', type: 'line', data: latencyHistory.value.map(d => d.p99), smooth: true, lineStyle: { width: 2 }, itemStyle: { color: '#E6A23C' } },
    ],
    grid: { left: 40, right: 10, top: 10, bottom: 30 },
  })
}

function formatUptime(seconds?: number) {
  if (!seconds) return '-'
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  const d = Math.floor(h / 24)
  if (d > 0) return `${d}天${h % 24}时${m}分`
  return `${h}时${m}分`
}
</script>

<style lang="scss" scoped>
.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.gpu-gauge {
  display: flex;
  justify-content: center;
}

.gauge-text {
  display: flex;
  flex-direction: column;
  align-items: center;
  .gauge-value { font-size: 24px; font-weight: 700; }
  .gauge-label { font-size: 12px; color: var(--va-text-secondary); }
}

.text-muted { color: var(--va-text-secondary); }
</style>
