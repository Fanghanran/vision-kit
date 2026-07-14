# 待处理项解决方案

## 1. BroadcastChannel 多标签同步

### 问题

标签页 A 的 token 过期时执行 `localStorage.removeItem('va-token')`，导致所有标签页的 token 被清除，其他标签页也被踢到登录页。

### 解决方案

使用浏览器 `BroadcastChannel` API 实现标签页间通信。

**新建文件**：`frontend/src/composables/useMultiTabSync.ts`

```typescript
const channel = new BroadcastChannel('sentinelmind-auth')

// 发送消息
channel.postMessage({ type: 'token_expired', page: window.location.pathname })
channel.postMessage({ type: 'logout' })
channel.postMessage({ type: 'token_refreshed', token: newToken })

// 监听消息
channel.onmessage = (event) => {
  const msg = event.data
  if (msg.type === 'token_expired') {
    // 显示 toast 提示，询问是否同步登出
    ElMessage.warning('检测到其他标签页登出，3 秒后同步登出...')
    setTimeout(() => { /* 清除 token 跳转登录 */ }, 3000)
  }
  if (msg.type === 'logout') {
    // 立即同步登出
    clearAuthAndRedirect()
  }
  if (msg.type === 'token_refreshed') {
    // 同步新 token（其他标签页也刷新了）
    localStorage.setItem('va-token', msg.token)
  }
}
```

**修改文件**：
- `frontend/src/api/client.ts`：401 处理中发送 `token_expired` 消息，收到 `token_refreshed` 时同步新 token
- `frontend/src/stores/auth.ts`：logout 时发送 `logout` 消息
- `frontend/src/composables/useWebSocket.ts`：收到 4001 时发送 `token_expired`，不清除 localStorage

**关键逻辑**：
1. 标签页 A token 过期 → 广播 `token_expired` → 自己跳转登录页
2. 其他标签页收到广播 → 显示 3 秒倒计时 → 同步登出
3. 正常登出 → 广播 `logout` → 所有标签页立即同步
4. Token 刷新 → 广播 `token_refreshed` → 其他标签页同步新 token

---

## 2. 缩略图缓存定期清理

### 问题

`data/snapshots/thumbs/` 目录的缩略图生成后不会过期或清理，长期运行可能无限增长。

### 解决方案

**方案**：复用已有的数据清理定时任务，在 `ClipRecorder.cleanup_expired` 中添加 thumbs 清理逻辑。

**修改文件**：`src/sentinelmind/core/recorder.py`

```python
def cleanup_expired(self) -> int:
    # ... 现有的截图/视频清理逻辑 ...

    # 新增：清理缩略图
    thumbs_dir = Path(self._snapshot_dir) / "thumbs"
    if thumbs_dir.exists():
        for thumb_file in thumbs_dir.glob("*.jpg"):
            if self._is_expired(thumb_file, self._snapshot_retention_days):
                thumb_file.unlink()
                count += 1
        # 清理空目录
        self._remove_empty_dirs(thumbs_dir)

    return count
```

**关键逻辑**：
1. 缩略图与原图保留相同天数（`snapshot_retention_days`，默认 30 天）
2. 清理时机：每小时执行一次（复用现有定时任务）
3. 原子删除：先检查文件是否过期，再删除
4. 空目录清理：删除后清理空的日期目录

**影响**：无新依赖，无新 API，纯后台清理。

---

## 3. 后端模块集成控制项检查

### 问题

控制面板的开关存储在 `system_controls` 表中，但各模块（LLM/通知/录制等）没有读取这些值，开关只是前端显示用，实际不生效。

### 解决方案

各模块在执行前检查 `database.get_control_value(key)`，为 False 时跳过执行。

**修改文件**：

| 文件 | 修改点 |
|------|--------|
| `src/sentinelmind/llm/analyzer.py` | `analyze()` 开头检查 `llm.enabled` |
| `src/sentinelmind/actions/notifier.py` | `execute()` 开头检查 `notification.webhook.enabled` / `notification.email.enabled` |
| `src/sentinelmind/core/recorder.py` | `save_clip()` / `save_snapshot()` 开头检查 `recording.enabled` |
| `src/sentinelmind/rules/engine.py` | 热重载线程检查 `rules.hot_reload` |
| `src/sentinelmind/core/camera.py` | 重连逻辑检查 `camera.auto_reconnect` |
| `src/sentinelmind/web/api/app.py` | WSManager 检查 `websocket.enabled` |
| `src/sentinelmind/storage/database.py` | `save_audit_log()` 检查 `audit.enabled` |

**注入方式**：通过构造函数注入 `database` 实例，各模块调用 `database.get_control_value(key)`。

**示例 — LLMAnalyzer**：

```python
class LLMAnalyzer:
    def __init__(self, provider, database=None):
        self._provider = provider
        self._database = database

    async def analyze(self, alert, screenshot_base64=None):
        # 检查控制项
        if self._database and not self._database.get_control_value("llm.enabled"):
            return self._build_fallback_analysis(alert)
        # ... 正常分析逻辑 ...
```

**降级行为**：

| 模块 | 控制项关闭时的行为 |
|------|-------------------|
| LLM 分析 | 返回降级分析（规则引擎原始结果） |
| Webhook 通知 | 跳过发送，记录日志 |
| 邮件通知 | 跳过发送，记录日志 |
| 告警录制 | 跳过录制，返回空路径 |
| 规则热重载 | 停止扫描文件变化 |
| 摄像头重连 | 停止重连循环 |
| WebSocket | 拒绝新连接，现有连接保持 |
| 审计日志 | 跳过记录，返回 |

**性能考虑**：
- `get_control_value()` 从 SQLite 查询，WAL 模式下微秒级
- 可选优化：缓存控制项值，每 10 秒刷新一次（避免每帧都查库）

---

## 实施优先级

| 项 | 优先级 | 原因 |
|---|--------|------|
| #3 后端集成控制项 | **高** | 控制面板目前"假开关"，功能不完整 |
| #1 BroadcastChannel | 中 | 多标签场景较少，临时方案可用 |
| #2 缩略图清理 | 低 | 短期不会积累太多，后续补上 |
