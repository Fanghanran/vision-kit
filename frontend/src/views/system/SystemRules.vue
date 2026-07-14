<template>
  <ModuleStatusCard
    title="规则引擎"
    status="ok"
    :metrics="rulesMetrics"
  >
    <template #header-right>
      <el-button size="small" :icon="Refresh" circle @click="fetchStatus" />
    </template>

    <ControlSwitch
      label="规则热重载"
      description="规则 YAML 变更后自动加载，无需重启"
      :model-value="controls['rules.hot_reload'] ?? true"
      api-key="rules.hot_reload"
      @update:model-value="val => controls['rules.hot_reload'] = val"
    />
    <ControlSwitch
      label="摄像头热重载"
      description="摄像头配置变更后自动重载"
      :model-value="controls['cameras.hot_reload'] ?? true"
      api-key="cameras.hot_reload"
      @update:model-value="val => controls['cameras.hot_reload'] = val"
    />

    <el-descriptions :column="2" border style="margin-top: 16px" size="small">
      <el-descriptions-item label="默认滑动窗口">{{ config?.rules?.default_window_size || 5 }} 帧</el-descriptions-item>
      <el-descriptions-item label="默认冷却时间">{{ config?.rules?.default_cooldown || 300 }}s</el-descriptions-item>
      <el-descriptions-item label="规则目录">{{ config?.rules?.rules_dir || 'configs/rules' }}</el-descriptions-item>
    </el-descriptions>
  </ModuleStatusCard>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted } from 'vue'
import { Refresh } from '@element-plus/icons-vue'
import ModuleStatusCard from '@/components/system/ModuleStatusCard.vue'
import ControlSwitch from '@/components/system/ControlSwitch.vue'
import client from '@/api/client'
import { useSystemStore } from '@/stores/system'

const systemStore = useSystemStore()
const config = computed(() => systemStore.config)
const controls = reactive<Record<string, boolean>>({})
const status = ref<any>(null)

const rulesMetrics = computed(() => [
  { label: '已加载规则', value: status.value?.total_rules ?? '-', color: 'var(--va-primary)' },
  { label: '启用中', value: status.value?.enabled_rules ?? '-', color: 'var(--va-success)' },
  { label: '已停用', value: status.value?.disabled_rules ?? '-', color: 'var(--va-text-secondary)' },
  { label: '今日触发', value: status.value?.today_triggers ?? '-', color: 'var(--va-warning)' },
])

async function fetchStatus() {
  try {
    const { data } = await client.get('/api/system/controls')
    Object.entries(data.controls || {}).forEach(([key, info]: [string, any]) => {
      if (key.startsWith('rules.') || key.startsWith('cameras.')) controls[key] = info.value
    })
    // 尝试获取规则状态
    try {
      const { data: rules } = await client.get('/api/rules')
      if (Array.isArray(rules)) {
        status.value = {
          total_rules: rules.length,
          enabled_rules: rules.filter((r: any) => r.enabled !== false).length,
          disabled_rules: rules.filter((r: any) => r.enabled === false).length,
        }
      }
    } catch { /* ignore */ }
  } catch { /* ignore */ }
}

onMounted(fetchStatus)
</script>
