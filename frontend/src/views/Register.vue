<template>
  <div class="register-page">
    <!-- 左侧品牌区 -->
    <div class="register-brand">
      <div class="brand-bg"></div>
      <div class="brand-content">
        <div class="brand-icon">
          <svg viewBox="0 0 80 80" fill="none">
            <rect x="8" y="16" width="64" height="44" rx="6" stroke="currentColor" stroke-width="3"/>
            <ellipse cx="40" cy="38" rx="14" ry="9" stroke="currentColor" stroke-width="3"/>
            <circle cx="40" cy="38" r="4" stroke="currentColor" stroke-width="2" fill="none"/>
          </svg>
        </div>
        <h1 class="brand-name">SentinelMind</h1>
        <p class="brand-desc">多路视频智能分析框架</p>
      </div>
    </div>

    <!-- 右侧注册区 -->
    <div class="register-form-area">
      <div class="register-card">
        <h2 class="form-title">创建账户</h2>
        <p class="form-subtitle">注册后默认为观察者角色</p>

        <el-form @submit.prevent="doRegister" label-position="top">
          <el-form-item>
            <el-input v-model="username" placeholder="用户名" :prefix-icon="User" size="large" :disabled="loading" />
          </el-form-item>
          <el-form-item>
            <el-input v-model="email" placeholder="邮箱（可选）" :prefix-icon="Message" size="large" :disabled="loading" />
          </el-form-item>
          <el-form-item>
            <el-input v-model="password" type="password" placeholder="密码（至少6位）" :prefix-icon="Lock" size="large" show-password :disabled="loading" />
          </el-form-item>
          <el-form-item>
            <el-input v-model="confirmPassword" type="password" placeholder="确认密码" :prefix-icon="Lock" size="large" show-password :disabled="loading" @keyup.enter="doRegister" />
          </el-form-item>
          <el-form-item>
            <el-button type="primary" native-type="submit" :loading="loading" size="large" class="register-btn" round>
              {{ loading ? '注册中...' : '注 册' }}
            </el-button>
          </el-form-item>
        </el-form>

        <div class="login-link">
          已有账户？<router-link to="/login">去登录</router-link>
        </div>

        <transition name="fade">
          <p v-if="error" class="register-error">
            <el-icon><WarningFilled /></el-icon> {{ error }}
          </p>
        </transition>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { User, Lock, Message, WarningFilled } from '@element-plus/icons-vue'
import { useAuthStore } from '@/stores/auth'
import client from '@/api/client'

const router = useRouter()
const authStore = useAuthStore()

const username = ref('')
const email = ref('')
const password = ref('')
const confirmPassword = ref('')
const loading = ref(false)
const error = ref('')

async function doRegister() {
  if (!username.value || !password.value) {
    error.value = '请输入用户名和密码'
    return
  }
  if (password.value.length < 6) {
    error.value = '密码至少 6 位'
    return
  }
  if (password.value !== confirmPassword.value) {
    error.value = '两次输入的密码不一致'
    return
  }
  loading.value = true
  error.value = ''
  try {
    const { data } = await client.post('/api/auth/register', {
      username: username.value,
      password: password.value,
      email: email.value || undefined,
    })
    // 注册成功，设置 token（与 login 流程一致）
    authStore.token = data.token
    authStore.user = data.user
    localStorage.setItem('va-token', data.token)
    client.defaults.headers.common['Authorization'] = `Bearer ${data.token}`
    if (data.refresh_token) {
      localStorage.setItem('va-refresh-token', data.refresh_token)
    }
    router.replace({ name: 'Dashboard' })
  } catch (e: any) {
    error.value = e?.response?.data?.detail || '注册失败'
  } finally {
    loading.value = false
  }
}
</script>

<style lang="scss" scoped>
.register-page {
  display: flex;
  min-height: 100vh;
}

.register-brand {
  flex: 1;
  position: relative;
  background: linear-gradient(135deg, #000c17 0%, #001529 40%, #00264d 100%);
  display: flex;
  align-items: center;
  justify-content: center;
  color: #fff;

  .brand-bg {
    position: absolute;
    inset: 0;
    background: radial-gradient(ellipse at 30% 50%, rgba(24, 144, 255, 0.12) 0%, transparent 60%);
  }

  .brand-content {
    position: relative;
    z-index: 1;
    text-align: center;
  }

  .brand-icon {
    width: 80px;
    height: 80px;
    margin: 0 auto 24px;
    color: #1890ff;
  }

  .brand-name {
    font-size: 32px;
    font-weight: 800;
    background: linear-gradient(135deg, #1890ff, #69c0ff);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    margin-bottom: 8px;
  }

  .brand-desc {
    font-size: 14px;
    color: rgba(255, 255, 255, 0.55);
    letter-spacing: 4px;
  }
}

.register-form-area {
  width: 480px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--va-bg-primary, #f5f7fa);
  padding: 0 48px;
}

.register-card {
  width: 100%;
  max-width: 360px;
}

.form-title {
  font-size: 28px;
  font-weight: 700;
  color: var(--va-text-primary);
  margin-bottom: 8px;
}

.form-subtitle {
  font-size: 14px;
  color: var(--va-text-secondary);
  margin-bottom: 24px;
}

.register-btn {
  width: 100%;
  height: 48px;
  font-size: 16px;
  letter-spacing: 4px;
  margin-top: 4px;
}

.login-link {
  text-align: center;
  font-size: 14px;
  color: var(--va-text-secondary);
  margin-top: 16px;
  a {
    color: var(--va-primary);
    text-decoration: none;
    &:hover { text-decoration: underline; }
  }
}

.register-error {
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

.fade-enter-active, .fade-leave-active { transition: opacity 0.3s ease; }
.fade-enter-from, .fade-leave-to { opacity: 0; }

@media (max-width: 768px) {
  .register-brand { display: none; }
  .register-form-area { width: 100%; padding: 24px; }
}
</style>
