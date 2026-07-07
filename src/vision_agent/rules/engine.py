"""
规则引擎模块 — 声明式 + 函数式混合规则评估

设计来源：docs/modules/rules/rule_engine.md

职责：
- 从 YAML 配置文件加载规则（声明式 + Python 扩展）
- 对每帧的追踪结果运行所有适用规则
- 三层防线过滤：滑动窗口去重 → 冷却时间 → 时间窗口
- 事件生成（Event 对象）
- 规则热重载（文件扫描 mtime）
- 内置规则注册

设计决策（来自 rule_engine.md 第 7 节）：
- 声明式 YAML 覆盖 80% 常见场景，Python 扩展覆盖 20% 复杂场景
- 三层防线共享，确保所有规则行为一致
- 滑动窗口用帧数而非时间（帧数更稳定）
- 热重载用文件扫描而非 watchdog（跨平台可靠）
"""

from __future__ import annotations

import importlib
import logging
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import numpy as np

from vision_agent.core.types import Event, Severity, Track

logger = logging.getLogger(__name__)


# ─── 协议接口 ────────────────────────────────────────────────


@runtime_checkable
class RuleProtocol(Protocol):
    """规则协议接口（rule_engine.md 2.1 节）"""

    @property
    def name(self) -> str:
        """规则名称，全局唯一"""
        ...

    @property
    def camera_ids(self) -> list[str] | None:
        """适用摄像头 ID 列表，None 表示全部"""
        ...

    def evaluate(
        self,
        tracks: list[Track],
        frame: np.ndarray,
        context: dict[str, Any],
    ) -> Event | None:
        """评估当前帧，返回 Event 或 None"""
        ...

    def reset(self) -> None:
        """重置内部状态"""
        ...


# ─── 缓存协议 ────────────────────────────────────────────────


@runtime_checkable
class CacheProtocol(Protocol):
    """缓存接口（rule_engine.md 2.3 节）

    用于三层防线的状态存储。
    storage/cache.py 实现后可替换为 Redis 等。
    """

    def get(self, key: str) -> Any | None: ...

    def set(self, key: str, value: Any, ttl: int = 0) -> None: ...

    def incr(self, key: str) -> int: ...


# ─── 内存缓存 ────────────────────────────────────────────────


class MemoryCache:
    """简单的内存缓存实现

    当 storage/cache.py 实现后可替换为 Redis 缓存。
    """

    def __init__(self) -> None:
        self._store: dict[str, Any] = {}

    def get(self, key: str) -> Any | None:
        return self._store.get(key)

    def set(self, key: str, value: Any, ttl: int = 0) -> None:
        self._store[key] = value

    def incr(self, key: str) -> int:
        current = self._store.get(key, 0)
        self._store[key] = current + 1
        return self._store[key]

    def delete(self, key: str) -> None:
        self._store.pop(key, None)

    def clear(self) -> None:
        self._store.clear()


# ─── 三层防线 ────────────────────────────────────────────────


