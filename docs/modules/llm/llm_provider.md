# LLM 提供者 — 设计书

## 1. 模块职责

LLM 提供者（LLM Provider）封装大语言模型 API 的底层调用，为 LLMAnalyzer 提供统一的调用接口。它负责处理 LLM 调用的全部非业务复杂性：网络请求、断路器保护、指数退避重试、月度预算追踪与告警、响应缓存、超时控制，以及错误降级。

核心定位：
- 作为 LLMAnalyzer 和 LLM API 之间的隔离层，LLMAnalyzer 只关心"发 prompt、收结果"，不关心网络细节
- 通过 Protocol 定义接口，支持多种 LLM 后端（OpenAI 兼容 API、本地模型等）
- 实现完整的可靠性保障：断路器防止雪崩、重试提高成功率、预算控制防超支、缓存降低成本

## 2. 对外接口

### Protocol：LLMProviderProtocol

| 方法 | 参数 | 返回值 | 说明 |
|------|------|--------|------|
| `chat` | `messages: list[dict]`, `temperature: float = 0.3`, `max_tokens: int = 1024` | `str 或 None` | 纯文本对话，messages 为 OpenAI 格式的消息列表，失败返回 None |
| `chat_with_image` | `prompt: str`, `image_base64: str`, `temperature: float = 0.3`, `max_tokens: int = 1024` | `str 或 None` | 带图片的对话，prompt 为文本指令，image_base64 为 base64 编码的图片，失败返回 None |
| `get_model_name` | （无参数） | `str` | 返回当前使用的模型标识 |

### 实现类：OpenAICompatibleProvider

| 方法 | 参数 | 返回值 | 说明 |
|------|------|--------|------|
| `__init__` | `config: LLMProviderConfig` | None | 初始化提供者，创建 HTTP 客户端，加载预算状态 |
| `chat` | 同 Protocol | 同 Protocol | 带断路器、重试、预算检查、缓存的文本对话 |
| `chat_with_image` | 同 Protocol | 同 Protocol | 带断路器、重试、预算检查、缓存的图文对话 |
| `get_model_name` | （无参数） | `str` | 返回 config.model |
| `get_monthly_cost` | （无参数） | `float` | 返回当月累计调用成本（美元） |
| `reset_circuit_breaker` | （无参数） | None | 手动重置断路器到 CLOSED 状态，运维用 |
| `close` | （无参数） | None | 关闭 HTTP 客户端连接，释放资源 |

### 内部组件

| 组件 | 类型 | 说明 |
|------|------|------|
| CircuitBreaker | 类 | 断路器状态机 |
| ResponseCache | 类 | LLM 响应缓存 |
| BudgetTracker | 类 | 月度预算追踪 |

### 数据结构：LLMProviderConfig

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| api_base | str | （必填） | API 基础地址 |
| api_key | str | （必填） | API 密钥 |
| model | str | （必填） | 模型标识 |
| timeout | int | 30 | 请求超时秒数 |
| max_retries | int | 2 | 最大重试次数 |
| monthly_budget | float | 100.0 | 月度预算上限（美元） |
| budget_alert_threshold | float | 0.8 | 预算告警阈值比例 |
| cache_enabled | bool | True | 是否启用响应缓存 |
| cache_ttl | int | 3600 | 缓存过期时间（秒） |
| cache_max_size | int | 256 | 缓存最大条目数 |

## 3. 内部逻辑

### 3.1 断路器（CircuitBreaker）

断路器是一个三状态机，防止在 LLM 服务不可用时持续发起请求造成资源浪费和上游延迟。

**状态定义**：

| 状态 | 含义 | 行为 |
|------|------|------|
| CLOSED | 正常状态，允许请求通过 | 正常计数失败次数 |
| OPEN | 熔断状态，拒绝所有请求 | 直接返回 None，不发起 HTTP 请求 |
| HALF_OPEN | 试探状态，允许有限请求 | 放行 1 个请求测试服务是否恢复 |

**状态转换规则**：

- CLOSED → OPEN：连续失败次数达到阈值（默认 5 次）时触发。失败包括 HTTP 连接错误、超时、5xx 响应。
- OPEN → HALF_OPEN：断路器打开后经过冷却超时（默认 3000 秒 = 50 分钟）自动转入半开状态。
- HALF_OPEN → CLOSED：半开状态下放行的请求成功，断路器恢复关闭状态。
- HALF_OPEN → OPEN：半开状态下放行的请求失败，断路器重新打开，重置冷却计时器。

**关键字段**：

