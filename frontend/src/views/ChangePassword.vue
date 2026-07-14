<template>
  <div class="change-password-page">
    <div class="cp-card">
      <div class="cp-header">
        <div class="cp-icon">
          <el-icon :size="40" color="#1890ff"><Lock /></el-icon>
        </div>
        <h2 class="cp-title">修改默认密码</h2>
        <p class="cp-desc">
          为了您的账户安全，请修改默认密码后再继续使用系统。
        </p>
      </div>

      <el-form
        ref="formRef"
        :model="form"
        :rules="formRules"
        label-position="top"
        @submit.prevent="doChange"
      >
        <el-form-item label="新密码" prop="newPassword">
          <el-input
            v-model="form.newPassword"
            type="password"
            show-password
            placeholder="至少 6 位"
            size="large"
          />
        </el-form-item>
        <el-form-item label="确认密码" prop="confirmPassword">
          <el-input
            v-model="form.confirmPassword"
            type="password"
            show-password
            placeholder="再次输入新密码"
            size="large"
            @keyup.enter="doChange"
          />
        </el-form-item>
        <el-form-item>
          <el-button
            type="primary"
            native-type="submit"
            :loading="loading"
            size="large"
            class="cp-btn"
          >
            {{ loading ? '修改中...' : '确认修改' }}
          </el-button>
        </el-form-item>
      </el-form>

      <transition name="fade">
        <p v-if="error" class="cp-error">
          <el-icon><WarningFilled /></el-icon> {{ error }}
        </p>
      </transition>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive } from 'vue'
import { useRouter } from 'vue-router'
import { Lock, WarningFilled } from '@element-plus/icons-vue'
import { useAuthStore } from '@/stores/auth'
import type { FormInstance } from 'element-plus'
import client from '@/api/client'

const router = useRouter()
const authStore = useAuthStore()

const formRef = ref<FormInstance>()
const form = reactive({
  newPassword: '',
  confirmPassword: '',
})
const loading = ref(false)
const error = ref('')

const formRules = {
  newPassword: [
    { required: true, message: '请输入新密码', trigger: 'blur' },
    { min: 6, message: '密码至少 6 位', trigger: 'blur' },
  ],
  confirmPassword: [
    { required: true, message: '请确认新密码', trigger: 'blur' },
    {
      validator: (_rule: any, value: string, callback: any) => {
        if (value !== form.newPassword) {
          callback(new Error('两次输入的密码不一致'))
        } else {
          callback()
        }
      },
      trigger: 'blur',
    },
  ],
}

async function doChange() {
  const valid = await formRef.value?.validate().catch(() => false)
  if (!valid) return

  loading.value = true
  error.value = ''
  try {
    // 调用改密 API（首次改密不需要旧密码）
    await client.post('/api/auth/change-password', {
      old_password: '',  // 首次改密，旧密码留空
      new_password: form.newPassword,
    })
    // 清除 must_change_password 标记
    if (authStore.user) {
      (authStore.user as any).must_change_password = false
    }
    // 重新获取用户信息
    await authStore.fetchMe()
    router.replace({ name: 'Dashboard' })
  } catch (e: any) {
    error.value = e?.response?.data?.detail || '修改失败，请重试'
  } finally {
    loading.value = false
  }
}
</script>

<style lang="scss" scoped>
.change-password-page {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #000c17 0%, #001529 40%, #00264d 100%);
}

.cp-card {
  width: 420px;
  padding: 40px;
  background: var(--va-bg-card, #fff);
  border-radius: 12px;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.3);
}

.cp-header {
  text-align: center;
  margin-bottom: 32px;
}

.cp-icon {
  margin-bottom: 16px;
}

.cp-title {
  font-size: 22px;
  font-weight: 700;
  color: var(--va-text-primary);
  margin-bottom: 8px;
}

.cp-desc {
  font-size: 14px;
  color: var(--va-text-secondary);
  line-height: 1.6;
}

.cp-btn {
  width: 100%;
  height: 44px;
  font-size: 16px;
  letter-spacing: 2px;
}

.cp-error {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  color: #f5222d;
  font-size: 14px;
  padding: 10px 16px;
  background: #fff2f0;
  border-radius: 8px;
  border: 1px solid #ffccc7;
  margin-top: 16px;
}

.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.3s ease;
}
.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}
</style>
