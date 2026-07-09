<template>
  <div class="profile-page">
    <!-- 顶部栏 -->
    <header class="profile-header">
      <el-button :icon="ArrowLeft" text @click="$router.push('/')">返回</el-button>
      <h3>个人设置</h3>
      <div></div>
    </header>

    <div class="profile-body">
      <!-- 左侧 — 个人信息卡 -->
      <div class="profile-left">
        <el-card shadow="hover">
          <div class="info-card">
            <div class="info-avatar">
              <el-popover
                placement="bottom"
                :width="200"
                trigger="click"
                :show-arrow="false"
                popper-class="avatar-color-popover"
              >
                <template #reference>
                  <el-avatar
                    :size="72"
                    :style="{ background: detail?.avatar_bg || '#1890ff', fontSize: '30px', cursor: 'pointer' }"
                  >
                    {{ initial }}
                  </el-avatar>
                </template>
                <div class="color-ring">
                  <span
                    v-for="(color, i) in avatarColors"
                    :key="color"
                    class="dot-wrap"
                    :style="{ transform: dotPosition(i) }"
                  >
                    <span
                      class="color-dot"
                      :style="{ background: color }"
                      :class="{ active: (detail?.avatar_bg || '#1890ff') === color }"
                      @click="changeColor(color)"
                    />
                  </span>
                  <span class="ring-avatar" :style="{ background: detail?.avatar_bg || '#1890ff' }">
                    {{ initial }}
                  </span>
                </div>
              </el-popover>
              <p class="avatar-tip">点击更换颜色</p>
            </div>
            <div class="info-detail">
              <div class="info-name">
                {{ detail?.username }}
                <el-tag :type="roleTagType(detail?.role)" size="small" style="margin-left:8px">
                  {{ roleLabel(detail?.role) }}
                </el-tag>
              </div>
              <div class="info-meta">
                <div class="meta-row">
                  <el-icon><Message /></el-icon>
                  <span v-if="!editingEmail" class="meta-value link" @click="startEditEmail">
                    {{ detail?.email || '点击设置邮箱' }}
                  </span>
                  <template v-else>
                    <el-input v-model="emailValue" size="small" style="width:200px" @blur="saveEmail" @keyup.enter="saveEmail" ref="emailInputRef" />
                    <el-button size="small" type="primary" link @click="saveEmail" :loading="savingEmail">保存</el-button>
                  </template>
                </div>
                <div class="meta-row">
                  <el-icon><Clock /></el-icon>
                  <span>注册于 {{ formatTime(detail?.created_at) }}</span>
                </div>
                <div class="meta-row" v-if="detail?.last_login">
                  <el-icon><Promotion /></el-icon>
                  <span>
                    最后登录 {{ formatTime(detail?.last_login.time) }}
                    <template v-if="detail?.last_login.ip"> · {{ detail?.last_login.ip }}</template>
                  </span>
                </div>
              </div>
            </div>
          </div>
        </el-card>
      </div>

      <!-- 右侧 — 标签页设置 -->
      <div class="profile-right">
        <el-card shadow="hover">
          <el-tabs v-model="activeTab">
            <!-- ◉ 通知设置 -->
            <el-tab-pane label="通知设置" name="notify">
              <div class="tab-section">
                <el-form label-width="100px" label-position="left">
                  <el-divider content-position="left">告警推送</el-divider>
                  <el-form-item label="启用">
                    <el-switch v-model="prefs.notify_alert.enabled" @change="savePrefs" />
                  </el-form-item>
                  <el-form-item label="推送渠道" v-if="prefs.notify_alert.enabled">
                    <el-checkbox-group v-model="prefs.notify_alert.channels" @change="savePrefs">
                      <el-checkbox value="webhook" label="webhook">钉钉/企微 Webhook</el-checkbox>
                      <el-checkbox value="email" label="email">邮件</el-checkbox>
                    </el-checkbox-group>
                  </el-form-item>

                  <el-divider content-position="left">系统通知</el-divider>
                  <el-form-item label="启用">
                    <el-switch v-model="prefs.notify_system.enabled" @change="savePrefs" />
                  </el-form-item>
                  <el-form-item label="推送渠道" v-if="prefs.notify_system.enabled">
                    <el-checkbox-group v-model="prefs.notify_system.channels" @change="savePrefs">
                      <el-checkbox value="webhook" label="webhook">钉钉/企微 Webhook</el-checkbox>
                      <el-checkbox value="email" label="email">邮件</el-checkbox>
                    </el-checkbox-group>
                  </el-form-item>

                  <el-divider content-position="left">日报推送</el-divider>
                  <el-form-item label="启用">
                    <el-switch v-model="prefs.notify_daily.enabled" @change="savePrefs" />
                  </el-form-item>
                  <el-form-item label="推送渠道" v-if="prefs.notify_daily.enabled">
                    <el-checkbox-group v-model="prefs.notify_daily.channels" @change="savePrefs">
                      <el-checkbox value="webhook" label="webhook">钉钉/企微 Webhook</el-checkbox>
                      <el-checkbox value="email" label="email">邮件</el-checkbox>
                    </el-checkbox-group>
                  </el-form-item>
                </el-form>
              </div>
            </el-tab-pane>

            <!-- ◉ 安全设置 -->
            <el-tab-pane label="安全设置" name="security">
              <div class="tab-section">
                <!-- 活跃会话 -->
                <div class="security-block">
                  <div class="security-row">
                    <div>
                      <div class="security-title">活跃会话</div>
                      <div class="security-desc">{{ detail?.active_sessions ?? 0 }} 个设备在线</div>
                    </div>
                    <el-button size="small" @click="showMySessions">管理会话</el-button>
                  </div>
                </div>

                <el-divider />

                <!-- 修改密码 -->
                <div class="security-block">
                  <div class="security-title" style="margin-bottom:12px">修改密码</div>
                  <el-form :model="pwForm" label-width="100px" label-position="left" @submit.prevent="doChangePassword">
                    <el-form-item label="旧密码" required>
                      <el-input v-model="pwForm.old" type="password" show-password />
                    </el-form-item>
                    <el-form-item label="新密码" required>
                      <el-input v-model="pwForm.new1" type="password" show-password />
                    </el-form-item>
                    <el-form-item label="确认密码" required>
                      <el-input v-model="pwForm.new2" type="password" show-password />
                    </el-form-item>
                    <el-form-item>
                      <el-button type="primary" native-type="submit" :loading="pwLoading">修改密码</el-button>
                      <el-button @click="resetPwForm">重置</el-button>
                    </el-form-item>
                  </el-form>
                </div>

                <el-divider />

                <!-- 登录历史 -->
                <div class="security-block">
                  <div class="security-row">
                    <div>
                      <div class="security-title">最近登录记录</div>
                    </div>
                    <el-button size="small" :loading="historyLoading" @click="refreshMyHistory">刷新</el-button>
                  </div>
                  <el-timeline v-if="loginHistory.length" style="margin-top:12px">
                    <el-timeline-item
                      v-for="h in loginHistory"
                      :key="h.id"
                      :timestamp="formatTime(h.created_at)"
                      :type="h.success ? 'success' : 'danger'"
                      size="small"
                    >
                      {{ h.ip || '未知IP' }} — {{ h.success ? '登录成功' : (h.reason || '失败') }}
                    </el-timeline-item>
                  </el-timeline>
                  <div v-else class="text-muted" style="padding:12px 0">暂无登录记录</div>
                </div>
              </div>
            </el-tab-pane>
          </el-tabs>
        </el-card>
      </div>
    </div>

    <!-- 会话管理弹窗 -->
    <el-dialog v-model="sessionsVisible" title="我的活跃会话" width="480px" destroy-on-close>
      <el-table :data="mySessions" stripe v-if="mySessions.length" max-height="300" style="margin-top:12px">
        <el-table-column label="IP" prop="ip" />
        <el-table-column label="剩余时间" width="150">
          <template #default="{ row }">{{ formatRemaining(row.remaining_seconds) }}</template>
        </el-table-column>
      </el-table>
      <div v-else class="text-muted" style="padding:20px 0;text-align:center">无活跃会话</div>
      <div v-if="mySessions.length" style="margin-top:12px;text-align:right">
        <el-button size="small" type="danger" plain @click="revokeAll">退出所有设备</el-button>
      </div>
      <template #footer>
        <el-button @click="sessionsVisible = false">关闭</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted, nextTick } from 'vue'
