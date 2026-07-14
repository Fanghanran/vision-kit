# 管理员控制面板 — 设计书

## 1. 模块职责

管理员控制面板提供系统级功能的开关控制，让管理员无需修改配置文件即可动态启停系统各功能模块。

核心定位：
- 作为系统运行时的"总开关面板"
- 所有开关变更即时生效（热重载），无需重启进程
- 变更记录到审计日志，可追溯
- 仅管理员可访问（`manage:config` 权限）

---

## 2. 可控功能清单

| 模块 | 配置项 | 默认值 | 说明 |
|------|--------|--------|------|
| **LLM 分析** | `llm.enabled` | `true` | LLM 智能分析开关。关闭后告警只包含规则引擎原始结果 |
| **通知渠道** | `notification.webhook.enabled` | `false` | Webhook 通知开关 |
| **通知渠道** | `notification.email.enabled` | `false` | 邮件通知开关 |
| **录制功能** | `recording.enabled` | `true` | 告警视频片段录制开关 |
| **规则热重载** | `rules.hot_reload` | `true` | 规则文件热重载开关 |
| **摄像头热重载** | `cameras.hot_reload` | `true` | 摄像头配置热重载开关 |
| **帧率限制** | `pipeline.max_fps` | `5` | 单路最大帧率（动态调节 GPU 负载） |
| **队列策略** | `pipeline.discard_policy` | `drop_oldest` | 队列满时策略：drop_oldest / drop_newest |
| **审计日志** | `audit.enabled` | `true` | 审计日志记录开关 |
| **WebSocket** | `websocket.enabled` | `true` | WebSocket 实时推送开关 |
| **摄像头自动重连** | `camera.auto_reconnect` | `true` | 断线后自动重连开关 |

---

## 3. 数据存储

控制面板状态持久化到 SQLite（与 auth.db 同库），进程重启后恢复。

### DDL

```sql
CREATE TABLE IF NOT EXISTS system_controls (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_by  TEXT DEFAULT '',
    updated_at  REAL NOT NULL
);
```

### 数据格式

```json
{
  "key": "llm.enabled",
  "value": "true",
  "updated_by": "admin",
  "updated_at": 1720000000.0
}
```

---

## 4. 对外接口

### 4.1 REST API

| 方法 | 路径 | 权限 | 说明 |
|------|------|------|------|
| `GET` | `/api/system/controls` | `manage:config` | 获取所有控制项 |
| `PUT` | `/api/system/controls/{key}` | `manage:config` | 更新单个控制项 |
| `PUT` | `/api/system/controls` | `manage:config` | 批量更新控制项 |

#### GET /api/system/controls

返回：
```json
{
  "controls": {
    "llm.enabled": { "value": true, "updated_by": "admin", "updated_at": 1720000000.0 },
    "notification.webhook.enabled": { "value": false, "updated_by": "", "updated_at": 0 },
    ...
  }
}
```

#### PUT /api/system/controls/{key}

请求体：
```json
{
  "value": true
}
```

响应：
```json
{
  "key": "llm.enabled",
  "value": true,
  "updated_by": "admin",
  "updated_at": 1720000000.0
}
```

#### PUT /api/system/controls（批量）

请求体：
```json
{
  "controls": {
    "llm.enabled": true,
    "notification.webhook.enabled": false
  }
}
```

---

## 5. 控制项生效机制

### 5.1 可热重载项（即时生效）

| 控制项 | 生效方式 |
|--------|---------|
| `llm.enabled` | LLMAnalyzer 检查标志位，False 时跳过分析 |
| `notification.webhook.enabled` | Notifier 检查标志位，False 时跳过发送 |
| `notification.email.enabled` | 同上 |
| `rules.hot_reload` | RuleEngine 热重载线程检查标志位 |
| `cameras.hot_reload` | ConfigManager 热重载线程检查标志位 |
| `recording.enabled` | ClipRecorder 检查标志位 |
| `websocket.enabled` | WSManager 检查标志位，False 时拒绝新连接 |
| `audit.enabled` | DatabaseManager.save_audit_log 检查标志位 |
| `camera.auto_reconnect` | CameraThread 重连循环检查标志位 |

### 5.2 需重启项（延迟生效）

| 控制项 | 说明 |
|--------|------|
| `pipeline.max_fps` | 需要重启摄像头线程才能生效 |
| `pipeline.discard_policy` | 需要重启队列才能生效 |

