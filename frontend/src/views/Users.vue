<template>
  <div class="users-page">
    <div class="page-header">
      <h2>用户管理</h2>
      <el-button type="primary" :icon="Plus" @click="openCreate">添加用户</el-button>
    </div>

    <!-- 统计卡片 -->
    <el-row :gutter="16" class="stats-row">
      <el-col :span="6">
        <el-card shadow="hover" :body-style="{ padding: '20px' }">
          <div class="stat-card">
            <div class="stat-icon" style="background:#e6f4ff"><el-icon :size="24" color="#1677ff"><Avatar /></el-icon></div>
            <div class="stat-body">
              <div class="stat-value">{{ stats.total_users }}</div>
              <div class="stat-label">总用户</div>
            </div>
          </div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="hover" :body-style="{ padding: '20px' }">
          <div class="stat-card">
            <div class="stat-icon" style="background:#fff1f0"><el-icon :size="24" color="#cf1322"><Star /></el-icon></div>
            <div class="stat-body">
              <div class="stat-value">{{ stats.by_role?.admin || 0 }}</div>
              <div class="stat-label">管理员</div>
            </div>
          </div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="hover" :body-style="{ padding: '20px' }">
          <div class="stat-card">
            <div class="stat-icon" style="background:#fffbe6"><el-icon :size="24" color="#d48806"><Warning /></el-icon></div>
            <div class="stat-body">
              <div class="stat-value">{{ stats.by_role?.operator || 0 }}</div>
              <div class="stat-label">操作员</div>
            </div>
          </div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="hover" :body-style="{ padding: '20px' }">
          <div class="stat-card">
            <div class="stat-icon" style="background:#f6ffed"><el-icon :size="24" color="#389e0d"><View /></el-icon></div>
            <div class="stat-body">
              <div class="stat-value">{{ stats.by_role?.viewer || 0 }}</div>
              <div class="stat-label">观察者 <span style="color:#999;font-size:12px">在线 {{ stats.online_count }}</span></div>
            </div>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <!-- 搜索筛选 + 表格 -->
    <el-card shadow="hover">
      <div class="toolbar">
        <el-input v-model="search" placeholder="搜索用户名或邮箱..." clearable style="width:260px" :prefix-icon="Search" />
        <div class="toolbar-right">
          <el-select v-model="filterRole" placeholder="角色" clearable style="width:120px">
            <el-option label="管理员" value="admin" />
            <el-option label="操作员" value="operator" />
            <el-option label="观察者" value="viewer" />
          </el-select>
          <el-select v-model="filterStatus" placeholder="状态" clearable style="width:100px;margin-left:8px">
            <el-option label="正常" :value="0" />
            <el-option label="禁用" :value="1" />
          </el-select>
        </div>
      </div>

      <el-table :data="paginatedUsers" v-loading="loading" stripe row-key="username">
        <el-table-column prop="username" label="用户名" min-width="120" />
        <el-table-column prop="email" label="邮箱" min-width="160">
          <template #default="{ row }">{{ row.email || '-' }}</template>
        </el-table-column>
        <el-table-column label="角色" width="90">
          <template #default="{ row }">
            <el-tag :type="roleTagType(row.role)" size="small">{{ roleLabel(row.role) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <el-switch
              :model-value="row.status === 0"
              active-text="正常"
              inactive-text="禁用"
              inline-prompt
              size="small"
              :disabled="row.username === 'admin'"
              @change="toggleStatus(row)"
            />
          </template>
        </el-table-column>
        <el-table-column label="注册时间" width="160">
          <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="240" fixed="right">
          <template #default="{ row }">
            <el-button size="small" type="primary" link @click.stop="openDrawer(row)">详情</el-button>
            <el-button size="small" link @click.stop="openEdit(row)">编辑</el-button>
            <el-button size="small" link @click.stop="showSessions(row)">会话</el-button>
            <el-popconfirm v-if="row.username !== 'admin'" title="确定删除？" @confirm="handleDelete(row.username)">
              <template #reference>
                <el-button size="small" type="danger" link @click.stop>删除</el-button>
              </template>
            </el-popconfirm>
          </template>
        </el-table-column>
      </el-table>

      <!-- 分页 -->
      <el-pagination
        v-model:current-page="page"
        v-model:page-size="pageSize"
        :page-sizes="[10, 20, 50]"
        :total="filteredUsers.length"
        layout="total, sizes, prev, pager, next, jumper"
        @current-change="onPageChange"
        @size-change="onPageSizeChange"
        style="margin-top: 16px; justify-content: flex-end"
      />
    </el-card>

    <!-- 右侧资料卡抽屉 -->
    <el-drawer v-model="drawerVisible" title="用户资料" size="440px" direction="rtl" destroy-on-close @open="onDrawerOpen">
      <template v-if="drawerUser">
        <!-- 头像区 -->
        <div class="drawer-avatar">
          <el-avatar :size="64" :style="{ background: drawerUser.avatar_bg || '#1890ff', fontSize: '28px' }">
            {{ drawerUser.username.charAt(0).toUpperCase() }}
          </el-avatar>
          <div class="drawer-name">{{ drawerUser.username }}</div>
          <div class="drawer-tags">
            <el-tag :type="roleTagType(drawerUser.role)">{{ roleLabel(drawerUser.role) }}</el-tag>
            <el-tag :type="drawerUser.status === 0 ? 'success' : 'danger'" style="margin-left:6px">
              {{ drawerUser.status === 0 ? '正常' : '禁用' }}
            </el-tag>
          </div>
        </div>

        <el-divider />

        <!-- 基本信息 -->
        <el-descriptions :column="1" border size="small" title="基本信息">
          <el-descriptions-item label="ID">{{ drawerUser.id }}</el-descriptions-item>
          <el-descriptions-item label="邮箱">{{ drawerUser.email || '-' }}</el-descriptions-item>
          <el-descriptions-item label="注册时间">{{ formatTime(drawerUser.created_at) }}</el-descriptions-item>
          <el-descriptions-item label="最后修改">{{ drawerUser.updated_at !== drawerUser.created_at ? formatTime(drawerUser.updated_at) : '未修改' }}</el-descriptions-item>
        </el-descriptions>

        <el-divider />

        <!-- 当前权限 -->
        <div class="drawer-section">
          <h4>当前权限</h4>
          <div class="perm-list-row">{{ (PERMISSIONS[drawerUser.role] || []).map(p => permLabel(p)).join('、') }}</div>
        </div>

        <el-divider />

        <!-- 登录历史 -->
        <div class="drawer-section">
          <div class="section-header">
            <h4>最近登录记录</h4>
            <el-button size="small" link type="primary" :loading="historyLoading" @click="refreshHistory">刷新</el-button>
          </div>
          <el-timeline v-if="loginHistory.length">
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
          <div v-else class="text-muted">暂无登录记录</div>
        </div>
      </template>
    </el-drawer>

    <!-- 添加/编辑弹窗 -->
    <el-dialog v-model="dialogVisible" :title="editing ? '编辑用户' : '添加用户'" width="460px" destroy-on-close>
      <el-form :model="form" label-width="80px">
        <el-form-item label="用户名" required>
          <el-input v-model="form.username" :disabled="!!editing" placeholder="字母数字下划线" />
        </el-form-item>
        <el-form-item label="邮箱">
          <el-input v-model="form.email" placeholder="user@example.com" />
        </el-form-item>
        <el-form-item label="密码" :required="!editing">
          <el-input v-model="form.password" type="password" show-password :placeholder="editing ? '留空不修改' : '至少6位'" />
        </el-form-item>
        <el-form-item label="角色">
          <el-select v-model="form.role">
            <el-option label="管理员 (admin) — 全部权限" value="admin" />
            <el-option label="操作员 (operator) — 告警+摄像头管理" value="operator" />
            <el-option label="观察者 (viewer) — 仅查看" value="viewer" />
          </el-select>
        </el-form-item>
        <el-form-item v-if="editing" label="状态">
          <el-switch v-model="form.statusActive" active-text="正常" inactive-text="禁用" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" :disabled="!form.username" @click="handleSave">保存</el-button>
      </template>
    </el-dialog>

    <!-- 活跃会话弹窗 -->
    <el-dialog v-model="sessionsVisible" title="活跃会话" width="480px" destroy-on-close>
      <template v-if="sessionsUser">
        <div class="sessions-header">
          <span>用户：<strong>{{ sessionsUser.username }}</strong></span>
          <el-button size="small" type="danger" plain :loading="revoking" @click="handleRevoke">强制下线</el-button>
        </div>
        <el-table :data="sessionsList" stripe v-if="sessionsList.length" style="margin-top:12px">
          <el-table-column label="IP" prop="ip" />
          <el-table-column label="剩余时间" width="150">
            <template #default="{ row }">{{ formatRemaining(row.remaining_seconds) }}</template>
          </el-table-column>
        </el-table>
        <div v-else class="text-muted" style="padding:20px 0;text-align:center">无活跃会话</div>
      </template>
      <template #footer>
        <el-button @click="sessionsVisible = false">关闭</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted } from 'vue'
import { Plus, Search, Avatar, Star, Warning, View } from '@element-plus/icons-vue'
import { useAuthStore } from '@/stores/auth'
import { ElMessage } from 'element-plus'

const authStore = useAuthStore()

const users = ref<any[]>([])
const loading = ref(false)
const stats = ref<{ total_users: number; by_role: Record<string,number>; active_count: number; disabled_count: number; online_count: number }>(
  { total_users: 0, by_role: {}, active_count: 0, disabled_count: 0, online_count: 0 },
)

const PERMISSIONS: Record<string, string[]> = {
  admin: ['manage:users', 'manage:config', 'manage:alerts', 'control:cameras', 'view:alerts', 'view:cameras'],
  operator: ['manage:alerts', 'control:cameras', 'view:alerts', 'view:cameras'],
  viewer: ['view:alerts', 'view:cameras'],
}

async function loadAll() {
  loading.value = true
  try {
    users.value = await authStore.listUsers()
    stats.value = await authStore.getUserStats()
  } finally { loading.value = false }
}

// 搜索筛选
const search = ref('')
const filterRole = ref('')
const filterStatus = ref<number | ''>('')

const filteredUsers = computed(() => {
  return users.value.filter(u => {
    if (search.value) {
      const q = search.value.toLowerCase()
      if (!u.username.toLowerCase().includes(q) && !(u.email || '').toLowerCase().includes(q)) return false
    }
    if (filterRole.value && u.role !== filterRole.value) return false
    if (filterStatus.value !== '' && u.status !== filterStatus.value) return false
    return true
  })
})

// 分页
const page = ref(1)
const pageSize = ref(10)

const paginatedUsers = computed(() => {
  const start = (page.value - 1) * pageSize.value
  return filteredUsers.value.slice(start, start + pageSize.value)
})

function onPageChange() {
  // el-pagination 自动触发
}

function onPageSizeChange() {
  page.value = 1
}

// 右侧资料卡抽屉
const drawerVisible = ref(false)
const drawerUser = ref<any>(null)
const loginHistory = ref<any[]>([])
const historyLoading = ref(false)

function openDrawer(row: any) {
  drawerUser.value = row
  drawerVisible.value = true
}

async function onDrawerOpen() {
  await refreshHistory()
}

async function refreshHistory() {
  if (!drawerUser.value) return
  historyLoading.value = true
  try {
    loginHistory.value = await authStore.getLoginHistory(drawerUser.value.username)
  } catch { loginHistory.value = []
  } finally { historyLoading.value = false }
}

// 编辑弹窗
const dialogVisible = ref(false)
const editing = ref<any>(null)
const form = reactive({ username: '', email: '', password: '', role: 'viewer', statusActive: true })
const saving = ref(false)

function openCreate() {
  editing.value = null
  form.username = ''; form.email = ''; form.password = ''; form.role = 'viewer'; form.statusActive = true
  dialogVisible.value = true
}

function openEdit(row: any) {
  editing.value = row
  form.username = row.username; form.email = row.email || ''; form.password = ''
  form.role = row.role; form.statusActive = row.status === 0
  dialogVisible.value = true
}

async function handleSave() {
  saving.value = true
  try {
    const status = form.statusActive ? 0 : 1
    if (editing.value) {
      const payload: Record<string,any> = { email: form.email, role: form.role, status }
      if (form.password) payload.password = form.password
      await authStore.updateUser(form.username, payload)
      ElMessage.success('用户已更新')
    } else {
      if (!form.password) { ElMessage.warning('请输入密码'); saving.value = false; return }
      await authStore.createUser(form.username, form.password, form.role, form.email)
      ElMessage.success('用户已创建')
    }
    dialogVisible.value = false
    await loadAll()
  } catch (e: any) { ElMessage.error(e?.response?.data?.detail || '操作失败')
  } finally { saving.value = false }
}

// 删除
async function handleDelete(username: string) {
  try { await authStore.deleteUser(username); ElMessage.success(`用户 ${username} 已删除`); await loadAll()
  } catch (e: any) { ElMessage.error(e?.response?.data?.detail || '删除失败') }
}

// 状态切换
async function toggleStatus(row: any) {
  const newStatus = row.status === 0 ? 1 : 0
  try {
    await authStore.updateUser(row.username, { status: newStatus })
    row.status = newStatus
    stats.value = await authStore.getUserStats()
    ElMessage.success(newStatus === 0 ? '已启用' : '已禁用')
  } catch { ElMessage.error('操作失败') }
}

// 会话管理
const sessionsVisible = ref(false)
const sessionsUser = ref<any>(null)
const sessionsList = ref<any[]>([])
const revoking = ref(false)

async function showSessions(row: any) {
  sessionsUser.value = row
  sessionsVisible.value = true
  try {
    sessionsList.value = await authStore.getUserSessions(row.username)
  } catch { sessionsList.value = [] }
}

async function handleRevoke() {
  revoking.value = true
  try {
    await authStore.revokeSessions(sessionsUser.value.username)
    ElMessage.success('已强制下线')
    sessionsList.value = []
  } catch (e: any) { ElMessage.error(e?.response?.data?.detail || '操作失败')
  } finally { revoking.value = false }
}

// 标签
function roleTagType(r: string) { return { admin: 'danger', operator: 'warning', viewer: 'info' }[r] || 'info' }
function roleLabel(r: string) { return { admin: '管理员', operator: '操作员', viewer: '观察者' }[r] || r }
function permLabel(p: string) {
  return { 'manage:users':'用户管理','manage:config':'配置管理','manage:alerts':'告警处理',
    'control:cameras':'摄像头控制','view:alerts':'查看告警','view:cameras':'查看监控' }[p] || p
}
function formatTime(ts: number) { return ts ? new Date(ts * 1000).toLocaleString('zh-CN') : '-' }
function formatRemaining(sec: number) {
  if (sec < 60) return `${sec} 秒`
  if (sec < 3600) return `${Math.floor(sec/60)} 分 ${sec%60} 秒`
  return `${Math.floor(sec/3600)} 时 ${Math.floor(sec%3600/60)} 分`
}

onMounted(() => loadAll())
</script>

<style lang="scss" scoped>
.users-page { padding: 20px; }
.page-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; h2 { margin: 0; font-size: 20px; } }
.stats-row { margin-bottom: 16px; }
.stat-card { display: flex; align-items: center; gap: 16px; }
.stat-icon { width: 48px; height: 48px; border-radius: 8px; display: flex; align-items: center; justify-content: center; }
.stat-value { font-size: 24px; font-weight: 700; line-height: 1.2; }
.stat-label { font-size: 13px; color: #999; margin-top: 2px; }
.toolbar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
.toolbar-right { display: flex; }
.drawer-avatar { text-align: center; padding-bottom: 8px; }
.drawer-name { font-size: 18px; font-weight: 600; margin: 12px 0 8px; }
.drawer-tags { display: flex; justify-content: center; }
.drawer-section {
  h4 { margin: 0 0 8px; font-size: 14px; }
}
.section-header { display: flex; align-items: center; justify-content: space-between; h4 { margin: 0; } }
.perm-list-row { font-size: 13px; color: #666; line-height: 1.8; }
.sessions-header { display: flex; align-items: center; justify-content: space-between; }
.text-muted { color: #999; font-size: 13px; padding: 12px 0; }
</style>
