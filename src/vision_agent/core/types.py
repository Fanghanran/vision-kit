"""
统一数据模型 — Vision Agent 所有模块共享的数据结构

设计决策（来自 architecture.md 6.4 节）：
- 所有 ID 用 UUID，不用自增整数（分布式友好，合并不冲突）
- 时间戳统一用 Unix 秒（float），序列化简单，比较快
- 帧图像（numpy 数组）只在内存中传递，不序列化到数据库，持久化只存文件路径
- 所有数据模型支持 to_dict() / from_dict()，方便 JSON 序列化
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


# ─── 枚举 ───────────────────────────────────────────────────


class AlertStatus(str, Enum):
    """告警状态（architecture.md 6.2 节）

    状态流转：
        pending → acknowledged → resolved   （正常处理）
        pending → rejected                  （误报标记）
    """

    PENDING = "pending"
    ACKNOWLEDGED = "acknowledged"
    REJECTED = "rejected"
    RESOLVED = "resolved"


class Severity(str, Enum):
    """事件严重级别"""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class CameraStatus(str, Enum):
    """摄像头连接状态"""

    CONNECTING = "connecting"
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    ERROR = "error"


def _safe_enum(enum_cls: type, value: str, default: Any) -> Any:
    """安全的枚举反序列化，无效值回退到默认值"""
    try:
        return enum_cls(value)
    except (ValueError, KeyError):
        return default


# ─── 检测结果 ────────────────────────────────────────────────


@dataclass
class BoundingBox:
    """边界框（architecture.md 6.1 节）"""

    x1: float
    y1: float
    x2: float
    y2: float

    @property
    def width(self) -> float:
        return self.x2 - self.x1

    @property
    def height(self) -> float:
        return self.y2 - self.y1

    @property
    def center(self) -> tuple[float, float]:
        return ((self.x1 + self.x2) / 2, (self.y1 + self.y2) / 2)

    @property
    def area(self) -> float:
        return max(0, self.width) * max(0, self.height)

    def to_dict(self) -> dict[str, float]:
        return {"x1": self.x1, "y1": self.y1, "x2": self.x2, "y2": self.y2}

    @classmethod
    def from_dict(cls, data: dict[str, float]) -> BoundingBox:
        return cls(x1=data["x1"], y1=data["y1"], x2=data["x2"], y2=data["y2"])


@dataclass
class Detection:
    """单帧检测结果（architecture.md 6.1 节）

    表示 YOLO 在某一帧中检测到的一个目标。
    """

    frame_id: int
    class_id: int
    class_name: str
    confidence: float
    bbox: BoundingBox
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "frame_id": self.frame_id,
            "class_id": self.class_id,
            "class_name": self.class_name,
            "confidence": self.confidence,
            "bbox": self.bbox.to_dict(),
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Detection:
        return cls(
            frame_id=data["frame_id"],
            class_id=data["class_id"],
            class_name=data["class_name"],
            confidence=data["confidence"],
            bbox=BoundingBox.from_dict(data["bbox"]),
            timestamp=data.get("timestamp", time.time()),
        )


@dataclass
class Track:
    """追踪目标（architecture.md 6.1 节）

    表示 BoT-SORT 追踪器维护的一个跨帧目标。
    track_id 在同一目标的生命周期内保持不变。
    """

    track_id: int
    class_name: str
    bbox: BoundingBox
    trajectory: list[tuple[float, float, float]] = field(default_factory=list)
    velocity: tuple[float, float] = (0.0, 0.0)
    first_seen: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)
    age: int = 0
    hit_streak: int = 0

    @property
    def center(self) -> tuple[float, float]:
        return self.bbox.center

    @property
    def duration(self) -> float:
        """目标已存在的时长（秒）"""
        return self.last_seen - self.first_seen

    def to_dict(self) -> dict[str, Any]:
        return {
            "track_id": self.track_id,
            "class_name": self.class_name,
            "bbox": self.bbox.to_dict(),
            "trajectory": self.trajectory,
            "velocity": list(self.velocity),
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "age": self.age,
            "hit_streak": self.hit_streak,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Track:
        return cls(
            track_id=data["track_id"],
            class_name=data["class_name"],
            bbox=BoundingBox.from_dict(data["bbox"]),
            trajectory=[tuple(p) for p in data.get("trajectory", [])],
            velocity=tuple(data.get("velocity", [0.0, 0.0])),
            first_seen=data.get("first_seen", 0.0),
            last_seen=data.get("last_seen", 0.0),
            age=data.get("age", 0),
            hit_streak=data.get("hit_streak", 0),
        )


# ─── 事件与告警 ──────────────────────────────────────────────


@dataclass
class Event:
    """规则引擎产出的事件（architecture.md 6.2 节）

    当规则引擎判定某条规则被触发时，生成一个 Event。
    Event 包含触发它的检测结果和追踪目标。
    """

    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    event_type: str = ""
    camera_id: str = ""
    camera_name: str = ""
    rule_name: str = ""
    detections: list[Detection] = field(default_factory=list)
    tracks: list[Track] = field(default_factory=list)
    snapshot_path: str = ""
    timestamp: float = field(default_factory=time.time)
    severity: Severity = Severity.WARNING
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "camera_id": self.camera_id,
            "camera_name": self.camera_name,
            "rule_name": self.rule_name,
            "detections": [d.to_dict() for d in self.detections],
            "tracks": [t.to_dict() for t in self.tracks],
            "snapshot_path": self.snapshot_path,
            "timestamp": self.timestamp,
            "severity": self.severity.value,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Event:
        return cls(
            event_id=data.get("event_id", str(uuid.uuid4())),
            event_type=data.get("event_type", ""),
            camera_id=data.get("camera_id", ""),
            camera_name=data.get("camera_name", ""),
            rule_name=data.get("rule_name", ""),
            detections=[Detection.from_dict(d) for d in data.get("detections", [])],
            tracks=[Track.from_dict(t) for t in data.get("tracks", [])],
            snapshot_path=data.get("snapshot_path", ""),
            timestamp=data.get("timestamp", 0.0),
            severity=_safe_enum(Severity, data.get("severity", "warning"), Severity.WARNING),
            metadata=data.get("metadata", {}),
        )


@dataclass
class LLMAnalysis:
    """LLM 分析结果（architecture.md 6.2 节）

    LLM 看截图和事件上下文后输出的结构化分析。
    可以为 None（LLM 不可用时跳过分析）。
    """

    description: str = ""
    risk_level: str = ""
    suggestion: str = ""
    context: str = ""
    raw_response: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "description": self.description,
            "risk_level": self.risk_level,
            "suggestion": self.suggestion,
            "context": self.context,
            "raw_response": self.raw_response,
        }

    @classmethod
    def from_dict(cls, data: dict[str, str]) -> LLMAnalysis:
        return cls(
            description=data.get("description", ""),
            risk_level=data.get("risk_level", ""),
            suggestion=data.get("suggestion", ""),
            context=data.get("context", ""),
            raw_response=data.get("raw_response", ""),
        )


@dataclass
class Alert:
    """最终告警（architecture.md 6.2 节）

    告警是系统对外输出的核心产物。
    包含触发事件、LLM 分析结果、视频片段路径、通知状态。

    状态流转：
        pending → acknowledged → resolved   （正常处理）
        pending → rejected                  （误报标记）
    """

    alert_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    event: Event = field(default_factory=Event)
    llm_analysis: Optional[LLMAnalysis] = None
    video_clip_path: str = ""
    status: AlertStatus = AlertStatus.PENDING
    notified_channels: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    acknowledged_at: float = 0.0
    acknowledged_by: str = ""
    rejected_at: float = 0.0

    # ─── 状态流转 ────────────────────────────────────────────

    def acknowledge(self, by: str = "") -> None:
        """确认告警（pending → acknowledged）"""
        if self.status != AlertStatus.PENDING:
            raise ValueError(
                f"只能确认 pending 状态的告警，当前状态: {self.status.value}"
            )
        self.status = AlertStatus.ACKNOWLEDGED
        self.acknowledged_at = time.time()
        self.acknowledged_by = by

    def reject(self, by: str = "") -> None:
        """标记为误报（pending → rejected）"""
        if self.status != AlertStatus.PENDING:
            raise ValueError(
                f"只能拒绝 pending 状态的告警，当前状态: {self.status.value}"
            )
        self.status = AlertStatus.REJECTED
        self.rejected_at = time.time()
        self.acknowledged_by = by

    def resolve(self) -> None:
        """标记为已解决（acknowledged → resolved）"""
        if self.status != AlertStatus.ACKNOWLEDGED:
            raise ValueError(
                f"只能解决 acknowledged 状态的告警，当前状态: {self.status.value}"
            )
        self.status = AlertStatus.RESOLVED

    # ─── 序列化 ──────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        return {
            "alert_id": self.alert_id,
            "event": self.event.to_dict(),
            "llm_analysis": self.llm_analysis.to_dict() if self.llm_analysis else None,
            "video_clip_path": self.video_clip_path,
            "status": self.status.value,
            "notified_channels": self.notified_channels,
            "created_at": self.created_at,
            "acknowledged_at": self.acknowledged_at,
            "acknowledged_by": self.acknowledged_by,
            "rejected_at": self.rejected_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Alert:
        llm_data = data.get("llm_analysis")
        return cls(
            alert_id=data.get("alert_id", str(uuid.uuid4())),
            event=Event.from_dict(data["event"]) if "event" in data else Event(),
            llm_analysis=LLMAnalysis.from_dict(llm_data) if llm_data else None,
            video_clip_path=data.get("video_clip_path", ""),
            status=_safe_enum(AlertStatus, data.get("status", "pending"), AlertStatus.PENDING),
            notified_channels=data.get("notified_channels", []),
            created_at=data.get("created_at", 0.0),
            acknowledged_at=data.get("acknowledged_at", 0.0),
            acknowledged_by=data.get("acknowledged_by", ""),
            rejected_at=data.get("rejected_at", 0.0),
        )


# ─── 系统状态 ────────────────────────────────────────────────


@dataclass
class CameraState:
    """摄像头运行状态（architecture.md 6.3 节）

    用于 Web 界面的状态面板显示和系统健康检查。
    """

    camera_id: str = ""
    status: CameraStatus = CameraStatus.CONNECTING
    current_fps: float = 0.0
    gpu_latency_ms: float = 0.0
    queue_size: int = 0
    last_frame_time: float = 0.0
    total_detections: int = 0
    total_alerts: int = 0
    uptime_seconds: float = 0.0
    error_message: str = ""

    @property
    def is_online(self) -> bool:
        return self.status == CameraStatus.CONNECTED

    def to_dict(self) -> dict[str, Any]:
        return {
            "camera_id": self.camera_id,
            "status": self.status.value,
            "current_fps": self.current_fps,
            "gpu_latency_ms": self.gpu_latency_ms,
            "queue_size": self.queue_size,
            "last_frame_time": self.last_frame_time,
            "total_detections": self.total_detections,
            "total_alerts": self.total_alerts,
            "uptime_seconds": self.uptime_seconds,
            "error_message": self.error_message,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CameraState:
        return cls(
            camera_id=data.get("camera_id", ""),
            status=_safe_enum(CameraStatus, data.get("status", "connecting"), CameraStatus.CONNECTING),
            current_fps=data.get("current_fps", 0.0),
            gpu_latency_ms=data.get("gpu_latency_ms", 0.0),
            queue_size=data.get("queue_size", 0),
            last_frame_time=data.get("last_frame_time", 0.0),
            total_detections=data.get("total_detections", 0),
            total_alerts=data.get("total_alerts", 0),
            uptime_seconds=data.get("uptime_seconds", 0.0),
            error_message=data.get("error_message", ""),
        )