class DefenseFilter:
    """三层防线过滤器（rule_engine.md 2.3 节）

    依次执行：
    1. 滑动窗口去重 — 连续 N 帧触发才放行
    2. 冷却时间 — 同一规则+摄像头在冷却期内只允许一次告警
    3. 时间窗口 — 检查当前时间是否在配置的时间窗口内
    """

    def __init__(
        self,
        cache: CacheProtocol,
        default_window_size: int = 5,
        default_cooldown: int = 300,
    ) -> None:
        self._cache = cache
        self._default_window_size = default_window_size
        self._default_cooldown = default_cooldown

    def check(
        self,
        rule_name: str,
        camera_id: str,
        event: Event,
        window_size: int | None = None,
        cooldown: int | None = None,
        time_windows: list[dict[str, Any]] | None = None,
    ) -> bool:
        """执行三层过滤，返回 True 表示允许通过

        缓存故障时降级为放行（宁可多报不漏报）。

        Args:
            rule_name: 规则名称
            camera_id: 摄像头 ID
            event: 事件对象
            window_size: 滑动窗口大小（覆盖默认值）
            cooldown: 冷却时间秒数（覆盖默认值）
            time_windows: 时间窗口配置列表

        Returns:
            True 表示通过（应生成告警）
        """
        ws = window_size if window_size is not None else self._default_window_size
        cd = cooldown if cooldown is not None else self._default_cooldown

        key_prefix = f"rule:{rule_name}:camera:{camera_id}"

        try:
            # 第一层：滑动窗口去重
            if not self._sliding_window_check(f"{key_prefix}:sliding_window", ws):
                return False

            # 第二层：冷却时间
            if not self._cooldown_check(f"{key_prefix}:cooldown", cd):
                return False

            # 第三层：时间窗口
            if not self._time_window_check(time_windows):
                return False

            return True
        except Exception as e:
            logger.warning(
                "defense_filter_error rule=%s camera=%s error=%s action=allow",
                rule_name,
                camera_id,
                str(e),
            )
            return True  # 缓存故障时放行

    def reset_rule(self, rule_name: str) -> None:
        """重置指定规则的所有防线状态"""
        keys_to_remove = (
            [k for k in self._cache._store if rule_name in k]
            if isinstance(self._cache, MemoryCache)
            else []
        )
        for key in keys_to_remove:
            del self._cache._store[key]

    def _sliding_window_check(self, key: str, window_size: int) -> bool:
        """第一层：滑动窗口去重（rule_engine.md 3.3 节）

        连续触发帧数达到窗口大小才放行。
        未触发时重置计数器。
        """
        count = self._cache.incr(key)
        if count >= window_size:
            # 达到阈值，放行并重置
            self._cache.set(key, 0)
            return True
        return False

    def _cooldown_check(self, key: str, cooldown_seconds: int) -> bool:
        """第二层：冷却时间（rule_engine.md 3.3 节）

        同一规则+摄像头在冷却期内只允许一次告警。
        """
        now = time.time()
        last_time = self._cache.get(key)
        if last_time is not None and (now - last_time) < cooldown_seconds:
            return False
        self._cache.set(key, now)
        return True

    def _time_window_check(self, time_windows: list[dict[str, Any]] | None) -> bool:
        """第三层：时间窗口（rule_engine.md 3.3 节）

        检查当前时间是否在配置的时间窗口内。
        未配置则默认放行。
        支持跨午夜窗口（如 22:00-06:00）。
        """
        if not time_windows:
            return True

        now = datetime.now()
        current_time = now.hour * 60 + now.minute
        weekday = now.weekday()  # 0=周一，6=周日

        for tw in time_windows:
            start_str = tw.get("start", "00:00")
            end_str = tw.get("end", "23:59")
            days = tw.get("days", list(range(7)))

            if weekday not in days:
                continue

            try:
                start_h, start_m = map(int, start_str.split(":"))
                end_h, end_m = map(int, end_str.split(":"))
            except (ValueError, AttributeError):
                logger.warning("time_window_parse_error tw=%s", tw)
                continue

            start_minutes = start_h * 60 + start_m
            end_minutes = end_h * 60 + end_m

            if end_minutes <= start_minutes:
                # 跨午夜窗口
                if current_time >= start_minutes or current_time < end_minutes:
                    return True
            else:
                if start_minutes <= current_time < end_minutes:
                    return True

        return False


# ─── 内置规则评估器 ──────────────────────────────────────────


