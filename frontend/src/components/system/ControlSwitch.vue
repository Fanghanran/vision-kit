<template>
  <div class="control-switch">
    <div class="control-info">
      <div class="control-label">{{ label }}</div>
      <div class="control-desc" v-if="description">{{ description }}</div>
    </div>
    <el-switch
      :model-value="modelValue"
      :disabled="loading"
      @change="handleChange"
    />
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import client from '@/api/client'
import { ElMessage } from 'element-plus'

const props = defineProps<{
  label: string
  description?: string
  modelValue: boolean
  apiKey: string
}>()

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
}>()

const loading = ref(false)

async function handleChange(val: boolean) {
  loading.value = true
  try {
    await client.put(`/api/system/controls/${props.apiKey}`, { value: val })
    emit('update:modelValue', val)
  } catch {
    // 回滚：el-switch 已经变了，需要重新赋值
    emit('update:modelValue', !val)
    ElMessage.error(`保存 ${props.label} 失败`)
  } finally {
    loading.value = false
  }
}
</script>

<style lang="scss" scoped>
.control-switch {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 0;
  border-bottom: 1px solid var(--va-border, #eee);

  &:last-child { border-bottom: none; }
}

.control-info {
  flex: 1;
}

.control-label {
  font-size: 14px;
  font-weight: 500;
  color: var(--va-text-primary);
  margin-bottom: 2px;
}

.control-desc {
  font-size: 12px;
  color: var(--va-text-secondary);
}
</style>
