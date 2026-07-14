<template>
  <el-card shadow="hover" class="module-card">
    <template #header>
      <div class="card-header">
        <div class="header-left">
          <span class="module-title">{{ title }}</span>
          <el-tag v-if="status" :type="statusTagType" size="small" effect="dark">
            {{ statusLabel }}
          </el-tag>
        </div>
        <slot name="header-right" />
      </div>
    </template>

    <!-- 状态指标 -->
    <el-row :gutter="16" class="metrics-row">
      <el-col v-for="m in metrics" :key="m.label" :span="6">
        <div class="metric-item">
          <div class="metric-value" :style="{ color: m.color }">
            {{ m.value }}
          </div>
          <div class="metric-label">{{ m.label }}</div>
        </div>
      </el-col>
    </el-row>

    <!-- 控制项 -->
    <div class="controls-section">
      <slot />
    </div>

    <!-- 配置详情 -->
    <slot name="config" />
  </el-card>
</template>

<script setup lang="ts">
import { computed } from 'vue'

interface Metric {
  label: string
  value: string | number
  color?: string
}

const props = defineProps<{
  title: string
  status?: 'ok' | 'warning' | 'error' | string
  metrics?: Metric[]
}>()

const statusTagType = computed(() => {
  if (props.status === 'ok') return 'success'
  if (props.status === 'warning') return 'warning'
  if (props.status === 'error') return 'danger'
  return 'info'
})

const statusLabel = computed(() => {
  if (props.status === 'ok') return '正常'
  if (props.status === 'warning') return '警告'
  if (props.status === 'error') return '异常'
  return props.status || ''
})
</script>

<style lang="scss" scoped>
.module-card {
  margin-bottom: 16px;
}

.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.header-left {
  display: flex;
  align-items: center;
  gap: 12px;
}

.module-title {
  font-size: 16px;
  font-weight: 600;
}

.metrics-row {
  margin-bottom: 16px;
}

.metric-item {
  text-align: center;
  padding: 8px 0;
}

.metric-value {
  font-size: 24px;
  font-weight: 700;
  line-height: 1.2;
}

.metric-label {
  font-size: 12px;
  color: var(--va-text-secondary);
  margin-top: 4px;
}

.controls-section {
  border-top: 1px solid var(--va-border, #eee);
  padding-top: 8px;
}
</style>