class ObjectInZoneRule:
    """物体进入区域检测规则（声明式）"""

    def __init__(
        self,
        name: str,
        zone: list[tuple[float, float]],
        target_classes: list[str] | None = None,
        camera_ids: list[str] | None = None,
        severity: str = "warning",
    ) -> None:
        self._name = name
        self._zone = zone
        self._target_classes = target_classes
        self._camera_ids = camera_ids
        self._severity = (
            Severity(severity)
            if severity in [s.value for s in Severity]
            else Severity.WARNING
        )

    @property
    def name(self) -> str:
        return self._name

    @property
    def camera_ids(self) -> list[str] | None:
        return self._camera_ids

    def evaluate(
        self,
        tracks: list[Track],
        frame: np.ndarray,
        context: dict[str, Any],
    ) -> Event | None:
        triggered_tracks: list[Track] = []
        for track in tracks:
            if self._target_classes and track.class_name not in self._target_classes:
                continue
            if self._point_in_zone(track.center, self._zone):
                triggered_tracks.append(track)

        if triggered_tracks:
            return Event(
                event_type="object_in_zone",
                camera_id=context.get("camera_id", ""),
                camera_name=context.get("camera_name", ""),
                rule_name=self._name,
                tracks=triggered_tracks,
                severity=self._severity,
                metadata={"zone": self._zone, "count": len(triggered_tracks)},
            )
        return None

    def reset(self) -> None:
        pass

    @staticmethod
    def _point_in_zone(
        point: tuple[float, float], zone: list[tuple[float, float]]
    ) -> bool:
        """射线法判断点是否在多边形内"""
        x, y = point
        n = len(zone)
        inside = False
        j = n - 1
        for i in range(n):
            xi, yi = zone[i]
            xj, yj = zone[j]
            if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi) + xi):
                inside = not inside
            j = i
        return inside


class CountLineRule:
    """计数线检测规则（声明式）"""

    def __init__(
        self,
        name: str,
        line_start: tuple[float, float],
        line_end: tuple[float, float],
        threshold: int = 1,
        target_classes: list[str] | None = None,
        camera_ids: list[str] | None = None,
        severity: str = "warning",
    ) -> None:
        self._name = name
        self._line_start = line_start
        self._line_end = line_end
        self._threshold = threshold
        self._target_classes = target_classes
        self._camera_ids = camera_ids
        self._severity = (
            Severity(severity)
            if severity in [s.value for s in Severity]
            else Severity.WARNING
        )
        self._counted_tracks: set[int] = set()
        self._max_tracked = 10000  # 防止内存泄漏

    @property
    def name(self) -> str:
        return self._name

    @property
    def camera_ids(self) -> list[str] | None:
        return self._camera_ids

    def evaluate(
        self,
        tracks: list[Track],
        frame: np.ndarray,
        context: dict[str, Any],
    ) -> Event | None:
        # 定期清理防止无限增长
        if len(self._counted_tracks) > self._max_tracked:
            self._counted_tracks.clear()

        for track in tracks:
            if self._target_classes and track.class_name not in self._target_classes:
                continue
            if track.track_id in self._counted_tracks:
                continue
            if len(track.trajectory) < 2:
                continue
            if self._trajectory_crosses_line(track.trajectory):
                self._counted_tracks.add(track.track_id)

        if len(self._counted_tracks) >= self._threshold:
            return Event(
                event_type="count_line",
                camera_id=context.get("camera_id", ""),
                camera_name=context.get("camera_name", ""),
                rule_name=self._name,
                tracks=tracks,
                severity=self._severity,
                metadata={
                    "count": len(self._counted_tracks),
                    "threshold": self._threshold,
                },
            )
        return None

    def reset(self) -> None:
        self._counted_tracks.clear()

    def _trajectory_crosses_line(
        self, trajectory: list[tuple[float, float, float]]
    ) -> bool:
        """检查轨迹是否穿过计数线"""
        if len(trajectory) < 2:
            return False
        p1 = trajectory[-2]
        p2 = trajectory[-1]
        return self._segments_intersect(
            (p1[0], p1[1]),
            (p2[0], p2[1]),
            self._line_start,
            self._line_end,
        )

    @staticmethod
    def _segments_intersect(
        p1: tuple[float, float],
        p2: tuple[float, float],
        p3: tuple[float, float],
        p4: tuple[float, float],
    ) -> bool:
        """判断两条线段是否相交"""

        def cross(
            o: tuple[float, float], a: tuple[float, float], b: tuple[float, float]
        ) -> float:
            return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

        d1 = cross(p3, p4, p1)
        d2 = cross(p3, p4, p2)
        d3 = cross(p1, p2, p3)
        d4 = cross(p1, p2, p4)

        if ((d1 > 0 and d2 < 0) or (d1 < 0 and d2 > 0)) and (
            (d3 > 0 and d4 < 0) or (d3 < 0 and d4 > 0)
        ):
            return True

        if abs(d1) < 1e-9 and CountLineRule._on_segment(p3, p4, p1):
            return True
        if abs(d2) < 1e-9 and CountLineRule._on_segment(p3, p4, p2):
            return True
        if abs(d3) < 1e-9 and CountLineRule._on_segment(p1, p2, p3):
            return True
        if abs(d4) < 1e-9 and CountLineRule._on_segment(p1, p2, p4):
            return True

        return False

    @staticmethod
    def _on_segment(
        p: tuple[float, float],
        q: tuple[float, float],
        r: tuple[float, float],
    ) -> bool:
        """检查点 r 是否在线段 pq 上"""
        return min(p[0], q[0]) <= r[0] <= max(p[0], q[0]) and min(p[1], q[1]) <= r[
            1
        ] <= max(p[1], q[1])


