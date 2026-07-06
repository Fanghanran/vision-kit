# Web API — 设计书

## 1. 模块职责

Web API 模块为 Vision Agent 提供对外的 HTTP REST API 和 WebSocket 实时推送能力。基于 FastAPI 框架构建，通过 uvicorn ASGI 服务器运行。它承担三个核心职责：

1. **数据查询接口**：提供摄像头状态、告警列表/详情、系统统计、配置查看等 REST 端点，供前端和第三方系统调用。
2. **告警管理接口**：提供告警状态更新（确认/标记误报/解决）端点，支持值班人员对告警进行处置操作。
3. **实时推送**：通过 WebSocket 连接，将新告警和系统状态变化实时推送给前端，避免前端轮询。

核心定位：
- 独立线程运行，通过共享存储层读取数据，不干扰主处理管线
- 安全第一：路径白名单、Bearer Token 认证、日志脱敏
- 端点设计遵循 RESTful 规范，错误响应格式统一

## 2. 对外接口

### 2.1 REST 端点总览

| 端点 | 方法 | 认证 | 说明 |
|------|------|------|------|
| `/health` | GET | 否 | 健康检查，返回系统状态 |
| `/api/cameras` | GET | 是 | 摄像头列表及运行状态 |
| `/api/alerts` | GET | 是 | 告警列表（分页、筛选） |
| `/api/alerts/{alert_id}` | GET | 是 | 单条告警详情 |
| `/api/alerts/{alert_id}/snapshot` | GET | 是 | 告警截图（返回图片二进制） |
| `/api/alerts/{alert_id}/clip` | GET | 是 | 告警视频片段（返回视频二进制） |
| `/api/alerts/{alert_id}/status` | PUT | 是 | 更新告警状态 |
| `/api/stats` | GET | 是 | 系统统计数据 |
| `/api/config` | GET | 是 | 系统配置（脱敏） |
| `/ws` | WebSocket | 是（token 参数） | 实时推送连接 |

### 2.2 端点详细定义

#### GET /health

- 认证：不需要
- 响应状态码：200（ok/degraded）、503（unhealthy）
- 响应体（JSON）：

| 字段 | 类型 | 说明 |
|------|------|------|
| status | str | "ok" / "degraded" / "unhealthy" |
| uptime_seconds | float | 系统运行时长 |
| gpu_utilization | float | GPU 使用率（0-1） |
| gpu_memory_used_mb | int | GPU 显存已用（MB） |
| gpu_memory_total_mb | int | GPU 显存总量（MB） |
| queue_depth | int | 帧队列积压数 |
| inference_latency_p50_ms | float | 推理延迟 P50（ms） |
| inference_latency_p99_ms | float | 推理延迟 P99（ms） |
| active_cameras | int | 在线摄像头数 |
| total_cameras | int | 总摄像头数 |
| today_alerts | int | 今日告警数 |
| llm_success_rate | float | LLM 调用成功率（0-1） |
| warning | str | 仅 status=degraded 时存在，描述降级原因 |

#### GET /api/cameras

- 认证：Bearer Token
- 查询参数：无
- 响应体（JSON）：CameraState 列表，每个元素包含 camera_id、status、current_fps、gpu_latency_ms、queue_size、last_frame_time、total_frames、total_detections、total_alerts、uptime_seconds、error_message

#### GET /api/alerts

- 认证：Bearer Token
- 查询参数：

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| page | int | 1 | 页码（从 1 开始） |
| page_size | int | 20 | 每页条数（最大 100） |
| status | str | None | 按状态筛选：pending/acknowledged/rejected/resolved |
| camera_id | str | None | 按摄像头筛选 |
| event_type | str | None | 按事件类型筛选 |
| severity | str | None | 按严重级别筛选 |
| start_time | float | None | 起始时间戳（Unix 秒） |
| end_time | float | None | 结束时间戳（Unix 秒） |

- 响应体（JSON）：

