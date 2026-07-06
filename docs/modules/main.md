# 主入口 — 设计书

## 1. 模块职责

主入口模块（`__main__.py`）是 Vision Agent 的程序入口，负责以下核心任务：

1. **命令行参数解析**：接受 --config（配置文件路径）和 --check（仅校验配置）参数。
2. **配置加载与校验**：加载 YAML 配置文件，校验格式、必填项、运行环境（GPU、端口、文件权限等）。
3. **组件组装**：按依赖顺序创建所有模块实例，通过构造函数注入依赖。
4. **系统启动**：启动各组件的运行线程，进入主循环。
5. **优雅关闭**：捕获系统信号，按依赖逆序停止各组件，确保数据不丢失。
6. **日志初始化**：配置日志级别、文件输出、脱敏 Filter。

核心定位：
- 是系统中唯一知道所有模块存在的地方（组装层），其他模块通过 Protocol 接口交互
- 不包含业务逻辑，只负责"组装"和"生命周期管理"
- 出错时快速失败（fail fast），不做隐式降级

## 2. 对外接口

### 命令行参数

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| --config | str | 否 | "configs/settings.yaml" | 配置文件路径（相对于项目根目录或绝对路径） |
| --check | flag | 否 | False | 仅校验配置和运行环境，不启动系统。校验通过输出 "Config OK" 并以 exit code 0 退出，失败输出错误信息并以 exit code 1 退出 |
| --log-level | str | 否 | 从配置读取 | 覆盖配置文件中的日志级别（DEBUG/INFO/WARNING/ERROR） |
| --version | flag | 否 | False | 显示版本号并退出 |

### 退出码

| 退出码 | 含义 |
|--------|------|
| 0 | 正常退出（启动后收到 SIGINT/SIGTERM 并优雅关闭，或 --check 通过） |
| 1 | 启动失败（配置错误、环境校验失败、组件初始化失败） |
| 2 | 参数错误（命令行参数不合法） |

### 调用方式

```bash
# 正常启动
python -m vision_agent --config configs/settings.yaml

# 校验配置
python -m vision_agent --check --config configs/settings.yaml

# 覆盖日志级别
python -m vision_agent --config configs/settings.yaml --log-level DEBUG

# 显示版本
python -m vision_agent --version
```

## 3. 内部逻辑

### 3.1 启动流程总览

```
main()
  ├── 解析命令行参数
  ├── --version → 打印版本，退出
  ├── 加载配置文件
  │     ├── 文件不存在 → 报错退出
  │     ├── YAML 解析失败 → 报错退出
  │     └── 配置版本校验 → 不匹配则报错退出
  ├── 校验配置
  │     ├── 格式校验（必填项、类型）
  │     ├── 环境校验（GPU、端口、文件权限）
  │     └── --check 模式 → 校验完毕后退出
  ├── 初始化日志
  │     ├── 设置日志级别
  │     ├── 配置 RotatingFileHandler
  │     └── 注册日志脱敏 Filter
  ├── 组装组件（按依赖顺序）
  ├── 注册信号处理器
  ├── 启动各组件
  ├── 进入主循环（等待退出信号）
  └── 优雅关闭
```

### 3.2 配置加载

1. **读取配置文件**：使用 PyYAML 加载指定路径的 YAML 文件。
2. **文件不存在处理**：捕获 FileNotFoundError，输出清晰的错误提示："配置文件不存在：{path}。请从 configs/settings.yaml.example 复制并填写配置。"，以 exit code 1 退出。
3. **YAML 语法错误**：捕获 yaml.YAMLError，输出错误详情（包含行号和列号），以 exit code 1 退出。
4. **环境变量替换**：扫描配置中的 `${VAR_NAME}` 模式，替换为对应的环境变量值。若环境变量不存在：
   - 必填项（api_key、api_token 等）：报错退出，提示 "环境变量 {VAR_NAME} 未设置"
   - 可选项：使用默认值
5. **配置版本校验**：读取配置顶层 version 字段，与代码期望的版本对比。不匹配则输出版本不匹配的错误信息和升级脚本路径，以 exit code 1 退出。

### 3.3 配置校验

配置校验分为两层：格式校验和环境校验。

**格式校验**：

