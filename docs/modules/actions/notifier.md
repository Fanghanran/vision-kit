# 通知器 — 设计书

## 1. 模块职责

通知器（Notifier）负责将告警事件及其 LLM 分析结果通过多种渠道发送给相关人员。支持的渠道包括：企业微信群机器人 Webhook、钉钉群机器人 Webhook、SMTP 邮件。通知器是 ActionProtocol 的实现，由规则引擎的 actions 配置驱动，在告警生成后异步执行。

核心定位：
- 作为 pipeline 处理层的最后一步，将分析结果转化为人可感知的通知
- 支持多渠道并行发送，一个告警可同时推送到 Webhook 和邮件
- 通知失败有重试机制，但不阻塞后续告警的处理
- 通知结果记入 Alert.notified_channels，便于追踪通知状态

## 2. 对外接口

### Protocol：ActionProtocol

通知器实现系统定义的 ActionProtocol 接口：

| 方法 | 参数 | 返回值 | 说明 |
|------|------|--------|------|
| `execute` | `alert: Alert` | `bool` | 执行通知发送，成功返回 True |
| `name` | （属性） | `str` | 行动名称标识 |

### 类：WebhookNotifier

| 方法 | 参数 | 返回值 | 说明 |
|------|------|--------|------|
| `__init__` | `config: WebhookConfig` | None | 初始化，配置 Webhook URL、类型、重试参数 |
| `execute` | `alert: Alert` | `bool` | 构造消息体并 POST 到 Webhook URL |
| `name` | （属性） | `str` | 返回 `"webhook"` |
| `_build_message` | `alert: Alert` | `dict` | 根据 Webhook 类型构造消息体（企微/钉钉格式不同） |
| `_build_wechat_message` | `alert: Alert` | `dict` | 构造企业微信 Markdown 格式消息体 |
| `_build_dingtalk_message` | `alert: Alert` | `dict` | 构造钉钉 Markdown 格式消息体 |
| `_send_request` | `payload: dict` | `bool` | 发送 HTTP POST 请求，带重试 |

### 类：EmailNotifier

| 方法 | 参数 | 返回值 | 说明 |
|------|------|--------|------|
| `__init__` | `config: EmailConfig` | None | 初始化，配置 SMTP 服务器、认证、收件人 |
| `execute` | `alert: Alert` | `bool` | 构造邮件内容并通过 SMTP 发送 |
| `name` | （属性） | `str` | 返回 `"email"` |
| `_build_email` | `alert: Alert` | `tuple[str, str, str]` | 返回 (subject, html_body, text_body) 三元组 |
| `_render_html_template` | `alert: Alert` | `str` | 渲染 HTML 邮件模板 |
| `_send_email` | `subject: str`, `html: str`, `text: str` | `bool` | 通过 SMTP SSL 连接发送邮件 |

### 数据结构：WebhookConfig

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| enabled | bool | True | 是否启用 |
| type | str | "wechat" | Webhook 类型：wechat（企微）/ dingtalk（钉钉） |
| url | str | （必填，环境变量） | Webhook URL，必须用 `${WEBHOOK_URL}` |
| max_retries | int | 2 | 发送失败最大重试次数 |
| retry_interval | float | 2.0 | 重试间隔秒数 |
| timeout | int | 10 | HTTP 请求超时秒数 |

### 数据结构：EmailConfig

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| enabled | bool | False | 是否启用（默认关闭） |
| smtp_host | str | （必填） | SMTP 服务器地址 |
| smtp_port | int | 465 | SMTP 端口（465=SSL，587=STARTTLS） |
| smtp_user | str | （必填） | SMTP 用户名 |
| smtp_pass | str | （必填，环境变量） | SMTP 密码，必须用 `${EMAIL_PASS}` |
| use_ssl | bool | True | 是否使用 SSL |
| from_addr | str | smtp_user | 发件人地址 |
| from_name | str | "Vision Agent" | 发件人显示名称 |
| to_addrs | list[str] | （必填） | 收件人邮箱列表 |
| timeout | int | 30 | SMTP 连接超时秒数 |

## 3. 内部逻辑

### 3.1 WebhookNotifier 执行流程

当 `execute(alert)` 被调用时：

1. **构造消息体**：调用 `_build_message(alert)` 根据配置的 Webhook 类型构造对应格式的消息体。

