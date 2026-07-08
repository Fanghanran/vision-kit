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
    <div class="header-spacer"></div>
    <div class="header-right">
      <el-tooltip content="切换暗色模式">
        <el-button :icon="isDark ? Sunny : Moon" circle @click="$emit('toggle-dark')" />
      </el-tooltip>
      <el-dropdown v-if="authStore.isLoggedIn" trigger="click" class="user-menu">
        <span class="user-dropdown">
          <el-avatar :size="32" :style="{ background: (authStore.user as any)?.avatar_bg || '#1890ff' }">
            {{ ((authStore.user as any)?.username || '?')[0].toUpperCase() }}
          </el-avatar>
          <span class="user-name">{{ authStore.user?.username }}</span>
          <el-icon><ArrowDown /></el-icon>
        </span>
        <template #dropdown>
          <el-dropdown-menu>
            <el-dropdown-item @click="$router.push('/profile')">
              <el-icon><Setting /></el-icon> 个人设置
            </el-dropdown-item>
            <el-dropdown-item divided @click="authStore.logout()">
              <el-icon><SwitchButton /></el-icon> 退出登录
            </el-dropdown-item>
          </el-dropdown-menu>
        </template>
      </el-dropdown>
    </div>
  </header>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { VideoCamera, Moon, Sunny, Setting, ArrowDown, SwitchButton } from '@element-plus/icons-vue'
import { useSystemStore } from '@/stores/system'
import { useAuthStore } from '@/stores/auth'

defineEmits<{ 'toggle-dark': [] }>()

const systemStore = useSystemStore()
const authStore = useAuthStore()

const isDark = computed(() => document.documentElement.getAttribute('data-theme') === 'dark')
const health = computed(() => systemStore.health)

const statusTagType = computed(() => {
  const s = health.value?.status
  if (s === 'ok') return 'success'
  if (s === 'degraded') return 'warning'
  return 'danger'
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

.header-spacer {
  flex: 1;
}

.header-right {
  display: flex;
  align-items: center;
  gap: 16px;
}

.user-menu {
  .user-dropdown {
    display: flex;
    align-items: center;
    gap: 8px;
    cursor: pointer;
    color: #fff;
    .user-name { font-size: 14px; }
    &:hover { opacity: 0.8; }
  }
}
</style>