| 校验项 | 规则 | 错误处理 |
|--------|------|---------|
| system.data_dir | 非空字符串 | 缺失则报错 |
| system.log_dir | 非空字符串 | 缺失则报错 |
| gpu.device_id | 非负整数 | 缺失则报错 |
| detector.model_path | 非空字符串 | 缺失则报错 |
| detector.confidence | 0-1 浮点数 | 缺失则报错 |
| llm.enabled | bool | 默认 True |
| llm.model | 非空字符串 | llm.enabled=true 时必填 |
| llm.api_base | 合法 URL | llm.enabled=true 时必填 |
| notification.webhook.url | 非空字符串 | webhook.enabled=true 时必填 |
| web.port | 1-65535 整数 | 默认 8080 |

**环境校验**：

| 校验项 | 规则 | 错误处理 |
|--------|------|---------|
| GPU 可用性 | 检查 torch.cuda.is_available() 和指定 device_id | 不可用则 WARNING（CPU 模式降级），若配置要求 GPU 则 ERROR |
| 模型文件存在 | 检查 detector.model_path 指向的文件是否存在 | 不存在则报错 |
| 端口占用 | 检查 web.port 是否被占用 | 被占用则报错 |
| 数据目录可写 | 检查 system.data_dir 是否存在且可写 | 不存在则尝试创建，创建失败则报错 |
| 日志目录可写 | 检查 system.log_dir 是否存在且可写 | 同上 |
| FFmpeg 可用 | 执行 ffmpeg -version 检查是否安装 | 不可用则 WARNING |
| 摄像头目录权限 | 检查 configs/cameras/ 权限是否为 600 | 权限过宽则 WARNING 并提示修复命令 |

**--check 模式**：

当命令行指定了 --check 参数时：
1. 执行完所有格式校验和环境校验。
2. 所有校验通过：输出 "Config OK" 和配置摘要（摄像头数量、规则数量、LLM 状态、通知渠道），以 exit code 0 退出。
3. 任何校验失败：输出所有错误信息（一次性输出，不逐个退出），以 exit code 1 退出。

### 3.4 日志初始化

1. **根日志器配置**：
   - 日志级别：优先使用命令行 --log-level 参数，否则从配置读取，默认 INFO
   - 日志格式：`%(asctime)s %(levelname)-5s [%(name)s] %(message)s`
   - 日期格式：`%Y-%m-%d %H:%M:%S`

2. **文件输出**：
   - 路径：`{system.log_dir}/vision_agent.log`
   - 使用 RotatingFileHandler
   - 单文件最大 50MB（maxBytes=50*1024*1024）
   - 保留 5 个历史文件（backupCount=5）

3. **控制台输出**：
   - 使用 StreamHandler（stdout）
   - 格式简化：`%(asctime)s %(levelname)-5s %(message)s`（不含模块名，开发时更简洁）
   - 仅输出 INFO 及以上级别

4. **日志脱敏 Filter**：
   - 创建 SensitivityFilter 实例
   - 注册到文件 Handler 和控制台 Handler
   - 自动替换日志中的敏感信息

5. **第三方库日志级别**：
   - uvicorn.access：WARNING（减少请求日志噪音）
   - httpx：WARNING（减少 HTTP 客户端日志噪音）

### 3.5 组件组装顺序

组装严格按照依赖关系的拓扑顺序进行，确保创建每个组件时其依赖已就绪。

