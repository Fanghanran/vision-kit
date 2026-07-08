<template>
  <aside class="app-sidebar" :class="{ collapsed: isCollapsed }">
    <el-menu
      :default-active="route.path"
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
      <el-menu-item index="/system">
        <el-icon><Monitor /></el-icon>
        <template #title>系统</template>
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
import { ref, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { DataBoard, Bell, VideoCamera, Monitor, Film, UserFilled, Fold, Expand } from '@element-plus/icons-vue'

const route = useRoute()
const authStore = useAuthStore()
const isCollapsed = ref(false)

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
