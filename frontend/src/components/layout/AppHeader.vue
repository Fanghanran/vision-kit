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
    </div>
    <div class="header-right">
      <el-tooltip content="切换暗色模式">
        <el-button :icon="isDark ? Sunny : Moon" circle @click="$emit('toggle-dark')" />
      </el-tooltip>
      <el-tag :type="authStore.isAuthenticated ? 'success' : 'danger'" size="small">
        {{ authStore.isAuthenticated ? '已认证' : '未认证' }}
      </el-tag>
      <el-button v-if="authStore.isAuthenticated" size="small" @click="handleLogout">
        退出
      </el-button>
    </div>
  </header>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { VideoCamera, Moon, Sunny } from '@element-plus/icons-vue'
import { useAuthStore } from '@/stores/auth'
import { useSystemStore } from '@/stores/system'

defineEmits<{ 'toggle-dark': [] }>()

const authStore = useAuthStore()
const systemStore = useSystemStore()

const isDark = computed(() => document.documentElement.getAttribute('data-theme') === 'dark')
const health = computed(() => systemStore.health)

const statusTagType = computed(() => {
  const s = health.value?.status
  if (s === 'ok') return 'success'
  if (s === 'degraded') return 'warning'
  return 'danger'
})

function handleLogout() {
  authStore.clearToken()
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

.status-tag {
  margin-left: 8px;
}

.header-right {
  display: flex;
  align-items: center;
  gap: 12px;
}
</style>