```
步骤 1：Config（配置对象）
  → 从已加载的 YAML 构建配置对象
  → 所有后续组件通过 config 对象读取各自的配置段

步骤 2：Storage（存储层）
  → 创建 Database 实例（SQLite 连接）
  → 初始化数据库表（若不存在则创建）

步骤 3：Cache（缓存层）
  → 若配置启用 Redis，尝试连接 Redis
  → 连接失败则降级为内存缓存
  → 若配置未启用 Redis，直接使用内存缓存

步骤 4：Detector（检测器）
  → 创建 YOLODetector 实例
  → 调用 warmup() 预热模型到 GPU

步骤 5：Tracker（追踪器）
  → 创建 BoTSORTTracker 实例（依赖 config.tracker 配置）

步骤 6：RuleEngine（规则引擎）
  → 加载规则目录下的所有 YAML 规则文件
  → 动态加载 Python 插件规则（若有）
  → 初始化三层防线（去重、冷却、时间窗口）

步骤 7：LLM Provider + Analyzer（LLM 层）
  → 若 llm.enabled=true：
    → 创建 OpenAICompatibleProvider 实例
    → 创建 LLMAnalyzer 实例，注入 provider
  → 若 llm.enabled=false：
    → provider = None
    → analyzer = None（或不传入 provider 的 analyzer）

步骤 8：Notifier（通知器）
  → 若 webhook.enabled=true：创建 WebhookNotifier 实例
  → 若 email.enabled=true：创建 EmailNotifier 实例
  → 将所有已启用的通知器注册到规则引擎的 actions 列表

步骤 9：Pipeline（主管线）
  → 创建 Pipeline 实例，注入所有组件
  → Pipeline 内部创建 FrameQueue、ResultQueue
  → Pipeline 内部创建 CameraThread（每路一个）、InferenceThread、ActionThread

步骤 10：Web Server（Web 服务）
  → 创建 FastAPI 应用实例
  → 注入 Storage 引用（供 API 查询数据）
  → 注入 Pipeline 引用（供获取实时状态）
  → 配置 CORS、认证、路径白名单
```

### 3.6 启动各组件

组装完成后，按依赖正序启动：

1. **启动 Storage**：数据库连接已在组装阶段建立。
2. **启动 CameraThread × N**：每路摄像头启动独立线程，开始采集帧。
3. **启动 InferenceThread**：开始从 FrameQueue 取帧推理。
4. **启动 ActionThread**：开始从 ResultQueue 取结果处理。
5. **启动 Web Server**：在独立线程中启动 uvicorn。
6. **启动数据清理定时任务**：后台定时清理过期截图和视频。

启动顺序的关键原则：先启动下游（ActionThread），再启动上游（CameraThread），避免下游未就绪时上游就开始产生数据导致队列积压。但由于队列是有界的，即使顺序不对也不会出问题，只是可能丢弃最初几帧。

### 3.7 主循环

启动完成后，主线程进入等待状态：

1. 使用 `threading.Event.wait()` 阻塞主线程，等待退出信号。
2. 主线程不做任何业务处理，仅等待。
3. 退出信号通过 `_shutdown_event.set()` 触发。

### 3.8 信号处理

注册 SIGINT 和 SIGTERM 信号处理器：

1. **SIGINT**（Ctrl+C）：用户在终端按下 Ctrl+C 时触发。
2. **SIGTERM**（systemd stop / kill）：进程管理器停止服务时触发。

信号处理函数的逻辑：
1. 记录 INFO 日志："收到退出信号，开始优雅关闭..."
2. 设置 `_shutdown_event`，唤醒主循环。
3. 信号处理器不直接执行关闭逻辑（避免信号处理器中的复杂操作），只设置标志位。

### 3.9 优雅关闭

收到退出信号后，按依赖逆序关闭各组件：

```
步骤 1：停止采集层
  → 通知所有 CameraThread 停止
  → 等待 FrameQueue 排空或超时 3 秒
  → 等待所有 CameraThread 结束

步骤 2：停止推理层
  → 通知 InferenceThread 停止
  → 等待 ResultQueue 排空或超时 3 秒
  → 等待 InferenceThread 结束

步骤 3：停止处理层
  → 通知 ActionThread 停止（完成当前正在处理的告警）
  → 等待 ActionThread 结束

步骤 4：停止 Web 服务
  → 通知 uvicorn 关闭
  → 断开所有 WebSocket 连接
  → 等待 Web 线程结束

步骤 5：停止数据清理
  → 取消定时任务

步骤 6：关闭存储
  → 关闭数据库连接
  → 关闭 Redis 连接（若启用）

步骤 7：释放 Detector 资源
  → 调用 detector.release() 释放 GPU 显存

步骤 8：关闭 LLM Provider
  → 调用 provider.close() 关闭 HTTP 客户端

步骤 9：记录关闭完成日志
  → INFO: "系统已优雅关闭"
```

每个步骤都有超时保护，单个组件关闭超时不超过 10 秒。超时后记录 WARNING 日志并继续下一步，确保关闭流程不会卡住。

### 3.10 异常处理（未捕获异常）

main 函数顶层包裹 try-except：