import { ArrowLeft, Message, Clock, Promotion } from '@element-plus/icons-vue'
import { useAuthStore } from '@/stores/auth'
import { ElMessage } from 'element-plus'

const authStore = useAuthStore()

// 个人详情
const detail = ref<any>(null)
const initial = computed(() => (detail.value?.username || '?')[0])

// 头像颜色
const avatarColors = ['#1890ff', '#52c41a', '#faad14', '#f5222d', '#722ed1', '#13c2c2', '#eb2f96', '#fa541c']

function dotPosition(i: number) {
  const r = 52  // ring radius
  const angle = (i / avatarColors.length) * Math.PI * 2 - Math.PI / 2
  return `translate(${Math.cos(angle) * r}px, ${Math.sin(angle) * r}px)`
}

async function changeColor(color: string) {
  try {
    await authStore.updateProfile({ avatar_bg: color })
    detail.value.avatar_bg = color
    ElMessage.success('头像已更新')
  } catch { ElMessage.error('保存失败') }
}

// 邮箱编辑
const editingEmail = ref(false)
const emailValue = ref('')
const savingEmail = ref(false)
const emailInputRef = ref()

function startEditEmail() {
  emailValue.value = detail.value?.email || ''
  editingEmail.value = true
  nextTick(() => emailInputRef.value?.focus())
}