2. **企业微信消息格式**（type="wechat"）：
   - 消息类型为 markdown
   - 标题行：告警类型中文名 + 风险等级标记（用颜色 emoji 区分）
   - 摄像头信息：摄像头名称 + ID
   - 时间信息：事件发生时间（格式化为 "YYYY-MM-DD HH:MM:SS"）
   - LLM 分析结果（若有）：
     - 情况描述（description）
     - 风险等级（risk_level）
     - 建议措施（suggestion）
   - 若无 LLM 分析结果，显示规则引擎原始信息：事件类型 + 检测目标 + 严重级别
   - 截图链接（若有 Web 端可访问的 URL）

3. **钉钉消息格式**（type="dingtalk"）：
   - 消息类型为 markdown
   - 结构与企微类似，但使用钉钉 Markdown 语法差异（如加粗、链接格式）
   - 标题限 20 字符以内，超长截断

4. **发送请求**：调用 `_send_request(payload)` 发送 HTTP POST：
   - URL：config.url
   - Content-Type：application/json
   - 超时：config.timeout 秒
   - 重试：最多 config.max_retries 次（默认 2），间隔 config.retry_interval 秒（默认 2s）
   - 成功条件：HTTP 响应状态码 2xx
   - 重试条件：5xx 响应或网络错误（连接失败、超时）
   - 不重试条件：4xx 响应（客户端错误，重试无意义）

5. **更新通知记录**：发送成功后，将 `"webhook"` 追加到 `alert.notified_channels` 列表。

6. **日志记录**：成功记录 INFO 日志（alert_id、camera_id、Webhook 类型、耗时），失败记录 ERROR 日志（alert_id、错误原因、是否重试）。

### 3.2 EmailNotifier 执行流程

当 `execute(alert)` 被调用时：

1. **构造邮件内容**：调用 `_build_email(alert)` 返回三元组 (subject, html_body, text_body)。

2. **邮件主题**：格式为 "[Vision Agent][风险等级] 摄像头名称 - 事件类型"。例如："[Vision Agent][高] 仓库入口 - 区域闯入"。

3. **HTML 邮件模板**：通过 `_render_html_template()` 渲染，内容包含：
   - 页头：系统名称 + 告警时间
   - 告警摘要：事件类型、摄像头、风险等级（用颜色标签区分）
   - LLM 分析区域（若有）：情况描述、建议措施
   - 无 LLM 分析时的降级区域：规则引擎原始信息
   - 截图嵌入（若有）：通过 CID 内嵌图片方式嵌入邮件正文
   - 页脚：系统名称 + 告警 ID（便于溯源）

4. **纯文本备选**：同时生成 text_body 纯文本版本，用于不支持 HTML 的邮件客户端。

5. **SMTP 发送**：调用 `_send_email(subject, html, text)`：
   - 连接 SMTP 服务器：根据 use_ssl 选择 SSL（端口 465）或 STARTTLS（端口 587）
   - 登录认证：使用 smtp_user 和 smtp_pass
   - 发送邮件：使用 MIMEMultipart 构造，同时包含 HTML 和纯文本版本（alternative）
   - 截图内嵌：若截图路径存在且文件可读，作为 MIMEImage 附件嵌入，HTML 中通过 `<img src="cid:snapshot">` 引用
   - 超时：config.timeout 秒
   - 成功后关闭连接

6. **更新通知记录**：发送成功后，将 `"email"` 追加到 `alert.notified_channels` 列表。

7. **邮件发送不重试**：SMTP 发送失败不重试（邮件发送失败通常是因为地址错误或服务器问题，重试无意义）。记录 ERROR 日志。

### 3.3 通知内容模板

通用的通知内容结构（各渠道实现格式不同，但信息相同）：

| 区域 | 内容 | 是否必有 |
|------|------|---------|
| 标题/主题 | [风险等级标记] 摄像头名称 - 事件类型 | 是 |
| 基本信息 | 摄像头名称、摄像头 ID、事件时间 | 是 |
| LLM 分析 - 情况描述 | LLMAnalysis.description | LLM 分析可用时 |
| LLM 分析 - 风险等级 | LLMAnalysis.risk_level | LLM 分析可用时 |
| LLM 分析 - 建议措施 | LLMAnalysis.suggestion | LLM 分析可用时 |
| 规则引擎信息 | 事件类型、检测目标数量和类型、严重级别 | LLM 分析不可用时（降级） |
| 截图 | 告警截图（Webhook 为链接，邮件为内嵌图片） | 截图存在时 |
| 告警 ID | alert_id（用于溯源） | 是 |

风险等级标记颜色映射：

