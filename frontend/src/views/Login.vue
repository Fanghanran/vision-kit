<template>
  <div class="login-page">
    <!-- 左侧品牌区 -->
    <div class="login-brand">
      <div class="brand-bg"></div>
      <div class="brand-grid"></div>
      <div class="brand-content">
        <div class="brand-icon">
          <svg viewBox="0 0 80 80" fill="none">
            <rect x="8" y="16" width="64" height="44" rx="6" stroke="currentColor" stroke-width="3"/>
            <ellipse cx="40" cy="38" rx="14" ry="9" stroke="currentColor" stroke-width="3"/>
            <circle cx="40" cy="38" r="4" stroke="currentColor" stroke-width="2" fill="none"/>
            <rect x="28" y="62" width="24" height="6" rx="3" stroke="currentColor" stroke-width="2"/>
            <path d="M4 52 L16 60 L28 52" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
            <path d="M52 52 L64 60 L76 52" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
          </svg>
        </div>
        <h1 class="brand-name">Vision Agent</h1>
        <p class="brand-desc">多路视频智能分析框架</p>
        <div class="brand-features">
          <div class="feature-item">
            <span class="feature-dot"></span>多路摄像头实时监控
          </div>
          <div class="feature-item">
            <span class="feature-dot"></span>YOLO 智能检测 + 追踪
          </div>
          <div class="feature-item">
            <span class="feature-dot"></span>LLM 事件分析 + 告警通知
          </div>
        </div>
      </div>
      <div class="brand-footer">© 2026 Vision Agent</div>
    </div>

    <!-- 右侧登录区 -->
    <div class="login-form-area">
      <div class="login-card">
        <h2 class="form-title">欢迎回来</h2>
        <p class="form-subtitle">请登录您的账户以继续</p>

        <el-form @submit.prevent="doLogin" label-position="top" class="login-form">
          <el-form-item>
            <el-input
              v-model="username"
              placeholder="用户名"
              :prefix-icon="User"
              size="large"
              :disabled="loading"
              class="login-input"
            />
          </el-form-item>
          <el-form-item>
            <el-input
              v-model="password"
              type="password"
              placeholder="密码"
              :prefix-icon="Lock"
              size="large"
              show-password
              :disabled="loading"
              class="login-input"
              @keyup.enter="doLogin"
            />
          </el-form-item>
          <el-form-item>
            <el-button
              type="primary"
              native-type="submit"
              :loading="loading"
              size="large"
              class="login-btn"
              round
            >
              {{ loading ? '登录中...' : '登 录' }}
            </el-button>
          </el-form-item>
        </el-form>

        <transition name="fade">
          <p v-if="error" class="login-error">
            <el-icon><WarningFilled /></el-icon> {{ error }}
          </p>
        </transition>

        <div class="register-link">
          没有账户？<router-link to="/register">去注册</router-link>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { User, Lock, WarningFilled } from '@element-plus/icons-vue'
import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const authStore = useAuthStore()

const username = ref('')
const password = ref('')
const loading = ref(false)
const error = ref('')

async function doLogin() {
  if (!username.value || !password.value) {
    error.value = '请输入用户名和密码'
    return
  }
  loading.value = true
  error.value = ''
  try {
    await authStore.login(username.value, password.value)
    // 检查是否需要强制改密
    if (authStore.user?.must_change_password) {
      router.replace({ name: 'ChangePassword' })
    } else {
      router.replace({ name: 'Dashboard' })
    }
  } catch (e: any) {
    error.value = e.message || '登录失败'
  } finally {
    loading.value = false
  }
}
</script>

<style lang="scss" scoped>
.login-page {
  display: flex;
  min-height: 100vh;
}

// ─── 左侧品牌区 ──────────────────────────────────────────

