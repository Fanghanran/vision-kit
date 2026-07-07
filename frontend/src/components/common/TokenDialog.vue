<template>
  <el-dialog
    title="系统认证"
    :model-value="visible"
    :close-on-click-modal="false"
    :close-on-press-escape="false"
    :show-close="false"
    width="400px"
    @update:model-value="$emit('update:visible', $event)"
  >
    <el-form @submit.prevent="handleSubmit">
      <el-form-item label="API Token">
        <el-input
          v-model="inputToken"
          placeholder="请输入 API Token"
          type="password"
          show-password
          autofocus
        />
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button type="primary" @click="handleSubmit" :disabled="!inputToken.trim()">
        确认
      </el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useAuthStore } from '@/stores/auth'

defineProps<{ visible: boolean }>()
const emit = defineEmits<{ 'update:visible': [value: boolean] }>()

const authStore = useAuthStore()
const inputToken = ref('')

function handleSubmit() {
  const token = inputToken.value.trim()
  if (token) {
    authStore.setToken(token)
    emit('update:visible', false)
    inputToken.value = ''
  }
}
</script>
