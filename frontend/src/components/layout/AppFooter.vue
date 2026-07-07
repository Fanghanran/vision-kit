<template>
  <footer class="app-footer">
    <span class="last-update" v-if="lastUpdate">
      最后更新: {{ lastUpdate }}
    </span>
  </footer>
</template>

<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue'

const lastUpdate = ref('')

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
  justify-content: flex-end;
  height: 32px;
  padding: 0 24px;
  background: var(--va-bg-card);
  border-top: 1px solid var(--va-border);
  font-size: 12px;
  color: var(--va-text-secondary);
}
</style>
