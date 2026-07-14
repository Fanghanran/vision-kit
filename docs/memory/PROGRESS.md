# SentinelMind — 开发进度

> 最后更新: 2026-07-14

## 项目概况

- **定位**：多路视频智能分析框架（看懂→想明白→做决定）
- **原名**：Vision Agent（2026-07-14 改名为 SentinelMind）
- **仓库**：https://github.com/Fanghanran/vision-kit.git
- **作者**：Fang
- **技术栈**：Python 3.10+ / FastAPI / YOLO / Vue 3 / Element Plus / SQLite
- **测试**：287 个全部通过（P0~P4 + 补充项）

---

## 代码状态

### 第一版功能（P0~P4 全部完成）

| 批次 | 内容 | 状态 | 测试 |
|------|------|------|------|
| P0 | 全局 API 认证 + RBAC + WS 认证 | ✅ | 23 个 |
| P0-v2 | Token 持久化多设备 | ✅ | 8 个 |
| P1 | WS 重连+心跳+提示音+操作历史+审计日志 | ✅ | 16 个 |
| P2 | 深色模式+通知设置+趋势对比+事件过滤+强制改密 | ✅ | 0 个 |
| P3 | URL 同步+缩略图+Token 刷新+用户注册 | ✅ | 0 个 |
| P4 | 管理员控制面板（按模块分组） | ✅ | 17 个 |
| 补充 | BroadcastChannel+后端集成控制项+启动检查+配置示例 | ✅ | 0 个 |

### 后端模块（15 个）

| 模块 | 文件 | 状态 | 说明 |
|------|------|------|------|
| core/types.py | `src/sentinelmind/core/types.py` | ✅ | Detection/Track/Event/Alert 数据模型 |
| core/camera.py | `src/sentinelmind/core/camera.py` | ✅ | FFmpeg 采集 + 自动重连 + 控制项检查 |
| core/detector.py | `src/sentinelmind/core/detector.py` | ✅ | YOLO 批量推理 + FP16 |
| core/tracker.py | `src/sentinelmind/core/tracker.py` | ✅ | BoT-SORT 多目标追踪 |
| core/recorder.py | `src/sentinelmind/core/recorder.py` | ✅ | 环形缓冲 + FFmpeg 转码 + 控制项检查 |
| core/pipeline.py | `src/sentinelmind/core/pipeline.py` | ✅ | 三层线程编排 + 优雅关闭 |
| core/exceptions.py | `src/sentinelmind/core/exceptions.py` | ✅ | 分层异常体系 |
| config/settings.py | `src/sentinelmind/config/settings.py` | ✅ | YAML 配置 + 热重载 + 环境变量 |
| rules/engine.py | `src/sentinelmind/rules/engine.py` | ✅ | 三层防线 + 控制项检查 |
| rules/manager.py | `src/sentinelmind/rules/manager.py` | ✅ | 规则 CRUD |
| storage/database.py | `src/sentinelmind/storage/database.py` | ✅ | SQLite + 审计日志 + 操作历史 + 控制项表 |
| storage/cache.py | `src/sentinelmind/storage/cache.py` | ✅ | MemoryCache + RedisCache |
| llm/analyzer.py | `src/sentinelmind/llm/analyzer.py` | ✅ | LLM 分析 + RAG 预留 + 控制项检查 |
| llm/provider.py | `src/sentinelmind/llm/provider.py` | ✅ | 断路器 + 重试 + 预算 |
| actions/notifier.py | `src/sentinelmind/actions/notifier.py` | ✅ | Webhook + 邮件 + 控制项检查 |
| web/api/app.py | `src/sentinelmind/web/api/app.py` | ✅ | REST API + WS + 全局认证 + RBAC + 审计 |
| web/api/rules.py | `src/sentinelmind/web/api/rules.py` | ✅ | 规则管理 API |
| auth/models.py | `src/sentinelmind/auth/models.py` | ✅ | User/Role/UserStatus |
| auth/manager.py | `src/sentinelmind/auth/manager.py` | ✅ | 用户 CRUD + Token 持久化 + 登录历史 |
| __main__.py | `src/sentinelmind/__main__.py` | ✅ | CLI 入口 + 启动环境检查 |

