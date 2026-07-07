<template>
  <div class="alert-detail" v-loading="loading">
    <el-page-header @back="router.back()" content="告警详情" style="margin-bottom: 16px" />

    <el-row :gutter="16">
      <!-- 左侧：截图 + 视频 -->
      <el-col :span="10">
        <el-card shadow="hover">
          <template #header>现场截图</template>
          <el-image
            v-if="alert?.snapshot_path"
            :src="snapshotUrl"
            fit="contain"
            style="width: 100%; max-height: 400px; cursor: zoom-in"
            :preview-src-list="[snapshotUrl]"
            :initial-index="0"
            preview-teleported
          >
            <template #error>
              <div class="image-error">
                <el-icon><Picture /></el-icon>
                <span>截图加载失败</span>
              </div>
            </template>
          </el-image>
          <el-empty v-else description="无截图" :image-size="80" />

          <!-- 视频播放器 -->
          <div v-if="alert?.video_clip_path" class="video-section">
            <div class="video-label">视频片段</div>
            <video
              ref="videoRef"
              :src="videoUrl"
              controls
              preload="metadata"
              style="width: 100%; max-height: 300px; border-radius: 4px"
            >
              您的浏览器不支持视频播放
            </video>
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
                <el-popconfirm
                  title="确认处理此告警？"
                  confirm-button-text="确认"
                  cancel-button-text="取消"
                  @confirm="handleAcknowledge"
                >
                  <template #reference>
                    <el-button type="primary" :loading="actionLoading">确认处理</el-button>
                  </template>
                </el-popconfirm>
                <el-popconfirm
                  title="标记此告警为误报？此操作不可撤销。"
                  confirm-button-text="确认"
                  cancel-button-text="取消"
                  @confirm="handleReject"
                >
                  <template #reference>
                    <el-button type="warning" :loading="actionLoading">标记误报</el-button>
                  </template>
                </el-popconfirm>
              </div>
              <el-tag v-else :type="statusTagType(alert?.status)" size="large">
                {{ statusLabel(alert?.status) }}
              </el-tag>
            </div>
          </template>

          <el-descriptions :column="2" border>
            <el-descriptions-item label="事件类型">
              <el-tag :type="severityTagType(alert?.severity)" effect="plain">
                {{ eventTypeLabel(alert?.event_type) }}
              </el-tag>
            </el-descriptions-item>
            <el-descriptions-item label="摄像头">
              {{ alert?.camera_name }} ({{ alert?.camera_id }})
            </el-descriptions-item>
            <el-descriptions-item label="发生时间">{{ formatTime(alert?.created_at) }}</el-descriptions-item>
            <el-descriptions-item label="严重级别">
              <el-tag :type="severityTagType(alert?.severity)" effect="dark">
                {{ severityLabel(alert?.severity) }}
              </el-tag>
            </el-descriptions-item>
            <el-descriptions-item label="风险等级">
              <el-tag :type="riskTagType(alert?.risk_level)" effect="dark" v-if="alert?.risk_level">
                {{ alert?.risk_level }}
              </el-tag>
              <span v-else class="text-muted">-</span>
            </el-descriptions-item>
            <el-descriptions-item label="告警ID">
              <el-text type="info" size="small" truncated>{{ alert?.alert_id }}</el-text>
            </el-descriptions-item>
          </el-descriptions>

          <!-- LLM 分析 -->
          <div v-if="alert?.llm_analysis" class="llm-section">
            <h4><el-icon><MagicStick /></el-icon> LLM 智能分析</h4>
            <el-descriptions :column="1" border>
              <el-descriptions-item label="📋 情况描述">
                {{ alert.llm_analysis.description }}
              </el-descriptions-item>
              <el-descriptions-item label="⚠️ 风险等级">
                <el-tag :type="riskTagType(alert.llm_analysis.risk_level)" effect="dark">
                  {{ alert.llm_analysis.risk_level }}
                </el-tag>
              </el-descriptions-item>
              <el-descriptions-item label="💡 建议措施">
                {{ alert.llm_analysis.suggestion }}
              </el-descriptions-item>
              <el-descriptions-item v-if="alert.llm_analysis.context" label="📝 补充说明">
                {{ alert.llm_analysis.context }}
              </el-descriptions-item>
            </el-descriptions>
          </div>

          <!-- 操作历史时间线 -->
          <div class="history-section">
            <h4><el-icon><Clock /></el-icon> 操作历史</h4>
            <el-timeline>
              <el-timeline-item
                v-for="item in timeline"
                :key="item.time"
                :timestamp="item.time"
                :type="item.type"
                placement="top"
              >
                {{ item.content }}
              </el-timeline-item>
            </el-timeline>
          </div>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAlertsStore } from '@/stores/alerts'