| 字段 | 类型 | 说明 |
|------|------|------|
| items | list[dict] | 告警列表，每条包含 alert_id、event_type、camera_id、camera_name、severity、status、risk_level、created_at |
| total | int | 符合条件的总条数 |
| page | int | 当前页码 |
| page_size | int | 每页条数 |

#### GET /api/alerts/{alert_id}

- 认证：Bearer Token
- 路径参数：alert_id（UUID 字符串）
- 响应体（JSON）：完整的 Alert 对象，包含 event 详情、llm_analysis（若有）、video_clip_path、status、notified_channels、created_at、acknowledged_at、acknowledged_by
- 错误响应：404（alert_id 不存在）

#### GET /api/alerts/{alert_id}/snapshot

- 认证：Bearer Token
- 路径参数：alert_id
- 响应：截图图片二进制（Content-Type: image/jpeg）
- 错误响应：404（告警不存在或截图文件不存在）
- 安全说明：文件路径不暴露在响应中，通过 alert_id 间接访问

#### GET /api/alerts/{alert_id}/clip

- 认证：Bearer Token
- 路径参数：alert_id
- 查询参数：

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| download | bool | false | true 时返回 Content-Disposition: attachment |

- 响应：视频二进制（Content-Type: video/mp4）
- 错误响应：404（告警不存在或视频片段不存在）

#### PUT /api/alerts/{alert_id}/status

- 认证：Bearer Token
- 路径参数：alert_id
- 请求体（JSON）：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| status | str | 是 | 目标状态：acknowledged / rejected / resolved |
| acknowledged_by | str | 否 | 操作人标识 |

- 状态转换规则：

| 当前状态 | 允许的目标状态 | 不允许的目标状态 |
|----------|---------------|-----------------|
| pending | acknowledged, rejected | resolved |
| acknowledged | resolved | acknowledged, rejected |
| rejected | （终态，不可变更） | 所有 |
| resolved | （终态，不可变更） | 所有 |

- 响应体（JSON）：更新后的 Alert 对象
- 错误响应：404（告警不存在）、400（非法状态转换）

#### GET /api/stats

- 认证：Bearer Token
- 查询参数：

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| period | str | "today" | 统计周期：today / 7d / 30d |

- 响应体（JSON）：

| 字段 | 类型 | 说明 |
|------|------|------|
| period | str | 统计周期 |
| total_alerts | int | 告警总数 |
| alerts_by_type | dict | 按事件类型分组的告警数 |
| alerts_by_severity | dict | 按严重级别分组的告警数 |
| alerts_by_camera | dict | 按摄像头分组的告警数 |
| alerts_by_status | dict | 按状态分组的告警数 |
| avg_llm_risk_level | str | LLM 分析的平均风险等级 |
| active_cameras | int | 在线摄像头数 |
| system_uptime_hours | float | 系统运行时长（小时） |

#### GET /api/config

- 认证：Bearer Token
- 响应体（JSON）：系统配置信息（脱敏处理）
- 脱敏规则：
  - 字段名包含 password、api_key、token、secret、credential 的值替换为 "***"
  - RTSP URL 中的密码部分替换为 "***"
  - 数组类型的字段递归脱敏
- 错误响应：无

#### WebSocket /ws

- 认证：连接时通过 URL 参数 `?token=<api_token>` 验证
- 握手失败：token 缺失返回 HTTP 401，token 无效返回 HTTP 403
- 连接后 token 失效：服务端主动断开，状态码 4001
- 推送消息格式（JSON）：

| 消息类型 | type 字段 | 内容 | 触发时机 |
|----------|----------|------|---------|
| 新告警 | "new_alert" | Alert 简要信息（同列表字段） | 新告警生成时 |
| 告警状态更新 | "alert_status" | alert_id + new_status + updated_by | 告警状态变更时 |
| 系统状态 | "system_status" | 精简版 health 信息 | 每 10 秒定时推送 |
| 摄像头状态变化 | "camera_status" | camera_id + new_status | 摄像头上线/下线时 |

- 连接管理：维护已连接 WebSocket 客户端列表，广播消息时遍历列表发送，发送失败的连接自动移除

## 3. 内部逻辑

