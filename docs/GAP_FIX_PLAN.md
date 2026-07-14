# Vision Agent — 监控面板 + 用户系统 差距修复方案

> 按优先级分 4 个批次（P0~P3），每批设计完整后实施。共 20 项差距，全部覆盖。

---

## 批次总览

| 批次 | 优先级 | 主题 | 项数 | 预估工作量 |
|------|--------|------|------|-----------|
| P0 | 🔴 立即 | 安全加固（全局认证 / RBAC / WS 认证） | 3 | 1 天 |
| P1 | 🟡 尽快 | 稳定性（WS 重连+心跳 / 提示音 / 操作历史 / 审计日志） | 5 | 1.5 天 |
| P2 | 🟢 提升 | 体验打磨（深色模式 / 浏览器通知 / 趋势对比 / 事件过滤 / 强制改密） | 5 | 1 天 |
| P3 | 🔵 优化 | 锦上添花（动画 / 虚拟滚动 / URL 同步 / 缩略图 / 移动端 / Token 刷新 / 注册） | 7 | 1.5 天 |

---

## P0 — 安全加固（3 项）

### P0-1 全局 API 认证中间件

**问题**：`/api/alerts`、`/api/cameras`、`/api/stats`、`/api/config` 等路由无认证，任何人可调用。

**方案**：在 FastAPI 的 `create_app` 中注册一个全局认证依赖，对 `/api/*` 路由统一拦截（排除 `/api/auth/login` 等公开端点）。

**修改文件**：
- `src/vision_agent/web/api/app.py`

**实现步骤**：
1. 在 `create_app()` 中定义公开路径白名单：`PUBLIC_PATHS = {"/api/auth/login", "/health", "/ws"}`
2. 定义 `get_current_user_or_401(request)` 函数：
   - 读取 `Authorization` header
   - Bearer Token 格式校验
   - 调用 `auth_mgr.verify_token()` 验证
   - 无效则 raise HTTPException(401)
3. 在 FastAPI 实例创建后添加全局依赖：`app = FastAPI(dependencies=[Depends(_global_auth)])`
4. 公开路径在 `_global_auth` 中跳过

**接口变更**：
- 所有 `/api/*`（除白名单外）现在要求 `Authorization: Bearer <token>`
- 前端 `api/client.ts` 已注入 token，无需修改

---

### P0-2 RBAC 权限落地到业务 API

**问题**：当前只有角色级别检查（`_require_role(Role.ADMIN)`），不检查具体权限。operator 理论上可以管理告警，但告警更新 API 没有权限校验。

**方案**：新增基于权限字符串的依赖注入器，替换现有的角色检查。

**修改文件**：
- `src/vision_agent/auth/models.py`（已有 PERMISSIONS，无需改）
- `src/vision_agent/auth/manager.py`（已有 `has_permission`，无需改）
- `src/vision_agent/web/api/app.py`

**实现步骤**：
1. 在 `app.py` 中新增 `_require_permission(*permissions: str)` 工厂函数：
   ```python
   def _require_permission(*permissions):
       def checker(request: Request):
           user = _require_auth(request)
           if user.role == Role.ADMIN:  # admin 拥有所有权限
               return user
           if any(auth_mgr.has_permission(user, p) for p in permissions):
               return user
           raise HTTPException(403, "权限不足")
       return checker
   ```
2. 为各业务端点配置正确的权限：
   | 端点 | 所需权限 |
   |------|---------|
   | `GET /api/alerts` | `view:alerts` |
   | `PUT /api/alerts/{id}/status` | `manage:alerts` |
   | `GET /api/cameras` | `view:cameras` |
   | `POST /api/cameras/{id}/toggle` | `control:cameras` |
   | `POST/PUT/DELETE /api/cameras` | `control:cameras` |
   | `GET /api/stats` | `view:alerts` |
   | `GET /api/config` | `manage:config` |
   | `GET /api/alerts/{id}/snapshot` | `view:alerts` |
   | `GET /api/alerts/{id}/clip` | `view:alerts` |
   | `/ws/video/{camera_id}` | `view:cameras` |
   | `/api/cameras/{id}/replay` | `view:cameras` |
   | `/api/cameras/{id}/timeline` | `view:cameras` |
   | `POST/PUT/DELETE /api/rules/*` | `manage:config` |
   | `GET /api/rules/*` | `view:alerts` |