| 风险等级 | 企微标记 | 钉钉标记 | 邮件颜色 |
|----------|---------|---------|---------|
| 紧急 | 红色字体 | 红色字体 | #FF0000 |
| 高 | 橙色字体 | 橙色字体 | #FF6600 |
| 中 | 黄色字体 | 黄色字体 | #FF9900 |
| 低 | 灰色字体 | 灰色字体 | #999999 |

### 3.4 异步发送机制

通知发送在 ActionThread 中执行，但通过异步化避免阻塞主处理管线：

1. ActionThread 从 ResultQueue 取到结果后，先同步执行规则引擎评估和告警生成。
2. 告警生成后，将 LLM 分析和通知发送提交为异步任务（通过 concurrent.futures.ThreadPoolExecutor）。
3. ActionThread 继续处理下一条结果，不等待通知发送完成。
4. 异步任务完成后，通过回调更新 Alert.notified_channels 和日志。
5. 线程池大小：默认 4 个 worker 线程，足够处理多个通知渠道的并发发送。

此机制确保：通知发送延迟（如网络慢、LLM 分析耗时）不会导致 ResultQueue 积压。

## 4. 依赖关系

| 依赖项 | 类型 | 说明 |
|--------|------|------|
| httpx | 运行时依赖 | Webhook HTTP POST 请求 |
| smtplib | 标准库 | SMTP 邮件发送 |
| email.mime | 标准库 | 邮件 MIME 构造 |
| ssl | 标准库 | SSL 连接 |
| jinja2 | 运行时依赖 | HTML 邮件模板渲染 |
| core/types | 模块依赖 | 使用 Alert、Event、LLMAnalysis 数据模型 |
| config | 模块依赖 | 读取 notification 配置段 |
| logging | 标准库 | 日志记录 |
| concurrent.futures | 标准库 | 异步任务执行（ThreadPoolExecutor） |

### 被依赖关系

| 被依赖方 | 调用方式 | 说明 |
|----------|----------|------|
| pipeline / ActionThread | 注册为规则引擎的 action | 告警生成后由规则引擎触发执行 |

## 5. 配置项

配置来自 `configs/settings.yaml` 的 `notification` 段：

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| notification.webhook.enabled | bool | True | 是否启用 Webhook 通知 |
| notification.webhook.type | str | "wechat" | Webhook 类型：wechat / dingtalk |
| notification.webhook.url | str | （必填，环境变量） | Webhook URL，用 `${WEBHOOK_URL}` |
| notification.webhook.max_retries | int | 2 | 发送失败重试次数 |
| notification.webhook.retry_interval | float | 2.0 | 重试间隔秒数 |
| notification.webhook.timeout | int | 10 | HTTP 请求超时秒数 |
| notification.email.enabled | bool | False | 是否启用邮件通知 |
| notification.email.smtp_host | str | （必填） | SMTP 服务器地址 |
| notification.email.smtp_port | int | 465 | SMTP 端口 |
| notification.email.smtp_user | str | （必填） | SMTP 用户名 |
| notification.email.smtp_pass | str | （必填，环境变量） | SMTP 密码，用 `${EMAIL_PASS}` |
| notification.email.use_ssl | bool | True | 是否使用 SSL |
| notification.email.from_name | str | "Vision Agent" | 发件人显示名称 |
| notification.email.to_addrs | list[str] | （必填） | 收件人邮箱列表 |
| notification.email.timeout | int | 30 | SMTP 连接超时秒数 |

## 6. 错误处理

### 6.1 WebhookNotifier 错误处理

| 错误场景 | 处理方式 | 是否影响后续告警 |
|----------|----------|-----------------|
| Webhook URL 配置为空 | execute() 返回 False，记录 ERROR 日志 | 不影响 |
| HTTP 连接失败 | 重试 max_retries 次，间隔 retry_interval 秒 | 不影响 |
| HTTP 超时 | 同连接失败，重试 | 不影响 |
| HTTP 5xx 响应 | 同连接失败，重试 | 不影响 |
| HTTP 4xx 响应 | 不重试，记录 ERROR 日志（含响应 body） | 不影响 |
| 消息体构造失败（数据字段缺失） | 用默认值填充缺失字段，记录 WARNING 日志 | 不影响 |
| 所有重试耗尽仍失败 | 返回 False，记录 ERROR 日志，alert.notified_channels 不更新 | 不影响 |

### 6.2 EmailNotifier 错误处理