### 3.1 FastAPI 应用初始化

Web 模块在独立线程中启动 FastAPI 应用，初始化顺序：

1. 创建 FastAPI 实例，配置应用标题、版本、描述。
2. 注册 CORS 中间件：允许配置文件中指定的 origins（默认 localhost:3000）。
3. 注册路径白名单中间件。
4. 注册日志脱敏 Filter。
5. 注册所有路由端点。
6. 注册 startup 和 shutdown 生命周期事件。
7. 通过 uvicorn 在独立线程中启动（host 和 port 从配置读取）。

### 3.2 路径白名单中间件

在请求到达路由处理函数之前执行检查：

1. 获取请求路径（不含查询参数）。
2. 检查路径是否匹配白名单前缀：`/api/`、`/ws`、`/health`、`/static/`、`/`（根路径）。
3. 匹配成功：放行请求，交给路由处理。
4. 匹配失败：直接返回 HTTP 404，响应体为 `{"detail": "Not Found"}`，不泄露任何文件存在性信息。
5. 白名单匹配使用前缀匹配（startswith），不是精确匹配，确保 `/api/` 下的所有子路径都能通过。

### 3.3 Bearer Token 认证

通过 FastAPI 的 Depends 机制实现认证依赖：

1. 从配置读取 `web.api_token`。
2. 若 api_token 未配置（为空或 None），跳过认证（仅限开发模式），记录 WARNING 日志。
3. 若 api_token 已配置，从请求头中提取 `Authorization` 字段。
4. 验证格式：必须为 `Bearer <token>` 格式。
5. 验证内容：token 必须与配置的 api_token 完全匹配（常量时间比较，防时序攻击）。
6. 验证失败：返回 HTTP 401，响应体为 `{"detail": "Invalid or missing token"}`。
7. 所有 `/api/*` 路由（除 /health 外）通过 Depends 引用此认证函数。
8. WebSocket 连接在握手阶段通过查询参数 `token` 验证，逻辑与 REST 认证相同。

### 3.4 告警查询与分页

`GET /api/alerts` 的查询逻辑：

1. 从查询参数构建过滤条件字典。
2. 调用 Storage 层的 `query_alerts(filters, page, page_size)` 方法。
3. Storage 层负责 SQL 查询构建、分页计算、结果排序（按 created_at 降序）。
4. Web 层将查询结果序列化为 JSON 响应。
5. 响应中的 items 列表只包含摘要信息（不含完整 detections/tracks 数据），减少传输量。

### 3.5 告警截图/视频返回

`GET /api/alerts/{alert_id}/snapshot` 和 `/clip` 的处理逻辑：

1. 通过 alert_id 从 Storage 查询告警记录。
2. 从告警记录中获取 snapshot_path / video_clip_path。
3. 校验文件路径存在且可读。
4. 用 FileResponse 返回文件二进制，设置正确的 Content-Type。
5. 文件路径不暴露在响应头或 body 中（仅通过 alert_id 间接访问）。
6. 若文件不存在，返回 HTTP 404。

### 3.6 告警状态更新

`PUT /api/alerts/{alert_id}/status` 的处理逻辑：

1. 验证请求体中 status 字段值的合法性。
2. 从 Storage 查询当前告警状态。
3. 验证状态转换合法性（参照状态转换规则表）。
4. 更新告警状态：
   - acknowledged：设置 status="acknowledged"，acknowledged_at=当前时间，acknowledged_by=请求中的操作人
   - rejected：设置 status="rejected"
   - resolved：设置 status="resolved"
5. 通过 WebSocket 广播告警状态变更消息。
6. 返回更新后的告警对象。

### 3.7 WebSocket 连接管理

1. **连接建立**：客户端连接 `/ws?token=<token>`，验证 token 后加入已连接客户端列表。
2. **心跳机制**：服务端每 30 秒发送 ping 帧，客户端需在 10 秒内回复 pong。超时未回复则断开。
3. **消息广播**：当新告警生成或系统状态变化时，遍历客户端列表发送 JSON 消息。发送失败（连接已断开）的客户端从列表中移除。
4. **连接断开**：客户端主动断开或心跳超时，从列表中移除，记录 DEBUG 日志。
5. **并发安全**：客户端列表使用 asyncio.Lock 保护，确保多协程并发访问安全。