async function saveEmail() {
  editingEmail.value = false
  const val = emailValue.value.trim()
  if (val === (detail.value?.email || '')) return
  savingEmail.value = true
  try {
    await authStore.updateProfile({ email: val })
    detail.value.email = val
    ElMessage.success('邮箱已更新')
  } catch (e: any) { ElMessage.error(e?.response?.data?.detail || '保存失败')
  } finally { savingEmail.value = false }
}

// 通知偏好
const prefs = reactive({
  notify_alert: { enabled: true, channels: ['webhook'] as string[] },
  notify_system: { enabled: true, channels: ['webhook'] as string[] },
  notify_daily: { enabled: false, channels: ['webhook'] as string[] },
})

let _prefTimer: ReturnType<typeof setTimeout> | null = null

async function savePrefs() {
  if (_prefTimer) clearTimeout(_prefTimer)
  _prefTimer = setTimeout(async () => {
    try {
      await authStore.updatePreferences(prefs)
    } catch { /* silent */ }
  }, 500)
}

// 安全
const activeTab = ref('notify')
const loginHistory = ref<any[]>([])
const historyLoading = ref(false)

async function refreshMyHistory() {
  if (!detail.value) return
  historyLoading.value = true
  try {
    loginHistory.value = await authStore.getLoginHistory(detail.value.username)
  } catch { loginHistory.value = []
  } finally { historyLoading.value = false }
}

// 会话
const sessionsVisible = ref(false)
const mySessions = ref<any[]>([])

async function showMySessions() {
  sessionsVisible.value = true
  try {
    mySessions.value = await authStore.getUserSessions(detail.value.username)
  } catch { mySessions.value = [] }
}

async function revokeAll() {
  try {
    await authStore.revokeSessions(detail.value.username)
    mySessions.value = []
    ElMessage.success('已退出所有设备')
  } catch (e: any) { ElMessage.error(e?.response?.data?.detail || '操作失败') }
}

// 密码
const pwForm = ref({ old: '', new1: '', new2: '' })
const pwLoading = ref(false)

function resetPwForm() {
  pwForm.value = { old: '', new1: '', new2: '' }
}

async function doChangePassword() {
  if (!pwForm.value.old || !pwForm.value.new1 || !pwForm.value.new2) { ElMessage.warning('请填写所有字段'); return }
  if (pwForm.value.new1 !== pwForm.value.new2) { ElMessage.warning('两次新密码不一致'); return }
  if (pwForm.value.new1.length < 6) { ElMessage.warning('新密码至少6位'); return }
  pwLoading.value = true
  try {
    await authStore.changePassword(pwForm.value.old, pwForm.value.new1)
    ElMessage.success('密码已修改')
    pwForm.value = { old: '', new1: '', new2: '' }
  } catch { ElMessage.error('密码修改失败')
  } finally { pwLoading.value = false }
}

