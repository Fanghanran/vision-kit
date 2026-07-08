# 优化清单

> 记录待实现的优化方向，按优先级排序。

## 1. 全局配置选择性热加载

**状态**：待实现
**优先级**：中
**影响**：运维体验

### 问题

当前 `settings.yaml` 修改后必须重启进程才能生效，运维不便。

### 方案

全局配置热加载时，只更新**安全字段**，跳过**不可变字段**（记录 WARNING 提示重启）。

| 字段 | 能热加载？ | 原因 |
|------|-----------|------|
| `system.log_level` | ✅ | 只改日志过滤，无副作用 |
| `llm.*` | ✅ | 下次调用时生效 |
| `notification.*` | ✅ | 下次通知时生效 |
| `rules.*` | ✅ | 规则引擎已支持热重载 |
| `gpu.device_id` | ❌ | 需要重新初始化 CUDA |
| `detector.model_path` | ❌ | 需要重新加载模型 |
| `web.port` | ❌ | 需要重启 uvicorn |
| `storage.*` | ❌ | 需要重新建连接 |

### 实现要点

- ConfigManager 监听 settings.yaml 的 mtime
- 变化时解析新配置，对比旧配置
- 安全字段直接更新，不可变字段记录 WARNING
- 通知下游模块（通过 watcher 回调）

---

## 2. 视频监控面板

**状态**：设计完成，待实现
**优先级**：高
**影响**：核心功能

### 设计文档

详见 `docs/frontend/MONITOR_PANEL.md`

### 核心功能

- 可调整布局（1×1 / 2×2 / 3×3 / 4×4）
- 实时视频流（WebSocket JPEG 推送）
- 检测框/轨迹叠加
- 历史回放 + 时间轴
- 拖拽分配摄像头

### 后端新增 API

| 端点 | 说明 |
|------|------|
| `/ws/video/{id}` | WebSocket JPEG 实时帧 |
| `/api/cameras/{id}/replay` | 历史录像 MP4 |
| `/api/cameras/{id}/timeline` | 有录像的时间段 |

---

## 3. 用户角色系统

**状态**：待设计
**优先级**：高
**影响**：安全

### 需求

- 用户名/密码登录
- JWT Token 认证
- 角色权限控制（admin / operator / viewer）
- WebSocket 连接认证

### 角色权限矩阵

| 操作 | admin | operator | viewer |
|------|-------|----------|--------|
| 查看告警 | ✅ | ✅ | ✅ |
| 确认/标记误报 | ✅ | ✅ | ❌ |
| 修改配置 | ✅ | ❌ | ❌ |
| 管理用户 | ✅ | ❌ | ❌ |
| 查看摄像头 | ✅ | ✅ | ✅ |
| 控制摄像头 | ✅ | ✅ | ❌ |

---

## 4. WebSocket 端点兼容性修复

**状态**：待修复
**优先级**：中
**影响**：实时推送功能

### 问题

WebSocket 端点 `/ws` 在 uvicorn 中返回 403，疑似 uvicorn WebSocket 升级兼容性问题。

### 可能原因

- uvicorn 版本与 websockets 库版本不兼容
- HTTP 中间件干扰 WebSocket 升级
- FastAPI WebSocket 参数解析问题

### 修复方向

- 升级 uvicorn[standard]
- 测试不同 websockets 库版本
- 移除可能干扰的中间件

---

## 5. Docker 部署

**状态**：待实现
**优先级**：低
**影响**：部署体验

### 需求

- Dockerfile（后端 + 前端）
- docker-compose.yml（后端 + 前端 + Redis 可选）
- 环境变量配置
- 数据卷持久化

---

## 6. 性能优化

**状态**：待分析
**优先级**：低
**影响**：大规模部署

### 方向

- GPU 推理 batch 优化
- 数据库查询索引优化
- 前端虚拟滚动（万级告警列表）
- WebSocket 消息压缩