---

## 6. 前端设计

### 6.1 页面位置

在 System.vue 的"系统监控"标签页下方，新增"系统控制"卡片。

```
┌─────────────────────────────────────────────────────────────┐
│ 系统控制                                        [保存全部]  │
├─────────────────────────────────────────────────────────────┤
│ 🔌 LLM 分析          [开/关]  关闭后告警仅含规则引擎结果    │
│ 📧 Webhook 通知       [开/关]  企业微信/钉钉群机器人推送     │
│ ✉️ 邮件通知           [开/关]  SMTP 邮件告警                │
│ 🎥 告警录制           [开/关]  告警前后视频片段录制          │
│ 🔄 规则热重载         [开/关]  规则 YAML 变更后自动加载      │
│ 📹 摄像头热重载       [开/关]  摄像头配置变更后自动重载      │
│ 🔁 摄像头自动重连     [开/关]  断线后自动重连                │
│ 📝 审计日志           [开/关]  操作审计记录                  │
│ 🔌 WebSocket 推送     [开/关]  实时告警推送                  │
│ ⚡ 最大帧率           [5 fps]  单路摄像头最大帧率             │
│ 📦 队列策略           [下拉]   drop_oldest / drop_newest     │
└─────────────────────────────────────────────────────────────┘
```

### 6.2 交互设计

- 每个开关为 `el-switch`，切换即时调用 API
- 变更成功后显示 Toast 提示
- 变更记录写入审计日志
- "保存全部"按钮用于批量保存（减少 API 调用）
- 只读控制项（需重启）显示锁定图标 + tooltip 提示

---

## 7. 依赖关系

| 依赖模块 | 依赖方向 | 说明 |
|----------|----------|------|
| storage/database | 控制面板 → 数据库 | system_controls 表读写 |
| auth/manager | 控制面板 → 认证 | 权限检查（manage:config） |
| llm/analyzer | LLM → 控制面板 | 检查 llm.enabled 标志位 |
| actions/notifier | 通知 → 控制面板 | 检查 notification.* 标志位 |
| rules/engine | 规则引擎 → 控制面板 | 检查 rules.hot_reload 标志位 |
| core/recorder | 录制器 → 控制面板 | 检查 recording.enabled 标志位 |
| web/api/app | API → 控制面板 | REST 端点 + WebSocket 控制 |

---

## 8. 错误处理

| 错误场景 | 处理方式 |
|----------|---------|
| 控制项 key 不在白名单 | 返回 400，提示"不支持的控制项" |
| value 类型错误（应为 bool 给了 string） | 返回 422，提示"值类型错误" |
| 非管理员访问 | 返回 403 |
| 数据库写入失败 | 返回 500，记录日志 |
| 控制项读取失败 | 使用默认值（不阻断系统运行） |

---

## 9. 安全设计

- 所有控制项变更记录到审计日志（`audit_logs` 表）
- 仅 `manage:config` 权限可访问（admin 角色）
- 控制项 key 白名单校验，防止注入
- 敏感控制项（如 `llm.enabled`）变更时发送 WebSocket 通知所有管理员

---

## 10. 实施计划

| 阶段 | 内容 | 工作量 |
|------|------|--------|
| Phase 1 | 后端：`system_controls` 表 + CRUD API + 控制项白名单 | 2h |
| Phase 2 | 后端：各模块集成标志位检查（LLM/通知/录制/规则） | 3h |
| Phase 3 | 前端：System.vue 控制面板 UI | 2h |
| Phase 4 | 测试：控制项 CRUD + 生效验证 + 权限检查 | 1h |

---

## 11. 配置项初始值

系统首次启动时，从 `settings.yaml` 读取初始值写入 `system_controls` 表：

```python
_DEFAULT_CONTROLS = {
    "llm.enabled": True,
    "notification.webhook.enabled": False,
    "notification.email.enabled": False,
    "recording.enabled": True,
    "rules.hot_reload": True,
    "cameras.hot_reload": True,
    "camera.auto_reconnect": True,
    "websocket.enabled": True,
    "audit.enabled": True,
    "pipeline.max_fps": 5,
    "pipeline.discard_policy": "drop_oldest",
}
```

后续修改 `system_controls` 表中的值，不影响 `settings.yaml` 文件。