// 标签
function roleTagType(r?: string) { return { admin: 'danger', operator: 'warning', viewer: 'info' }[r || ''] || 'info' }
function roleLabel(r?: string) { return { admin: '管理员', operator: '操作员', viewer: '观察者' }[r || ''] || r || '' }
function formatTime(ts: number) { return ts ? new Date(ts * 1000).toLocaleString('zh-CN') : '-' }
function formatRemaining(sec: number) {
  if (sec < 60) return `${sec} 秒`
  if (sec < 3600) return `${Math.floor(sec / 60)} 分 ${sec % 60} 秒`
  return `${Math.floor(sec / 3600)} 时 ${Math.floor(sec % 3600 / 60)} 分`
}

// 初始化
onMounted(async () => {
  try {
    detail.value = await authStore.fetchDetail()
    prefs.notify_alert = detail.value.preferences?.notify_alert || { enabled: true, channels: ['webhook'] }
    prefs.notify_system = detail.value.preferences?.notify_system || { enabled: true, channels: ['webhook'] }
    prefs.notify_daily = detail.value.preferences?.notify_daily || { enabled: false, channels: ['webhook'] }
  } catch { /* fallback */ }
})
</script>

<style lang="scss" scoped>
.profile-page { min-height: 100vh; background: var(--va-bg-primary, #F5F7FA); }
.profile-header {
  display: flex; align-items: center; justify-content: space-between;
  height: 56px; padding: 0 24px; background: var(--va-sidebar); color: #fff;
  h3 { font-size: 16px; font-weight: 600; margin: 0; }
  .el-button { color: #fff; }
}
.profile-body { max-width: 960px; margin: 32px auto; padding: 0 24px; display: flex; gap: 24px; }
.profile-left { width: 380px; flex-shrink: 0; }
.profile-right { flex: 1; min-width: 0; }

// 信息卡
.info-card { display: flex; gap: 20px; }
.info-avatar { text-align: center; }
.avatar-tip { font-size: 12px; color: #999; margin-top: 8px; }
.info-detail { flex: 1;}
.info-name { font-size: 18px; font-weight: 600; margin-bottom: 12px; display: flex; align-items: center; }
.info-meta { display: flex; flex-direction: column; gap: 8px; }
.meta-row { display: flex; align-items: center; gap: 6px; font-size: 13px; color: #666;
  .el-icon { color: #999; font-size: 14px; }
}
.meta-value.link { color: var(--va-primary, #1890ff); cursor: pointer;
  &:hover { text-decoration: underline; }
}

// 安全
.security-block { .el-form { margin-bottom: 0; } }
.security-row { display: flex; align-items: center; justify-content: space-between; }
.security-title { font-size: 14px; font-weight: 600; }
.security-desc { font-size: 13px; color: #999; margin-top: 4px; }

.tab-section { min-height: 300px; }
.text-muted { color: #999; font-size: 13px; }
</style>

<style lang="scss">
.avatar-color-popover {
  padding: 8px !important;
  .color-ring {
    position: relative;
    width: 130px; height: 130px;
    margin: 0 auto 4px;
    .dot-wrap {
      position: absolute;
      top: 50%; left: 50%;
      width: 0; height: 0;
    }
    .color-dot {
      display: block;
      width: 14px; height: 14px;
      border-radius: 50%;
      cursor: pointer;
      margin: -7px 0 0 -7px;
      border: 2px solid transparent;
      transition: transform 0.15s;
      &.active { border-color: #333; transform: scale(1.3); }
      &:hover { transform: scale(1.25); }
    }
  }
  .ring-avatar {
    position: absolute;
    top: 50%; left: 50%;
    transform: translate(-50%, -50%);
    width: 44px; height: 44px;
    border-radius: 50%;
    display: flex;
    align-items: center;
    justify-content: center;
    color: #fff;
    font-size: 20px;
    font-weight: 600;
    pointer-events: none;
  }
}
</style>