3. 用户管理端点保持 `_require_role(Role.ADMIN)`（管理员专属）

---

### P0-3 WebSocket 认证

**问题**：`/ws` 和 `/ws/video/{camera_id}` 端点不接受 token 参数，任何人可连接查看实时画面。

**方案**：WebSocket 握手阶段从 query string 读取 token 并验证。

**修改文件**：
- `src/vision_agent/web/api/app.py`

**实现步骤**：
1. 在 `websocket_endpoint` 和 `video_stream` 中，连接建立后首先读取 query 参数：
   ```python
   token = ws.query_params.get("token", "")
   if not auth_mgr.verify_token(token):
       await ws.close(code=4001, reason="认证失败")
       return
   ```
2. 前端修改 `useWebSocket.ts` 和 `useVideoStream.ts`，连接时附加 `?token=<token>`

---

## P0-v2 — Token 持久化与多设备登录（P0 补充）

> P0 实现了全局认证和 RBAC，但 Token 仅存储在内存字典中，存在单槽位、不持久化、logout 粒度粗等问题。P0-v2 在不改变前端行为的前提下，将 Token 持久化到 SQLite，支持多设备同时登录。

### P0-v2-1 数据层：新增 `active_tokens` 表

**问题**：Token 仅存于内存 `_tokens: dict[str, tuple]`，进程重启后全部丢失；一个用户只有一个槽位，新登录会覆盖旧 token。

**方案**：新增 `active_tokens` 表，每条记录对应一个设备 token。

**修改文件**：`src/vision_agent/auth/manager.py`

**DDL**：
```sql
CREATE TABLE IF NOT EXISTS active_tokens (
    token       TEXT PRIMARY KEY,
    username    TEXT NOT NULL,
    ip          TEXT DEFAULT '',
    expires_at  REAL NOT NULL,
    created_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_active_tokens_username ON active_tokens(username);
CREATE INDEX IF NOT EXISTS idx_active_tokens_expires  ON active_tokens(expires_at);
```

---

### P0-v2-2 `login()` — 持久化 token

**修改点**：
1. 生成 token 后 `INSERT INTO active_tokens (...)`
2. 保留内存 `_tokens` 写入（向后兼容，后续版本可去掉）

**逻辑**：
```python
def login(self, username, password, ip="") -> str | None:
    # ... 密码验证 ...
    token = secrets.token_urlsafe(32)
    expiry = time.time() + self.TOKEN_EXPIRY
    # 持久化
    conn.execute("INSERT INTO active_tokens (token,username,ip,expires_at,created_at) VALUES (?,?,?,?,?)",
                 (token, username, ip, expiry, time.time()))
    conn.commit()
    # 内存保留（兼容）
    with self._lock:
        self._tokens[username] = (token, ip, expiry)
    return token
```

---

### P0-v2-3 `verify_token()` — 先查表、后查内存

**修改点**：
1. 先从 `active_tokens` 表 `SELECT token, expires_at`
2. 命中且未过期 → 返回用户
3. 表中没有 → fallback 到内存 `_tokens`（兼容升级过渡期的旧 token）
4. 发现过期 token → 顺手 `DELETE`（惰性清理）

**逻辑**：
```python
def verify_token(self, token: str) -> User | None:
    now = time.time()
    conn = self._get_conn()
    row = conn.execute("SELECT username, expires_at FROM active_tokens WHERE token = ?", (token,)).fetchone()
    if row:
        if row["expires_at"] > now:
            user = self.get_user(row["username"])
            if user and user.is_active:
                return user
        else:
            conn.execute("DELETE FROM active_tokens WHERE token = ?", (token,))
            conn.commit()
        return None
    # Fallback 内存
    return self._verify_token_memory(token)
```

---

### P0-v2-4 `logout()` → `logout_by_token()` — 单设备登出

**问题**：当前 `logout(username)` 按用户名删除，导致单设备登出时踢掉用户所有设备。

**方案**：新增 `logout_by_token(token: str)`，只删除当前 token；保留 `logout(username)` 用于强制下线。

**API 端点变更**：`POST /api/auth/logout`
```python
@app.post("/api/auth/logout")
async def auth_logout(request: Request, user: Any = Depends(_require_auth)) -> Any:
    token = _get_token_from_header(request)  # 从 Authorization 头提取
    auth_mgr.logout_by_token(token)
    return {"message": "已退出"}
```