class ZoneEmptyRule:
    """区域清空检测规则（声明式）"""

    def __init__(
        self,
        name: str,
        zone: list[tuple[float, float]],
        target_classes: list[str] | None = None,
        camera_ids: list[str] | None = None,
        severity: str = "warning",
    ) -> None:
        self._name = name
        self._zone = zone
        self._target_classes = target_classes
        self._camera_ids = camera_ids
        self._severity = (
            Severity(severity)
            if severity in [s.value for s in Severity]
            else Severity.WARNING
        )

    @property
    def name(self) -> str:
        return self._name

    @property
    def camera_ids(self) -> list[str] | None:
        return self._camera_ids

    def evaluate(
        self,
        tracks: list[Track],
        frame: np.ndarray,
        context: dict[str, Any],
    ) -> Event | None:
        for track in tracks:
            if self._target_classes and track.class_name not in self._target_classes:
                continue
            if ObjectInZoneRule._point_in_zone(track.center, self._zone):
                return None  # 区域内有物体，未触发

        return Event(
            event_type="zone_empty",
            camera_id=context.get("camera_id", ""),
            camera_name=context.get("camera_name", ""),
            rule_name=self._name,
            tracks=[],
            severity=self._severity,
            metadata={"zone": self._zone},
        )

    def reset(self) -> None:
        pass


# ─── 声明式规则工厂 ─────────────────────────────────────────


# 内置评估器注册表
_BUILTIN_EVALUATORS: dict[str, type] = {
    "object_in_zone": ObjectInZoneRule,
    "count_line": CountLineRule,
    "zone_empty": ZoneEmptyRule,
}


def create_rule_from_yaml(config: dict[str, Any]) -> RuleProtocol | None:
    """从 YAML 配置创建声明式规则实例

    Args:
        config: 规则 YAML 配置字典

    Returns:
        规则实例，失败返回 None
    """
    name = config.get("name", "")
    conditions = config.get("conditions", [])
    if not conditions:
        logger.error("rule_no_conditions name=%s", name)
        return None

    # 取第一个条件的 type 作为规则类型
    cond_type = conditions[0].get("type", "")
    params = conditions[0].get("params", {})

    rule_class = _BUILTIN_EVALUATORS.get(cond_type)
    if rule_class is None:
        logger.error("rule_unknown_type name=%s type=%s", name, cond_type)
        return None

    camera_ids = config.get("camera_ids")
    severity = config.get("severity", "warning")

    try:
        if cond_type == "object_in_zone":
            return rule_class(
                name=name,
                zone=[tuple(p) for p in params.get("zone", [])],
                target_classes=params.get("target_classes"),
                camera_ids=camera_ids,
                severity=severity,
            )
        elif cond_type == "count_line":
            return rule_class(
                name=name,
                line_start=tuple(params.get("line_start", [0, 0])),
                line_end=tuple(params.get("line_end", [0, 0])),
                threshold=params.get("threshold", 1),
                target_classes=params.get("target_classes"),
                camera_ids=camera_ids,
                severity=severity,
            )
        elif cond_type == "zone_empty":
            return rule_class(
                name=name,
                zone=[tuple(p) for p in params.get("zone", [])],
                target_classes=params.get("target_classes"),
                camera_ids=camera_ids,
                severity=severity,
            )
    except Exception as e:
        logger.error("rule_create_failed name=%s error=%s", name, str(e))
        return None

    return None