### 3.8 日志脱敏 Filter

实现 Python logging.Filter 子类，在日志输出前自动脱敏：

1. 检查日志消息文本，匹配以下模式并替换：
   - 字段名模式：`password=xxx`、`api_key=xxx`、`token=xxx`、`secret=xxx`、`credential=xxx`、`authorization=xxx` → 字段值替换为 `***`
   - RTSP URL 模式：`rtsp://user:pass@host` → `rtsp://user:***@host`
   - Bearer Token 模式：`Bearer xxx` → `Bearer ***`
2. 替换后的文本写入 log record.msg。
3. 此 Filter 注册到 uvicorn 的 access logger 和应用的 root logger。

### 3.9 配置脱敏

`GET /api/config` 端点返回配置前，递归遍历配置字典：

1. 对所有键值对，检查键名是否包含敏感关键词（password、api_key、token、secret、credential）。
2. 命中的键，值替换为 "***"。
3. 值为字符串类型时，额外检查是否包含 RTSP URL 模式，替换密码部分。
4. 值为列表类型时，递归处理每个元素。
5. 值为字典类型时，递归处理。
6. 返回脱敏后的配置字典。

## 4. 依赖关系

| 依赖项 | 类型 | 说明 |
|--------|------|------|
| fastapi | 运行时依赖 | Web 框架 |
| uvicorn | 运行时依赖 | ASGI 服务器 |
| starlette | 运行时依赖（fastapi 内置） | 中间件、WebSocket 支持 |
| storage/database | 模块依赖 | 读取告警数据、摄像头状态、统计数据 |
| core/types | 模块依赖 | 数据模型序列化（to_dict） |
| config | 模块依赖 | 读取 web 配置段 |
| logging | 标准库 | 日志记录 |
| asyncio | 标准库 | WebSocket 异步连接管理 |
| hashlib | 标准库 | Token 常量时间比较 |
| hmac | 标准库 | Token 常量时间比较 |
| threading | 标准库 | Web 服务独立线程运行 |

### 被依赖关系

| 被依赖方 | 调用方式 | 说明 |
|----------|----------|------|
| pipeline | 启动 Web 服务线程 | pipeline 在组件组装后启动 Web 服务 |
| 前端（Vue 3） | HTTP/WebSocket 调用 | 前端通过 API 和 WebSocket 与后端交互 |

## 5. 配置项

配置来自 `configs/settings.yaml` 的 `web` 段：

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| web.host | str | "0.0.0.0" | 监听地址 |
| web.port | int | 8080 | 监听端口 |
| web.api_token | str | （环境变量） | API 认证 Token，用 `${API_TOKEN}`，为空则不启用认证 |
| web.cors_origins | list[str] | ["http://localhost:3000"] | CORS 允许的来源列表 |
| web.websocket_heartbeat_interval | int | 30 | WebSocket 心跳间隔秒数 |
| web.websocket_heartbeat_timeout | int | 10 | WebSocket pong 超时秒数 |
| web.log_level | str | "info" | uvicorn 日志级别 |

## 6. 错误处理

### 6.1 统一错误响应格式

所有错误响应遵循统一格式：

| 字段 | 类型 | 说明 |
|------|------|------|
| detail | str | 错误描述（面向用户，不含技术细节） |

HTTP 状态码使用规范：

| 状态码 | 含义 | 使用场景 |
|--------|------|---------|
| 200 | 成功 | 正常响应 |
| 201 | 创建成功 | 预留 |
| 400 | 请求错误 | 参数不合法、非法状态转换 |
| 401 | 未认证 | Token 缺失或格式错误 |
| 403 | 无权限 | Token 无效 |
| 404 | 不存在 | 资源不存在、路径不在白名单 |
| 422 | 数据校验失败 | 请求体不符合 schema |
| 500 | 服务端错误 | 未预期的内部错误 |
| 503 | 服务不可用 | 系统健康状态为 unhealthy |