**前端影响**：无变化。前端仍然只调用 `/api/auth/logout`，不传 token body。

---

### P0-v2-5 `revoke_sessions()` — 强制下线从表删除

**修改前**：`self._tokens.pop(username, None)` — 只删内存。

**修改后**：
```python
def revoke_sessions(self, username: str) -> bool:
    conn = self._get_conn()
    cur = conn.execute("DELETE FROM active_tokens WHERE username = ?", (username,))
    conn.commit()
    with self._lock:
        self._tokens.pop(username, None)
    return cur.rowcount > 0
```

---

### P0-v2-6 `list_active_sessions()` — 从持久化表查询

**修改前**：遍历内存 `_tokens` 字典。

**修改后**：`SELECT * FROM active_tokens WHERE expires_at > ?`

**影响**：用户管理页面的"活跃会话"列表在进程重启后仍然准确。

---

### P0-v2-7 定期清理过期 token

**方案**：在 `AuthManager` 中新增 `_cleanup_expired_tokens()` 方法：
```python
def _cleanup_expired_tokens(self) -> int:
    conn = self._get_conn()
    cur = conn.execute("DELETE FROM active_tokens WHERE expires_at < ?", (time.time(),))
    conn.commit()
    return cur.rowcount
```

**调用时机**：
- 惰性：`verify_token()` 发现过期时顺手删
- 主动：`login()` 成功后调用一次（顺便清理）
- 可选：`__main__.py` 定时任务每小时执行一次

---

### P0-v2 影响评估

| 方面 | 影响 |
|------|------|
| 用户体验 | ✅ 多设备同时登录生效；服务器重启不用重新登录 |
| 安全性 | ✅ 无降级；单设备登出更精确 |
| 性能 | ✅ 每次验证多一次索引查询（WAL 模式，微秒级） |
| 兼容性 | ⚠️ 前端零改动；旧测试 fixture 需创建 token 表 |
| 回滚 | ✅ 内存 `_tokens` 保留，可随时回退到纯内存模式 |

### P0-v2 修改文件清单

| 文件 | 变更 |
|------|------|
| `src/vision_agent/auth/manager.py` | 新增表 DDL、改 login/verify_token/logout/revoke_sessions/list_active_sessions |
| `src/vision_agent/web/api/app.py` | `auth_logout` 端点改按 token 删除 |
| `tests/web/test_auth_security.py` | 补充多设备登录测试、持久化测试 |

---

## P1 — 稳定性（5 项）

### P1-1 WebSocket 指数退避重连

**问题**：`useWebSocket.ts` 的 `onclose` 仅标记断开，无重连逻辑。

**方案**：实现指数退避重连，带最大重试次数。

**修改文件**：
- `frontend/src/composables/useWebSocket.ts`

**实现步骤**：
1. 维护重连状态：`reconnectAttempts`、`reconnectDelay`、`maxReconnectAttempts = 10`
2. 退避策略：
   - 第 1 次：3s
   - 第 2 次：6s
   - 第 3 次：12s
   - 第 4 次及以后：30s（上限）
3. `onclose` 触发后：
   - 如果 `reconnectAttempts < maxReconnectAttempts`
   - 设置 `setTimeout(() => connect(), reconnectDelay)`
   - 每次重连 `reconnectAttempts++`
4. `onopen` 时重置 `reconnectAttempts = 0`
5. 暴露 `reconnecting` 状态给 UI（显示"正在重连..."提示）

---

### P1-2 WebSocket 心跳机制

**问题**：服务端不发送 ping，客户端也不发 pong，长连接可能在 NAT/代理层被静默断开。

**方案**：服务端定时 ping，客户端收到 ping 后 pong，客户端检测到超时未收到 ping 则主动断开重连。

**修改文件**：
- `src/vision_agent/web/api/app.py`（服务端）
- `frontend/src/composables/useWebSocket.ts`（客户端）

**服务端实现**：
1. 在 `WSManager` 中启动一个 `asyncio` 定时任务（每 30s）
2. 向所有活跃连接发送 `{"type": "ping"}`
3. 如果某连接连续 3 次 ping 发送失败，则移除该连接

