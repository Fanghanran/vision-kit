<template>
  <footer class="app-footer">
    <span :class="['ws-status', wsStatus]">
      {{ wsStatusText }}
    </span>
    <span class="last-update" v-if="lastUpdate">
      最后更新: {{ lastUpdate }}
    </span>
  </footer>
</template>

<script setup lang="ts">
import { computed, ref, onMounted, onUnmounted } from 'vue'
import { useWebSocket } from '@/composables/useWebSocket'

const { wsStatus } = useWebSocket()
const lastUpdate = ref('')

const wsStatusText = computed(() => {
  const map = { connected: '已连接', disconnected: '未连接', reconnecting: '重连中...' }
  return map[wsStatus.value] || '未知'
})

let timer: ReturnType<typeof setInterval>
onMounted(() => {
  timer = setInterval(() => {
    lastUpdate.value = new Date().toLocaleTimeString('zh-CN')
  }, 1000)
})
onUnmounted(() => clearInterval(timer))
</script>

<style lang="scss" scoped>
.app-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 32px;
  padding: 0 24px;
  background: var(--va-bg-card);
  border-top: 1px solid var(--va-border);
  font-size: 12px;
  color: var(--va-text-secondary);
}

.ws-status {
  &::before {
    content: '';
    display: inline-block;
    width: 8px;
    height: 8px;
    border-radius: 50%;
    margin-right: 6px;
  }
  &.connected::before { background: #67C23A; }
  &.disconnected::before { background: #F56C6C; }
  &.reconnecting::before { background: #E6A23C; }
}
</style>
