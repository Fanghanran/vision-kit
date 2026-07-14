# 异常体系（Exceptions） — 设计书

## 1. 模块职责

统一定义 SentinelMind 所有自定义异常类，建立分层异常体系。替代当前散落在各模块中的重复异常定义（`ConfigError` 在 pipeline.py 和 settings.py 各定义了一次），为调用方提供清晰的捕获粒度。

异常体系设计原则：
- **分层继承**：基类 `VisionAgentError` → 模块级异常 → 具体异常
- **单一定义**：所有异常在 `core/exceptions.py` 中定义一次，各模块 import 使用
- **可捕获粒度**：调用方可按模块捕获（`ConfigError`）或按场景捕获（`ConfigLoadError`）
- **附加上下文**：异常携带结构化上下文（camera_id、rule_name 等），便于日志和监控

---

## 2. 异常层次结构

```
VisionAgentError (基类)
├── ConfigError (配置相关)
│   ├── ConfigLoadError        配置文件加载失败
│   └── ConfigValidationError  配置校验不通过
├── StartupError               系统启动失败
├── CameraError (摄像头相关)
│   ├── CameraConnectionError  摄像头连接失败
│   └── CameraStreamError      视频流读取失败
├── DetectorError (检测器相关)
│   ├── ModelLoadError         模型加载失败
│   └── InferenceError         推理执行失败
├── TrackerError               追踪器异常
├── RuleError (规则引擎相关)
│   ├── RuleLoadError          规则加载失败
│   ├── RuleConfigError        规则配置校验失败
│   └── RuleEvalError          规则评估异常
├── RecorderError              录制器异常
├── StorageError (存储相关)
│   ├── DatabaseError          数据库操作失败
│   └── CacheError             缓存操作失败
├── LLMError (LLM 相关)
│   ├── LLMConnectionError     LLM 连接失败
│   ├── LLMRateLimitError      LLM 限流
│   └── LLMResponseError       LLM 响应解析失败
├── NotifyError                通知发送失败
└── WebError (Web 服务相关)
    ├── APIError               API 请求处理失败
    └── WebSocketError         WebSocket 异常
```

---

## 3. 各异常类定义

### 3.1 基类

| 异常类 | 父类 | 说明 |
|--------|------|------|
| `VisionAgentError` | `Exception` | 所有自定义异常的基类，附带 `message: str` 和 `context: dict` |

### 3.2 配置模块（config/settings.py 使用）

| 异常类 | 父类 | 触发场景 | 当前定义位置 |
|--------|------|----------|-------------|
| `ConfigError` | `VisionAgentError` | 配置相关异常基类 | settings.py:130, pipeline.py:55（重复） |
| `ConfigLoadError` | `ConfigError` | 文件不存在、YAML 语法错误、PyYAML 未安装 | settings.py:134 |
| `ConfigValidationError` | `ConfigError` | 必填字段缺失、类型错误、范围越界、路径不可写 | settings.py:138 |

### 3.3 核心模块（core/ 使用）

| 异常类 | 父类 | 触发场景 | 当前处理方式 |
|--------|------|----------|-------------|
| `StartupError` | `VisionAgentError` | GPU 不可用、模型文件缺失、数据目录不可写 | pipeline.py:51 定义，但未实际使用 |
| `CameraConnectionError` | `CameraError` | FFmpeg 连接失败、RTSP 地址无效 | camera.py 抛 IOError |
| `CameraStreamError` | `CameraError` | 帧读取失败、FFmpeg 进程异常退出 | camera.py 抛 IOError |
| `ModelLoadError` | `DetectorError` | 模型文件不存在、加载失败 | detector.py:219 抛 FileNotFoundError |
| `InferenceError` | `DetectorError` | 推理 OOM、CUDA 错误 | detector.py 捕获 RuntimeError |

### 3.4 规则引擎（rules/ 使用）

| 异常类 | 父类 | 触发场景 | 当前处理方式 |
|--------|------|----------|-------------|
| `RuleError` | `VisionAgentError` | 规则相关异常基类 | — |
| `RuleLoadError` | `RuleError` | YAML 解析失败、Python 扩展导入失败 | engine.py 日志记录 |
| `RuleConfigError` | `RuleError` | zone 顶点不足、threshold 无效、line 退化 | builtin/__init__.py 抛 ValueError |
| `RuleEvalError` | `RuleError` | 单条规则 evaluate 异常 | engine.py 捕获并跳过 |

### 3.5 存储模块（storage/ 使用，待实现）

| 异常类 | 父类 | 触发场景 |
|--------|------|----------|
| `StorageError` | `VisionAgentError` | 存储相关异常基类 |
| `DatabaseError` | `StorageError` | SQL 执行失败、连接超时、事务回滚 |
| `CacheError` | `StorageError` | Redis 连接失败、缓存读写超时 |