.login-brand {
  flex: 1;
  position: relative;
  background: linear-gradient(135deg, #000c17 0%, #001529 40%, #00264d 100%);
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  overflow: hidden;

  .brand-bg {
    position: absolute;
    inset: 0;
    background:
      radial-gradient(ellipse at 30% 50%, rgba(24, 144, 255, 0.12) 0%, transparent 60%),
      radial-gradient(ellipse at 70% 30%, rgba(24, 144, 255, 0.06) 0%, transparent 50%);
    animation: bg-pulse 6s ease-in-out infinite;
  }

  @keyframes bg-pulse {
    0%, 100% { opacity: 0.8; }
    50% { opacity: 1; }
  }

  .brand-grid {
    position: absolute;
    inset: 0;
    background-image:
      linear-gradient(rgba(24, 144, 255, 0.06) 1px, transparent 1px),
      linear-gradient(90deg, rgba(24, 144, 255, 0.06) 1px, transparent 1px);
    background-size: 60px 60px;
    animation: grid-drift 20s linear infinite;
  }

  @keyframes grid-drift {
    0% { transform: translate(0, 0); }
    100% { transform: translate(60px, 60px); }
  }

  .brand-content {
    position: relative;
    z-index: 1;
    text-align: center;
    color: #fff;
    padding: 0 60px;
  }

  .brand-icon {
    width: 100px;
    height: 100px;
    margin: 0 auto 32px;
    color: #1890ff;
    animation: float 3s ease-in-out infinite;
  }

  @keyframes float {
    0%, 100% { transform: translateY(0); }
    50% { transform: translateY(-8px); }
  }

  .brand-name {
    font-size: 36px;
    font-weight: 800;
    letter-spacing: 2px;
    margin-bottom: 12px;
    background: linear-gradient(135deg, #1890ff, #69c0ff);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
  }

  .brand-desc {
    font-size: 16px;
    color: rgba(255, 255, 255, 0.55);
    margin-bottom: 48px;
    letter-spacing: 4px;
  }

  .brand-features {
    text-align: left;
    display: inline-flex;
    flex-direction: column;
    gap: 16px;
  }

  .feature-item {
    display: flex;
    align-items: center;
    gap: 12px;
    font-size: 15px;
    color: rgba(255, 255, 255, 0.7);
  }

  .feature-dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: #1890ff;
    box-shadow: 0 0 8px rgba(24, 144, 255, 0.6);
  }

  .brand-footer {
    position: absolute;
    bottom: 24px;
    color: rgba(255, 255, 255, 0.25);
    font-size: 12px;
    z-index: 1;
  }
}

// ─── 右侧登录区 ──────────────────────────────────────────

.login-form-area {
  width: 480px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--va-bg-primary, #f5f7fa);
  padding: 0 48px;
}

.login-card {
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
  margin-bottom: 36px;
}

.login-form {
  .el-form-item {
    margin-bottom: 20px;
  }
}

.login-input :deep(.el-input__wrapper) {
  border-radius: 8px;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.06);
  transition: box-shadow 0.3s;

  &:hover {
    box-shadow: 0 2px 8px rgba(24, 144, 255, 0.12);
  }

  &.is-focus {
    box-shadow: 0 0 0 1px #1890ff, 0 2px 8px rgba(24, 144, 255, 0.15);
  }
}

.login-btn {
  width: 100%;
  height: 48px;
  font-size: 16px;
  letter-spacing: 4px;
  margin-top: 4px;
}

.login-error {
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

.register-link {
  text-align: center;
  font-size: 14px;
  color: var(--va-text-secondary);
  margin-top: 24px;
  a {
    color: var(--va-primary);
    text-decoration: none;
    &:hover { text-decoration: underline; }
  }
}

.login-hint {
  text-align: center;
  color: var(--va-text-secondary);
  font-size: 12px;
  margin-top: 24px;
  opacity: 0.5;
}

.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.3s ease;
}
.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}

// ─── 响应式 ──────────────────────────────────────────────

@media (max-width: 768px) {
  .login-brand {
    display: none;
  }
  .login-form-area {
    width: 100%;
    padding: 24px;
  }
}
</style>