**客户端实现**：
1. 收到 `{"type": "ping"}` 后发送 `{"type": "pong"}`
2. 维护 `lastPingTime`，每收到 ping 更新
3. 客户端定时器（每 45s）检查：如果 `now - lastPingTime > 60s`，认为连接已死，主动 `close()` 触发重连

---

### P1-3 告警提示音 + 浏览器通知

**问题**：新告警到达时没有任何声音或桌面通知，值班人员可能错过。

**方案**：实现 `useNotification` composable，整合提示音 + 浏览器 Notification API。

**修改文件**：
- `frontend/src/composables/useNotification.ts`（新建）
- `frontend/src/stores/alerts.ts`
- `frontend/src/views/Dashboard.vue`

**实现步骤**：
1. **提示音**：
   - 准备两个短音频文件（public/sounds/alert-critical.mp3、alert-normal.mp3）
   - 用 HTML5 `<audio>` 元素预加载
   - `playAlertSound(severity)` 根据严重级别选择不同音效
   - 提供用户设置开关（存 localStorage：`va-sound-enabled`）

2. **浏览器通知**：
   - 首次使用时请求 `Notification.requestPermission()`
   - `showBrowserNotification(alert)` 构造通知：
     ```js
     new Notification('Vision Agent 告警', {
       body: `[${severityLabel}] ${cameraName} - ${eventTypeLabel}`,
       icon: '/favicon.ico',
       tag: alert_id, // 去重
     })
     ```
   - 点击通知跳转告警详情页
   - 提供用户设置开关（存 localStorage：`va-desktop-notify-enabled`）

3. 在 `alertsStore.addRealtimeAlert` 中调用通知函数

---

### P1-4 告警详情 — 操作历史时间线

**问题**：后端没有操作审计表，无法展示"谁、什么时候、做了什么"。

**方案**：新增 `alert_actions` 表，在告警状态变更时自动记录。

**修改文件**：
- `src/vision_agent/storage/database.py`
- `src/vision_agent/web/api/app.py`
- `frontend/src/views/AlertDetail.vue`（新增操作历史区块）

**数据库设计**：
```sql
CREATE TABLE alert_actions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_id    TEXT NOT NULL,
    action_type TEXT NOT NULL,  -- 'created' / 'acknowledged' / 'rejected' / 'resolved' / 'llm_analyzed'
    actor       TEXT NOT NULL,   -- 'system' / username
    actor_role  TEXT DEFAULT '',
    details     TEXT DEFAULT '', -- JSON 额外信息
    created_at  REAL NOT NULL,
    FOREIGN KEY (alert_id) REFERENCES alerts(alert_id)
);
CREATE INDEX idx_alert_actions_alert_id ON alert_actions(alert_id);
CREATE INDEX idx_alert_actions_created ON alert_actions(created_at);
```

**后端实现**：
1. `DatabaseManager` 新增方法：
   - `save_alert_action(alert_id, action_type, actor, details)`
   - `get_alert_actions(alert_id)` → list[dict]
2. 在 `update_alert_status` 端点中，状态变更成功后记录 action：
   ```python
   database.save_alert_action(
       alert_id, new_status, acknowledged_by or "system", {}
   )
   ```
3. 告警创建时（`ActionThread` 中）自动记录 `created` action
4. LLM 分析完成后记录 `llm_analyzed` action
5. 新增 API：`GET /api/alerts/{alert_id}/actions`

**前端实现**：
1. 在 AlertDetail.vue 底部新增操作历史区块：
   ```
   ┌─────────────────────────────────────┐
   │ 操作历史                            │
   │ ● 14:35:20  张三(操作员)  确认告警   │
   │ ● 14:32:15  系统           生成告警  │
   │ ● 14:32:10  LLM           完成分析  │
   │ ● 14:32:05  规则引擎       触发事件  │
   └─────────────────────────────────────┘
   ```
2. 使用 Element Plus `el-timeline` 组件
3. 不同 action_type 用不同颜色图标区分

---

### P1-5 操作审计日志

**问题**：系统中没有任何操作审计，管理员无法追踪"谁在什么时候做了什么"。

**方案**：新增 `audit_logs` 表，记录所有敏感操作（用户管理、配置变更、告警操作、摄像头控制）。

**修改文件**：
- `src/vision_agent/storage/database.py`
- `src/vision_agent/web/api/app.py`
- `frontend/src/views/System.vue`（新增审计日志页面/标签页）