### 3.6 LLM 模块（llm/ 使用，待实现）

| 异常类 | 父类 | 触发场景 |
|--------|------|----------|
| `LLMError` | `VisionAgentError` | LLM 相关异常基类 |
| `LLMConnectionError` | `LLMError` | API 端点不可达、DNS 解析失败 |
| `LLMRateLimitError` | `LLMError` | 触发速率限制（HTTP 429） |
| `LLMResponseError` | `LLMError` | 响应格式异常、JSON 解析失败 |

### 3.7 通知模块（actions/ 使用，待实现）

| 异常类 | 父类 | 触发场景 |
|--------|------|----------|
| `NotifyError` | `VisionAgentError` | Webhook 调用失败、邮件发送失败 |

### 3.8 Web 模块（web/ 使用，待实现）

| 异常类 | 父类 | 触发场景 |
|--------|------|----------|
| `WebError` | `VisionAgentError` | Web 服务相关异常基类 |
| `APIError` | `WebError` | 请求参数校验失败、内部处理异常 |
| `WebSocketError` | `WebError` | WebSocket 连接异常 |

---

## 4. 使用规范

### 4.1 抛出规范

```python
# ✅ 正确：附带结构化上下文
raise ConfigLoadError("配置文件不存在", context={"path": str(path)})

# ✅ 正确：附带原始异常链
raise ModelLoadError("模型加载失败", context={"model_path": path}) from original_error

# ❌ 错误：使用裸 Exception
raise Exception("出错了")
```

### 4.2 捕获规范

```python
# ✅ 捕获具体异常
try:
    config_manager.load()
except ConfigLoadError as e:
    logger.error("配置加载失败: %s", e)
    sys.exit(1)
except ConfigValidationError as e:
    logger.error("配置校验失败: %s", e)
    sys.exit(1)

# ✅ 捕获模块级异常（兜底）
try:
    rule_engine.evaluate(...)
except RuleError as e:
    logger.error("规则引擎异常: %s", e)
    # 降级：跳过规则评估
```

### 4.3 迁移计划

现有模块中的异常需要逐步迁移到统一异常类：

| 当前代码 | 迁移目标 |
|---------|---------|
| `pipeline.py` 的 `StartupError` | 删除，从 `core/exceptions.py` 导入 |
| `pipeline.py` 的 `ConfigError` | 删除，从 `core/exceptions.py` 导入 |
| `settings.py` 的三个异常类 | 删除，从 `core/exceptions.py` 导入 |
| `camera.py` 的 `IOError` | 改为 `CameraConnectionError` / `CameraStreamError` |
| `detector.py` 的 `FileNotFoundError` | 改为 `ModelLoadError` |
| `builtin/__init__.py` 的 `ValueError` | 改为 `RuleConfigError` |

---

## 5. 依赖关系

```
core/exceptions.py  ← 不依赖任何其他模块（纯异常定义）
    ↑
    ├── config/settings.py      import ConfigError, ConfigLoadError, ConfigValidationError
    ├── core/pipeline.py        import StartupError, ConfigError
    ├── core/camera.py          import CameraConnectionError, CameraStreamError
    ├── core/detector.py        import ModelLoadError, InferenceError
    ├── core/tracker.py         import TrackerError
    ├── core/recorder.py        import RecorderError
    ├── rules/engine.py         import RuleError, RuleLoadError
    ├── rules/builtin/*.py      import RuleConfigError
    ├── storage/database.py     import DatabaseError
    ├── storage/cache.py        import CacheError
    ├── llm/analyzer.py         import LLMError, LLMConnectionError
    ├── actions/notifier.py     import NotifyError
    └── web/api/*.py            import APIError, WebError
```

`core/exceptions.py` 是零依赖模块，只依赖 Python 标准库 `Exception`。

---

## 6. 当前异常使用统计

| 模块 | 当前使用的异常 | 需要迁移的 |
|------|---------------|-----------|
| config/settings.py | `ConfigError`, `ConfigLoadError`, `ConfigValidationError` | 改为从 exceptions 导入 |
| core/pipeline.py | `StartupError`, `ConfigError`（重复定义） | 删除定义，改为导入 |
| core/camera.py | `IOError`（2处） | 改为 `CameraConnectionError` / `CameraStreamError` |
| core/detector.py | `FileNotFoundError`, `RuntimeError` | 改为 `ModelLoadError` / `InferenceError` |
| rules/builtin/__init__.py | `ValueError`（5处） | 改为 `RuleConfigError` |
| 其他模块 | `Exception` 兜底 | 保持不变（兜底捕获合理） |
