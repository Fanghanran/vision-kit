"""
配置管理模块 — YAML 配置加载、校验、环境变量替换、热重载

设计来源：docs/modules/config/config.md

职责：
- 从 settings.yaml + cameras/*.yaml 加载配置
- 环境变量替换（${VAR} 和 ${VAR:-default} 语法）
- 全局配置 + 摄像头配置的分层合并
- 启动时全面校验（必填项、类型、范围、路径）
- 摄像头配置热重载（文件监控 + 回调通知）
- 全局配置不支持热重载（GPU/模型/端口等运行时不可变资源）
"""

from __future__ import annotations

import copy
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

# 当前配置格式版本号
EXPECTED_VERSION = 1

# 环境变量正则：匹配 ${VAR} 和 ${VAR:-default}
_ENV_PATTERN = re.compile(r"\$\{([^}:]+)(?::-(.*?))?\}")

# 敏感字段关键字（环境变量缺失时替换为空字符串而非保留原文）
_SENSITIVE_KEYS = {"password", "api_key", "token", "secret"}

# 默认配置值
_DEFAULTS: dict[str, Any] = {
    "version": 1,
    "system": {
        "name": "Vision Agent",
        "data_dir": "data",
        "log_dir": "logs",
        "log_level": "INFO",
        "log_max_size_mb": 50,
        "log_backup_count": 5,
    },
    "gpu": {
        "device_id": 0,
        "batch_size": 8,
        "batch_timeout_ms": 50,
        "fp16": True,
    },
    "detector": {
        "model_path": "",
        "model_name": "yolo11m",
        "confidence": 0.5,
        "iou": 0.45,
        "input_size": 640,
        "classes": None,
    },
    "tracker": {
        "type": "botsort",
        "track_thresh": 0.5,
        "track_buffer": 30,
    },
    "rules": {
        "rules_dir": "configs/rules",
        "hot_reload": True,
        "hot_reload_interval": 5,
    },
    "llm": {
        "enabled": True,
        "provider": "openai_compatible",
        "api_base": "",
        "api_key": "",
        "model": "gpt-4o-mini",
        "timeout": 30,
        "max_retries": 2,
        "daily_budget": 10.0,
    },
    "rag": {
        "enabled": False,
        "vector_store": "chromadb",
        "persist_dir": "data/vector_db",
        "top_k": 5,
    },
    "notification": {
        "webhook": {"enabled": False, "url": ""},
        "email": {
            "enabled": False,
            "smtp_host": "",
            "smtp_port": 587,
            "username": "",
            "password": "",
            "recipients": [],
        },
    },
    "storage": {
        "type": "sqlite",
        "sqlite": {"path": "data/vision_agent.db"},
        "postgres": {
            "host": "",
            "port": 5432,
            "database": "",
            "username": "",
            "password": "",
        },
    },
    "redis": {
        "enabled": False,
        "host": "localhost",
        "port": 6379,
        "password": "",
        "db": 0,
    },
    "web": {
        "host": "0.0.0.0",
        "port": 8080,
        "api_token": "",
        "cors_origins": ["*"],
    },
}


# ─── 异常（统一定义在 core/exceptions.py）────────────────────

from vision_agent.core.exceptions import (  # noqa: E402, F401
    ConfigError,  # exported for downstream use
    ConfigLoadError,
    ConfigValidationError,
)


# ─── 辅助函数 ────────────────────────────────────────────────


def load_yaml(path: str | Path) -> dict[str, Any]:
    """加载并解析单个 YAML 文件

    Args:
        path: YAML 文件路径

    Returns:
        解析后的字典

    Raises:
        ConfigLoadError: 文件不存在或 YAML 语法错误
    """
    path = Path(path)
    if not path.exists():
        raise ConfigLoadError(f"配置文件不存在: {path}")

    try:
        import yaml
    except ImportError:
        raise ConfigLoadError("PyYAML 未安装，请执行: pip install pyyaml")

    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise ConfigLoadError(f"YAML 解析失败: {path} - {e}")

    if not isinstance(data, dict):
        raise ConfigLoadError(f"配置文件根元素必须是字典: {path}")

    return data