### 6.2 异常处理

| 异常类型 | 处理方式 |
|----------|----------|
| RequestValidationError | 返回 422 + 校验错误详情 |
| HTTPException | 按指定状态码返回 |
| Storage 查询异常 | 捕获后返回 500，日志记录完整堆栈，响应中不含堆栈信息 |
| 文件读取异常（截图/视频） | 捕获后返回 404 |
| 未预期异常 | 全局异常处理器捕获，返回 500，日志记录完整堆栈 |

所有异常处理都不在响应中暴露堆栈跟踪、文件路径或数据库连接字符串。

### 6.3 WebSocket 错误处理

| 场景 | 处理方式 |
|------|----------|
| token 缺失 | 拒绝握手，返回 HTTP 401 |
| token 无效 | 拒绝握手，返回 HTTP 403 |
| 连接后 token 失效 | 主动断开，状态码 4001 |
| 消息发送失败 | 从客户端列表移除，记录 DEBUG 日志 |
| 客户端异常断开 | 从客户端列表移除，记录 DEBUG 日志 |
| 心跳超时 | 主动断开，从客户端列表移除 |

## 7. 设计决策

### 7.1 路径白名单而非黑名单

决策：采用白名单策略，只允许已知安全的路径通过，其余全部返回 404。

理由：黑名单策略需要枚举所有"不安全"的路径模式（.git、.env、configs/、data/ 等），容易遗漏。新增目录或文件时需要同步更新黑名单，维护成本高且容易出错。白名单策略只需维护几条已知安全的路径前缀（/api/、/ws、/health、/static/、/），简洁明确，新增的路径默认被拦截，安全性更好。

### 7.2 /health 不需要认证

决策：健康检查端点 /health 不要求 Bearer Token 认证。

理由：/health 端点用于外部监控系统（Prometheus、cron、K8s 探针）定期检查系统状态。如果要求认证，每个监控客户端都需要配置 Token，增加运维复杂度。/health 只返回系统运行指标，不包含业务数据和敏感信息，无需保护。

### 7.3 截图/视频通过 API 间接访问

决策：不通过 FastAPI 的 StaticFiles 暴露截图和视频目录，而是通过 `/api/alerts/{id}/snapshot` 和 `/api/alerts/{id}/clip` 端点间接访问。

理由：直接暴露文件目录会让攻击者通过枚举文件名获取监控画面。通过 alert_id 间接访问有两个安全优势：一是需要认证才能访问，二是文件路径不暴露在 URL 中，攻击者无法直接访问文件系统。

### 7.4 Token 常量时间比较

决策：验证 Token 时使用 hmac.compare_digest() 进行常量时间比较。

理由：普通字符串比较（==）在遇到第一个不匹配字符时立即返回，攻击者可以通过测量响应时间逐字符猜测 Token（时序攻击）。hmac.compare_digest() 无论 Token 是否匹配都遍历完整字符串，时间恒定，消除时序侧信道。

### 7.5 WebSocket 心跳机制

决策：服务端每 30 秒发送 ping，客户端 10 秒内需回复 pong。

理由：WebSocket 连接可能因网络中断而"静默断开"（TCP 连接未正常关闭），服务端无法及时感知。心跳机制确保：（1）及时清理死连接，释放资源；（2）前端可据此判断连接状态并自动重连。30 秒间隔平衡了实时性和网络开销。

### 7.6 告警列表返回摘要而非完整数据

决策：`GET /api/alerts` 列表端点只返回告警摘要信息，完整数据（含 detections、tracks）通过 `GET /api/alerts/{id}` 详情端点获取。

理由：告警列表是前端最高频的查询，每条告警的 detections 和 tracks 数据量可能很大（一条告警包含数十个检测框和追踪轨迹），全量返回会导致：（1）响应体积过大，页面加载慢；（2）JSON 序列化耗时增加 API 延迟。摘要模式（alert_id、事件类型、摄像头、状态、时间）足以支撑列表展示，详情按需加载。