1. **KeyboardInterrupt**：等同于 SIGINT，触发优雅关闭。
2. **其他未捕获异常**：记录 FATAL 日志（包含完整堆栈），触发优雅关闭，以 exit code 1 退出。
3. **组件初始化失败**：在组装阶段捕获，记录 ERROR 日志并立即退出（fail fast），不尝试启动系统。

## 4. 依赖关系

| 依赖项 | 类型 | 说明 |
|--------|------|------|
| config | 模块依赖 | 加载和校验配置 |
| core/camera | 模块依赖 | 创建 CameraThread |
| core/detector | 模块依赖 | 创建 YOLODetector |
| core/tracker | 模块依赖 | 创建 BoTSORTTracker |
| core/pipeline | 模块依赖 | 创建 Pipeline（主管线） |
| core/recorder | 模块依赖 | 视频片段录制 |
| rules/engine | 模块依赖 | 创建规则引擎 |
| llm/provider | 模块依赖 | 创建 LLM 提供者 |
| llm/analyzer | 模块依赖 | 创建 LLM 分析器 |
| actions/notifier | 模块依赖 | 创建通知器 |
| storage/database | 模块依赖 | 创建存储层 |
| storage/cache | 模块依赖 | 创建缓存层 |
| web/api | 模块依赖 | 创建 Web 服务 |
| argparse | 标准库 | 命令行参数解析 |
| signal | 标准库 | 系统信号处理 |
| threading | 标准库 | 线程管理和事件同步 |
| logging | 标准库 | 日志初始化 |
| pathlib | 标准库 | 文件路径处理 |
| sys | 标准库 | 退出码设置 |
| yaml | 运行时依赖 | YAML 配置解析 |
| torch | 运行时依赖 | GPU 可用性检查 |

### 依赖方向

`__main__.py` 是系统的组装根节点，它知道所有模块的存在，但所有业务模块之间通过 Protocol 接口交互，不直接依赖具体实现。这是"组装根"模式的典型应用：依赖关系呈扇形从 main 向外辐射，业务模块之间无循环依赖。

## 5. 配置项

`__main__.py` 本身不定义配置项，但读取以下配置来初始化日志和校验环境：

| 配置项 | 说明 |
|--------|------|
| version | 配置格式版本号，启动时校验 |
| system.name | 系统名称，用于日志和显示 |
| system.data_dir | 数据目录路径 |
| system.log_dir | 日志目录路径 |
| system.log_level | 日志级别 |
| gpu.device_id | GPU 编号 |

其余配置项由各自模块读取，main 只负责传递 config 对象。

## 6. 错误处理

### 6.1 启动阶段错误（fail fast）

启动阶段的任何错误都不做降级，直接报错退出：

| 错误场景 | 处理方式 | 退出码 |
|----------|----------|--------|
| 配置文件不存在 | 输出文件路径和提示信息 | 1 |
| YAML 语法错误 | 输出行号和错误描述 | 1 |
| 配置版本不匹配 | 输出版本信息和升级脚本路径 | 1 |
| 必填配置项缺失 | 输出缺失的配置项名称 | 1 |
| 环境变量未设置 | 输出环境变量名称 | 1 |
| 模型文件不存在 | 输出文件路径 | 1 |
| 端口被占用 | 输出端口号和占用进程信息 | 1 |
| 数据目录不可写 | 输出目录路径和权限信息 | 1 |
| GPU 不可用（且配置要求 GPU） | 输出 GPU 状态 | 1 |
| 组件初始化失败 | 输出组件名称和错误详情 | 1 |

理由：Vision Agent 是长时间运行的监控系统，启动阶段的配置错误如果被忽略，会在运行时产生不可预测的行为（如连接错误的摄像头、使用错误的模型）。fail fast 策略确保系统要么以正确配置运行，要么不运行。

### 6.2 运行阶段错误（容错降级）

运行阶段的错误由各组件独立处理（参见各模块设计书），main 不干预。main 只处理以下情况：

| 情况 | 处理方式 |
|------|----------|
| 某个线程意外退出 | 记录 ERROR 日志，评估是否需要关闭系统 |
| 不可恢复的致命错误 | 触发优雅关闭流程 |

### 6.3 关闭阶段错误

