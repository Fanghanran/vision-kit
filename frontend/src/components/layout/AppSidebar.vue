<template>
  <aside class="app-sidebar">
    <el-menu
      :default-active="route.path"
      router
      background-color="#001529"
      text-color="#ffffffa6"
      active-text-color="#1890ff"
      :collapse="isCollapsed"
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
      <el-menu-item index="/system">
        <el-icon><Monitor /></el-icon>
        <template #title>系统</template>
      </el-menu-item>
    </el-menu>
  </aside>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useRoute } from 'vue-router'
import { DataBoard, Bell, VideoCamera, Monitor } from '@element-plus/icons-vue'

const route = useRoute()
const isCollapsed = ref(false)
</script>

<style lang="scss" scoped>
.app-sidebar {
  width: 200px;
  min-height: calc(100vh - 56px - 32px);
  background: var(--va-sidebar);

  .el-menu {
    border-right: none;
  }
}
</style>
