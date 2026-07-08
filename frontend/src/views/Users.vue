<template>
  <div class="users-page">
    <el-card shadow="hover">
      <template #header>
        <div class="card-header">
          <span>用户管理</span>
          <el-button type="primary" size="small" :icon="Plus" @click="openCreateDialog">添加用户</el-button>
        </div>
      </template>
      <el-table :data="users" v-loading="loading" stripe>
        <el-table-column prop="username" label="用户名" />
        <el-table-column prop="email" label="邮箱" />
        <el-table-column label="角色" width="100">
          <template #default="{ row }">
            <el-tag :type="roleTagType(row.role)" size="small">{{ roleLabel(row.role) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-switch
              :model-value="row.status === 0"
              active-text="正常"
              inactive-text="禁用"
              inline-prompt
              :disabled="row.username === 'admin'"
              @change="toggleStatus(row)"
            />
          </template>
        </el-table-column>
        <el-table-column label="注册时间" width="170">
          <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="200" fixed="right">
          <template #default="{ row }">
            <el-button size="small" type="primary" link @click="openDetail(row)">详情</el-button>
            <el-button size="small" link @click="openEditDialog(row)">编辑</el-button>
            <el-popconfirm
              v-if="row.username !== 'admin'"
              title="确定删除此用户？"
              @confirm="doDeleteUser(row.username)"
            >
              <template #reference>
                <el-button size="small" type="danger" link>删除</el-button>
              </template>
            </el-popconfirm>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 添加/编辑弹窗 -->
    <el-dialog v-model="dialogVisible" :title="editingUser ? '编辑用户' : '添加用户'" width="440px" append-to-body>
      <el-form :model="form" label-width="80px">
        <el-form-item label="用户名" required>
          <el-input v-model="form.username" :disabled="!!editingUser" />
        </el-form-item>
        <el-form-item label="邮箱">
          <el-input v-model="form.email" />
        </el-form-item>
        <el-form-item label="密码" :required="!editingUser">
          <el-input v-model="form.password" type="password" show-password :placeholder="editingUser ? '留空不修改' : '请输入密码'" />
        </el-form-item>
        <el-form-item label="角色">
          <el-select v-model="form.role">
            <el-option label="管理员" value="admin" />
            <el-option label="操作员" value="operator" />
            <el-option label="观察者" value="viewer" />
          </el-select>
        </el-form-item>
        <el-form-item v-if="editingUser" label="状态">
          <el-switch v-model="form.status" :active-value="0" :inactive-value="1" active-text="正常" inactive-text="禁用" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" :disabled="!form.username" @click="doSave">保存</el-button>
      </template>
    </el-dialog>

    <!-- 用户详情弹窗 -->
    <el-dialog v-model="detailVisible" title="用户详情" width="440px" append-to-body>
      <el-descriptions v-if="detailUser" :column="1" border>
        <el-descriptions-item label="ID">{{ detailUser.id }}</el-descriptions-item>
        <el-descriptions-item label="用户名">{{ detailUser.username }}</el-descriptions-item>
        <el-descriptions-item label="邮箱">{{ detailUser.email || '-' }}</el-descriptions-item>
        <el-descriptions-item label="角色">
          <el-tag :type="roleTagType(detailUser.role)" size="small">{{ roleLabel(detailUser.role) }}</el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="状态">
          <el-tag :type="detailUser.status === 0 ? 'success' : 'danger'" size="small">
            {{ detailUser.status === 0 ? '正常' : '禁用' }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="注册时间">{{ formatTime(detailUser.created_at) }}</el-descriptions-item>
        <el-descriptions-item label="最后修改">{{ formatTime(detailUser.updated_at) }}</el-descriptions-item>
      </el-descriptions>
      <template #footer>
        <el-button @click="detailVisible = false">关闭</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted } from 'vue'
import { Plus } from '@element-plus/icons-vue'
import { useAuthStore } from '@/stores/auth'
import { ElMessage } from 'element-plus'

const authStore = useAuthStore()

const users = ref<any[]>([])
const loading = ref(false)

async function loadUsers() {
  loading.value = true
  try { users.value = await authStore.listUsers() } finally { loading.value = false }
}

// 添加 / 编辑
const dialogVisible = ref(false)
const editingUser = ref<any>(null)
const form = reactive({ username: '', email: '', password: '', role: 'viewer', status: 0 })
const saving = ref(false)

function openCreateDialog() {
  editingUser.value = null
  form.username = ''; form.email = ''; form.password = ''; form.role = 'viewer'
  dialogVisible.value = true
}

function openEditDialog(row: any) {
  editingUser.value = row
  form.username = row.username; form.email = row.email || ''; form.password = ''; form.role = row.role; form.status = row.status
  dialogVisible.value = true
}

async function doSave() {
  saving.value = true
  try {
    if (editingUser.value) {
      const payload: Record<string, any> = { email: form.email, role: form.role, status: form.status }
      if (form.password) payload.password = form.password
      await authStore.updateUser(form.username, payload)
      ElMessage.success('用户已更新')
    } else {
      if (!form.password) { ElMessage.warning('请输入密码'); saving.value = false; return }
      await authStore.createUser(form.username, form.password, form.role, form.email)
      ElMessage.success('用户已创建')
    }
    dialogVisible.value = false
    await loadUsers()
  } catch (e: any) { ElMessage.error(e?.response?.data?.detail || '操作失败')
  } finally { saving.value = false }
}

// 详情
const detailVisible = ref(false); const detailUser = ref<any>(null)
function openDetail(row: any) { detailUser.value = row; detailVisible.value = true }

// 状态切换
async function toggleStatus(row: any) {
  const newStatus = row.status === 0 ? 1 : 0
  try {
    await authStore.updateUser(row.username, { status: newStatus })
    row.status = newStatus
    ElMessage.success(newStatus === 0 ? '已启用' : '已禁用')
  } catch (e: any) { ElMessage.error('操作失败') }
}

// 删除
async function doDeleteUser(username: string) {
  try { await authStore.deleteUser(username); ElMessage.success(`用户 ${username} 已删除`); await loadUsers()
  } catch (e: any) { ElMessage.error(e?.response?.data?.detail || '删除失败') }
}

function roleTagType(r?: string) { return { admin: 'danger', operator: 'warning', viewer: 'info' }[r || ''] || 'info' }
function roleLabel(r?: string) { return { admin: '管理员', operator: '操作员', viewer: '观察者' }[r || ''] || r || '' }
function formatTime(ts: number) { return ts ? new Date(ts * 1000).toLocaleString('zh-CN') : '-' }

onMounted(() => loadUsers())
</script>

<style lang="scss" scoped>
.card-header { display: flex; align-items: center; justify-content: space-between; }
</style>