**数据库设计**：
```sql
CREATE TABLE audit_logs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    username    TEXT NOT NULL,
    role        TEXT NOT NULL,
    action      TEXT NOT NULL,  -- 'user.create' / 'user.update' / 'alert.acknowledge' / 'camera.toggle' / 'config.view' / 'rule.delete'
    resource    TEXT DEFAULT '', -- 操作对象，如 'admin' / 'cam_01' / 'intrusion_rule'
    details     TEXT DEFAULT '', -- JSON 详情
    ip          TEXT DEFAULT '',
    created_at  REAL NOT NULL
);
CREATE INDEX idx_audit_logs_username ON audit_logs(username);
CREATE INDEX idx_audit_logs_action ON audit_logs(action);
CREATE INDEX idx_audit_logs_created ON audit_logs(created_at);
```

**后端实现**：
1. `DatabaseManager` 新增方法：
   - `save_audit_log(username, role, action, resource, details, ip)`
   - `list_audit_logs(filters, page, page_size)`
2. 在 `app.py` 中新增中间件/装饰器 `audit(action: str)`：
   ```python
   def audit(action: str):
       def decorator(func):
           @wraps(func)
           async def wrapper(*args, **kwargs):
               result = await func(*args, **kwargs)
               # 记录审计
               request = kwargs.get('request')
               user = kwargs.get('user')
               if user and request:
                   database.save_audit_log(...)
               return result
           return wrapper
       return decorator
   ```
3. 为所有敏感端点添加 `@audit("user.create")` 等装饰器
4. 新增 API：`GET /api/audit/logs`（仅管理员）

**前端实现**：
1. System.vue 新增"审计日志"标签页
2. 表格列：时间 / 用户 / 角色 / 操作 / 对象 / IP
3. 支持按操作类型、用户名、时间范围筛选
4. 分页查询

---

## P2 — 体验提升（5 项）

### P2-1 深色模式切换

**问题**：CSS 变量已定义，但没有切换入口。

**方案**：在 AppHeader 添加切换按钮，偏好持久化到 localStorage，支持系统偏好自动检测。

**修改文件**：
- `frontend/src/components/layout/AppHeader.vue`
- `frontend/src/styles/variables.scss`（已有，确认完整）
- `frontend/src/App.vue`

**实现步骤**：
1. AppHeader 右侧添加月亮/太阳图标按钮
2. 点击切换 `data-theme="dark"` 属性（挂在 `<html>` 或 `<body>` 上）
3. 偏好存 `localStorage.setItem('va-theme', 'dark'|'light')`
4. App.vue 初始化时读取偏好并应用
5. 支持 `prefers-color-scheme: dark` 自动检测（首次访问时）
6. Element Plus 暗色主题：`import 'element-plus/theme-chalk/dark/css-vars.css'` 在暗色时加载
7. ECharts 图表暗色切换：通过 `dispose()` + `init(null, 'dark')` 重建

---

### P2-2 浏览器通知设置集成到个人设置

**问题**：P1-3 的提示音和浏览器通知有用户开关，但设置页面没有对应 UI。

**方案**：在 Profile.vue 的通知设置标签页中新增"提示音"和"浏览器通知"开关。

**修改文件**：
- `frontend/src/views/Profile.vue`

**实现步骤**：
1. 在通知设置标签页新增区块：
   - 提示音：启用/禁用，音量滑块
   - 浏览器通知：启用/禁用（若浏览器未授权则显示"去授权"按钮触发 requestPermission）
2. 偏好存 localStorage，不调用后端 API（纯前端设置）

---

### P2-3 统计卡片趋势对比（昨日同期）

**问题**：设计书要求 hover 显示与昨日对比，但后端无昨日数据。

**方案**：后端 stats API 增加昨日统计，前端 StatCard 展示趋势。

**修改文件**：
- `src/vision_agent/web/api/app.py`（`get_stats` 端点）
- `frontend/src/views/Dashboard.vue`

**后端实现**：
1. `get_stats` 端点在返回今日统计时，同时计算昨日同期统计：
   ```python
   yesterday_start = start - 86400
   yesterday_end = end - 86400
   yesterday_stats = database.get_stats({"start_time": yesterday_start, "end_time": yesterday_end})
   ```