### 前端页面（10 个）

| 页面 | 路由 | 状态 | 说明 |
|------|------|------|------|
| Dashboard | `/` | ✅ | 统计卡片+趋势图+实时告警流+饼图+柱状图 |
| AlertList | `/alerts` | ✅ | 分页筛选+URL 同步 |
| AlertDetail | `/alerts/:id` | ✅ | 截图+LLM 分析+状态操作 |
| Cameras | `/cameras` | ✅ | 卡片网格+详情抽屉 |
| Monitor | `/monitor` | ✅ | 1×1~4×4 视频网格+WebSocket 推流 |
| Rules | `/rules` | ✅ | 三模式：查/写/测 |
| System | `/system` | ✅ | GPU/运行状态/推理性能/配置 |
| Audit | `/audit` | ✅ | 审计日志+控制项开关（管理员） |
| Login | `/login` | ✅ | 用户名密码登录 |
| Register | `/register` | ✅ | 自助注册（需开启配置） |
| ChangePassword | `/change-password` | ✅ | 首次登录强制改密 |
| Profile | `/profile` | ✅ | 个人设置+通知偏好+安全+头像 |
| Users | `/users` | ✅ | 用户管理（管理员） |
| System 子页面 | `/system/*` | ✅ | LLM/通知/录制/规则/摄像头模块控制 |

### 前端组件

| 目录 | 内容 |
|------|------|
| `components/layout/` | AppHeader + AppSidebar + AppFooter |
| `components/system/` | ControlSwitch + ModuleStatusCard |
| `components/monitor/` | VideoPlayer |
| `components/common/` | TokenDialog |
| `composables/` | useWebSocket + useVideoStream + useNotification + useMultiTabSync + useCountUp |
| `stores/` | auth + alerts + cameras + system |

---

## 安全体系

| 功能 | 状态 | 说明 |
|------|------|------|
| 全局 API 认证 | ✅ | `Depends(_require_permission(...))` |
| RBAC 三级权限 | ✅ | admin / operator / viewer |
| WS 认证 | ✅ | `?token=` 参数验证 |
| Token 持久化 | ✅ | SQLite `active_tokens` 表，多设备支持 |
| 日志脱敏 | ✅ | SanitizeFilter 自动掩码 |
| 路径白名单 | ✅ | `/api/*`、`/ws`、`/health`、`/static/` |
| 配置脱敏 | ✅ | `/api/config` 自动掩码密码/Key |
| 审计日志 | ✅ | 操作历史 + 审计日志表 |
| 启动环境检查 | ✅ | 目录权限、模型文件、GPU、FFmpeg |

---

## 控制面板

| 模块 | 控制项 | 默认值 |
|------|--------|--------|
| LLM 分析 | `llm.enabled` / `llm.cache_enabled` | true / true |
| 通知 | `notification.webhook.enabled` / `notification.email.enabled` | false / false |
| 录制 | `recording.enabled` | true |
| 规则 | `rules.hot_reload` | true |
| 摄像头 | `camera.auto_reconnect` / `websocket.enabled` | true / true |
| 审计 | `audit.enabled` | true |

---

## 第二版规划（详细设计已完成）

| 批次 | 内容 | 优先级 | 预估 |
|------|------|--------|------|
| **第一批** | systemd + 备份 + Docker | 高 | 3 天 |
| **第二批** | HTTPS + RAG | 中 | 4 天 |
| **第三批** | PostgreSQL + 端-云 | 低 | 7 天 |

设计书：`docs/V2_DEPLOYMENT_DESIGN.md`

---

## 关联项目

| 项目 | 仓库 | 状态 |
|------|------|------|
| **Nexus** | https://github.com/Fanghanran/nexus.git | 设计完成，代码待实现 |
| **SentinelMind** | https://github.com/Fanghanran/vision-kit.git | 第一版完成 |

---

## 工作流

```
1. 写代码（读设计书 → 写实现）
2. Review agent（审阅 + ruff check）
3. Test agent（测试）
4. 根据 Review 修代码
5. 确认测试通过
6. git commit + push（等用户指示）
```

---

| | |
|---|---|
| **文档版本** | v2.0 |
| **作者** | Fang |
| **最后更新** | 2026-07-14 |