| 错误场景 | 处理方式 |
|----------|----------|
| 单个组件关闭超时 | 记录 WARNING 日志，继续关闭下一个组件 |
| 单个组件关闭异常 | 记录 ERROR 日志，继续关闭下一个组件 |
| 数据库关闭失败 | 记录 ERROR 日志，不重试 |

关闭流程不因单个组件的失败而中断，确保所有组件都能得到关闭机会。

### 6.4 日志规范

main 模块的日志使用 `[main]` 模块标识：

| 事件 | 级别 | 格式 |
|------|------|------|
| 配置加载成功 | INFO | `[main] config_loaded path={path} cameras={n} rules={n}` |
| 配置校验通过 | INFO | `[main] config_validated cameras={n} rules={n} llm={enabled}` |
| 组件创建 | INFO | `[main] component_created name={component}` |
| 组件启动 | INFO | `[main] component_started name={component}` |
| 系统启动完成 | INFO | `[main] system_started cameras={n} port={port}` |
| 收到退出信号 | INFO | `[main] shutdown_signal signal={SIGINT/SIGTERM}` |
| 关闭步骤 | INFO | `[main] shutting_down step={step_name}` |
| 关闭完成 | INFO | `[main] system_stopped uptime={seconds}s` |
| 启动失败 | ERROR | `[main] startup_failed step={step} error={error}` |
| 关闭超时 | WARNING | `[main] shutdown_timeout component={name} timeout={seconds}s` |

## 7. 设计决策

### 7.1 组装根模式（Composition Root）

决策：所有组件的创建和依赖注入集中在 `__main__.py` 中完成。

理由：集中组装使得依赖关系一目了然，任何模块的依赖变更只需要修改 main 一个文件。各业务模块通过 Protocol 接口交互，不知道也不关心具体实现是什么，符合依赖倒置原则。对比分散式组装（每个模块自行创建依赖），集中式更容易测试和维护，也更容易支持配置驱动的组件替换（如切换检测器、切换存储后端）。

### 7.2 --check 模式

决策：支持 `--check` 参数仅校验配置，不启动系统。

理由：在生产环境中部署前需要验证配置的正确性。--check 模式允许在部署脚本中先校验配置，失败则不执行后续部署步骤，避免因配置错误导致服务启动失败后需要人工排查。它也方便 CI/CD 流水线集成配置校验。

### 7.3 优雅关闭按依赖逆序

决策：关闭顺序为采集→推理→处理→Web→存储，与启动顺序相反。

理由：先停上游再停下游，确保：
- 停止采集后，FrameQueue 中的剩余帧可以被推理层消费完
- 停止推理后，ResultQueue 中的剩余结果可以被处理层消费完
- 处理层完成当前告警后再关闭，确保告警通知不丢失
- 最后关闭存储，确保所有数据写入完成

如果反过来先关存储再关处理，会导致处理层写入数据时数据库已关闭，产生错误。

### 7.4 信号处理器只设标志位

决策：信号处理函数只设置 threading.Event，不直接执行关闭逻辑。

理由：信号处理器在信号处理上下文中执行，有诸多限制（如不能获取锁、不能做 IO 操作）。如果在信号处理器中执行复杂的关闭逻辑，可能引发死锁或其他不可预期的行为。设置标志位后，由主循环检测标志位并执行关闭逻辑，确保关闭过程在正常执行上下文中进行。

### 7.5 单个组件关闭超时不超过 10 秒

决策：每个组件的关闭等待有 10 秒超时，超时后强制继续。

理由：系统可能部署在 systemd 等进程管理器下，这些管理器通常有强制 kill 的超时时间（如 systemd 默认 90 秒的 TimeoutStopSec）。如果某个组件的关闭卡住导致整个关闭流程超过管理器的超时时间，进程会被强制 kill（SIGKILL），无法记录日志。10 秒的单步超时确保总关闭时间可控（约 60-80 秒，6-8 个组件），在管理器强制 kill 之前完成。

### 7.6 日志初始化早于组件组装

决策：在配置加载完成后、组件组装之前初始化日志系统。

理由：组件组装过程中会输出大量日志（组件创建、模型加载、连接建立等），如果日志系统未初始化，这些日志会丢失或格式不规范。早期初始化确保所有组件的生命周期事件都被完整记录。