2. 在返回体中增加 `yesterday_total_alerts` 字段

**前端实现**：
1. StatCard 的 `displayValue` 改为支持趋势显示
2. hover 时展示 tooltip："较昨日 ↑12%" / "较昨日 ↓5%"
3. 计算公式：`(today - yesterday) / max(yesterday, 1) * 100`

---

### P2-4 告警趋势图事件类型切换

**问题**：只能按时间范围切换，不能按事件类型筛选。

**方案**：后端 stats API 增加 `event_type` 分组，前端图表增加类型筛选器。

**修改文件**：
- `src/vision_agent/web/api/app.py`（`get_stats` 端点）
- `src/vision_agent/storage/database.py`（`get_stats` 方法）
- `frontend/src/views/Dashboard.vue`

**后端实现**：
1. `get_stats` 目前返回的 `alerts_by_type` 为空字典，需要让 `database.get_stats` 真正按 event_type 分组
2. 修改 `DatabaseManager.get_stats` 的 SQL，增加 `GROUP BY event_type`
3. 返回体中 `alerts_by_type` 已存在，填充数据即可

**前端实现**：
1. 在告警趋势卡片 header 增加下拉选择器：
   - 全部
   - 闯入
   - 离岗
   - 聚集
   - 遗留物
   - 人数统计
2. 选择后重新 `fetchStats`，`updateTrendChart` 只展示选定类型的数据
3. 多类型支持：允许复选多个类型叠加显示（多条线）

---

### P2-5 默认 admin 首次登录强制改密

**问题**：默认密码 `admin123` 硬编码，无强制修改机制。

**方案**：新增 `must_change_password` 标记，首次登录后重定向到改密页面。

**修改文件**：
- `src/vision_agent/auth/manager.py`
- `src/vision_agent/auth/models.py`
- `src/vision_agent/web/api/app.py`
- `frontend/src/router/index.ts`
- `frontend/src/views/Login.vue`
- `frontend/src/views/Profile.vue`（或新建 ChangePassword.vue）

**后端实现**：
1. `User` 模型新增 `must_change_password: bool = False`
2. `AuthManager._init_default_admin` 创建 admin 时设置 `must_change_password = True`
3. `login()` 返回体中增加 `must_change_password` 字段
4. 若 `must_change_password = True`，则所有其他 API 返回 403 + `{"detail": "请先修改默认密码", "code": "MUST_CHANGE_PASSWORD"}`
5. 修改密码后自动清除标记

**前端实现**：
1. Login.vue 登录成功后检查 `must_change_password`
2. 若为 true，重定向到 `/change-password` 页面
3. 改密页面：只显示新密码输入框，无旧密码要求（首次改密）
4. 路由守卫：若 `must_change_password = true`，只允许访问 `/change-password`，其他页面全部拦截

---

## P3 — 锦上添花（7 项）

### P3-1 仪表盘数字滚动动画

**问题**：统计卡片数字直接显示，无过渡动画。

**方案**：实现数字滚动 hook，数字变化时从旧值滚动到新值。

**修改文件**：
- `frontend/src/composables/useCountUp.ts`（新建）
- `frontend/src/views/Dashboard.vue`

**实现步骤**：
1. `useCountUp(targetValue, duration = 800)` hook：
   - 使用 `requestAnimationFrame` 实现缓动动画
   - 支持 easing 函数（easeOutExpo）
   - 返回当前显示值 ref
2. StatCard 中用 `useCountUp` 替代直接显示

---

### P3-2 告警列表虚拟滚动

**问题**：使用普通 `el-table`，大数据量时性能差。

**方案**：替换为 Element Plus 的 `el-table-v2` 虚拟滚动组件。

**修改文件**：
- `frontend/src/views/AlertList.vue`

**实现步骤**：
1. 将 `el-table` 替换为 `el-table-v2`
2. 定义 `columns` 数组（与现有列一致）
3. 设置 `height` 为固定值（如 600px）
4. 虚拟滚动自动处理大数据量渲染

---

### P3-3 告警列表 URL 参数同步

**问题**：筛选条件不能通过 URL 分享。

**方案**：筛选器变更时同步到 URL query string，页面加载时从 URL 恢复。

**修改文件**：
- `frontend/src/views/AlertList.vue`

