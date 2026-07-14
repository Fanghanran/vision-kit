# 管理员控制面板 V2 — 按模块分组设计书

## 1. 设计目标

将扁平的控制项列表改为**按模块分组的独立面板**，每个模块有自己的子页面/卡片，包含：
- 模块级总开关
- 模块内细粒度控制项
- 模块状态指标（运行状态、错误计数等）
- 模块相关配置查看

---

## 2. 模块分组方案

### 2.1 侧边栏结构

```
👤 用户管理
📋 规则
📹 摄像头
🖥️ 系统
  ├── 系统监控（当前 System.vue）
  ├── 🔌 LLM 模块 ← 独立页面
  ├── 📧 通知模块 ← 独立页面
  ├── 🎥 录制模块 ← 独立页面
  ├── 🔄 规则引擎 ← 独立页面
  └── 📝 审计日志（已有 /audit）
```

### 2.2 各模块控制面板内容

---

#### 📄 LLM 模块（`/system/llm`）

| 控制项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `llm.enabled` | 开关 | true | 模块总开关 |
| `llm.model` | 只读 | gpt-4o-mini | 当前模型（来自配置） |
| `llm.api_base` | 只读 | - | API 地址（脱敏） |
| `llm.timeout` | 数字 | 30 | 请求超时（秒） |
| `llm.monthly_budget` | 只读 | 100 | 月度预算（美元） |
| `llm.cache_enabled` | 开关 | true | 响应缓存 |

**状态指标**：
- 今日调用次数
- 成功率（最近 1 小时）
- 当月已用金额 / 预算
- 断路器状态（关闭/打开/半开）

---

#### 📧 通知模块（`/system/notification`）

| 控制项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `notification.webhook.enabled` | 开关 | false | Webhook 通知 |
| `notification.webhook.type` | 只读 | wechat | 类型（企微/钉钉） |
| `notification.email.enabled` | 开关 | false | 邮件通知 |
| `notification.email.smtp_host` | 只读 | - | SMTP 服务器 |

**状态指标**：
- 今日发送数
- 发送成功率
- 最近一次发送时间

---

#### 🎥 录制模块（`/system/recording`）

| 控制项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `recording.enabled` | 开关 | true | 模块总开关 |
| `recording.buffer_duration` | 只读 | 30 | 环形缓冲时长（秒） |
| `recording.default_before` | 只读 | 15 | 告警前截取秒数 |
| `recording.default_after` | 只读 | 15 | 告警后截取秒数 |
| `recording.retention_days` | 只读 | 7 | 视频保留天数 |
| `recording.snapshot_retention_days` | 只读 | 30 | 截图保留天数 |

**状态指标**：
- 磁盘占用（截图 + 视频）
- 今日录制片段数
- 缓冲区状态（各路帧数）

---

#### 🔄 规则引擎（`/system/rules`）

| 控制项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `rules.hot_reload` | 开关 | true | 规则热重载 |
| `rules.default_window_size` | 只读 | 5 | 默认滑动窗口（帧） |
| `rules.default_cooldown` | 只读 | 300 | 默认冷却时间（秒） |

**状态指标**：
- 已加载规则数
- 启用 / 禁用规则数
- 今日触发次数（按规则分）

---

#### 📹 摄像头模块（`/system/cameras`）

| 控制项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `camera.auto_reconnect` | 开关 | true | 自动重连 |
| `websocket.enabled` | 开关 | true | WebSocket 推送 |

**状态指标**：
- 在线 / 离线 / 降级摄像头数
- 各路 FPS
- 帧队列积压

---

#### 🖥️ 系统基础（`/system`，当前 System.vue）

保留当前系统监控页面，新增：

| 控制项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `audit.enabled` | 开关 | true | 审计日志 |

**状态指标**：
- GPU 状态（使用率/显存）
- 推理延迟 P50/P99
- 系统运行时长
- 队列积压

---

## 3. 路由设计

```
/system              → 系统基础监控（当前 System.vue）
/system/llm          → LLM 模块控制面板
/system/notification → 通知模块控制面板
/system/recording    → 录制模块控制面板
/system/rules        → 规则引擎控制面板
/system/cameras      → 摄像头模块控制面板
/audit               → 审计日志（已有）
```

### 侧边栏菜单结构

```vue
<!-- 系统模块折叠菜单 -->
<el-sub-menu v-if="authStore.isAdmin" index="system-group">
  <template #title>
    <el-icon><Monitor /></el-icon>
    <span>系统管理</span>
  </template>
  <el-menu-item index="/system">系统监控</el-menu-item>
  <el-menu-item index="/system/llm">LLM 模块</el-menu-item>
  <el-menu-item index="/system/notification">通知模块</el-menu-item>
  <el-menu-item index="/system/recording">录制模块</el-menu-item>
  <el-menu-item index="/system/rules">规则引擎</el-menu-item>
  <el-menu-item index="/system/cameras">摄像头模块</el-menu-item>
</el-sub-menu>
<el-menu-item v-if="authStore.isAdmin" index="/audit">
  <el-icon><Document /></el-icon>
  <template #title>审计日志</template>
</el-menu-item>
```

