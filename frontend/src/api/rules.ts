import client from './client'

// ─── 类型 ──────────────────────────────────────────────────────

export interface RuleSummary {
  name: string
  filename: string
  type: string
  camera_ids: string[] | null
  severity: string
  cooldown: number | null
  window_size: number | null
  enabled: boolean
  actions: string[]
  description: string
}

export interface RuleDetail {
  filename: string
  filepath: string
  config: RuleConfig
  yaml_raw: string
}

export interface RuleCondition {
  type: string                      // object_in_zone / count_line / zone_empty
  params: {
    zone?: number[][]               // 多边形顶点
    line_start?: number[]           // 计数线起点
    line_end?: number[]             // 计数线终点
    threshold?: number              // 计数阈值
    target_classes?: string[] | null
    [key: string]: any
  }
}

export interface RuleAction {
  type: string                      // notify / record_clip / llm_analyze
}

export interface RuleConfig {
  name: string
  description?: string
  conditions: RuleCondition[]
  camera_ids?: string[] | null
  severity?: string
  cooldown?: number | null
  window_size?: number | null
  time_windows?: { start: string; end: string; days: number[] }[] | null
  actions?: RuleAction[]
  enabled?: boolean
}

export interface RuleCreatePayload {
  name: string
  description?: string
  conditions: RuleCondition[]
  camera_ids?: string[] | null
  severity?: string
  cooldown?: number | null
  window_size?: number | null
  time_windows?: { start: string; end: string; days: number[] }[] | null
  actions?: RuleAction[]
  enabled?: boolean
}

export interface TestResult {
  valid: boolean
  rule_type: string
  params: Record<string, any>
  actions: string[]
  message: string
}

// ─── API ───────────────────────────────────────────────────────

export const rulesApi = {
  /** 列出所有规则 */
  list: () => client.get<RuleSummary[]>('/api/rules').then(r => r.data),

  /** 查看单条规则详情（含 YAML 原文） */
  get: (name: string) =>
    client.get<RuleDetail>(`/api/rules/${encodeURIComponent(name)}`).then(r => r.data),

  /** 创建规则 */
  create: (payload: RuleCreatePayload) =>
    client.post<{ message: string; name: string; filepath: string }>('/api/rules', payload).then(r => r.data),

  /** 更新规则 */
  update: (name: string, payload: RuleCreatePayload) =>
    client.put<{ message: string; name: string; filepath: string }>(
      `/api/rules/${encodeURIComponent(name)}`, payload,
    ).then(r => r.data),

  /** 删除规则 */
  delete: (name: string) =>
    client.delete<{ message: string; name: string }>(
      `/api/rules/${encodeURIComponent(name)}`,
    ).then(r => r.data),

  /** 测试规则（干跑校验，不写文件） */
  test: (payload: RuleCreatePayload) =>
    client.post<TestResult>('/api/rules/test', payload).then(r => r.data),
}
