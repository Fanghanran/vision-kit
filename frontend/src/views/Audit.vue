<template>
  <div class="audit-page">
    <!-- 审计开关 -->
    <el-card shadow="hover" style="margin-bottom: 16px">
      <div class="audit-switch">
        <div class="switch-info">
          <div class="switch-label">📝 审计日志记录</div>
          <div class="switch-desc">关闭后不再记录操作审计，已有记录保留</div>
        </div>
        <el-switch
          v-model="auditEnabled"
          :loading="switchLoading"
          @change="toggleAudit"
        />
      </div>
    </el-card>

    <el-card shadow="hover">
      <template #header>
        <div class="card-header">
          <span>审计日志</span>
          <el-button size="small" :icon="Refresh" :loading="auditLoading" @click="fetchAuditLogs">刷新</el-button>
        </div>
      </template>

      <!-- 筛选 -->
      <el-row :gutter="12" style="margin-bottom: 16px">
        <el-col :span="6">
          <el-input v-model="auditFilter.username" placeholder="用户名" clearable size="small" />
        </el-col>
        <el-col :span="6">
          <el-select v-model="auditFilter.action" placeholder="操作类型" clearable size="small">
            <el-option label="开关摄像头" value="camera.toggle" />
            <el-option label="添加摄像头" value="camera.create" />
            <el-option label="更新摄像头" value="camera.update" />
            <el-option label="删除摄像头" value="camera.delete" />
            <el-option label="确认告警" value="alert.acknowledged" />
            <el-option label="标记误报" value="alert.rejected" />
            <el-option label="解决告警" value="alert.resolved" />
            <el-option label="创建用户" value="user.create" />
            <el-option label="删除用户" value="user.delete" />
            <el-option label="更新控制项" value="control.update" />
            <el-option label="批量更新控制项" value="control.batch_update" />
          </el-select>
        </el-col>
        <el-col :span="6">
          <el-button type="primary" size="small" @click="fetchAuditLogs">查询</el-button>
        </el-col>
      </el-row>

      <el-table :data="auditLogs" v-loading="auditLoading" stripe>
        <el-table-column prop="created_at" label="时间" width="160">
          <template #default="{ row }">{{ formatAuditTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column prop="username" label="用户" width="100" />
        <el-table-column prop="role" label="角色" width="80">
          <template #default="{ row }">
            <el-tag :type="{ admin: 'danger', operator: 'warning', viewer: 'info' }[row.role] || 'info'" size="small">
              {{ roleLabel(row.role) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="action" label="操作" width="140">
          <template #default="{ row }">
            <el-tag :type="actionTagType(row.action)" size="small">{{ actionLabel(row.action) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="resource" label="对象" width="120">
          <template #default="{ row }">{{ resourceLabel(row.resource, row.action) }}</template>
        </el-table-column>
        <el-table-column prop="ip" label="IP" width="120" />
      </el-table>

      <el-pagination
        v-model:current-page="auditPage"
        v-model:page-size="auditPageSize"
        :page-sizes="[10, 20, 50]"
        :total="auditTotal"
        layout="total, sizes, prev, pager, next, jumper"
        @current-change="fetchAuditLogs"
        @size-change="onAuditPageSizeChange"
        style="margin-top: 16px; justify-content: flex-end"
      />
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { Refresh } from '@element-plus/icons-vue'
import client from '@/api/client'
import { ElMessage } from 'element-plus'

const auditLogs = ref<any[]>([])
const auditTotal = ref(0)
const auditLoading = ref(false)
const auditPage = ref(1)
const auditPageSize = ref(10)
const auditFilter = ref({ username: '', action: '' })

// 审计开关
const auditEnabled = ref(true)
const switchLoading = ref(false)

async function toggleAudit(val: boolean) {
  switchLoading.value = true
  try {
    await client.put('/api/system/controls/audit.enabled', { value: val })
    ElMessage.success(val ? '审计日志已启用' : '审计日志已停用')
  } catch {
    auditEnabled.value = !val  // 回滚
    ElMessage.error('保存失败')
  } finally {
    switchLoading.value = false
  }
}

onMounted(async () => {
  // 获取当前审计开关状态
  try {
    const { data } = await client.get('/api/system/controls')
    auditEnabled.value = data.controls?.['audit.enabled']?.value ?? true
  } catch { /* ignore */ }
  fetchAuditLogs()
})

async function fetchAuditLogs() {
  auditLoading.value = true
  try {
    const params: any = { page: auditPage.value, page_size: auditPageSize.value }
    if (auditFilter.value.username) params.username = auditFilter.value.username
    if (auditFilter.value.action) params.action = auditFilter.value.action
    const { data } = await client.get('/api/audit/logs', { params })
    auditLogs.value = data.items || []
    auditTotal.value = data.total || 0
  } catch (e) {
    console.error('fetchAuditLogs failed:', e)
  } finally {
    auditLoading.value = false
  }
}

function onAuditPageSizeChange() {
  auditPage.value = 1
  fetchAuditLogs()
}

function formatAuditTime(ts: number) {
  if (!ts) return ''
  return new Date(ts * 1000).toLocaleString('zh-CN')
}

function actionLabel(action: string): string {
  const map: Record<string, string> = {
    'camera.toggle': '开关摄像头',
    'camera.create': '添加摄像头',
    'camera.update': '更新摄像头',
    'camera.delete': '删除摄像头',
    'alert.acknowledged': '确认告警',
    'alert.rejected': '标记误报',
    'alert.resolved': '解决告警',
    'user.create': '创建用户',
    'user.delete': '删除用户',
    'user.update': '编辑用户',
    'control.update': '更新控制项',
    'control.batch_update': '批量更新控制项',
  }
  return map[action] || action
}

function actionTagType(action: string): string {
  if (action.startsWith('camera.')) return 'warning'
  if (action.startsWith('alert.')) return 'danger'
  if (action.startsWith('user.')) return 'info'
  if (action.startsWith('control.')) return 'primary'
  return ''
}

function roleLabel(role: string): string {
  const map: Record<string, string> = {
    admin: '管理员',
    operator: '操作员',
    viewer: '观察者',
  }
  return map[role] || role
}

// 控制项 key → 中文名称
const CONTROL_KEY_LABELS: Record<string, string> = {
  'llm.enabled': 'LLM 分析',
  'llm.cache_enabled': 'LLM 响应缓存',
  'notification.webhook.enabled': 'Webhook 通知',
  'notification.email.enabled': '邮件通知',
  'recording.enabled': '告警录制',
  'rules.hot_reload': '规则热重载',
  'cameras.hot_reload': '摄像头热重载',
  'camera.auto_reconnect': '摄像头自动重连',
  'websocket.enabled': 'WebSocket 推送',
  'audit.enabled': '审计日志',
}

function resourceLabel(resource: string, action: string): string {
  if (!resource) return '-'
  // 控制项操作：key → 中文
  if (action?.startsWith('control.')) {
    return CONTROL_KEY_LABELS[resource] || resource
  }
  return resource
}
</script>

<style lang="scss" scoped>
.audit-page {
  padding: 0;
}
.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.audit-switch {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.switch-info {
  flex: 1;
}
.switch-label {
  font-size: 15px;
  font-weight: 600;
  color: var(--va-text-primary);
}
.switch-desc {
  font-size: 12px;
  color: var(--va-text-secondary);
  margin-top: 2px;
}
</style>
