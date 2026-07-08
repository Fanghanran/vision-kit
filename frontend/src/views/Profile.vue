<template>
  <div class="profile-standalone">
    <header class="profile-header">
      <el-button :icon="ArrowLeft" text @click="$router.push('/')">返回</el-button>
      <h3>个人设置</h3>
      <div></div>
    </header>

    <div class="profile-body">
      <!-- 头像卡片 -->
      <el-card shadow="hover" class="avatar-card">
        <div class="avatar-area">
          <el-avatar
            :size="80"
            :style="{ background: avatarColor }"
            @click="toggleAvatarEditor"
          >
            {{ initial.toUpperCase() }}
          </el-avatar>
          <p class="avatar-tip">点击更换头像</p>
        </div>
        <div v-if="showAvatarPicker" class="avatar-picker">
          <div
            v-for="color in avatarColors"
            :key="color"
            class="avatar-color"
            :style="{ background: color }"
            :class="{ active: avatarColor === color }"
            @click="selectAvatarColor(color)"
          >
            {{ initial }}
          </div>
        </div>
        <el-descriptions :column="1" border style="margin-top: 16px">
          <el-descriptions-item label="用户名">{{ user?.username }}</el-descriptions-item>
          <el-descriptions-item label="角色">
            <el-tag :type="roleTagType(user?.role)" size="small">{{ roleLabel(user?.role) }}</el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="邮箱">
            <span v-if="!editingEmail" @dblclick="startEditEmail" class="email-display">
              {{ user?.email || '点击设置' }}
            </span>
            <el-input
              v-else
              v-model="emailValue"
              size="small"
              placeholder="请输入邮箱"
              @blur="saveEmail"
              @keyup.enter="saveEmail"
              ref="emailInputRef"
            />
          </el-descriptions-item>
          <el-descriptions-item label="注册时间">
            {{ user?.created_at ? formatTime(user.created_at) : '-' }}
          </el-descriptions-item>
        </el-descriptions>
      </el-card>

      <!-- 修改密码 -->
      <el-card shadow="hover">
        <template #header>修改密码</template>
        <el-form :model="pwForm" label-width="100px" @submit.prevent="doChangePassword">
          <el-form-item label="旧密码" required>
            <el-input v-model="pwForm.old" type="password" show-password />
          </el-form-item>
          <el-form-item label="新密码" required>
            <el-input v-model="pwForm.new1" type="password" show-password />
          </el-form-item>
          <el-form-item label="确认新密码" required>
            <el-input v-model="pwForm.new2" type="password" show-password />
          </el-form-item>
          <el-form-item>
            <el-button type="primary" native-type="submit" :loading="pwLoading">修改密码</el-button>
            <el-button @click="pwForm = { old: '', new1: '', new2: '' }">重置</el-button>
          </el-form-item>
        </el-form>
      </el-card>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, nextTick } from 'vue'
import { ArrowLeft } from '@element-plus/icons-vue'
import { useAuthStore } from '@/stores/auth'
import { ElMessage } from 'element-plus'

const authStore = useAuthStore()
const user = computed(() => authStore.user as any)
const initial = computed(() => (user.value?.username || '?')[0])

const avatarColor = ref(user.value?.avatar_bg || '#1890ff')
const showAvatarPicker = ref(false)
const avatarColors = ['#1890ff', '#52c41a', '#faad14', '#f5222d', '#722ed1', '#13c2c2', '#eb2f96', '#fa541c']

function toggleAvatarEditor() { showAvatarPicker.value = !showAvatarPicker.value }

async function selectAvatarColor(color: string) {
  avatarColor.value = color
  showAvatarPicker.value = false
  try {
    await authStore.updateProfile({ avatar_bg: color })
    ElMessage.success('头像已更新')
  } catch (e: any) { ElMessage.error('保存失败') }
}

// 邮箱编辑
const editingEmail = ref(false)
const emailValue = ref(user.value?.email || '')
const emailInputRef = ref()

function startEditEmail() {
  emailValue.value = user.value?.email || ''
  editingEmail.value = true
  nextTick(() => emailInputRef.value?.focus())
}

async function saveEmail() {
  editingEmail.value = false
  if (emailValue.value === (user.value?.email || '')) return
  try {
    await authStore.updateProfile({ email: emailValue.value })
    ElMessage.success('邮箱已更新')
  } catch (e: any) { ElMessage.error('保存失败') }
}

// 密码
const pwForm = reactive({ old: '', new1: '', new2: '' })
const pwLoading = ref(false)

async function doChangePassword() {
  if (!pwForm.old || !pwForm.new1 || !pwForm.new2) { ElMessage.warning('请填写所有字段'); return }
  if (pwForm.new1 !== pwForm.new2) { ElMessage.warning('两次新密码不一致'); return }
  if (pwForm.new1.length < 6) { ElMessage.warning('新密码至少6位'); return }
  pwLoading.value = true
  try {
    await authStore.changePassword(pwForm.old, pwForm.new1)
    ElMessage.success('密码已修改')
    pwForm.old = ''; pwForm.new1 = ''; pwForm.new2 = ''
  } catch (e: any) { ElMessage.error(e?.response?.data?.detail || '修改失败')
  } finally { pwLoading.value = false }
}

function roleTagType(r?: string) { return { admin: 'danger', operator: 'warning', viewer: 'info' }[r || ''] || 'info' }
function roleLabel(r?: string) { return { admin: '管理员', operator: '操作员', viewer: '观察者' }[r || ''] || r || '' }
function formatTime(ts: number) { return ts ? new Date(ts * 1000).toLocaleString('zh-CN') : '-' }
</script>

<style lang="scss" scoped>
.profile-standalone { min-height: 100vh; background: var(--va-bg-primary, #F5F7FA); }
.profile-header { display: flex; align-items: center; justify-content: space-between; height: 56px; padding: 0 24px; background: var(--va-sidebar); color: #fff; h3 { font-size: 16px; font-weight: 600; } }
.profile-body { max-width: 480px; margin: 40px auto; display: flex; flex-direction: column; gap: 24px; padding: 0 24px; }
.avatar-card { text-align: center; }
.avatar-area { margin-bottom: 8px; cursor: pointer; }
.avatar-tip { font-size: 12px; color: var(--va-text-secondary); margin-top: 8px; }
.avatar-picker { display: flex; justify-content: center; gap: 8px; flex-wrap: wrap; }
.avatar-color { width: 40px; height: 40px; border-radius: 50%; display: flex; align-items: center; justify-content: center; color: #fff; font-weight: 600; font-size: 18px; cursor: pointer; border: 2px solid transparent; transition: transform 0.2s;
  &.active { border-color: #000; transform: scale(1.15); }
  &:hover { transform: scale(1.1); }
}
.email-display { color: var(--va-text-secondary); cursor: pointer; &:hover { color: var(--va-primary); } }
</style>
