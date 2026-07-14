<template>
  <el-config-provider :locale="zhCn">
    <div v-if="isLoginPage" class="app-container">
      <router-view />
    </div>
    <div v-else-if="isStandalone" class="app-container">
      <router-view />
    </div>
    <div v-else class="app-container" :class="{ 'dark-mode': isDark }">
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
import { ref, onMounted, computed } from 'vue'
import { useRoute } from 'vue-router'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import { useWebSocket } from '@/composables/useWebSocket'
import { initMultiTabSync } from '@/composables/useMultiTabSync'
import { useAuthStore } from '@/stores/auth'
import AppHeader from '@/components/layout/AppHeader.vue'
import AppSidebar from '@/components/layout/AppSidebar.vue'
import AppFooter from '@/components/layout/AppFooter.vue'

const route = useRoute()
const { connect } = useWebSocket()
const authStore = useAuthStore()

const isDark = ref(false)
const isLoginPage = computed(() => route.name === 'Login')
const isStandalone = computed(() => ['Profile'].includes(route.name as string))

onMounted(async () => {
  // 初始化多标签页同步
  initMultiTabSync()
  await authStore.fetchMe()
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
