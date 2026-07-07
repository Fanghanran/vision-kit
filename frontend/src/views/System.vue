<template>
  <div class="system">
    <el-row :gutter="16">
      <!-- GPU 状态 -->
      <el-col :span="8">
        <el-card shadow="hover">
          <template #header>GPU 状态</template>
          <el-descriptions :column="1" border>
            <el-descriptions-item label="使用率">
              <el-progress :percentage="gpuPercent" :color="gpuColor" />
            </el-descriptions-item>
            <el-descriptions-item label="显存">
              {{ health?.gpu_memory_used_mb?.toFixed(0) || 0 }} / {{ health?.gpu_memory_total_mb?.toFixed(0) || 0 }} MB
            </el-descriptions-item>
          </el-descriptions>
        </el-card>
      </el-col>

      <!-- 运行状态 -->
      <el-col :span="8">
        <el-card shadow="hover">
          <template #header>运行状态</template>
          <el-descriptions :column="1" border>
            <el-descriptions-item label="运行时间">{{ formatUptime(health?.uptime_seconds) }}</el-descriptions-item>
            <el-descriptions-item label="在线摄像头">{{ health?.active_cameras || 0 }} / {{ health?.total_cameras || 0 }}</el-descriptions-item>
            <el-descriptions-item label="今日告警">{{ health?.today_alerts || 0 }}</el-descriptions-item>
            <el-descriptions-item label="队列积压">{{ health?.queue_depth || 0 }} 帧</el-descriptions-item>
          </el-descriptions>
        </el-card>
      </el-col>

      <!-- 推理性能 -->
      <el-col :span="8">
        <el-card shadow="hover">
          <template #header>推理性能</template>
          <el-descriptions :column="1" border>
            <el-descriptions-item label="延迟 P50">{{ health?.inference_latency_p50_ms?.toFixed(1) || '-' }} ms</el-descriptions-item>
            <el-descriptions-item label="延迟 P99">{{ health?.inference_latency_p99_ms?.toFixed(1) || '-' }} ms</el-descriptions-item>
            <el-descriptions-item label="LLM 成功率">{{ ((health?.llm_success_rate || 0) * 100).toFixed(0) }}%</el-descriptions-item>
          </el-descriptions>
        </el-card>
      </el-col>
    </el-row>

    <!-- 系统配置 -->
    <el-card shadow="hover" style="margin-top: 16px">
      <template #header>系统配置（脱敏）</template>
      <el-descriptions :column="2" border v-if="config">
        <el-descriptions-item label="模型">{{ config?.detector?.model_path || '-' }}</el-descriptions-item>
        <el-descriptions-item label="置信度">{{ config?.detector?.confidence || '-' }}</el-descriptions-item>
        <el-descriptions-item label="LLM">{{ config?.llm?.enabled ? config?.llm?.model : '禁用' }}</el-descriptions-item>
        <el-descriptions-item label="通知渠道">
          <el-tag v-if="config?.notification?.webhook?.enabled" size="small" style="margin-right: 4px">Webhook</el-tag>
          <el-tag v-if="config?.notification?.email?.enabled" size="small">Email</el-tag>
          <span v-if="!config?.notification?.webhook?.enabled && !config?.notification?.email?.enabled">无</span>
        </el-descriptions-item>
      </el-descriptions>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { useSystemStore } from '@/stores/system'

const systemStore = useSystemStore()
const health = computed(() => systemStore.health)
const config = computed(() => systemStore.config)

const gpuPercent = computed(() => Math.round((health.value?.gpu_utilization || 0) * 100))
const gpuColor = computed(() => gpuPercent.value > 80 ? '#F56C6C' : gpuPercent.value > 50 ? '#E6A23C' : '#67C23A')

onMounted(() => {
  systemStore.fetchHealth()
  systemStore.fetchConfig()
})

function formatUptime(seconds?: number) {
  if (!seconds) return '-'
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  return `${h}h ${m}m`
}
</script>