def substitute_env_vars(config: dict[str, Any]) -> dict[str, Any]:
    """递归替换配置中的 ${VAR} 和 ${VAR:-default} 环境变量

    设计来源：config.md 3.3 节

    - 敏感字段（password/api_key/token/secret）缺失时替换为空字符串
    - 非敏感字段缺失时保留原始 ${VAR} 文本
    - 支持 ${VAR:-default} 默认值语法

    Args:
        config: 原始配置字典

    Returns:
        替换后的配置字典（深拷贝，不修改原字典）
    """
    return _substitute_recursive(copy.deepcopy(config), path="")


def _substitute_recursive(value: Any, path: str) -> Any:
    """递归替换辅助函数"""
    if isinstance(value, str):
        return _substitute_string(value, path)
    elif isinstance(value, dict):
        return {
            k: _substitute_recursive(v, f"{path}.{k}" if path else k)
            for k, v in value.items()
        }
    elif isinstance(value, list):
        return [
            _substitute_recursive(item, f"{path}[{i}]") for i, item in enumerate(value)
        ]
    return value


def _substitute_string(value: str, path: str) -> str:
    """替换字符串中的环境变量引用"""

    def replacer(match: re.Match) -> str:
        var_name = match.group(1)
        default_value = match.group(2)  # None if no :- syntax

        env_value = os.environ.get(var_name)
        if env_value is not None:
            return env_value

        # 环境变量不存在
        if default_value is not None:
            return default_value

        # 判断是否为敏感字段
        path_lower = path.lower()
        is_sensitive = any(kw in path_lower for kw in _SENSITIVE_KEYS)

        if is_sensitive:
            logger.warning("env_missing_sensitive path=%s var=%s", path, var_name)
            return ""
        else:
            logger.warning("env_missing path=%s var=%s", path, var_name)
            return match.group(0)  # 保留原始 ${VAR} 文本

    return _ENV_PATTERN.sub(replacer, value)


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """深度合并两个字典，override 优先

    设计来源：config.md 3.2 节

    - 标量值：override 直接覆盖 base
    - 列表值：override 完全替换 base
    - 字典值：递归合并

    Args:
        base: 基础字典
        override: 覆盖字典

    Returns:
        合并后的字典（新字典，不修改输入）
    """
    result = copy.deepcopy(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


# ─── 校验规则 ────────────────────────────────────────────────


@dataclass
class ValidationResult:
    """校验结果"""

    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return len(self.errors) == 0

    def add_error(self, msg: str) -> None:
        self.errors.append(msg)

    def add_warning(self, msg: str) -> None:
        self.warnings.append(msg)


def validate_global_config(config: dict[str, Any]) -> ValidationResult:
    """校验全局配置（config.md 3.4 节）

    Args:
        config: 全局配置字典（已合并默认值）

    Returns:
        校验结果
    """
    result = ValidationResult()

    # version
    version = config.get("version")
    if version is None:
        result.add_error("缺少 version 字段")
    elif version != EXPECTED_VERSION:
        result.add_error(f"version 不匹配: 配置={version}, 期望={EXPECTED_VERSION}")

    # system
    system = config.get("system", {})
    for dir_key in ("data_dir", "log_dir"):
        dir_path = system.get(dir_key, "")
        if dir_path:
            try:
                Path(dir_path).mkdir(parents=True, exist_ok=True)
            except OSError as e:
                result.add_error(f"system.{dir_key} 不可写: {dir_path} - {e}")

    log_level = system.get("log_level", "INFO")
    if log_level not in ("DEBUG", "INFO", "WARNING", "ERROR"):
        result.add_error(f"system.log_level 无效: {log_level}")

    # gpu
    gpu = config.get("gpu", {})
    device_id = gpu.get("device_id", 0)
    if not isinstance(device_id, int) or device_id < 0:
        result.add_error(f"gpu.device_id 必须是 >= 0 的整数: {device_id}")

    batch_size = gpu.get("batch_size", 8)
    if not isinstance(batch_size, int) or not (1 <= batch_size <= 64):
        result.add_error(f"gpu.batch_size 必须是 1-64 的整数: {batch_size}")

    # detector
    detector = config.get("detector", {})
    model_path = detector.get("model_path", "")
    if not model_path:
        result.add_error("detector.model_path 不能为空")
    elif not Path(model_path).exists():
        result.add_error(f"detector.model_path 文件不存在: {model_path}")

    confidence = detector.get("confidence", 0.5)
    if not isinstance(confidence, (int, float)) or not (0.0 <= confidence <= 1.0):
        result.add_error(f"detector.confidence 必须是 0.0-1.0: {confidence}")

    iou = detector.get("iou", 0.45)
    if not isinstance(iou, (int, float)) or not (0.0 <= iou <= 1.0):
        result.add_error(f"detector.iou 必须是 0.0-1.0: {iou}")

    # storage
    storage = config.get("storage", {})
    storage_type = storage.get("type", "sqlite")
    if storage_type not in ("sqlite", "postgres"):
        result.add_error(f"storage.type 必须是 sqlite 或 postgres: {storage_type}")

    # web
    web = config.get("web", {})
    port = web.get("port", 8080)
    if not isinstance(port, int) or not (1 <= port <= 65535):
        result.add_error(f"web.port 必须是 1-65535 的整数: {port}")

    # rules
    rules = config.get("rules", {})
    rules_dir = rules.get("rules_dir", "configs/rules")
    if rules_dir:
        try:
            Path(rules_dir).mkdir(parents=True, exist_ok=True)
        except OSError:
            result.add_warning(f"rules.rules_dir 创建失败: {rules_dir}")

    # llm
    llm = config.get("llm", {})
    if llm.get("enabled", False):
        api_key = llm.get("api_key", "")
        if not api_key:
            result.add_warning("llm.enabled=true 但 api_key 为空，LLM 将被禁用")

    return result


def validate_camera_config(
    cam_config: dict[str, Any], existing_ids: set[str]
) -> ValidationResult:
    """校验单个摄像头配置（config.md 3.4 节）

    Args:
        cam_config: 摄像头配置字典
        existing_ids: 已有的摄像头 ID 集合（用于唯一性检查）

    Returns:
        校验结果
    """
    result = ValidationResult()
    camera = cam_config.get("camera", {})

    # camera.id
    cam_id = camera.get("id", "")
    if not cam_id:
        result.add_error("camera.id 不能为空")
    elif not re.match(r"^[a-zA-Z0-9_]+$", cam_id):
        result.add_error(f"camera.id 格式不合法（仅允许字母数字下划线）: {cam_id}")
    elif cam_id in existing_ids:
        result.add_error(f"camera.id 重复: {cam_id}")

    # camera.name
    if not camera.get("name", ""):
        result.add_error("camera.name 不能为空")

    # camera.rtsp_url
    rtsp_url = camera.get("rtsp_url", "")
    if not rtsp_url:
        result.add_error("camera.rtsp_url 不能为空")
    elif not rtsp_url.startswith("rtsp://"):
        result.add_error(f"camera.rtsp_url 必须以 rtsp:// 开头: {rtsp_url}")

    # camera.fps
    fps = camera.get("fps", 5)
    if fps is not None and (not isinstance(fps, int) or not (1 <= fps <= 30)):
        result.add_warning(f"camera.fps 不在 1-30 范围，使用默认值 5: {fps}")

    return result


# ─── ConfigManager 主类 ──────────────────────────────────────


class ConfigManager:
    """配置管理器（config.md 2.1 节）

    生命周期：__init__ → load → [get/get_camera/reload] → (销毁)

    - 全局配置不支持热重载（GPU/模型/端口等运行时不可变资源）
    - 摄像头配置支持热重载（文件监控 + 回调通知）
    """

    def __init__(self, config_path: str | Path):
        """初始化配置管理器

        Args:
            config_path: settings.yaml 的路径
        """
        self._config_path = Path(config_path)
        self._config_dir = self._config_path.parent
        self._global_config: dict[str, Any] = {}
        self._camera_configs: dict[str, dict[str, Any]] = {}  # camera_id → raw config
        self._file_to_cam_id: dict[str, str] = {}  # 文件路径 → camera_id
        self._watchers: list[Callable] = []
        self._lock = threading.Lock()
        self._loaded = False

        # 热重载相关
        self._hot_reload_enabled = True
        self._hot_reload_interval = 10.0
        self._camera_mtimes: dict[str, float] = {}  # 文件路径 → mtime
        self._reload_thread: threading.Thread | None = None
        self._running = False

    # ─── 公开接口 ──────────────────────────────────────────────

    def load(self) -> None:
        """加载并校验全部配置（config.md 3.1 节）

        流程：加载 YAML → 环境变量替换 → 合并默认值 → 校验 → 加载摄像头配置

        Raises:
            ConfigLoadError: 文件不存在或 YAML 语法错误
            ConfigValidationError: 配置校验不通过
        """
        # 1. 加载 settings.yaml
        raw_config = load_yaml(self._config_path)

        # 2. 环境变量替换
        raw_config = substitute_env_vars(raw_config)

        # 3. 合并默认值
        self._global_config = deep_merge(_DEFAULTS, raw_config)

        # 4. 校验全局配置
        validation = validate_global_config(self._global_config)
        for warning in validation.warnings:
            logger.warning("config_warning %s", warning)
        if not validation.is_valid:
            errors_str = "; ".join(validation.errors)
            raise ConfigValidationError(f"全局配置校验失败: {errors_str}")

        # 5. 加载摄像头配置
        self._load_camera_configs()

        # 6. 校验热重载配置
        self._hot_reload_enabled = self._global_config.get("rules", {}).get(
            "hot_reload", True
        )
        self._hot_reload_interval = float(
            self._global_config.get("rules", {}).get("hot_reload_interval", 10)
        )

        self._loaded = True
        logger.info(
            "config_loaded path=%s cameras=%d",
            self._config_path,
            len(self._camera_configs),
        )

    def get(self, path: str, default: Any = None) -> Any:
        """按点分路径获取全局配置值

        Args:
            path: 点分路径，如 "gpu.batch_size"
            default: 路径不存在时的默认值

        Returns:
            配置值
        """
        return _get_nested(self._global_config, path, default)

    def get_camera(self, camera_id: str) -> dict[str, Any] | None:
        """获取指定摄像头的完整配置（全局 + 摄像头合并后）

        设计来源：config.md 3.2 节 — 实时合并

        Args:
            camera_id: 摄像头 ID

        Returns:
            合并后的配置字典，不存在则返回 None
        """
        with self._lock:
            cam_raw = self._camera_configs.get(camera_id)
            if cam_raw is None:
                return None
            cam_raw = copy.deepcopy(cam_raw)

        # 全局配置作为基础，摄像头配置覆盖
        # 只合并与摄像头相关的配置段
        merged = {}
        for section in ("detector", "tracker", "recording", "rules"):
            global_section = self._global_config.get(section, {})
            cam_section = cam_raw.get(section, {})
            if global_section or cam_section:
                merged[section] = deep_merge(global_section, cam_section)

        # 摄像头自身配置
        merged["camera"] = cam_raw.get("camera", {})
        return merged

    def list_cameras(self) -> list[str]:
        """返回所有已配置的摄像头 ID 列表"""
        with self._lock:
            return list(self._camera_configs.keys())

    def get_all_cameras(self) -> dict[str, dict[str, Any]]:
        """返回所有摄像头的合并后配置"""
        with self._lock:
            camera_ids = list(self._camera_configs.keys())
        return {cam_id: self.get_camera(cam_id) for cam_id in camera_ids}

    def reload(self) -> None:
        """重新加载摄像头配置（全局配置不支持热重载）

        设计来源：config.md 3.5 节
        """
        with self._lock:
            old_configs = copy.deepcopy(self._camera_configs)

        self._load_camera_configs()

        with self._lock:
            new_configs = copy.deepcopy(self._camera_configs)

        old_ids = set(old_configs.keys())
        new_ids = set(new_configs.keys())

        # 通知变化
        added = new_ids - old_ids
        removed = old_ids - new_ids
        common = old_ids & new_ids

        for cam_id in added:
            self._notify_watchers("camera_added", cam_id, {}, new_configs[cam_id])
        for cam_id in removed:
            self._notify_watchers("camera_removed", cam_id, old_configs[cam_id], {})
        for cam_id in common:
            if old_configs[cam_id] != new_configs[cam_id]:
                self._notify_watchers(
                    "camera_updated", cam_id, old_configs[cam_id], new_configs[cam_id]
                )

        logger.info(
            "config_reloaded cameras=%d added=%d removed=%d",
            len(new_configs),
            len(added),
            len(removed),
        )

    def reload_camera(self, camera_id: str) -> bool:
        """重新加载指定摄像头的配置

        Args:
            camera_id: 摄像头 ID

        Returns:
            是否成功
        """
        cameras_dir = self._config_dir / "cameras"
        if not cameras_dir.exists():
            return False

        for yaml_file in cameras_dir.glob("*.yaml"):
            try:
                raw = load_yaml(yaml_file)
                raw = substitute_env_vars(raw)
                cam_id = raw.get("camera", {}).get("id", "")
                if cam_id == camera_id:
                    with self._lock:
                        existing_ids = set(self._camera_configs.keys()) - {camera_id}
                    validation = validate_camera_config(raw, existing_ids)
                    if validation.is_valid:
                        with self._lock:
                            old_config = self._camera_configs.get(camera_id, {})
                            self._camera_configs[camera_id] = raw
                            self._file_to_cam_id[str(yaml_file)] = cam_id
                        self._camera_mtimes[str(yaml_file)] = yaml_file.stat().st_mtime
                        self._notify_watchers(
                            "camera_updated", camera_id, old_config, raw
                        )
                        logger.info("camera_config_reloaded camera=%s", camera_id)
                        return True
                    else:
                        for err in validation.errors:
                            logger.error(
                                "camera_config_invalid camera=%s error=%s",
                                camera_id,
                                err,
                            )
                        return False
            except ConfigLoadError as e:
                logger.error("camera_config_load_failed file=%s error=%s", yaml_file, e)

        return False

    def watch(self, callback: Callable) -> None:
        """注册配置变化回调函数

        回调签名: (change_type: str, camera_id: str | None,
                   old_config: dict, new_config: dict) -> None

        change_type: "camera_added" / "camera_removed" / "camera_updated"
        """
        self._watchers.append(callback)

    # ─── 热重载 ────────────────────────────────────────────────

    def start_hot_reload(self) -> None:
        """启动摄像头配置热重载监控线程"""
        if self._reload_thread and self._reload_thread.is_alive():
            return
        self._running = True
        self._reload_thread = threading.Thread(
            target=self._hot_reload_loop, name="config-reload", daemon=True
        )
        self._reload_thread.start()
        logger.info(
            "config_hot_reload_started interval=%.0fs", self._hot_reload_interval
        )

    def stop_hot_reload(self) -> None:
        """停止热重载监控线程"""
        self._running = False
        if self._reload_thread:
            self._reload_thread.join(timeout=5)
            self._reload_thread = None
        logger.info("config_hot_reload_stopped")

    def _hot_reload_loop(self) -> None:
        """热重载主循环（config.md 3.5 节）"""
        while self._running:
            time.sleep(self._hot_reload_interval)
            if not self._running:
                break
            try:
                self._check_camera_changes()
            except Exception as e:
                logger.error("config_hot_reload_error error=%s", str(e), exc_info=True)

    def _check_camera_changes(self) -> None:
        """检查摄像头配置文件变化"""
        cameras_dir = self._config_dir / "cameras"
        if not cameras_dir.exists():
            return

        current_files: dict[str, float] = {}
        for yaml_file in cameras_dir.glob("*.yaml"):
            try:
                current_files[str(yaml_file)] = yaml_file.stat().st_mtime
            except OSError:
                continue

        # 检查新增和修改
        for file_path, mtime in current_files.items():
            old_mtime = self._camera_mtimes.get(file_path, 0)
            if mtime > old_mtime:
                self._try_reload_camera_file(file_path)

        # 检查删除
        for file_path in set(self._camera_mtimes.keys()) - set(current_files.keys()):
            self._handle_camera_file_removed(file_path)

        self._camera_mtimes = current_files

    def _try_reload_camera_file(self, file_path: str) -> None:
        """尝试重载单个摄像头配置文件"""
        try:
            raw = load_yaml(file_path)
            raw = substitute_env_vars(raw)
            cam_id = raw.get("camera", {}).get("id", "")
            if not cam_id:
                logger.warning("camera_config_no_id file=%s", file_path)
                return

            validation = validate_camera_config(
                raw, set(self._camera_configs.keys()) - {cam_id}
            )
            if not validation.is_valid:
                for err in validation.errors:
                    logger.error(
                        "camera_config_invalid camera=%s error=%s", cam_id, err
                    )
                return

            old_config = self._camera_configs.get(cam_id, {})
            self._camera_configs[cam_id] = raw
            change_type = "camera_added" if not old_config else "camera_updated"
            self._notify_watchers(change_type, cam_id, old_config, raw)
            logger.info("camera_config_changed camera=%s type=%s", cam_id, change_type)

        except ConfigLoadError as e:
            logger.error("camera_config_load_failed file=%s error=%s", file_path, e)

    def _handle_camera_file_removed(self, file_path: str) -> None:
        """处理摄像头配置文件被删除"""
        cam_id = self._file_to_cam_id.pop(file_path, None)
        if cam_id:
            with self._lock:
                old_config = self._camera_configs.pop(cam_id, {})
            self._notify_watchers("camera_removed", cam_id, old_config, {})
            logger.info("camera_config_removed camera=%s file=%s", cam_id, file_path)
        else:
            logger.warning("camera_config_file_removed_unknown file=%s", file_path)

    # ─── 内部方法 ──────────────────────────────────────────────

    def _load_camera_configs(self) -> None:
        """加载 cameras/ 目录下的所有摄像头配置"""
        cameras_dir = self._config_dir / "cameras"
        if not cameras_dir.exists():
            logger.info("cameras_dir_not_found path=%s", cameras_dir)
            return

        new_configs: dict[str, dict[str, Any]] = {}
        new_file_map: dict[str, str] = {}
        existing_ids: set[str] = set()

        for yaml_file in sorted(cameras_dir.glob("*.yaml")):
            try:
                raw = load_yaml(yaml_file)
                raw = substitute_env_vars(raw)

                cam_id = raw.get("camera", {}).get("id", "")
                if not cam_id:
                    logger.warning("camera_config_no_id file=%s", yaml_file)
                    continue

                validation = validate_camera_config(raw, existing_ids)
                if not validation.is_valid:
                    for err in validation.errors:
                        logger.error(
                            "camera_config_error file=%s error=%s", yaml_file, err
                        )
                    continue

                for w in validation.warnings:
                    logger.warning("camera_config_warning file=%s %s", yaml_file, w)

                new_configs[cam_id] = raw
                new_file_map[str(yaml_file)] = cam_id
                existing_ids.add(cam_id)
                self._camera_mtimes[str(yaml_file)] = yaml_file.stat().st_mtime

            except ConfigLoadError as e:
                logger.error("camera_config_load_failed file=%s error=%s", yaml_file, e)

        with self._lock:
            self._camera_configs = new_configs
            self._file_to_cam_id = new_file_map
        logger.info("camera_configs_loaded count=%d", len(new_configs))

    def _notify_watchers(
        self,
        change_type: str,
        camera_id: str,
        old_config: dict[str, Any],
        new_config: dict[str, Any],
    ) -> None:
        """通知所有观察者"""
        for callback in self._watchers:
            try:
                callback(change_type, camera_id, old_config, new_config)
            except Exception as e:
                logger.error(
                    "watcher_callback_error change=%s camera=%s error=%s",
                    change_type,
                    camera_id,
                    str(e),
                )


# ─── 工具函数 ────────────────────────────────────────────────


def _get_nested(data: dict[str, Any], path: str, default: Any = None) -> Any:
    """按点分路径从字典中获取值

    Args:
        data: 字典
        path: 点分路径，如 "gpu.batch_size"
        default: 路径不存在时的默认值

    Returns:
        值
    """
    keys = path.split(".")
    current = data
    for key in keys:
        if isinstance(current, dict) and key in current:
            current = current[key]
        else:
            return default
    return current