# ─── RuleEngine 主类 ─────────────────────────────────────────


class RuleEngine:
    """规则引擎（rule_engine.md 2.2 节）

    生命周期：__init__ → load_rules → [evaluate] → reload
    """

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        cache: CacheProtocol | None = None,
    ) -> None:
        """初始化规则引擎

        Args:
            config: rules 配置段（可选，有默认值）
            cache: 缓存实例（可选，默认使用内存缓存）
        """
        self._config = config or {}
        self._cache = cache or MemoryCache()
        self._rules: dict[str, RuleProtocol] = {}
        self._rule_sources: dict[str, str] = {}  # name → 文件路径
        self._rule_configs: dict[str, dict[str, Any]] = {}  # name → per-rule config
        self._lock = threading.Lock()

        # 防线配置
        self._defense = DefenseFilter(
            cache=self._cache,
            default_window_size=self._config.get("default_window_size", 5),
            default_cooldown=self._config.get("default_cooldown", 300),
        )

        # 热重载
        self._rules_dir: str = ""
        self._file_mtimes: dict[str, float] = {}
        self._hot_reload_thread: threading.Thread | None = None
        self._running = False

        # 统计
        self._total_evaluations = 0
        self._total_events = 0

    # ─── 公开接口 ──────────────────────────────────────────────

    def load_rules(self, rules_dir: str) -> None:
        """加载指定目录下所有 YAML 规则文件

        Args:
            rules_dir: 规则文件目录路径
        """
        self._rules_dir = rules_dir
        rules_path = Path(rules_dir)
        if not rules_path.exists():
            logger.warning("rules_dir_not_found path=%s", rules_dir)
            return

        for yaml_file in sorted(rules_path.glob("*.yaml")):
            self._load_single_rule_file(yaml_file)

        # 也扫描 .yml 文件
        for yaml_file in sorted(rules_path.glob("*.yml")):
            self._load_single_rule_file(yaml_file)

        logger.info("rules_loaded count=%d dir=%s", len(self._rules), rules_dir)

    def evaluate(
        self,
        camera_id: str,
        tracks: list[Track],
        frame: np.ndarray,
        timestamp: float = 0.0,
        camera_name: str = "",
    ) -> list[Event]:
        """对当前帧运行所有适用规则（rule_engine.md 3.2 节）

        Args:
            camera_id: 摄像头 ID
            tracks: 当前帧的追踪目标列表
            frame: 当前帧图像
            timestamp: 帧时间戳
            camera_name: 摄像头名称

        Returns:
            触发的事件列表（可能为空）
        """
        self._total_evaluations += 1
        context = {
            "camera_id": camera_id,
            "camera_name": camera_name,
            "timestamp": timestamp,
        }
        events: list[Event] = []

        with self._lock:
            rules = list(self._rules.items())

        for rule_name, rule in rules:
            # 检查摄像头适用性
            if rule.camera_ids is not None and camera_id not in rule.camera_ids:
                continue

            try:
                event = rule.evaluate(tracks, frame, context)
            except Exception as e:
                logger.error(
                    "rule_eval_error rule=%s camera=%s error=%s",
                    rule_name,
                    camera_id,
                    str(e),
                    exc_info=True,
                )
                continue

            if event is None:
                # 未触发时清除滑动窗口计数
                key = f"rule:{rule_name}:camera:{camera_id}:sliding_window"
                self._cache.set(key, 0)
                continue

            # 三层防线过滤
            rule_config = self._get_rule_config(rule_name)
            if self._defense.check(
                rule_name=rule_name,
                camera_id=camera_id,
                event=event,
                window_size=rule_config.get("window_size"),
                cooldown=rule_config.get("cooldown"),
                time_windows=rule_config.get("time_windows"),
            ):
                events.append(event)
                self._total_events += 1

        return events

    def get_rule(self, name: str) -> RuleProtocol | None:
        """按名称获取已加载的规则"""
        with self._lock:
            return self._rules.get(name)

    def unload_rule(self, name: str) -> bool:
        """卸载指定规则"""
        with self._lock:
            rule = self._rules.pop(name, None)
            self._rule_sources.pop(name, None)
            self._rule_configs.pop(name, None)
        if rule:
            rule.reset()
            self._defense.reset_rule(name)
            logger.info("rule_unloaded name=%s", name)
            return True
        return False

    def reload(self) -> None:
        """重新加载所有规则文件（rule_engine.md 3.4 节）"""
        if not self._rules_dir:
            logger.warning("rules_dir_not_set")
            return

        with self._lock:
            old_names = set(self._rules.keys())

        # 清除旧规则并重新加载
        with self._lock:
            self._rules.clear()
            self._rule_sources.clear()
            self._rule_configs.clear()

        self.load_rules(self._rules_dir)

        with self._lock:
            new_names = set(self._rules.keys())
        added = new_names - old_names
        removed = old_names - new_names
        logger.info(
            "rules_reloaded total=%d added=%d removed=%d",
            len(new_names),
            len(added),
            len(removed),
        )

    def register_builtin(self, rule_class: type) -> None:
        """注册一个内置规则类"""
        # 如果是评估器类型，注册到评估器注册表
        type_name = getattr(rule_class, "rule_type", None)
        if type_name:
            _BUILTIN_EVALUATORS[type_name] = rule_class
            logger.info(
                "builtin_registered type=%s class=%s", type_name, rule_class.__name__
            )

    def list_rules(self) -> list[dict[str, Any]]:
        """返回所有已加载规则的摘要信息"""
        with self._lock:
            return [
                {
                    "name": rule.name,
                    "camera_ids": rule.camera_ids,
                    "source": self._rule_sources.get(rule.name, "unknown"),
                }
                for rule in self._rules.values()
            ]

    # ─── 热重载 ────────────────────────────────────────────────

    def start_hot_reload(self) -> None:
        """启动规则热重载监控线程"""
        if not self._rules_dir:
            return
        if self._hot_reload_thread and self._hot_reload_thread.is_alive():
            return
        self._running = True
        self._hot_reload_thread = threading.Thread(
            target=self._hot_reload_loop, name="rule-reload", daemon=True
        )
        self._hot_reload_thread.start()
        interval = self._config.get("hot_reload_interval", 5)
        logger.info("rule_hot_reload_started interval=%ds", interval)

    def stop_hot_reload(self) -> None:
        """停止热重载监控线程"""
        self._running = False
        if self._hot_reload_thread:
            self._hot_reload_thread.join(timeout=5)
            self._hot_reload_thread = None
        logger.info("rule_hot_reload_stopped")

    def _hot_reload_loop(self) -> None:
        """热重载主循环"""
        interval = self._config.get("hot_reload_interval", 5)
        while self._running:
            time.sleep(interval)
            if not self._running:
                break
            try:
                self._check_rule_changes()
            except Exception as e:
                logger.error("rule_hot_reload_error error=%s", str(e), exc_info=True)

    def _check_rule_changes(self) -> None:
        """检查规则文件变化"""
        rules_path = Path(self._rules_dir)
        if not rules_path.exists():
            return

        current_files: dict[str, float] = {}
        for yaml_file in list(rules_path.glob("*.yaml")) + list(
            rules_path.glob("*.yml")
        ):
            try:
                current_files[str(yaml_file)] = yaml_file.stat().st_mtime
            except OSError:
                continue

        # 检查新增和修改
        for file_path, mtime in current_files.items():
            old_mtime = self._file_mtimes.get(file_path, 0)
            if mtime > old_mtime:
                self._reload_single_file(file_path)

        # 检查删除
        for file_path in set(self._file_mtimes.keys()) - set(current_files.keys()):
            self._remove_rules_from_file(file_path)

        self._file_mtimes.update(current_files)

    # ─── 内部方法 ──────────────────────────────────────────────

    def _load_single_rule_file(self, yaml_file: Path) -> None:
        """加载单个规则文件"""
        try:
            import yaml
        except ImportError:
            logger.error("pyyaml_not_installed")
            return

        try:
            with open(yaml_file, encoding="utf-8") as f:
                config = yaml.safe_load(f)
        except Exception as e:
            logger.error("rule_file_parse_error file=%s error=%s", yaml_file, e)
            return

        if not isinstance(config, dict):
            logger.error("rule_file_not_dict file=%s", yaml_file)
            return

        self._file_mtimes[str(yaml_file)] = yaml_file.stat().st_mtime

        # Python 扩展规则
        module_path = config.get("module")
        class_name = config.get("class")
        if module_path and class_name:
            self._load_python_rule(config, str(yaml_file))
            return

        # 声明式规则
        rule = create_rule_from_yaml(config)
        if rule:
            rule_name = rule.name
            with self._lock:
                if rule_name in self._rules:
                    logger.warning("rule_name_conflict name=%s", rule_name)
                self._rules[rule_name] = rule
                self._rule_sources[rule_name] = str(yaml_file)
                self._rule_configs[rule_name] = {
                    "window_size": config.get("window_size"),
                    "cooldown": config.get("cooldown"),
                    "time_windows": config.get("time_windows"),
                }
            rule.reset()
            logger.info("rule_loaded name=%s file=%s", rule_name, yaml_file)

    def _load_python_rule(self, config: dict[str, Any], source: str) -> None:
        """加载 Python 扩展规则"""
        module_path = config.get("module", "")
        class_name = config.get("class", "")
        name = config.get("name", class_name)

        try:
            module = importlib.import_module(module_path)
            rule_cls = getattr(module, class_name)
        except (ImportError, AttributeError) as e:
            logger.error(
                "python_rule_import_failed module=%s class=%s error=%s",
                module_path,
                class_name,
                str(e),
            )
            return

        if not isinstance(rule_cls, type):
            logger.error(
                "python_rule_not_class module=%s class=%s", module_path, class_name
            )
            return

        try:
            params = config.get("params", {})
            rule_instance = rule_cls(**params)
        except Exception as e:
            logger.error(
                "python_rule_init_failed class=%s error=%s", class_name, str(e)
            )
            return

        if not isinstance(rule_instance, RuleProtocol):
            logger.error("python_rule_no_protocol class=%s", class_name)
            return

        with self._lock:
            self._rules[name] = rule_instance
            self._rule_sources[name] = source
        rule_instance.reset()
        logger.info("python_rule_loaded name=%s class=%s", name, class_name)

    def _reload_single_file(self, file_path: str) -> None:
        """重新加载单个规则文件"""
        rules_path = Path(file_path)
        try:
            import yaml

            with open(rules_path, encoding="utf-8") as f:
                config = yaml.safe_load(f)
        except Exception as e:
            logger.error("rule_reload_failed file=%s error=%s", file_path, e)
            return

        if not isinstance(config, dict):
            return

        # 移除该文件的旧规则
        self._remove_rules_from_file(file_path)

        # 重新加载
        if config.get("module") and config.get("class"):
            self._load_python_rule(config, file_path)
        else:
            rule = create_rule_from_yaml(config)
            if rule:
                with self._lock:
                    self._rules[rule.name] = rule
                    self._rule_sources[rule.name] = file_path
                rule.reset()
                logger.info("rule_reloaded name=%s", rule.name)

    def _remove_rules_from_file(self, file_path: str) -> None:
        """移除来自指定文件的所有规则"""
        with self._lock:
            names_to_remove = [
                name for name, src in self._rule_sources.items() if src == file_path
            ]
            for name in names_to_remove:
                rule = self._rules.pop(name, None)
                self._rule_sources.pop(name, None)
                if rule:
                    rule.reset()
                    self._defense.reset_rule(name)
                    logger.info("rule_removed name=%s (file_deleted)", name)

    def _get_rule_config(self, rule_name: str) -> dict[str, Any]:
        """获取规则的防线配置（window_size、cooldown、time_windows）"""
        return self._rule_configs.get(rule_name, {})