import { ElMessage } from 'element-plus'
import { Picture, MagicStick, Clock } from '@element-plus/icons-vue'

const route = useRoute()
const router = useRouter()
const alertsStore = useAlertsStore()

const alertId = route.params.id as string
const alert = ref<any>(null)
const loading = ref(false)
const actionLoading = ref(false)

const snapshotUrl = computed(() => alert.value ? `/api/alerts/${alertId}/snapshot` : '')
const videoUrl = computed(() => alert.value ? `/api/alerts/${alertId}/clip` : '')

// 操作历史时间线
const timeline = computed(() => {
  const items: Array<{ time: string; content: string; type: string }> = []
  if (!alert.value) return items

  if (alert.value.created_at) {
    items.push({
      time: formatTime(alert.value.created_at),
      content: `系统生成告警 [${eventTypeLabel(alert.value.event_type)}]`,
      type: 'primary',
    })
  }
  if (alert.value.llm_analysis) {
    items.push({
      time: formatTime(alert.value.created_at + 2),
      content: `LLM 完成分析，风险等级：${alert.value.llm_analysis.risk_level}`,
      type: 'success',
    })
  }
  if (alert.value.acknowledged_at) {
    items.push({
      time: formatTime(alert.value.acknowledged_at),
      content: `${alert.value.acknowledged_by || '值班人员'} 确认告警`,
      type: 'warning',
    })
  }
  if (alert.value.status === 'rejected') {
    items.push({
      time: formatTime(alert.value.acknowledged_at || alert.value.created_at + 5),
      content: `${alert.value.acknowledged_by || '值班人员'} 标记为误报`,
      type: 'info',
    })
  }
  if (alert.value.status === 'resolved') {
    items.push({
      time: formatTime(alert.value.created_at + 10),
      content: '告警已解决',
      type: 'success',
    })
  }
  return items
})

onMounted(async () => {
  loading.value = true
  try {
    alert.value = await alertsStore.getAlert(alertId)
  } finally {
    loading.value = false
  }
})

async function handleAcknowledge() {
  actionLoading.value = true
  try {
    const updated = await alertsStore.updateAlertStatus(alertId, 'acknowledged')
    alert.value = updated
    ElMessage.success('告警已确认')
  } catch {
    ElMessage.error('操作失败')
  } finally {
    actionLoading.value = false
  }
}

async function handleReject() {
  actionLoading.value = true
  try {
    const updated = await alertsStore.updateAlertStatus(alertId, 'rejected')
    alert.value = updated
    ElMessage.success('已标记为误报')
  } catch {
    ElMessage.error('操作失败')
  } finally {
    actionLoading.value = false
  }
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
  margin-top: 20px;
  h4 {
    margin-bottom: 12px;
    color: var(--va-text-primary);
    display: flex;
    align-items: center;
    gap: 6px;
  }
}

.history-section {
  margin-top: 20px;
  h4 {
    margin-bottom: 12px;
    color: var(--va-text-primary);
    display: flex;
    align-items: center;
    gap: 6px;
  }
}

.video-section {
  margin-top: 16px;
  .video-label {
    font-size: 14px;
    font-weight: 500;
    margin-bottom: 8px;
    color: var(--va-text-primary);
  }
}

.image-error {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 200px;
  color: var(--va-text-secondary);
  gap: 8px;
}

.text-muted {
  color: var(--va-text-secondary);
}
</style>