**实现步骤**：
1. 使用 Vue Router 的 `useRoute` / `useRouter`
2. 筛选器变更时：`router.replace({ query: { status, camera_id, ... } })`
3. 页面加载时：从 `route.query` 读取初始值并应用到筛选器
4. 分页参数也同步到 URL（`page`, `page_size`）

---

### P3-4 截图缩略图支持

**问题**：后端直接返回原尺寸 JPEG，加载慢。

**方案**：后端支持 `?size=thumb` 参数返回缩略图。

**修改文件**：
- `src/vision_agent/web/api/app.py`
- `frontend/src/views/AlertDetail.vue`

**后端实现**：
1. `get_snapshot` 端点新增 `size` query 参数：
   - `size=thumb`：用 PIL 将图片缩放到最大边 320px，质量 70
   - 无参数或 `size=full`：返回原图
2. 缩略图缓存到磁盘（`data/snapshots/thumbs/`），避免重复生成

**前端实现**：
1. AlertDetail.vue 默认加载缩略图
2. 点击后弹出 `el-image-viewer` 查看原图

---

### P3-5 移动端响应式

**问题**：无汉堡菜单，小屏幕体验差。

**方案**：增加移动端适配：汉堡菜单 + 卡片单列 + 表格横向滚动。

**修改文件**：
- `frontend/src/components/layout/AppHeader.vue`
- `frontend/src/components/layout/AppSidebar.vue`
- `frontend/src/App.vue`
- `frontend/src/views/*.vue`（全局断点适配）

**实现步骤**：
1. AppHeader 左侧添加汉堡菜单按钮（只在 < 768px 显示）
2. 点击后侧边栏从左侧滑入（抽屉模式）
3. App.vue 中 `<768px` 时主内容区全宽
4. Dashboard 统计卡片在 `< 768px` 时改为 `span: 12`（2列）或 `span: 24`（1列）
5. AlertList.vue 表格添加横向滚动
6. 使用 CSS media query 或 Element Plus 的响应式栅格

---

### P3-6 Token 刷新机制

**问题**：Token 24h 过期后用户被踢出，无感知刷新。

**方案**：实现 refresh token 机制。

**修改文件**：
- `src/vision_agent/auth/manager.py`
- `src/vision_agent/web/api/app.py`
- `frontend/src/api/client.ts`
- `frontend/src/stores/auth.ts`

**后端实现**：
1. `login()` 返回 `{ token, refresh_token, expires_in }`
2. `refresh_token` 有效期 7 天，存数据库 `refresh_tokens` 表
3. 新增 `POST /api/auth/refresh` 端点：
   - 接收 refresh_token
   - 验证并签发新的 access_token
   - 返回新 token
4. `verify_token` 不检查 refresh_token（只验 access_token）

**前端实现**：
1. `api/client.ts` 的 axios 响应拦截器：
   - 收到 401 时，尝试用 refresh_token 调用 `/api/auth/refresh`
   - 成功则更新 token 并重试原请求
   - 失败则跳转到登录页
2. `localStorage` 同时存 `va-token` 和 `va-refresh-token`

---

### P3-7 用户自助注册

**问题**：只能管理员创建用户，不适合开放场景。

**方案**：新增用户注册端点（可开关）。

**修改文件**：
- `src/vision_agent/web/api/app.py`
- `frontend/src/views/Login.vue`
- `frontend/src/views/Register.vue`（新建）

**后端实现**：
1. 配置项 `web.allow_self_register: bool = false`（默认关闭）
2. `POST /api/auth/register`：
   - 若 `allow_self_register = false`，返回 403
   - 校验用户名、密码格式
   - 创建用户，role 固定为 viewer
   - 返回 token（自动登录）
3. 防止用户名枚举：注册失败时返回统一错误"用户名已存在或格式不合法"

**前端实现**：
1. Login.vue 底部增加"没有账号？去注册"链接
2. Register.vue：注册表单（用户名/密码/确认密码/邮箱）
3. 注册成功后自动登录并重定向到 Dashboard

---

## 实施建议

### 依赖顺序

```
P0-1 (全局认证) ──→ P0-2 (RBAC) ──→ P0-3 (WS 认证)
   │
   ↓
P1-1 (WS 重连) ──→ P1-2 (WS 心跳)
   │
   ↓
P1-4 (操作历史) ←── P1-5 (审计日志)
   │
   ↓
P1-3 (提示音/通知)
   │
   ↓
P2-1 ~ P2-5 (体验)
   │
   ↓
P3-1 ~ P3-7 (优化)
```