---

## 4. 每个模块页面通用布局

```
┌─────────────────────────────────────────────────────────────┐
│ 模块名称                          [模块总开关 ON/OFF]        │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌─── 状态指标卡片 ──────────────────────────────────────┐ │
│  │  [指标1]  [指标2]  [指标3]  [指标4]                    │ │
│  └────────────────────────────────────────────────────────┘ │
│                                                             │
│  ┌─── 控制项列表 ────────────────────────────────────────┐ │
│  │  控制项1: [开关/数字/只读]                              │ │
│  │  控制项2: [开关/数字/只读]                              │ │
│  │  ...                                                    │ │
│  └────────────────────────────────────────────────────────┘ │
│                                                             │
│  ┌─── 配置详情（只读）────────────────────────────────────┐ │
│  │  当前配置快照（脱敏）                                   │ │
│  └────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
```

---

## 5. 后端 API 设计

### 5.1 复用现有端点

`GET /api/system/controls` 返回所有控制项，前端按模块分组展示。

### 5.2 新增模块状态端点

| 端点 | 说明 |
|------|------|
| `GET /api/system/modules/llm/status` | LLM 模块状态（调用次数、成功率、断路器） |
| `GET /api/system/modules/notification/status` | 通知模块状态（发送数、成功率） |
| `GET /api/system/modules/recording/status` | 录制模块状态（磁盘占用、片段数） |
| `GET /api/system/modules/rules/status` | 规则引擎状态（规则数、触发次数） |
| `GET /api/system/modules/cameras/status` | 摄像头模块状态（在线数、FPS） |

### 5.3 端点实现

```python
@app.get("/api/system/modules/{module}/status")
async def get_module_status(module: str, user = Depends(_require_role(Role.ADMIN))):
    # 根据 module 返回对应模块的运行状态
    ...
```

---

## 6. 前端文件结构

```
frontend/src/
├── views/
│   ├── System.vue              ← 系统基础监控（保留）
│   ├── system/                 ← 新建目录
│   │   ├── SystemLLM.vue       ← LLM 模块
│   │   ├── SystemNotify.vue    ← 通知模块
│   │   ├── SystemRecording.vue ← 录制模块
│   │   ├── SystemRules.vue     ← 规则引擎
│   │   └── SystemCameras.vue   ← 摄像头模块
│   └── Audit.vue               ← 审计日志（已有）
├── components/
│   └── system/
│       ├── ModuleStatusCard.vue ← 通用状态指标卡片组件
│       └── ControlSwitch.vue    ← 通用控制开关组件（带回滚）
```

---

## 7. 通用组件设计

### 7.1 ModuleStatusCard

```vue
<props>
  title: string          // 指标标题
  value: number | string // 指标值
  icon: string           // 图标
  color: string          // 颜色
  trend?: number         // 趋势百分比
</props>
```

### 7.2 ControlSwitch

```vue
<props>
  label: string          // 控制项名称
  description: string    // 说明文字
  modelValue: boolean    // 当前值
  apiPath: string        // PUT 接口路径
</props>

<events>
  @update:modelValue     // 切换成功后触发
  @error                 // 保存失败时触发（已自动回滚）
</events>
```

内置：保存失败自动回滚 + 错误提示。

---

## 8. 与现有代码的关系

| 现有文件 | 处理方式 |
|----------|---------|
| `System.vue` | 保留为系统基础监控页，移除控制面板卡片 |
| `Audit.vue` | 保持不变 |
| `AppSidebar.vue` | 改用 `el-sub-menu` 折叠菜单 |
| `router/index.ts` | 新增 5 条子路由 |
| `database.py` | 无需改动（`system_controls` 表已支持） |
| `app.py` | 新增模块状态端点 |

---

## 9. 实施计划

| 阶段 | 内容 | 工作量 |
|------|------|--------|
| Phase 1 | 通用组件（ModuleStatusCard + ControlSwitch） | 1h |
| Phase 2 | 5 个模块页面 + 路由 | 3h |
| Phase 3 | 后端模块状态端点 | 2h |
| Phase 4 | 侧边栏折叠菜单 + System.vue 瘦身 | 1h |
| Phase 5 | 测试 | 1h |

---

## 10. 数据流

```
页面加载
  ↓
GET /api/system/controls → 获取所有控制项值
  ↓
按 module 分组过滤
  ↓
GET /api/system/modules/{module}/status → 获取模块状态指标
  ↓
渲染模块控制面板

用户切换开关
  ↓
PUT /api/system/controls/{key} → 更新单个控制项
  ↓
成功 → 更新本地状态
失败 → 回滚开关 + 错误提示
```