| 字段 | 类型 | 说明 |
|------|------|------|
| state | str | 当前状态：CLOSED / OPEN / HALF_OPEN |
| failure_count | int | 连续失败计数 |
| failure_threshold | int | 触发打开的失败阈值（默认 5） |
| cooldown_seconds | int | 冷却超时秒数（默认 3000） |
| last_failure_time | float | 最后一次失败的时间戳（Unix 秒） |
| half_open_request_pending | bool | 半开状态下是否已有请求在途 |

**调用前检查逻辑**：

调用 LLM 前先检查断路器状态。若为 OPEN 且冷却时间未到，直接返回 None。若为 OPEN 且冷却时间已到，转入 HALF_OPEN 并放行。若为 CLOSED 或 HALF_OPEN，正常放行并根据结果更新状态。

### 3.2 重试策略

**触发条件**：请求失败时触发重试，但有以下例外不重试：
- HTTP 4xx 响应（除 429 Too Many Requests 外）：客户端错误，重试无意义
- 断路器打开：重试会加剧问题
- 预算耗尽：重试不会解决预算问题

**退避策略**：指数退避（Exponential Backoff）
- 第 1 次重试：等待 1 秒
- 第 2 次重试：等待 3 秒
- 最大重试次数：由 config.max_retries 决定（默认 2）

**重试范围**：重试发生在 chat/chat_with_image 方法内部，对调用者透明。重试耗尽后返回 None。

**429 特殊处理**：HTTP 429 响应会解析 Retry-After 头（若有），使用 Retry-After 指定的秒数作为等待时间，而非固定的指数退避。若无 Retry-After 头，按指数退避处理。

### 3.3 预算控制（BudgetTracker）

**月度成本追踪**：
- 在内存中维护当月累计调用成本（单位：美元）
- 每次调用完成后，根据返回的 usage.total_tokens 和模型定价计算本次成本
- 成本计算公式：cost = prompt_tokens * prompt_price + completion_tokens * completion_price，价格按模型定价表查询
- 每月 1 日 00:00 自动重置累计成本（通过检查当前月份是否变化实现）

**预算告警**：
- 每次调用后检查累计成本是否超过 monthly_budget * budget_alert_threshold（默认 80%）
- 超过阈值时记录 WARNING 日志："LLM 月度预算已使用 {百分比}%（{已用}/{总额} 美元）"
- 告警只记录一次（通过 _budget_alerted 标记防止重复告警）
- 新月份重置时清除告警标记

**预算耗尽处理**：
- 累计成本超过 monthly_budget 时，后续调用直接返回 None，不发起 HTTP 请求
- 记录 ERROR 日志："LLM 月度预算已耗尽（{已用}/{总额} 美元），本月剩余时间将跳过 LLM 分析"
- 上游 LLMAnalyzer 收到 None 后自动降级

**持久化**：预算状态仅存内存，进程重启后清零。理由：月度预算按自然月计算，进程重启概率低且重启后当月已用金额重新累计影响有限（最差情况是某月多用一点）。

### 3.4 响应缓存（ResponseCache）

**缓存键生成**：
- 对请求内容计算 SHA256 哈希作为缓存键
- chat 方法：键 = SHA256(json.dumps(messages, sort_keys=True))
- chat_with_image 方法：键 = SHA256(prompt + image_base64 的前 1024 字符)
- 对 chat_with_image 只取图片前 1024 字符参与哈希，因为同一截图的 base64 编码是确定性的，前 1024 字符足以区分不同图片

**缓存策略**：
- TTL 过期：每条缓存记录有创建时间，超过 cache_ttl（默认 3600 秒）视为过期
- LRU 淘汰：缓存条目数达到 cache_max_size（默认 256）时，淘汰最久未访问的条目
- 惰性清理：访问时检查是否过期，过期则删除并视为缓存未命中
- 缓存命中时记录 DEBUG 日志，不消耗 API 调用和预算

**缓存不启用场景**：
- config.cache_enabled 为 False 时，不进行缓存查找和存储
- 温度参数 temperature > 0.7 时不缓存（高温度输出随机性强，缓存价值低）

### 3.5 调用主流程

chat/chat_with_image 方法的完整执行流程：

1. **缓存查找**：若缓存启用，计算缓存键查找。命中则直接返回缓存的响应文本。

2. **预算检查**：检查当月累计成本是否已超预算，超预算则记录日志并返回 None。

3. **断路器检查**：检查断路器状态，OPEN 且冷却未到则记录日志并返回 None。OPEN 且冷却已到则转入 HALF_OPEN。

4. **构建请求**：将 messages（或 prompt+image）构造为 OpenAI 兼容的 HTTP 请求体，设置 model、temperature、max_tokens 等参数。chat_with_image 将 image_base64 包装为 OpenAI vision 格式的消息（content 数组包含 type=text 和 type=image_url）。