| 错误场景 | 处理方式 | 是否影响后续告警 |
|----------|----------|-----------------|
| SMTP 服务器不可达 | 记录 ERROR 日志，返回 False | 不影响 |
| SMTP 认证失败 | 记录 ERROR 日志（提示检查账号密码），返回 False | 不影响 |
| 收件人地址为空列表 | 返回 False，记录 WARNING 日志 | 不影响 |
| HTML 模板渲染失败 | 降级为纯文本邮件，记录 WARNING 日志 | 不影响 |
| 截图文件读取失败 | 邮件中不嵌入截图，记录 WARNING 日志 | 不影响 |
| SSL 证书验证失败 | 记录 ERROR 日志，返回 False | 不影响 |

### 6.3 通用容错原则

- 通知失败不影响 pipeline 继续处理后续告警（异步执行隔离）
- 单个渠道失败不影响其他渠道的发送（多渠道独立）
- execute() 返回 False 时，pipeline 记录日志但不重试（避免告警风暴时重试堆积）
- 所有网络操作都有超时控制，不会无限等待

### 6.4 日志规范

| 事件 | 级别 | 格式 |
|------|------|------|
| Webhook 发送成功 | INFO | `[notify] webhook_sent alert={id} camera={cam} latency_ms={ms}` |
| Webhook 重试 | WARNING | `[notify] webhook_retry alert={id} attempt={n}/{max} reason={error}` |
| Webhook 失败 | ERROR | `[notify] webhook_failed alert={id} reason={error}` |
| 邮件发送成功 | INFO | `[notify] email_sent alert={id} to={addrs}` |
| 邮件发送失败 | ERROR | `[notify] email_failed alert={id} reason={error}` |
| 模板降级 | WARNING | `[notify] template_fallback alert={id} reason={error}` |

日志中不包含 Webhook URL、SMTP 密码等敏感信息。

## 7. 设计决策

### 7.1 支持企微和钉钉两种 Webhook

决策：同时支持企业微信群机器人和钉钉群机器人的 Webhook，通过配置 type 字段切换。

理由：国内监控场景中企微和钉钉是最常用的即时通讯工具。两者的 Webhook 接口格式不同（企微用 markdown 消息类型，钉钉也用 markdown 但字段结构不同），但都是 HTTP POST JSON 的简单模式。通过 `_build_wechat_message()` 和 `_build_dingtalk_message()` 分别构造，逻辑清晰且易于扩展其他 Webhook 类型（如飞书、Slack）。

### 7.2 邮件发送不重试

决策：SMTP 邮件发送失败后不重试，而 Webhook 失败会重试 2 次。

理由：Webhook 失败通常是网络抖动或服务端瞬时过载，重试有较大概率成功。SMTP 失败更可能是配置错误（地址错误、密码错误、服务器不可达），这些是持续性问题，重试无意义。且 SMTP 连接建立本身耗时较长（通常 5-15 秒），重试会显著增加阻塞时间。

### 7.3 HTML 和纯文本双版本邮件

决策：每封邮件同时包含 HTML 和纯文本两个版本（MIME alternative）。

理由：部分邮件客户端（如命令行邮件工具、安全策略严格的企业邮件网关）不支持或不渲染 HTML。纯文本版本确保在任何客户端都能正常显示告警内容。使用 MIMEMultipart("alternative") 让邮件客户端自动选择支持的最好格式。

### 7.4 截图以 CID 内嵌而非外部链接

决策：告警截图通过 MIMEImage 附件方式内嵌到邮件中，HTML 中用 `<img src="cid:snapshot">` 引用。

理由：外部链接依赖 Web 服务的可达性，如果收件人无法访问 Vision Agent 的 Web 端（如不在同一网段），截图将无法显示。内嵌方式确保截图在任何环境下都能正常显示，且不依赖额外的网络请求。缺点是邮件体积增大，但单张 JPEG 截图通常只有 100-300KB，可接受。

### 7.5 通知结果记录到 Alert.notified_channels

决策：成功发送通知后，将渠道名追加到 alert.notified_channels 列表。

理由：这为告警的通知状态提供可追溯性。Web API 可以展示某条告警已通过哪些渠道通知，运维人员可以据此判断是否需要手动补发。后期还可用于通知去重：检查 notified_channels 避免同一告警重复发送到同一渠道。

### 7.6 异步发送不阻塞主管线

决策：通知发送通过 ThreadPoolExecutor 异步执行，ActionThread 不等待完成。

理由：通知发送涉及网络 IO（Webhook POST、SMTP 连接），耗时从几百毫秒到数十秒不等（取决于网络状况和 LLM 分析耗时）。如果同步等待，当某条告警的通知发送卡住时，后续所有告警都会被延迟处理。异步化确保 ActionThread 的主循环始终流畅，不被 IO 操作阻塞。线程池大小默认 4，足够应对正常的告警并发量。