### 每批测试 checklist

- **P0**：用匿名请求访问所有 `/api/*` 端点，确认返回 401（白名单除外）
- **P1**：断开 WiFi 30 秒再恢复，确认 WebSocket 自动重连、心跳正常
- **P2**：切换深色模式，确认所有图表/表格/卡片正确变色
- **P3**：在 375px 宽度模拟器下测试所有页面布局

---

| 批次 | 预估代码变更文件数 |
|------|-----------------|
| P0 | 1 个后端文件 |
| P1 | 5 个后端文件 + 4 个前端文件 |
| P2 | 3 个后端文件 + 5 个前端文件 |
| P3 | 3 个后端文件 + 8 个前端文件 |
| **总计** | **12 个后端 + 17 个前端 = 29 个文件** |

---

## 第二版 — 运维部署 + 架构演进

> 第一版功能已全部完成（P0~P4 + 补充项，287 测试全绿）。第二版聚焦运维部署和架构扩展。

### 第二版总览

| 项 | 主题 | 优先级 | 说明 |
|---|------|--------|------|
| V2-1 | systemd 部署 | 高 | 服务文件 + 启动脚本 |
| V2-2 | 数据备份 | 高 | SQLite 备份 + 截图/视频归档 |
| V2-3 | Docker 容器化 | 中 | Dockerfile + docker-compose |
| V2-4 | HTTPS 反代 | 中 | Nginx + Let's Encrypt |
| V2-5 | PostgreSQL 迁移 | 低 | SQLite → PostgreSQL |
| V2-6 | RAG 知识检索 | 低 | ChromaDB 向量数据库 |
| V2-7 | 端-云架构 | 低 | 边缘设备 + 服务器协同 |

---

### V2-1 systemd 部署

**目标**：生产环境一键部署，支持开机自启、自动重启、日志管理。

**文件**：`deploy/vision-agent.service`

**配套**：
- `deploy/install.sh` — 一键安装脚本
- `deploy/uninstall.sh` — 卸载脚本

---

### V2-2 数据备份

**目标**：自动备份数据库和媒体文件，支持手动触发和定时执行。

**文件**：`scripts/backup.sh`

**备份内容**：
| 数据 | 备份方式 | 保留策略 |
|------|---------|---------|
| SQLite 数据库 | `sqlite3 .backup` 原子备份 | 保留最近 30 天 |
| 截图 | 按日期压缩归档 | 保留 90 天 |
| 视频片段 | 按日期压缩归档 | 保留 30 天 |
| 配置文件 | 备份到 backup/ 目录 | 保留最近 10 份 |

---

### V2-3 Docker 容器化

**目标**：一键部署，跨平台运行，支持 GPU 直通。

**文件**：`Dockerfile` + `docker-compose.yml` + `.dockerignore`

---

### V2-4 HTTPS 反代

**目标**：Nginx 反向代理 + Let's Encrypt 自动证书。

**文件**：`deploy/nginx/vision-agent.conf` + `scripts/setup-ssl.sh`

---

### V2-5 PostgreSQL 迁移

**目标**：SQLite → PostgreSQL，支持多用户并发。

**文件**：`scripts/migrate_sqlite_to_postgres.py` + `src/vision_agent/storage/postgres_backend.py`

---

### V2-6 RAG 知识检索

**目标**：让 LLM 分析参考历史案例和 SOP。

**文件**：`src/vision_agent/storage/vector_store.py` + `src/vision_agent/llm/rag.py`

---

### V2-7 端-云架构

**目标**：边缘设备做推理，服务器做规则+LLM+通知。

**扩展点**：`RemoteDetector` 实现 + `source_type: edge` 配置

---

### 第二版实施建议

| 阶段 | 内容 | 预估工作量 |
|------|------|-----------|
| Phase 1 | V2-1 systemd + V2-2 备份 | 1 天 |
| Phase 2 | V2-3 Docker + V2-4 HTTPS | 2 天 |
| Phase 3 | V2-5 PostgreSQL | 2 天 |
| Phase 4 | V2-6 RAG | 3 天 |
| Phase 5 | V2-7 端-云架构 | 5 天 |
