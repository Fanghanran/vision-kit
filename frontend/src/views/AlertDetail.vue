<template>
  <div class="alert-detail" v-loading="loading">
    <el-page-header @back="router.back()" content="告警详情" style="margin-bottom: 16px" />

    <el-row :gutter="16">
      <!-- 左侧：截图 + 视频 -->
      <el-col :span="10">
        <el-card shadow="hover">
          <el-image
            v-if="alert?.snapshot_path"
            :src="`/api/alerts/${alertId}/snapshot`"
            fit="contain"
            style="width: 100%; max-height: 400px"
            :preview-src-list="[`/api/alerts/${alertId}/snapshot`]"
          />
          <el-empty v-else description="无截图" :image-size="80" />

          <div v-if="alert?.video_clip_path" style="margin-top: 12px">
            <video
              :src="`/api/alerts/${alertId}/clip`"
              controls
              style="width: 100%; max-height: 300px"
            />
          </div>
        </el-card>
      </el-col>

      <!-- 右侧：信息 + 操作 -->
      <el-col :span="14">
        <el-card shadow="hover">
          <template #header>
            <div class="detail-header">
              <span>告警信息</span>
              <div class="actions" v-if="alert?.status === 'pending'">
                <el-button type="primary" @click="handleAcknowledge">确认处理</el-button>
                <el-button type="warning" @click="handleReject">标记误报</el-button>
              </div>
              <el-tag v-else :type="statusTagType(alert?.status)" size="large">
                {{ statusLabel(alert?.status) }}
              </el-tag>
            </div>
          </template>

          <el-descriptions :column="2" border>
            <el-descriptions-item label="类型">{{ eventTypeLabel(alert?.event_type) }}</el-descriptions-item>
            <el-descriptions-item label="摄像头">{{ alert?.camera_name }} ({{ alert?.camera_id }})</el-descriptions-item>
            <el-descriptions-item label="时间">{{ formatTime(alert?.created_at) }}</el-descriptions-item>
            <el-descriptions-item label="级别">
              <el-tag :type="severityTagType(alert?.severity)" effect="dark">
                {{ severityLabel(alert?.severity) }}
              </el-tag>
            </el-descriptions-item>
            <el-descriptions-item label="风险等级">{{ alert?.risk_level || '-' }}</el-descriptions-item>
            <el-descriptions-item label="告警ID">{{ alert?.alert_id }}</el-descriptions-item>
          </el-descriptions>

          <!-- LLM 分析 -->
          <div v-if="alert?.llm_analysis" class="llm-section">
            <h4>LLM 分析</h4>
            <el-descriptions :column="1" border>
              <el-descriptions-item label="情况描述">{{ alert.llm_analysis.description }}</el-descriptions-item>
              <el-descriptions-item label="风险等级">
                <el-tag :type="riskTagType(alert.llm_analysis.risk_level)" effect="dark">
                  {{ alert.llm_analysis.risk_level }}
                </el-tag>
              </el-descriptions-item>
              <el-descriptions-item label="建议措施">{{ alert.llm_analysis.suggestion }}</el-descriptions-item>
            </el-descriptions>
          </div>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAlertsStore } from '@/stores/alerts'
import { ElMessageBox } from 'element-plus'

const route = useRoute()
const router = useRouter()
const alertsStore = useAlertsStore()

const alertId = route.params.id as string
const alert = ref<any>(null)
const loading = ref(false)

onMounted(async () => {
  loading.value = true
  try {
    alert.value = await alertsStore.getAlert(alertId)
  } finally {
    loading.value = false
  }
})

async function handleAcknowledge() {
  try {
    await ElMessageBox.confirm('确认处理此告警？', '确认操作', { type: 'info' })
    const updated = await alertsStore.updateAlertStatus(alertId, 'acknowledged')
    alert.value = updated
  } catch {}
}

async function handleReject() {
  try {
    await ElMessageBox.confirm('标记此告警为误报？此操作不可撤销。', '标记误报', { type: 'warning' })
    const updated = await alertsStore.updateAlertStatus(alertId, 'rejected')
    alert.value = updated
  } catch {}
}

function statusTagType(s?: string) {
  return { pending: 'warning', acknowledged: '', rejected: 'info', resolved: 'success' }[s || ''] || 'info'
}
function statusLabel(s?: string) {
  return { pending: '待处理', acknowledged: '已确认', rejected: '误报', resolved: '已解决' }[s || ''] || s
}
function severityTagType(s?: string) {
  return { critical: 'danger', warning: 'warning', info: 'info' }[s || ''] || 'info'
}
function severityLabel(s?: string) {
  return { critical: '紧急', warning: '警告', info: '信息' }[s || ''] || s
}
function eventTypeLabel(t?: string) {
  const map: Record<string, string> = { intrusion: '闯入', absence: '离岗', crowd: '聚集', abandoned_object: '遗留', counting: '统计' }
  return map[t || ''] || t
}
function riskTagType(r?: string) {
  const map: Record<string, string> = { '紧急': 'danger', '高': 'warning', '中': '', '低': 'info' }
  return map[r || ''] || 'info'
}
function formatTime(ts?: number) {
  if (!ts) return ''
  return new Date(ts * 1000).toLocaleString('zh-CN')
}
</script>

<style lang="scss" scoped>
.detail-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.llm-section {
  margin-top: 16px;
  h4 { margin-bottom: 12px; color: var(--va-text-primary); }
}
</style>
