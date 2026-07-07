<template>
  <header class="app-header">
    <div class="header-left">
      <el-icon :size="24"><VideoCamera /></el-icon>
      <h1 class="header-title">Vision Agent</h1>
      <el-tag
        :type="statusTagType"
        size="small"
        effect="dark"
        class="status-tag"
      >
        {{ health?.status || '未知' }}
      </el-tag>
      <el-tag v-if="health?.uptime_seconds" type="info" size="small" effect="plain">
        运行 {{ formatUptime(health.uptime_seconds) }}
      </el-tag>
    </div>
    <div class="header-right">
      <el-tooltip content="切换暗色模式">
        <el-button :icon="isDark ? Sunny : Moon" circle @click="$emit('toggle-dark')" />
      </el-tooltip>
    </div>
  </header>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { VideoCamera, Moon, Sunny } from '@element-plus/icons-vue'
import { useSystemStore } from '@/stores/system'

defineEmits<{ 'toggle-dark': [] }>()

const systemStore = useSystemStore()

const isDark = computed(() => document.documentElement.getAttribute('data-theme') === 'dark')
const health = computed(() => systemStore.health)

const statusTagType = computed(() => {
  const s = health.value?.status
  if (s === 'ok') return 'success'
  if (s === 'degraded') return 'warning'
  return 'danger'
})

const wsStatusText = computed(() => {
  return { connected: '已连接', disconnected: '未连接' }[wsStatus.value] || '未知'
})

function formatUptime(seconds: number) {
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  if (h > 24) return `${Math.floor(h / 24)}天${h % 24}时`
  return `${h}时${m}分`
}
</script>

<style lang="scss" scoped>
.app-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 56px;
  padding: 0 24px;
  background: var(--va-sidebar);
  color: #fff;
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.1);
  z-index: 100;
}

.header-left {
  display: flex;
  align-items: center;
  gap: 12px;
}

.header-title {
  font-size: 18px;
  font-weight: 600;
}

.header-right {
  display: flex;
  align-items: center;
  gap: 16px;
}

.ws-indicator {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: #ffffffa6;
}

.ws-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  &.connected { background: #67C23A; }
  &.disconnected { background: #F56C6C; }
}
</style>