5. **发送请求并重试**：
   - 发送 HTTP POST 到 api_base/chat/completions
   - 请求成功（2xx）：提取 response.choices[0].message.content，更新断路器状态（若为 HALF_OPEN 则转 CLOSED），计算成本并更新预算追踪器，写入缓存，返回 content
   - 请求失败且可重试（连接错误、超时、5xx、429）：等待退避时间后重试，直到耗尽重试次数
   - 请求失败且不可重试（4xx 除 429）：立即返回 None

6. **重试耗尽**：所有重试均失败，更新断路器失败计数（CLOSED 状态下累加，达到阈值则转 OPEN），记录 ERROR 日志，返回 None。

### 3.6 超时控制

- 使用 HTTP 客户端的 timeout 参数（默认 30 秒），包含连接超时和读取超时
- 连接超时：5 秒（建立 TCP 连接的超时）
- 读取超时：config.timeout 秒（等待 LLM 生成响应的超时）
- 超时后抛出 TimeoutError，进入重试逻辑
- LLM 生成长文本可能耗时较长，30 秒是平衡响应速度和成功率的经验值

## 4. 依赖关系

| 依赖项 | 类型 | 说明 |
|--------|------|------|
| httpx | 运行时依赖 | HTTP 客户端，用于调用 LLM API。选择 httpx 而非 requests，因为 httpx 原生支持异步和超时配置 |
| hashlib | 标准库 | SHA256 缓存键生成 |
| time | 标准库 | 时间戳获取和计算 |
| json | 标准库 | 请求体序列化 |
| threading | 标准库 | 缓存和预算状态的线程安全锁 |
| logging | 标准库 | 日志记录 |
| config | 模块依赖 | 读取 llm 配置段 |

### 被依赖关系

| 被依赖方 | 调用方式 | 说明 |
|----------|----------|------|
| LLMAnalyzer | 通过构造函数注入 LLMProviderProtocol 实例 | Analyzer 只依赖 Protocol，不依赖具体实现 |
| pipeline | 创建 OpenAICompatibleProvider 实例并注入 Analyzer | 组装层负责实例化 |

## 5. 配置项

配置来自 `configs/settings.yaml` 的 `llm` 段：

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| llm.api_base | str | （必填） | API 基础地址，如 "https://api.openai.com/v1" |
| llm.api_key | str | （必填，环境变量） | API 密钥，必须用 `${LLM_API_KEY}` |
| llm.model | str | （必填） | 模型标识，如 "gpt-4o-mini" |
| llm.timeout | int | 30 | 请求超时秒数 |
| llm.max_retries | int | 2 | 最大重试次数 |
| llm.monthly_budget | float | 100.0 | 月度预算上限（美元） |
| llm.budget_alert_threshold | float | 0.8 | 预算告警阈值比例（0-1） |
| llm.cache_enabled | bool | True | 是否启用响应缓存 |
| llm.cache_ttl | int | 3600 | 缓存过期时间（秒） |
| llm.cache_max_size | int | 256 | 缓存最大条目数 |
| llm.circuit_breaker.failure_threshold | int | 5 | 断路器打开的连续失败阈值 |
| llm.circuit_breaker.cooldown_seconds | int | 3000 | 断路器冷却超时秒数 |

## 6. 错误处理

### 6.1 错误分类

| 错误类型 | HTTP 状态码 | 是否重试 | 说明 |
|----------|------------|---------|------|
| 连接错误 | 无 | 是 | DNS 解析失败、连接被拒、网络不可达 |
| 超时 | 无 | 是 | 请求超时 |
| 服务器错误 | 5xx | 是 | LLM 服务端内部错误 |
| 请求过多 | 429 | 是 | 触发速率限制，解析 Retry-After 头 |
| 参数错误 | 400 | 否 | 请求体格式错误（通常是代码 bug） |
| 认证失败 | 401/403 | 否 | API Key 无效或过期 |
| 资源不存在 | 404 | 否 | 模型标识不存在 |
| 其他客户端错误 | 4xx | 否 | 其他 4xx 错误 |

### 6.2 错误处理流程

1. **单次请求失败**：捕获 httpx 异常，判断是否可重试。可重试则等待退避时间后重试，不可重试则直接返回 None。

2. **重试耗尽**：所有重试失败后，更新断路器失败计数，记录 ERROR 日志（包含失败原因和重试次数），返回 None。

3. **断路器打开**：后续请求在断路器检查阶段直接返回 None，记录 WARNING 日志（包含剩余冷却时间），不发起 HTTP 请求。

4. **预算耗尽**：在预算检查阶段直接返回 None，记录 ERROR 日志，不发起 HTTP 请求。

