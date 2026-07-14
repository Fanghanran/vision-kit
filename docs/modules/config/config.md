# 配置管理（Config） — 设计书

## 1. 模块职责

配置管理模块负责 SentinelMind 所有配置的加载、校验、访问和热重载。它是系统中依赖最广泛的模块，几乎所有其他模块都通过配置管理获取运行参数。

模块职责包括：

1. **配置加载**：从 YAML 文件加载全局配置（settings.yaml）和摄像头配置（cameras/*.yaml），支持环境变量替换。
2. **配置合并**：实现全局配置与摄像头配置的分层合并，摄像头配置可覆盖全局默认值。
3. **配置校验**：启动时全面校验所有必填项和格式，发现错误立即报错退出，不带着错误运行。
4. **配置访问**：提供统一的 get 接口，支持点分路径访问（如 "gpu.batch_size"）。
5. **配置热重载**：摄像头配置和规则配置变化时自动重载，全局配置变化需要重启进程。
6. **版本管理**：配置文件顶部的 version 字段，启动时校验版本匹配。

---

## 2. 对外接口

### 2.1 ConfigManager 类

| 方法 | 签名 | 返回值 | 说明 |
|------|------|--------|------|
| __init__ | (config_path: str) -> None | — | 初始化，传入 settings.yaml 的路径 |
| load | () -> None | None | 加载并校验全部配置（全局 + 摄像头 + 规则） |
| get | (path: str, default: Any = None) -> Any | 配置值 | 按点分路径获取全局配置值 |
| get_camera | (camera_id: str) -> dict | 合并后的摄像头配置 | 获取指定摄像头的完整配置（全局+摄像头合并后） |
| list_cameras | () -> list[str] | 摄像头 ID 列表 | 返回所有已配置的摄像头 ID |
| get_all_cameras | () -> dict[str, dict] | 摄像头配置字典 | 返回所有摄像头的合并后配置 |
| reload | () -> None | None | 重新加载配置（全局配置需重启） |
| reload_camera | (camera_id: str) -> bool | 是否成功 | 重新加载指定摄像头的配置 |
| watch | (callback: Callable) -> None | None | 注册配置变化回调函数 |

### 2.2 辅助函数

| 函数 | 签名 | 返回值 | 说明 |
|------|------|--------|------|
| load_yaml | (path: str) -> dict | 解析后的字典 | 加载并解析单个 YAML 文件 |
| substitute_env_vars | (config: dict) -> dict | 替换后的字典 | 递归替换配置中的 ${ENV_VAR} 环境变量 |
| validate_config | (config: dict, schema: dict) -> list[str] | 错误消息列表 | 按 schema 校验配置 |
| deep_merge | (base: dict, override: dict) -> dict | 合并后的字典 | 深度合并两个字典，override 优先 |

---

## 3. 内部逻辑

### 3.1 配置加载流程

1. 读取 settings.yaml 文件，执行 YAML 解析。
2. 对解析结果执行环境变量替换（递归遍历所有字符串值，替换 `${VAR}` 模式）。
3. 校验 version 字段：读取配置中的 version 值，与代码期望的版本号对比。不匹配则报错退出并提示升级脚本路径。
4. 执行全局配置 schema 校验：
   - 检查所有必填字段是否存在。
   - 检查字段类型是否正确。
   - 检查枚举值是否合法。
   - 检查路径类字段（如模型文件路径）是否有效。
5. 扫描 cameras/ 目录下所有 `.yaml` 文件，逐个加载并执行环境变量替换。
6. 对每个摄像头配置执行校验：
   - camera.id 必填且全局唯一。
   - camera.rtsp_url 必须是合法的 RTSP URL 格式。
   - 覆盖的检测参数范围合法。
7. 将校验通过的配置存储在内存中，供其他模块访问。

### 3.2 配置合并逻辑

摄像头配置采用"全局默认 + 摄像头覆盖"的分层模型：

1. 以全局 settings.yaml 中的配置为基础层。
2. 每个摄像头的 YAML 配置为覆盖层。
3. 合并规则：
   - 标量值（int、float、str、bool）：摄像头配置直接覆盖全局配置。
   - 列表值：摄像头配置完全替换全局配置（不追加）。
   - 字典值：深度合并，递归处理子字段。
4. 合并示例：
   - 全局配置 detector.confidence = 0.5，摄像头配置 detection.confidence = 0.6 → 最终为 0.6。
   - 全局配置 gpu.batch_size = 8，摄像头配置未指定 → 最终为 8。
5. 合并时机：`get_camera(camera_id)` 调用时实时合并，确保全局配置变化后摄像头配置立即反映。

### 3.3 环境变量替换

1. 递归遍历配置字典的所有值。
2. 对字符串值，使用正则表达式匹配 `${VARIABLE_NAME}` 模式。
3. 对每个匹配的变量名，从 `os.environ` 中读取值。
4. 若环境变量不存在：
   - 若该字段为敏感信息字段（password、api_key、token、secret），记录 WARNING 日志并替换为空字符串。
   - 若该字段为非敏感信息，记录 WARNING 日志，保留原始 `${VAR}` 文本。
5. 支持默认值语法：`${VAR:-default_value}`，当环境变量不存在时使用默认值。
6. 环境变量替换在配置加载后、校验前执行。

### 3.4 配置校验

启动时执行全面校验，发现任何错误立即报错退出。

**全局配置校验项**：

| 校验项 | 校验规则 | 错误级别 |
|--------|----------|----------|
| version 字段 | 必须存在，值必须与代码版本匹配 | ERROR（退出） |
| system.data_dir | 路径必须可写 | ERROR（退出） |
| system.log_dir | 路径必须可写 | ERROR（退出） |
| gpu.device_id | 必须是 >= 0 的整数，GPU 必须可用 | ERROR（退出） |
| gpu.batch_size | 必须是 1-64 的整数 | ERROR（退出） |
| detector.model_path | 文件必须存在 | ERROR（退出） |
| detector.confidence | 必须是 0.0-1.0 的浮点数 | ERROR（退出） |
| detector.iou | 必须是 0.0-1.0 的浮点数 | ERROR（退出） |
| rules.rules_dir | 目录必须存在（不存在则自动创建） | WARNING |
| llm.api_key | 若 llm.enabled=true，必须已通过环境变量替换为非空值 | WARNING（降级为禁用） |
| storage.type | 必须是 "sqlite" 或 "postgres" | ERROR（退出） |
| web.port | 必须是 1-65535 的整数，且端口未被占用 | ERROR（退出） |

**摄像头配置校验项**：

| 校验项 | 校验规则 | 错误级别 |
|--------|----------|----------|
| camera.id | 必填，格式为字母数字下划线，全局唯一 | ERROR（跳过该路） |
| camera.name | 必填，字符串 | ERROR（跳过该路） |
| camera.rtsp_url | 必填，以 rtsp:// 开头 | ERROR（跳过该路） |
| camera.fps | 1-30 的整数 | WARNING（使用默认值 5） |
| camera.reconnect.max_retries | >= 0 的整数 | WARNING（使用默认值） |

### 3.5 热重载流程

**摄像头配置热重载**：

1. 定期扫描 cameras/ 目录（间隔 10 秒），记录每个文件的 mtime。
2. 若检测到文件变化：
   - 重新加载该 YAML 文件。
   - 执行环境变量替换和校验。
   - 若校验通过：更新内存中的配置，触发 watch 回调通知相关模块。
   - 若校验失败：保留旧配置不变，记录 ERROR 日志。
3. 若检测到新增文件：加载新摄像头配置，触发回调通知启动新采集线程。
4. 若检测到文件删除：触发回调通知停止对应采集线程。

**规则配置热重载**：

1. rules 配置段的热重载由规则引擎模块自行管理（扫描 rules_dir 目录）。
2. 配置管理模块仅负责提供 rules_dir 路径和 hot_reload 开关。
3. 当 rules_dir 路径本身变化时（全局配置重启才能生效），不支持热重载。

**全局配置热重载**：

全局配置（settings.yaml）不支持热重载，原因：
- 全局配置变化可能影响 GPU 设备、模型文件、端口绑定等运行时不可变的资源。
- 热重载全局配置的风险高于收益。
- 用户修改全局配置后需手动重启进程。

### 3.6 配置变化通知机制

1. ConfigManager 维护一个回调函数列表（_watchers: list[Callable]）。
2. 通过 `watch(callback)` 注册回调。
3. 当配置发生变化时（摄像头热重载），遍历回调列表，调用每个回调函数。
4. 回调函数签名：`(change_type: str, camera_id: str 或 None, old_config: dict, new_config: dict) -> None`
5. change_type 枚举值：
   - "camera_added"：新增摄像头
   - "camera_removed"：摄像头配置删除
   - "camera_updated"：摄像头配置变更
6. 下游模块（如 Pipeline）通过回调机制感知配置变化，自行决定如何处理（如重连摄像头、调整检测参数）。

---

## 4. 依赖关系

| 依赖模块 | 依赖方向 | 说明 |
|----------|----------|------|
| os（标准库） | 配置管理 → 系统 | 读取环境变量、检查文件路径 |
| yaml（PyYAML） | 配置管理 → 第三方库 | YAML 文件解析 |
| logging（标准库） | 配置管理 → 系统 | 日志记录 |
| 所有其他模块 | 其他模块 → 配置管理 | 所有模块通过 ConfigManager 获取配置 |

配置管理是系统的基础模块，在启动时最先初始化，在关闭时最后销毁。配置管理不依赖任何其他业务模块。

---

## 5. 配置项

### 5.1 settings.yaml 完整结构

```
version: int                    配置格式版本号（当前为 1）

system:
  name: str                     系统名称（默认 "SentinelMind"）
  data_dir: str                 数据目录路径（默认 "data"）
  log_dir: str                  日志目录路径（默认 "logs"）
  log_level: str                日志级别（DEBUG/INFO/WARNING/ERROR，默认 INFO）
  log_max_size_mb: int          单个日志文件最大大小（默认 50）
  log_backup_count: int         日志备份文件数量（默认 5）

gpu:
  device_id: int                GPU 设备编号（默认 0）
  batch_size: int               推理 batch 大小（默认 8）
  batch_timeout_ms: int         batch 超时毫秒数（默认 50）
  fp16: bool                    是否启用 FP16（默认 true）

detector:
  model_path: str               模型文件路径
  model_name: str               模型名称（默认 "yolo11m"）
  confidence: float             全局置信度阈值（默认 0.5）
  iou: float                    NMS IoU 阈值（默认 0.45）
  input_size: int               输入分辨率（默认 640）
  classes: list[str] 或 null    目标类别过滤（null=全部）

tracker:
  type: str                     追踪器类型（默认 "botsort"）
  track_thresh: float           追踪置信度阈值（默认 0.5）
  track_buffer: int             追踪缓冲帧数（默认 30）

rules:
  rules_dir: str                规则文件目录（默认 "configs/rules"）
  hot_reload: bool              是否热重载（默认 true）
  hot_reload_interval: int      热重载扫描间隔秒数（默认 5）

llm:
  enabled: bool                 是否启用 LLM（默认 true）
  provider: str                 LLM 提供商（默认 "openai_compatible"）
  api_base: str                 API 基础地址
  api_key: str                  API Key（必须用 ${LLM_API_KEY}）
  model: str                    模型名称（默认 "gpt-4o-mini"）
  timeout: int                  超时秒数（默认 30）
  max_retries: int              最大重试次数（默认 2）
  daily_budget: float           每日预算美元（默认 10.0）

rag:
  enabled: bool                 是否启用 RAG（默认 false，第二版实现）
  vector_store: str             向量数据库类型（默认 "chromadb"）
  persist_dir: str              向量数据目录（默认 "data/vector_db"）
  top_k: int                    检索返回条数（默认 5）

notification:
  webhook:
    enabled: bool               是否启用 Webhook（默认 false）
    url: str                    Webhook URL（${WEBHOOK_URL}）
  email:
    enabled: bool               是否启用邮件通知（默认 false）
    smtp_host: str              SMTP 服务器
    smtp_port: int              SMTP 端口
    username: str               用户名
    password: str               密码（${EMAIL_PASS}）
    recipients: list[str]       收件人列表

storage:
  type: str                     存储类型（默认 "sqlite"）
  sqlite:
    path: str                   SQLite 文件路径（默认 "data/sentinelmind.db"）
  postgres:
    host: str                   数据库主机
    port: int                   端口（默认 5432）
    database: str               数据库名
    username: str               用户名
    password: str               密码（${DB_PASS}）

redis:
  enabled: bool                 是否启用 Redis（默认 false）
  host: str                     Redis 主机（默认 "localhost"）
  port: int                     端口（默认 6379）
  password: str                 密码（${REDIS_PASS}，可选）
  db: int                       数据库编号（默认 0）

web:
  host: str                     监听地址（默认 "0.0.0.0"）
  port: int                     端口（默认 8080）
  api_token: str                API 认证 Token（${API_TOKEN}，可选）
  cors_origins: list[str]       CORS 来源列表
```

### 5.2 摄像头配置格式（cameras/cam_XX.yaml）

```
camera:
  id: str                       摄像头 ID（必填，如 "cam_01"）
  name: str                     摄像头名称（必填，如 "仓库入口"）
  rtsp_url: str                 RTSP 地址（必填）
  fps: int                      帧率（可选，默认 5）
  resolution: list[int]         分辨率 [width, height]（可选）
  reconnect:
    initial_delay: int          初始重连延迟秒数（默认 3）
    max_delay: int              最大重连延迟秒数（默认 60）
    max_retries: int            最大重试次数（默认 0=无限）

detection:                      可选，覆盖全局检测配置
  confidence: float             置信度阈值覆盖
  classes: list[str]            目标类别过滤覆盖

rules:                          该摄像头适用的规则列表（可选，缺省=全部规则）
  - rule_name: str              规则名称

recording:
  enabled: bool                 是否启用持续录制（默认 false）
  buffer_duration: int          环形缓冲时长秒数（默认 30）
  format: str                   录制格式（默认 "mp4"）
```

---

## 6. 错误处理

### 6.1 启动阶段错误

| 错误场景 | 处理方式 | 是否阻断启动 |
|----------|----------|-------------|
| settings.yaml 不存在 | 打印错误信息并退出，提示从 .example 复制 | 是 |
| YAML 语法错误 | 打印错误信息（含行号）并退出 | 是 |
| version 字段缺失或不匹配 | 打印错误信息并退出，提示升级脚本 | 是 |
| 必填字段缺失 | 打印错误信息（列出所有缺失字段）并退出 | 是 |
| 字段类型错误 | 打印错误信息并退出 | 是 |
| GPU 设备不可用 | 打印错误信息并退出 | 是 |
| 模型文件不存在 | 打印错误信息并退出 | 是 |
| 端口被占用 | 打印错误信息并退出 | 是 |
| 数据目录不可写 | 打印错误信息并退出 | 是 |
| 环境变量未设置（敏感字段） | 打印 WARNING 日志，替换为空字符串 | 否 |
| 环境变量未设置（非敏感字段） | 打印 WARNING 日志，保留原始文本 | 否 |

### 6.2 运行阶段错误

| 错误场景 | 处理方式 |
|----------|----------|
| 摄像头配置热重载失败（校验不通过） | 保留旧配置，记录 ERROR |
| 摄像头配置文件损坏 | 保留旧配置，记录 ERROR |
| 回调函数执行异常 | 捕获异常，记录 ERROR，不影响其他回调 |
| 配置文件被删除（正在运行时） | 保留内存中的旧配置，记录 WARNING |

### 6.3 配置缺失的容错策略

对于非必填配置项，系统提供合理的默认值，确保最小化配置即可启动。最小配置只需填写：
- camera.id、camera.name、camera.rtsp_url（摄像头三要素）
- detector.model_path（模型路径）

其余所有配置项都有默认值。

---

## 7. 设计决策

### 7.1 为什么使用 YAML 而非 JSON/TOML

YAML 的优势在于：支持注释（JSON 不支持），对非技术人员友好（缩进直观），支持多行字符串（方便填写 prompt 等长文本）。TOML 也支持注释，但在多层嵌套配置时语法不如 YAML 清晰。配置文件由运维人员编辑，可读性是首要考量。

### 7.2 为什么环境变量替换用 ${VAR} 语法而非 .env 文件

${VAR} 语法直接嵌入在 YAML 中，一个文件就能看到所有敏感信息的引用位置，不需要维护额外的 .env 文件映射关系。同时与 Docker、Kubernetes、systemd 等部署环境的环境变量机制完全兼容。支持 `${VAR:-default}` 语法提供了灵活的默认值处理。

### 7.3 为什么全局配置不支持热重载

全局配置涉及 GPU 设备绑定、模型加载、端口绑定等运行时不可变资源。如果热重载 global 配置导致 GPU 设备变更，需要卸载当前模型并重新加载，风险很高且收益很小（全局配置通常只在部署时设置一次）。摄像头配置和规则配置是高频变更的场景，值得支持热重载。

### 7.4 为什么启动时严格校验

"带着错误运行"是生产事故的温床。一个拼写错误的 RTSP 地址会导致该路永远连不上但不会报错；一个错误的置信度阈值可能导致大量误报或漏报。启动时全面校验，将所有问题暴露在部署阶段，避免运行时才发现配置错误。宁可启动失败，也不要带着错误跑。

### 7.5 为什么配置合并是实时的而非预计算的

如果在加载时预计算所有摄像头的合并配置，当全局配置变化时（虽然不支持热重载，但即使是加载阶段的配置调整），需要重新合并所有摄像头。实时合并（get_camera 时合并）保证了每次访问都拿到最新的合并结果，逻辑更简单，且合并操作是纯内存字典操作，开销极小。

### 7.6 为什么使用回调通知而非轮询

当摄像头配置发生变化时，下游模块（如 Pipeline）需要立即响应（启动/停止/重连摄像头）。使用回调通知（观察者模式）比轮询更及时、更高效。回调函数在配置管理的热重载线程中同步执行，保证了配置变化的即时响应。
