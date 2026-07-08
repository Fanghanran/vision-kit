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

## ~~5. Docker 部署~~（暂缓）

<!-- 后续版本实现

**状态**：待实现
**优先级**：低
**影响**：部署体验

### 需求

- Dockerfile（后端 + 前端）
- docker-compose.yml（后端 + 前端 + Redis 可选）
- 环境变量配置
- 数据卷持久化

-->

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

---

## 10. 摄像头管理功能

**状态**：待实现
**优先级**：高
**影响**：核心功能

### 需求

1. **开关摄像头**：可以暂停/恢复摄像头采集
2. **添加摄像头**：通过前端表单添加新摄像头
3. **删除摄像头**：移除已配置的摄像头

### 待确认

- 开关摄像头：是暂停采集（保留配置）还是停止线程？
- 添加摄像头：通过 Web 表单填写？还是上传 YAML 文件？
- 删除摄像头：软删除（标记禁用）还是硬删除（删配置文件）？
- 持久化：添加/删除后要不要写回 `configs/cameras/` 目录？

### 涉及文件

- 后端：`web/api/app.py`（新增 API）、`core/pipeline.py`（摄像头管理）
- 前端：`Cameras.vue`（管理 UI）、`api/cameras.ts`（API 调用）

---

## 7. FPS 计算精度

**状态**：待修复
**优先级**：低
**影响**：显示准确性

### 问题

FPS 计算结果保留小数位数不一致，应统一保留 1 位小数。

### 涉及文件

- `core/camera.py` — `_calculate_fps()` 方法

### 方案

```python
# 修改 _calculate_fps()
def _calculate_fps(self) -> float:
    if self._total_frames < 2 or not self._start_time:
        return 0.0
    elapsed = time.time() - self._start_time
    if elapsed <= 0:
        return 0.0
    return round(self._total_frames / elapsed, 1)
```

验证点：
- 刚启动时返回 0.0
- 运行一段时间后显示合理值（如 5.0）
- 重连后 FPS 重新计算

---

## 8. 前端卡顿

**状态**：待排查
**优先级**：高
**影响**：用户体验

### 问题

前端页面容易卡顿，可能原因：
- 图表（ECharts）频繁重绘
- WebSocket 消息处理导致 DOM 频繁更新
- 大量告警数据未做虚拟滚动
- 轮询间隔过短

### 优化方案

#### 8.1 图表优化

**问题**：Dashboard 的 3 个 ECharts 图表每 10 秒全量重绘。

**方案**：
- 使用 `chart.setOption(option, { notMerge: false })` 增量更新，不销毁重建
- 图表数据超过 100 个点时，滑窗丢弃旧数据
- 页面不可见时（`document.hidden`）暂停轮询

```typescript
// Dashboard.vue 优化
document.addEventListener('visibilitychange', () => {
  if (document.hidden) {
    clearInterval(refreshTimer)
  } else {
    refreshTimer = setInterval(fetchData, 10000)
  }
})
```

#### 8.2 告警列表优化

**问题**：告警列表一次性渲染全部数据。

**方案**：
- 使用 Element Plus 的 `el-table-v2` 虚拟滚动组件
- 只渲染可见区域的行（约 20 行）
- 支持万级数据无卡顿

#### 8.3 WebSocket 消息防抖

**问题**：高频 WebSocket 消息触发频繁 DOM 更新。

**方案**：
- 使用 `requestAnimationFrame` 合并同一帧内的多次更新
- 批量处理消息，每 100ms 刷新一次 UI

```typescript
// useWebSocket.ts 优化
let pendingMessages: WSMessage[] = []
let rafId: number | null = null

function handleMessage(msg: WSMessage) {
  pendingMessages.push(msg)
  if (!rafId) {
    rafId = requestAnimationFrame(() => {
      processBatch(pendingMessages)
      pendingMessages = []
      rafId = null
    })
  }
}
```

#### 8.4 轮询优化

**问题**：多个 store 独立轮询，请求叠加。

**方案**：
- 合并为单一轮询，一次请求获取所有数据
- 轮询间隔从 5-10 秒调整为 15-30 秒（非关键数据）
- 使用 `Promise.allSettled` 并发请求，避免串行等待

---

## 9. 摄像头总帧数计算异常

**状态**：待修复
**优先级**：中
**影响**：数据准确性

### 问题

摄像头状态页面显示的总帧数异常：
- 低值：60
- 高值：2933

### 根因分析

检查 `CameraThread` 代码：

```python
# camera.py 第 150-155 行
self._frame_seq = 0
self._total_frames = 0
```

**问题 1：重连时计数器未重置**

`_run_loop` 中连接成功后重置了退避延迟，但没有重置 `_total_frames`。如果摄像头断线重连，帧序号从断点继续累加，但 `_total_frames` 也继续累加，导致总帧数包含重连前的帧。

**问题 2：不同摄像头运行时长不同**

如果摄像头 A 运行了 10 分钟（5fps × 600s = 3000 帧），摄像头 B 刚启动 1 分钟（5fps × 60s = 300 帧），总帧数差异大是正常的。但用户可能期望看到"当前帧率"而非"累计帧数"。

**问题 3：test 模式帧率不稳定**

test 模式生成测试帧的速度受 CPU 影响，可能达不到目标帧率，导致总帧数偏低。

### 修复方案

#### 方案 A：重连时重置计数器（推荐）

```python
def _run_loop(self) -> None:
    while self._running:
        try:
            self._status = CameraStatus.CONNECTING
            self._error_message = ""
            self._frame_seq = 0        # 重置帧序号
            self._total_frames = 0     # 重置总帧数
            self._connect_and_read_frames()
            ...
```

#### 方案 B：增加"运行时帧数"字段

```python
@dataclass
class CameraState:
    total_frames: int          # 累计总帧数（含重连）
    session_frames: int        # 本次会话帧数（重连后重置）
```

#### 方案 C：前端显示帧率而非总帧数

```vue
<!-- 显示当前 FPS 而非总帧数 -->
<span>{{ camera.current_fps }} fps</span>
```

### 验证点

- 重连后帧数从 0 开始
- 运行 1 分钟后帧数 ≈ fps × 60
- 不同摄像头帧数差异在合理范围内