5. **缓存操作失败**：缓存读写异常不影响正常调用流程，捕获异常后忽略并继续执行正常的 API 调用。

### 6.3 上游降级链路

```
OpenAICompatibleProvider.chat_with_image() 返回 None
  ↓
LLMAnalyzer.analyze() 返回 None
  ↓
LLMAnalyzer._build_fallback_analysis() 构造降级分析
  ↓
告警通知照常发送（内容为规则引擎原始结果）
```

整条链路中任何一环的失败都会自动降级到下一级，不会抛出未捕获的异常。

### 6.4 日志规范

| 事件 | 级别 | 格式 |
|------|------|------|
| 请求发出 | DEBUG | `[llm] request_sent model={model} tokens_est={est}` |
| 缓存命中 | DEBUG | `[llm] cache_hit key={key[:16]}...` |
| 请求成功 | INFO | `[llm] request_ok model={model} tokens={usage} latency_ms={ms} cost=${cost}` |
| 重试 | WARNING | `[llm] retry attempt={n}/{max} reason={error} wait={seconds}s` |
| 断路器打开 | ERROR | `[llm] circuit_open failures={count} cooldown={seconds}s` |
| 断路器半开 | INFO | `[llm] circuit_half_open testing recovery` |
| 断路器恢复 | INFO | `[llm] circuit_closed service recovered` |
| 预算告警 | WARNING | `[llm] budget_alert used=${used}/${total} ({pct}%)` |
| 预算耗尽 | ERROR | `[llm] budget_exhausted used=${used}/${total}` |
| 请求失败（不可重试） | ERROR | `[llm] request_failed status={code} reason={error}` |

日志中不包含 API Key、请求/响应的完整 body 等敏感信息。

## 7. 设计决策

### 7.1 选择 httpx 而非 requests

决策：使用 httpx 作为 HTTP 客户端。

理由：httpx 支持同步和异步两种模式，API 风格与 requests 兼容，原生支持连接池和精细的超时配置（connect/read/write 分别设置）。虽然第一版只用同步模式，但后续如果需要异步调用（如并发分析多个告警），切换成本为零。

### 7.2 断路器冷却时间设为 50 分钟

决策：断路器打开后的冷却超时为 3000 秒（50 分钟），远长于常见的 30-60 秒。

理由：LLM API 不可用通常是持续性问题（服务宕机、API Key 过期、账户欠费），而非瞬时抖动。短冷却时间会导致频繁半开探测，消耗无意义的请求。50 分钟的冷却时间意味着在 LLM 恢复之前，系统每 50 分钟才做一次探测，对上游零负担。这个值可通过配置调整，在 LLM 服务稳定性高的环境中可以适当缩短。

### 7.3 预算仅存内存不持久化

决策：月度预算状态只在内存中维护，进程重启后清零。

理由：持久化预算状态需要额外的存储写入（每笔调用都要写磁盘），增加 IO 负担。进程重启的概率低（通常是系统维护或升级），重启后当月已用金额重新累计最多导致多用一个月度预算的 1/N（N 为当月已过天数），风险可控。如果未来需要精确预算控制，可增加定时（每小时）将预算状态写入 SQLite 的逻辑。

### 7.4 缓存键只取图片前 1024 字符

决策：chat_with_image 的缓存键计算时，只取 image_base64 的前 1024 字符参与 SHA256 哈希。

理由：base64 编码的图片数据量大（一张 1280p JPEG 约 100-200KB，base64 后约 130-270KB），对完整数据计算 SHA256 会增加约 1-2ms 的 CPU 开销。base64 编码的前 1024 字符在实际场景中足以区分不同的图片（碰撞概率极低），且同一张截图的 base64 编码是确定性的，不会出现"前缀相同但内容不同"的情况。

### 7.5 温度 > 0.7 时不缓存

决策：当 temperature 参数大于 0.7 时跳过缓存写入。

理由：高温度意味着 LLM 输出的随机性强，同一输入可能产生不同输出。缓存高温度的响应会导致后续请求总是拿到同一条结果，违背了调用者选择高温度的初衷。低温度（<= 0.7）的输出相对确定，缓存价值高且不会影响分析质量。

### 7.6 Protocol 与具体实现分离

决策：定义 LLMProviderProtocol 接口，OpenAICompatibleProvider 为默认实现。

理由：LLM 后端技术迭代快，可能需要切换到本地部署的模型（Ollama、vLLM）或不同的云服务。Protocol 定义了稳定的接口契约，新增后端只需实现 Protocol 即可替换，不影响 LLMAnalyzer 和 pipeline 的任何代码。这也便于测试：可以用 mock provider 单元测试 LLMAnalyzer 的逻辑，不依赖真实 API。
