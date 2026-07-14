<template>
  <aside class="app-sidebar" :class="{ collapsed: isCollapsed }">
    <el-menu
      :default-active="activeMenu"
      router
      background-color="#001529"
      text-color="#ffffffa6"
      active-text-color="#1890ff"
      :collapse="isCollapsed"
      :collapse-transition="false"
    >
      <el-menu-item index="/">
        <el-icon><DataBoard /></el-icon>
        <template #title>仪表盘</template>
      </el-menu-item>
      <el-menu-item index="/alerts">
        <el-icon><Bell /></el-icon>
        <template #title>告警</template>
      </el-menu-item>
      <el-menu-item index="/cameras">
        <el-icon><VideoCamera /></el-icon>
        <template #title>摄像头</template>
      </el-menu-item>
      <el-menu-item index="/monitor">
        <el-icon><Film /></el-icon>
        <template #title>监控</template>
      </el-menu-item>
      <el-menu-item v-if="authStore.isAdmin" index="/users">
        <el-icon><UserFilled /></el-icon>
        <template #title>用户管理</template>
      </el-menu-item>
      <el-menu-item index="/rules">
        <el-icon><List /></el-icon>
        <template #title>规则</template>
      </el-menu-item>

      <!-- 系统管理（管理员折叠菜单） -->
      <el-sub-menu v-if="authStore.isAdmin" index="system-group">
        <template #title>
          <el-icon><Monitor /></el-icon>
          <span>系统管理</span>
        </template>
        <el-menu-item index="/system">系统监控</el-menu-item>
        <el-menu-item index="/system/llm">LLM 模块</el-menu-item>
        <el-menu-item index="/system/notification">通知模块</el-menu-item>
        <el-menu-item index="/system/recording">录制模块</el-menu-item>
        <el-menu-item index="/system/rules">规则引擎</el-menu-item>
        <el-menu-item index="/system/cameras">摄像头模块</el-menu-item>
      </el-sub-menu>
      <el-menu-item v-else index="/system">
        <el-icon><Monitor /></el-icon>
        <template #title>系统</template>
      </el-menu-item>

      <el-menu-item v-if="authStore.isAdmin" index="/audit">
        <el-icon><Document /></el-icon>
        <template #title>审计日志</template>
      </el-menu-item>
    </el-menu>

    <!-- 折叠按钮 -->
    <div class="collapse-btn" @click="isCollapsed = !isCollapsed">
      <el-icon>
        <Fold v-if="!isCollapsed" />
        <Expand v-else />
      </el-icon>
    </div>
  </aside>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { DataBoard, Bell, VideoCamera, Monitor, Film, UserFilled, Fold, Expand, List, Document } from '@element-plus/icons-vue'

const route = useRoute()
const authStore = useAuthStore()
const isCollapsed = ref(false)

// 计算当前激活的菜单项（考虑 query 参数）
const activeMenu = computed(() => {
  if (route.query.tab === 'audit') return '/system?tab=audit'
  return route.path
})

// 响应式自动折叠
onMounted(() => {
  const mq = window.matchMedia('(max-width: 1199px)')
  isCollapsed.value = mq.matches
  mq.addEventListener('change', (e) => { isCollapsed.value = e.matches })
})
</script>

<style lang="scss" scoped>
.app-sidebar {
  display: flex;
  flex-direction: column;
  width: 200px;
  min-height: calc(100vh - 56px - 32px);
  background: var(--va-sidebar);
  transition: width 0.3s;

  &.collapsed { width: 64px; }

  .el-menu {
    border-right: none;
    flex: 1;
  }
}

.collapse-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 40px;
  color: #ffffffa6;
  cursor: pointer;
  border-top: 1px solid rgba(255, 255, 255, 0.1);
  &:hover { color: #fff; background: rgba(255, 255, 255, 0.05); }
}
</style>

<!-- 全局样式：el-sub-menu 弹出层美化 -->
<style lang="scss">
.el-menu--popup {
  background: #001529 !important;
  border: 1px solid rgba(255, 255, 255, 0.1) !important;
  border-radius: 4px !important;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.3) !important;
  padding: 4px 0 !important;

  .el-menu-item {
    height: 40px !important;
    line-height: 40px !important;
    padding: 0 20px !important;
    min-width: 120px !important;
    white-space: nowrap !important;
    font-size: 13px !important;

    &:hover {
      background: rgba(24, 144, 255, 0.15) !important;
    }

    &.is-active {
      color: #1890ff !important;
      background: rgba(24, 144, 255, 0.1) !important;
    }
  }
}

/* 折叠状态下 sub-menu 图标居中 */
.el-menu--collapse {
  .el-sub-menu__title {
    padding: 0 !important;
    justify-content: center;

    .el-sub-menu__icon-arrow {
      display: none;
    }
  }
}
</style>
