<template>
  <div class="rules-page">
    <div class="page-header">
      <h2>规则管理</h2>
      <el-button type="primary" @click="openCreate">+ 创建规则</el-button>
    </div>

    <!-- 模式指示 -->
    <div class="mode-tabs">
      <el-radio-group v-model="mode" size="small">
        <el-radio-button value="check">查（浏览规则）</el-radio-button>
        <el-radio-button value="write">写（创建/编辑）</el-radio-button>
        <el-radio-button value="test">测（干跑校验）</el-radio-button>
      </el-radio-group>
    </div>

    <!-- ── CHECK：浏览规则 ── -->
    <template v-if="mode === 'check'">
      <el-table :data="rules" v-loading="loading" stripe empty-text="暂无规则，点击「创建规则」添加">
        <el-table-column prop="name" label="名称" min-width="160">
          <template #default="{ row }">
            <el-button type="primary" link @click="viewDetail(row)">{{ row.name }}</el-button>
          </template>
        </el-table-column>
        <el-table-column prop="type" label="类型" width="130">
          <template #default="{ row }">
            <el-tag :type="typeTag(row.type)" size="small">{{ typeLabel(row.type) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="摄像头" width="110">
          <template #default="{ row }">
            <template v-if="row.camera_ids && row.camera_ids.length">
              <el-tag v-for="cid in row.camera_ids" :key="cid" size="small" style="margin-right:4px">{{ cid }}</el-tag>
            </template>
            <span v-else class="text-muted">全部</span>
          </template>
        </el-table-column>
        <el-table-column label="严重级别" width="90">
          <template #default="{ row }">
            <el-tag :type="severityTag(row.severity)" size="small">{{ severityLabel(row.severity) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="冷却" width="80">
          <template #default="{ row }">{{ row.cooldown ? row.cooldown + 's' : '-' }}</template>
        </el-table-column>
        <el-table-column label="动作" width="180">
          <template #default="{ row }">
            <template v-if="row.actions && row.actions.length">
              <el-tag v-for="a in row.actions" :key="a" size="small" type="info" style="margin-right:4px">
                {{ actionLabel(a) }}
              </el-tag>
            </template>
            <span v-else class="text-muted">-</span>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="70">
          <template #default="{ row }">
            <el-switch :model-value="row.enabled" size="small" disabled />
          </template>
        </el-table-column>
        <el-table-column label="操作" width="140" fixed="right">
          <template #default="{ row }">
            <el-button type="primary" link size="small" @click="editRule(row)">编辑</el-button>
            <el-popconfirm title="确定删除此规则？" @confirm="handleDelete(row.name)">
              <template #reference>
                <el-button type="danger" link size="small">删除</el-button>
              </template>
            </el-popconfirm>
          </template>
        </el-table-column>
      </el-table>
    </template>

    <!-- ── WRITE：创建/编辑 ── -->
    <template v-if="mode === 'write'">
      <el-card shadow="never">
        <template #header>
          <span>{{ editingName ? '编辑规则: ' + editingName : '创建新规则' }}</span>
          <el-button v-if="editingName" size="small" style="margin-left:12px" @click="resetForm">取消编辑</el-button>
        </template>

        <el-form :model="form" label-width="100px" label-position="top" :rules="formRules" ref="formRef">
          <el-row :gutter="20">
            <el-col :span="8">
              <el-form-item label="规则名称" prop="name">
                <el-input v-model="form.name" placeholder="唯一名称，如 门口闯入检测" :disabled="!!editingName" />
              </el-form-item>
            </el-col>
            <el-col :span="8">
              <el-form-item label="规则类型" prop="rule_type">
                <el-select v-model="form.rule_type" placeholder="选择类型" @change="onTypeChange">
                  <el-option label="区域闯入 (object_in_zone)" value="object_in_zone" />
                  <el-option label="计数线 (count_line)" value="count_line" />
                  <el-option label="区域清空 (zone_empty)" value="zone_empty" />
                </el-select>
              </el-form-item>
            </el-col>
            <el-col :span="8">
              <el-form-item label="严重级别">
                <el-select v-model="form.severity">
                  <el-option label="严重 (critical)" value="critical" />
                  <el-option label="警告 (warning)" value="warning" />
                  <el-option label="通知 (info)" value="info" />
                </el-select>
              </el-form-item>
            </el-col>
          </el-row>

          <el-form-item label="描述">
            <el-input v-model="form.description" placeholder="规则用途说明（可选）" />
          </el-form-item>

          <el-form-item label="适用摄像头">
            <el-select v-model="form.camera_ids" multiple placeholder="不选 = 全部摄像头" clearable>
              <el-option v-for="c in cameras" :key="c.camera_id" :label="c.camera_id + ' (' + (c.current_fps || 0) + 'fps)'" :value="c.camera_id" />
            </el-select>
          </el-form-item>

          <!-- 条件参数 -->
          <el-divider content-position="left">条件参数</el-divider>

          <template v-if="form.rule_type === 'object_in_zone' || form.rule_type === 'zone_empty'">
            <el-form-item label="多边形顶点坐标">
              <el-input v-model="form.zone_text"
                placeholder='[[100,200],[300,200],[300,400],[100,400]]'
                type="textarea" :rows="3" />
              <div class="form-hint">JSON 数组格式，至少 3 个顶点。示例：[[100,200],[300,200],[300,400],[100,400]]</div>
            </el-form-item>
          </template>

          <template v-if="form.rule_type === 'count_line'">
            <el-row :gutter="12">
              <el-col :span="12">
                <el-form-item label="线段起点 [x,y]">
                  <el-input v-model="form.line_start" placeholder="100,300" />
                </el-form-item>
              </el-col>
              <el-col :span="12">
                <el-form-item label="线段终点 [x,y]">
                  <el-input v-model="form.line_end" placeholder="500,300" />
                </el-form-item>
              </el-col>
            </el-row>
            <el-form-item label="触发阈值">
              <el-input-number v-model="form.threshold" :min="1" :max="9999" />
              <span class="form-hint" style="margin-left:8px">累计穿越次数达到阈值时触发告警</span>
            </el-form-item>
          </template>

          <el-form-item label="目标类别">
            <el-select v-model="form.target_classes" multiple placeholder="不选 = 所有类别" clearable allow-create>
              <el-option label="person" value="person" />
              <el-option label="car" value="car" />
              <el-option label="truck" value="truck" />
              <el-option label="bicycle" value="bicycle" />
              <el-option label="motorcycle" value="motorcycle" />
              <el-option label="dog" value="dog" />
              <el-option label="cat" value="cat" />
            </el-select>
          </el-form-item>

          <!-- 防线配置 -->
          <el-divider content-position="left">防线配置</el-divider>

          <el-row :gutter="20">
            <el-col :span="8">
              <el-form-item label="滑动窗口（帧）">
                <el-input-number v-model="form.window_size" :min="1" :max="100" />
                <span class="form-hint">连续 N 帧触发才放行</span>
              </el-form-item>
            </el-col>
            <el-col :span="8">
              <el-form-item label="冷却时间（秒）">
                <el-input-number v-model="form.cooldown" :min="0" :max="86400" />
                <span class="form-hint">同一规则两次告警的最小间隔</span>
              </el-form-item>
            </el-col>
            <el-col :span="8">
              <el-form-item label="启用">
                <el-switch v-model="form.enabled" active-text="启用" inactive-text="停用" />
              </el-form-item>
            </el-col>
          </el-row>

          <!-- 动作 -->
          <el-divider content-position="left">触发动作</el-divider>

          <el-checkbox-group v-model="form.actions">
            <el-checkbox value="record_clip" label="record_clip">录制视频片段</el-checkbox>
            <el-checkbox value="llm_analyze" label="llm_analyze">LLM 智能分析</el-checkbox>
            <el-checkbox value="notify" label="notify">推送通知</el-checkbox>
          </el-checkbox-group>

          <div style="margin-top: 24px">
            <el-button type="primary" :loading="saving" @click="handleSave">
              {{ editingName ? '更新规则' : '创建规则' }}
            </el-button>
            <el-button @click="handleTestCurrent" :loading="testing">先测试一下</el-button>
            <el-button @click="resetForm" v-if="editingName">取消</el-button>
          </div>
        </el-form>
      </el-card>
    </template>

    <!-- ── TEST：干跑校验 ── -->
    <template v-if="mode === 'test'">
      <el-card shadow="never">
        <template #header>测试规则（干跑校验 — 不写文件、不影响引擎）</template>
        <div class="test-hint">
          输入 JSON 格式的规则配置，点击测试校验语法和参数。通过后可以切换到「写」模式保存。
        </div>
        <el-input v-model="testInput" type="textarea" :rows="12"
          placeholder='{
  "name": "测试规则",
  "conditions": [{
    "type": "object_in_zone",
    "params": {
      "zone": [[100,200],[300,200],[300,400],[100,400]],
      "target_classes": ["person"]
    }
  }],
  "camera_ids": ["cam_01"],
  "severity": "warning",
  "cooldown": 300,
  "window_size": 5,
  "actions": [{"type": "notify"}, {"type": "record_clip"}]
}' />

        <div style="margin-top: 12px">
          <el-button type="primary" :loading="testing" @click="handleTest">校验规则</el-button>
          <el-button @click="loadExample">加载示例</el-button>
        </div>

        <el-alert v-if="testResult" :title="testResult.message"
          :type="testResult.valid ? 'success' : 'error'" :closable="true"
          style="margin-top: 16px" show-icon>
          <template v-if="testResult.valid && testResult.params" #default>
            <div class="test-result">
              <p><strong>类型：</strong>{{ typeLabel(testResult.rule_type || '') }}</p>
              <p><strong>参数：</strong></p>
              <pre>{{ JSON.stringify(testResult.params, null, 2) }}</pre>
              <p><strong>动作：</strong>{{ testResult.actions?.join(', ') || '-' }}</p>
            </div>
          </template>
        </el-alert>
      </el-card>
    </template>

    <!-- ── 详情对话框 ── -->
    <el-dialog v-model="detailVisible" :title="'规则详情: ' + detailRule?.name" width="700px" destroy-on-close>
      <template v-if="loadingDetail">加载中...</template>
      <template v-else-if="detail">
        <el-descriptions :column="2" border size="small">
          <el-descriptions-item label="名称">{{ detail.config?.name }}</el-descriptions-item>
          <el-descriptions-item label="文件名">{{ detail.filename }}</el-descriptions-item>
          <el-descriptions-item label="类型">{{ typeLabel(detail.config?.conditions?.[0]?.type || '') }}</el-descriptions-item>
          <el-descriptions-item label="严重级别">{{ severityLabel(detail.config?.severity || '') }}</el-descriptions-item>
          <el-descriptions-item label="冷却时间">{{ detail.config?.cooldown ?? '-' }}s</el-descriptions-item>
          <el-descriptions-item label="滑动窗口">{{ detail.config?.window_size ?? '-' }} 帧</el-descriptions-item>
          <el-descriptions-item label="启用">{{ detail.config?.enabled !== false ? '是' : '否' }}</el-descriptions-item>
          <el-descriptions-item label="文件路径" :span="2">{{ detail.filepath }}</el-descriptions-item>
        </el-descriptions>

        <el-divider content-position="left">YAML 原文</el-divider>
        <el-input :model-value="detail.yaml_raw" type="textarea" :rows="12" readonly style="font-family:monospace" />
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted, computed } from 'vue'
import { ElMessage } from 'element-plus'
import type { FormInstance } from 'element-plus'
import { rulesApi, type RuleSummary, type RuleDetail, type RuleCreatePayload } from '@/api/rules'
import { getCameras } from '@/api/cameras'

// ─── 模式 ──────────────────────────────────────────────────────

const mode = ref<'check' | 'write' | 'test'>('check')

// ─── CHECK 模式 ─────────────────────────────────────────────────

const rules = ref<RuleSummary[]>([])
const loading = ref(false)

async function fetchRules() {
  loading.value = true
  try {
    rules.value = await rulesApi.list()
  } catch (e: any) {
    ElMessage.error('加载规则失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    loading.value = false
  }
}

// ─── DETAIL 对话框 ──────────────────────────────────────────────

const detailVisible = ref(false)
const detail = ref<RuleDetail | null>(null)
const detailRule = ref<RuleSummary | null>(null)
const loadingDetail = ref(false)

async function viewDetail(row: RuleSummary) {
  detailRule.value = row
  detailVisible.value = true
  loadingDetail.value = true
  try {
    detail.value = await rulesApi.get(row.name)
  } catch (e: any) {
    ElMessage.error('加载详情失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    loadingDetail.value = false
  }
}

// ─── WRITE 模式 ─────────────────────────────────────────────────

interface RuleForm {
  name: string
  description: string
  rule_type: string
  severity: string
  camera_ids: string[]
  zone_text: string
  line_start: string
  line_end: string
  threshold: number
  target_classes: string[]
  window_size: number
  cooldown: number | null
  enabled: boolean
  actions: string[]
}

const defaultForm = (): RuleForm => ({
  name: '',
  description: '',
  rule_type: 'object_in_zone',
  severity: 'warning',
  camera_ids: [],
  zone_text: '[[100,200],[300,200],[300,400],[100,400]]',
  line_start: '100,300',
  line_end: '500,300',
  threshold: 1,
  target_classes: [],
  window_size: 5,
  cooldown: 300,
  enabled: true,
  actions: ['notify'],
})

const form = reactive<RuleForm>(defaultForm())
const editingName = ref<string | null>(null)
const saving = ref(false)
const testing = ref(false)
const formRef = ref<FormInstance>()

const formRules = {
  name: [{ required: true, message: '请输入规则名称', trigger: 'blur' }],
  rule_type: [{ required: true, message: '请选择规则类型', trigger: 'change' }],
}

// 从详情中拉取的其他数据
const cameras = ref<any[]>([])

onMounted(async () => {
  await fetchRules()
  try { cameras.value = await getCameras() } catch (_) { /* ignore */ }
})

function onTypeChange() {
  // 切换类型时重置条件参数为默认值
  if (form.rule_type === 'object_in_zone' || form.rule_type === 'zone_empty') {
    form.zone_text = '[[100,200],[300,200],[300,400],[100,400]]'
  } else if (form.rule_type === 'count_line') {
    form.line_start = '100,300'
    form.line_end = '500,300'
    form.threshold = 1
  }
}

function openCreate() {
  resetForm()
  mode.value = 'write'
}

async function editRule(row: RuleSummary) {
  resetForm()
  editingName.value = row.name
  mode.value = 'write'

  // 先加载完整详情，避免条件参数丢失
  try {
    const detail = await rulesApi.get(row.name)
    const cfg = detail.config
    const params = cfg.conditions?.[0]?.params || {}

    form.name = cfg.name || row.name
    form.description = cfg.description || ''
    form.rule_type = cfg.conditions?.[0]?.type || 'object_in_zone'
    form.severity = cfg.severity || 'warning'
    form.camera_ids = cfg.camera_ids || []
    form.window_size = cfg.window_size || 5
    form.cooldown = cfg.cooldown ?? null
    form.enabled = cfg.enabled !== false
    form.actions = (cfg.actions || []).map((a: any) => typeof a === 'string' ? a : a.type)
    form.target_classes = params.target_classes || []

    // zone 坐标
    if (params.zone && Array.isArray(params.zone)) {
      form.zone_text = JSON.stringify(params.zone)
    }
    // 线段参数
    if (params.line_start && Array.isArray(params.line_start)) {
      form.line_start = params.line_start.join(',')
    }
    if (params.line_end && Array.isArray(params.line_end)) {
      form.line_end = params.line_end.join(',')
    }
    if (typeof params.threshold === 'number') {
      form.threshold = params.threshold
    }
  } catch (e: any) {
    ElMessage.error('加载规则详情失败: ' + (e.response?.data?.detail || e.message))
  }
}

function resetForm() {
  Object.assign(form, defaultForm())
  editingName.value = null
}

// ─── 构建 payload ───────────────────────────────────────────────

function buildPayload(): RuleCreatePayload {
  const conditions: any[] = []
  const params: any = {}

  if (form.target_classes.length > 0) {
    params.target_classes = form.target_classes
  }

  if (form.rule_type === 'object_in_zone' || form.rule_type === 'zone_empty') {
    try {
      params.zone = JSON.parse(form.zone_text || '[]')
    } catch {
      params.zone = []
    }
  }

  if (form.rule_type === 'count_line') {
    params.line_start = (form.line_start || '').split(',').map(Number)
    params.line_end = (form.line_end || '').split(',').map(Number)
    params.threshold = form.threshold
  }

  conditions.push({ type: form.rule_type, params })

  const payload: RuleCreatePayload = {
    name: form.name,
    conditions,
    severity: form.severity,
    actions: form.actions.map(a => ({ type: a })),
    cooldown: form.cooldown,
    window_size: form.window_size,
    enabled: form.enabled,
    description: form.description || undefined,
    camera_ids: form.camera_ids.length > 0 ? form.camera_ids : null,
  }

  return payload
}

// ─── 保存 ──────────────────────────────────────────────────────

async function handleSave() {
  const valid = await formRef.value?.validate().catch(() => false)
  if (!valid) return

  saving.value = true
  try {
    const payload = buildPayload()
    if (editingName.value) {
      await rulesApi.update(editingName.value, payload)
      ElMessage.success('规则已更新，5 秒内热加载生效')
    } else {
      await rulesApi.create(payload)
      ElMessage.success('规则已创建，5 秒内热加载生效')
    }
    editingName.value = null
    resetForm()
    mode.value = 'check'
    await fetchRules()
  } catch (e: any) {
    ElMessage.error('保存失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    saving.value = false
  }
}

// ─── 删除 ──────────────────────────────────────────────────────

async function handleDelete(name: string) {
  try {
    await rulesApi.delete(name)
    ElMessage.success('规则已删除')
    await fetchRules()
  } catch (e: any) {
    ElMessage.error('删除失败: ' + (e.response?.data?.detail || e.message))
  }
}

// ─── TEST 模式 ──────────────────────────────────────────────────

const testInput = ref('')
const testResult = ref<{ valid: boolean; rule_type?: string; params?: any; actions?: string[]; message: string } | null>(null)

function loadExample() {
  testInput.value = JSON.stringify({
    name: '测试规则',
    conditions: [{
      type: 'object_in_zone',
      params: {
        zone: [[100, 200], [300, 200], [300, 400], [100, 400]],
        target_classes: ['person'],
      },
    }],
    camera_ids: ['cam_01'],
    severity: 'warning',
    cooldown: 300,
    window_size: 5,
    actions: [{ type: 'notify' }, { type: 'record_clip' }],
  }, null, 2)
}

async function handleTest() {
  testing.value = true
  testResult.value = null
  try {
    const payload = JSON.parse(testInput.value)
    const result = await rulesApi.test(payload)
    testResult.value = result
  } catch (e: any) {
    const detail = e.response?.data?.detail
    if (typeof detail === 'string') {
      testResult.value = { valid: false, message: '校验失败: ' + detail }
    } else {
      testResult.value = { valid: false, message: '解析失败: ' + (e.message || 'JSON 格式错误') }
    }
  } finally {
    testing.value = false
  }
}

async function handleTestCurrent() {
  // 将当前表单配置填入 test 模式
  const payload = buildPayload()
  testInput.value = JSON.stringify(payload, null, 2)
  mode.value = 'test'
  await handleTest()
}

// ─── 标签 ──────────────────────────────────────────────────────

function typeLabel(t: string) {
  const m: Record<string, string> = {
    object_in_zone: '区域闯入',
    count_line: '计数线',
    zone_empty: '区域清空',
  }
  return m[t] || t
}

function typeTag(t: string) {
  const m: Record<string, string> = {
    object_in_zone: 'warning',
    count_line: 'info',
    zone_empty: 'danger',
  }
  return m[t] || 'info'
}

function severityLabel(s: string) {
  const m: Record<string, string> = {
    critical: '严重',
    warning: '警告',
    info: '通知',
  }
  return m[s] || s
}

function severityTag(s: string) {
  const m: Record<string, string> = {
    critical: 'danger',
    warning: 'warning',
    info: 'info',
  }
  return m[s] || 'info'
}

function actionLabel(a: string) {
  const m: Record<string, string> = {
    notify: '通知',
    record_clip: '录像',
    llm_analyze: 'LLM',
  }
  return m[a] || a
}
</script>

<style lang="scss" scoped>
.rules-page {
  padding: 20px;
}

.page-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;

  h2 { margin: 0; font-size: 20px; }
}

.mode-tabs {
  margin-bottom: 16px;
}

.form-hint {
  color: #999;
  font-size: 12px;
  margin-top: 2px;
}

.test-hint {
  color: #666;
  margin-bottom: 12px;
  font-size: 13px;
}

.test-result {
  pre {
    background: #f5f7fa;
    padding: 8px 12px;
    border-radius: 4px;
    font-size: 12px;
    margin: 4px 0;
  }
  p { margin: 4px 0; }
}

.text-muted {
  color: #bbb;
  font-size: 12px;
}
</style>
