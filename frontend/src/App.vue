<template>
  <el-config-provider :locale="zhCn">
    <div class="app-container" :class="{ 'dark-mode': isDark }">
      <AppHeader @toggle-dark="toggleDark" />
      <div class="app-body">
        <AppSidebar />
        <main class="app-main">
          <router-view />
        </main>
      </div>
      <AppFooter />
    </div>
  </el-config-provider>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import { useWebSocket } from '@/composables/useWebSocket'
import AppHeader from '@/components/layout/AppHeader.vue'
import AppSidebar from '@/components/layout/AppSidebar.vue'
import AppFooter from '@/components/layout/AppFooter.vue'

const { connect } = useWebSocket()

const isDark = ref(false)

onMounted(() => {
  connect()
  // 读取暗色模式偏好
  isDark.value = localStorage.getItem('va-theme') === 'dark'
  document.documentElement.setAttribute('data-theme', isDark.value ? 'dark' : '')
})

function toggleDark() {
  isDark.value = !isDark.value
  localStorage.setItem('va-theme', isDark.value ? 'dark' : '')
  document.documentElement.setAttribute('data-theme', isDark.value ? 'dark' : '')
}
</script>

<style lang="scss">
* {
  margin: 0;
  padding: 0;
  box-sizing: border-box;
}

body {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC',
    'Hiragino Sans GB', 'Microsoft YaHei', sans-serif;
  background: var(--va-bg-primary, #F5F7FA);
  color: var(--va-text-primary, #303133);
}

.app-container {
  display: flex;
  flex-direction: column;
  min-height: 100vh;
}

.app-body {
  display: flex;
  flex: 1;
}

.app-main {
  flex: 1;
  padding: 24px;
  overflow-y: auto;
}
</style>
