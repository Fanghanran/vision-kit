"""
统一异常体系 — Vision Agent 所有自定义异常

设计来源：docs/modules/core/exceptions.md

异常层次：
    VisionAgentError (基类)
    ├── ConfigError → ConfigLoadError / ConfigValidationError
    ├── StartupError
    ├── CameraError → CameraConnectionError / CameraStreamError
    ├── DetectorError → ModelLoadError / InferenceError
    ├── TrackerError
    ├── RuleError → RuleLoadError / RuleConfigError / RuleEvalError
    ├── RecorderError
    ├── StorageError → DatabaseError / CacheError
    ├── LLMError → LLMConnectionError / LLMRateLimitError / LLMResponseError
    ├── NotifyError
    └── WebError → APIError / WebSocketError
"""

from __future__ import annotations


# ─── 基类 ────────────────────────────────────────────────────


class VisionAgentError(Exception):
    """所有自定义异常的基类"""

    def __init__(self, message: str = "", context: dict | None = None) -> None:
        super().__init__(message)
        self.context = context or {}


# ─── 配置模块 ────────────────────────────────────────────────


class ConfigError(VisionAgentError):
    """配置相关异常基类"""


class ConfigLoadError(ConfigError):
    """配置文件加载失败（文件不存在、YAML 语法错误、依赖缺失）"""


class ConfigValidationError(ConfigError):
    """配置校验不通过（必填字段缺失、类型错误、范围越界）"""


# ─── 启动 ────────────────────────────────────────────────────


class StartupError(VisionAgentError):
    """系统启动失败（GPU 不可用、模型文件缺失、数据目录不可写）"""


# ─── 摄像头模块 ──────────────────────────────────────────────


class CameraError(VisionAgentError):
    """摄像头相关异常基类"""


class CameraConnectionError(CameraError):
    """摄像头连接失败（FFmpeg 启动失败、RTSP 地址无效）"""


class CameraStreamError(CameraError):
    """视频流读取失败（帧读取超时、FFmpeg 进程异常退出）"""


# ─── 检测器模块 ──────────────────────────────────────────────


class DetectorError(VisionAgentError):
    """检测器相关异常基类"""


class ModelLoadError(DetectorError):
    """模型加载失败（文件不存在、格式错误、依赖缺失）"""


class InferenceError(DetectorError):
    """推理执行失败（CUDA OOM、模型崩溃）"""


# ─── 追踪器模块 ──────────────────────────────────────────────


class TrackerError(VisionAgentError):
    """追踪器异常"""


# ─── 规则引擎 ────────────────────────────────────────────────


class RuleError(VisionAgentError):
    """规则引擎相关异常基类"""


class RuleLoadError(RuleError):
    """规则加载失败（YAML 解析失败、Python 扩展导入失败）"""


class RuleConfigError(RuleError):
    """规则配置校验失败（zone 顶点不足、threshold 无效）"""


class RuleEvalError(RuleError):
    """规则评估异常（evaluate 方法执行出错）"""


# ─── 录制器 ──────────────────────────────────────────────────


class RecorderError(VisionAgentError):
    """录制器异常"""


# ─── 存储模块（待实现） ──────────────────────────────────────


class StorageError(VisionAgentError):
    """存储相关异常基类"""


class DatabaseError(StorageError):
    """数据库操作失败（SQL 执行失败、连接超时）"""


class CacheError(StorageError):
    """缓存操作失败（Redis 连接失败、读写超时）"""


# ─── LLM 模块（待实现） ─────────────────────────────────────


class LLMError(VisionAgentError):
    """LLM 相关异常基类"""


class LLMConnectionError(LLMError):
    """LLM 连接失败（API 端点不可达）"""


class LLMRateLimitError(LLMError):
    """LLM 限流（HTTP 429）"""


class LLMResponseError(LLMError):
    """LLM 响应解析失败（格式异常、JSON 解析错误）"""


# ─── 通知模块（待实现） ──────────────────────────────────────


class NotifyError(VisionAgentError):
    """通知发送失败（Webhook 调用失败、邮件发送失败）"""


# ─── Web 模块（待实现） ──────────────────────────────────────


class WebError(VisionAgentError):
    """Web 服务相关异常基类"""


class APIError(WebError):
    """API 请求处理失败"""


class WebSocketError(WebError):
    """WebSocket 连接异常"""
