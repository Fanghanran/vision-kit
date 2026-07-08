"""单元测试 — vision_agent.core.types 数据模型

覆盖范围：
- 每个 dataclass 的创建和默认值
- to_dict() / from_dict() 序列化往返一致性
- BoundingBox 属性（width, height, center, area）
- Track 属性（center, duration）
- Alert 状态流转（合法路径 + 非法路径）
- 嵌套对象序列化（Event/Alert）
- CameraState.is_online
"""

from __future__ import annotations

import pytest

from vision_agent.core.types import (
    Alert,
    AlertStatus,
    BoundingBox,
    CameraState,
    CameraStatus,
    Detection,
    Event,
    LLMAnalysis,
    Severity,
    Track,
)


# ─── BoundingBox ───────────────────────────────────────────────


class TestBoundingBox:
    """BoundingBox 创建、属性、序列化"""

    def test_creation_and_defaults(self):
        bb = BoundingBox(x1=10.0, y1=20.0, x2=50.0, y2=80.0)
        assert bb.x1 == 10.0
        assert bb.y1 == 20.0
        assert bb.x2 == 50.0
        assert bb.y2 == 80.0

    def test_width(self):
        bb = BoundingBox(x1=10.0, y1=0.0, x2=50.0, y2=0.0)
        assert bb.width == pytest.approx(40.0)

    def test_height(self):
        bb = BoundingBox(x1=0.0, y1=20.0, x2=0.0, y2=80.0)
        assert bb.height == pytest.approx(60.0)

    def test_center(self):
        bb = BoundingBox(x1=10.0, y1=20.0, x2=50.0, y2=80.0)
        cx, cy = bb.center
        assert cx == pytest.approx(30.0)
        assert cy == pytest.approx(50.0)

    def test_area(self):
        bb = BoundingBox(x1=0.0, y1=0.0, x2=10.0, y2=20.0)
        assert bb.area == pytest.approx(200.0)

    def test_area_negative_bbox_returns_zero(self):
        """当 x2 < x1 或 y2 < y1 时 area 应为 0"""
        bb = BoundingBox(x1=50.0, y1=50.0, x2=10.0, y2=10.0)
        assert bb.area == 0.0

    def test_roundtrip(self):
        bb = BoundingBox(x1=1.5, y1=2.5, x2=3.5, y2=4.5)
        d = bb.to_dict()
        bb2 = BoundingBox.from_dict(d)
        assert bb == bb2

    def test_to_dict_keys(self):
        bb = BoundingBox(x1=0, y1=0, x2=1, y2=1)
        d = bb.to_dict()
        assert set(d.keys()) == {"x1", "y1", "x2", "y2"}


# ─── Detection ─────────────────────────────────────────────────


class TestDetection:
    """Detection 创建、默认值、序列化"""

    def test_creation_with_explicit_values(self):
        det = Detection(
            frame_id=1,
            class_id=0,
            class_name="person",
            confidence=0.95,
            bbox=BoundingBox(0, 0, 100, 100),
            timestamp=1700000000.0,
        )
        assert det.frame_id == 1
        assert det.class_id == 0
        assert det.class_name == "person"
        assert det.confidence == 0.95

    def test_timestamp_default(self):
        det = Detection(
            frame_id=0, class_id=0, class_name="x",
            confidence=0.5, bbox=BoundingBox(0, 0, 1, 1),
        )
        assert det.timestamp > 0

    def test_roundtrip(self):
        det = Detection(
            frame_id=42,
            class_id=3,
            class_name="car",
            confidence=0.87,
            bbox=BoundingBox(10.0, 20.0, 110.0, 220.0),
            timestamp=1700000000.0,
        )
        d = det.to_dict()
        det2 = Detection.from_dict(d)
        assert det2.frame_id == det.frame_id
        assert det2.class_id == det.class_id
        assert det2.class_name == det.class_name
        assert det2.confidence == pytest.approx(det.confidence)
        assert det2.bbox == det.bbox
        assert det2.timestamp == pytest.approx(det.timestamp)

    def test_from_dict_missing_timestamp(self):
        """from_dict 在 timestamp 缺失时应使用当前时间（与构造函数默认值一致）"""
        import time

        before = time.time()
        d = {
            "frame_id": 1,
            "class_id": 0,
            "class_name": "person",
            "confidence": 0.9,
            "bbox": {"x1": 0, "y1": 0, "x2": 10, "y2": 10},
        }
        det = Detection.from_dict(d)
        after = time.time()
        assert before <= det.timestamp <= after


# ─── Track ─────────────────────────────────────────────────────


class TestTrack:
    """Track 创建、属性、序列化"""

    def test_creation_and_defaults(self):
        t = Track(
            track_id=1,
            class_name="person",
            bbox=BoundingBox(0, 0, 10, 10),
        )
        assert t.track_id == 1
        assert t.trajectory == []
        assert t.velocity == (0.0, 0.0)
        assert t.age == 0
        assert t.hit_streak == 0

    def test_center_delegates_to_bbox(self):
        bb = BoundingBox(10.0, 20.0, 30.0, 40.0)
        t = Track(track_id=1, class_name="x", bbox=bb)
        assert t.center == bb.center

    def test_duration(self):
        t = Track(
            track_id=1,
            class_name="x",
            bbox=BoundingBox(0, 0, 1, 1),
            first_seen=100.0,
            last_seen=115.5,
        )
        assert t.duration == pytest.approx(15.5)

    def test_duration_zero_when_equal(self):
        t = Track(
            track_id=1,
            class_name="x",
            bbox=BoundingBox(0, 0, 1, 1),
            first_seen=100.0,
            last_seen=100.0,
        )
        assert t.duration == pytest.approx(0.0)

    def test_roundtrip(self):
        t = Track(
            track_id=5,
            class_name="car",
            bbox=BoundingBox(1.0, 2.0, 3.0, 4.0),
            trajectory=[(1.0, 2.0, 0.1), (3.0, 4.0, 0.2)],
            velocity=(1.5, 2.5),
            first_seen=1000.0,
            last_seen=1010.0,
            age=100,
            hit_streak=50,
        )
        d = t.to_dict()
        t2 = Track.from_dict(d)
        assert t2.track_id == t.track_id
        assert t2.class_name == t.class_name
        assert t2.bbox == t.bbox
        assert t2.trajectory == t.trajectory
        assert t2.velocity == t.velocity
        assert t2.first_seen == pytest.approx(t.first_seen)
        assert t2.last_seen == pytest.approx(t.last_seen)
        assert t2.age == t.age
        assert t2.hit_streak == t.hit_streak


# ─── Event ─────────────────────────────────────────────────────


class TestEvent:
    """Event 创建、嵌套对象序列化"""

    def test_creation_defaults(self):
        ev = Event()
        assert ev.event_id  # uuid 应自动生成
        assert ev.event_type == ""
        assert ev.severity == Severity.WARNING
        assert ev.detections == []
        assert ev.tracks == []
        assert ev.metadata == {}

    def test_severity_enum_values(self):
        assert Severity.INFO.value == "info"
        assert Severity.WARNING.value == "warning"
        assert Severity.CRITICAL.value == "critical"

    def test_roundtrip_with_nested_objects(self):
        """Event 包含 Detection 和 Track 的序列化往返"""
        det = Detection(
            frame_id=1, class_id=0, class_name="person",
            confidence=0.9, bbox=BoundingBox(10, 20, 30, 40),
            timestamp=1700000000.0,
        )
        trk = Track(
            track_id=7, class_name="person",
            bbox=BoundingBox(10, 20, 30, 40),
            first_seen=1700000000.0, last_seen=1700000005.0,
        )
        ev = Event(
            event_id="evt-001",
            event_type="intrusion",
            camera_id="cam-1",
            camera_name="Front Door",
            rule_name="zone_a_intrusion",
            detections=[det],
            tracks=[trk],
            snapshot_path="/tmp/snap.jpg",
            timestamp=1700000000.0,
            severity=Severity.CRITICAL,
            metadata={"zone": "A"},
        )

        d = ev.to_dict()
        ev2 = Event.from_dict(d)

        assert ev2.event_id == ev.event_id
        assert ev2.event_type == ev.event_type
        assert ev2.camera_id == ev.camera_id
        assert ev2.severity == Severity.CRITICAL
        assert len(ev2.detections) == 1
        assert ev2.detections[0].class_name == "person"
        assert len(ev2.tracks) == 1
        assert ev2.tracks[0].track_id == 7
        assert ev2.metadata == {"zone": "A"}


# ─── LLMAnalysis ───────────────────────────────────────────────


class TestLLMAnalysis:
    """LLMAnalysis 创建、默认值、序列化"""

    def test_creation_defaults(self):
        la = LLMAnalysis()
        assert la.description == ""
        assert la.risk_level == ""
        assert la.suggestion == ""
        assert la.context == ""
        assert la.raw_response == ""

    def test_roundtrip(self):
        la = LLMAnalysis(
            description="Person detected in restricted area",
            risk_level="high",
            suggestion="Dispatch security team",
            context="Zone A, after hours",
            raw_response='{"risk": "high"}',
        )
        d = la.to_dict()
        la2 = LLMAnalysis.from_dict(d)
        assert la2 == la


# ─── Alert 状态流转 ────────────────────────────────────────────


class TestAlertStatusTransitions:
    """Alert 合法与非法状态流转"""

    def _make_alert(self) -> Alert:
        return Alert(
            alert_id="alert-test-001",
            event=Event(event_id="evt-001"),
            status=AlertStatus.PENDING,
        )

    def test_pending_to_acknowledged(self):
        a = self._make_alert()
        a.acknowledge(by="operator1")
        assert a.status == AlertStatus.ACKNOWLEDGED
        assert a.acknowledged_by == "operator1"
        assert a.acknowledged_at > 0

    def test_pending_to_rejected(self):
        a = self._make_alert()
        a.reject(by="reviewer1")
        assert a.status == AlertStatus.REJECTED
        assert a.acknowledged_by == "reviewer1"

    def test_acknowledged_to_resolved(self):
        a = self._make_alert()
        a.acknowledge()
        a.resolve()
        assert a.status == AlertStatus.RESOLVED

    def test_full_happy_path(self):
        """pending -> acknowledged -> resolved"""
        a = self._make_alert()
        assert a.status == AlertStatus.PENDING
        a.acknowledge(by="op")
        assert a.status == AlertStatus.ACKNOWLEDGED
        a.resolve()
        assert a.status == AlertStatus.RESOLVED

    def test_full_reject_path(self):
        """pending -> rejected"""
        a = self._make_alert()
        a.reject(by="reviewer")
        assert a.status == AlertStatus.REJECTED

    # ── 非法状态流转 ──

    def test_acknowledge_non_pending_raises(self):
        a = self._make_alert()
        a.reject()
        with pytest.raises(ValueError, match="pending"):
            a.acknowledge()

    def test_reject_non_pending_raises(self):
        a = self._make_alert()
        a.acknowledge()
        with pytest.raises(ValueError, match="pending"):
            a.reject()

    def test_resolve_non_acknowledged_raises(self):
        a = self._make_alert()
        with pytest.raises(ValueError, match="acknowledged"):
            a.resolve()

    def test_reject_after_acknowledge_raises(self):
        a = self._make_alert()
        a.acknowledge()
        with pytest.raises(ValueError):
            a.reject()

    def test_double_acknowledge_raises(self):
        a = self._make_alert()
        a.acknowledge()
        with pytest.raises(ValueError):
            a.acknowledge()

    def test_resolve_after_reject_raises(self):
        a = self._make_alert()
        a.reject()
        with pytest.raises(ValueError):
            a.resolve()


# ─── Alert 序列化 ──────────────────────────────────────────────


class TestAlertSerialization:
    """Alert to_dict / from_dict，含嵌套 Event 和 LLMAnalysis"""

    def test_roundtrip_with_llm_analysis(self):
        ev = Event(
            event_id="evt-100",
            event_type="loitering",
            severity=Severity.WARNING,
            detections=[
                Detection(
                    frame_id=1, class_id=0, class_name="person",
                    confidence=0.88, bbox=BoundingBox(5, 5, 50, 50),
                    timestamp=1700000000.0,
                ),
            ],
            tracks=[],
        )
        llm = LLMAnalysis(
            description="Suspicious loitering detected",
            risk_level="medium",
            suggestion="Monitor camera feed",
        )
        alert = Alert(
            alert_id="alert-200",
            event=ev,
            llm_analysis=llm,
            video_clip_path="/videos/alert-200.mp4",
            status=AlertStatus.PENDING,
            notified_channels=["email", "slack"],
            created_at=1700000000.0,
        )

        d = alert.to_dict()
        alert2 = Alert.from_dict(d)

        assert alert2.alert_id == alert.alert_id
        assert alert2.event.event_id == "evt-100"
        assert alert2.event.severity == Severity.WARNING
        assert len(alert2.event.detections) == 1
        assert alert2.llm_analysis is not None
        assert alert2.llm_analysis.risk_level == "medium"
        assert alert2.video_clip_path == "/videos/alert-200.mp4"
        assert alert2.status == AlertStatus.PENDING
        assert alert2.notified_channels == ["email", "slack"]

    def test_roundtrip_without_llm_analysis(self):
        """llm_analysis 为 None 时序列化往返"""
        alert = Alert(
            alert_id="alert-300",
            event=Event(event_id="evt-300"),
            llm_analysis=None,
        )
        d = alert.to_dict()
        assert d["llm_analysis"] is None
        alert2 = Alert.from_dict(d)
        assert alert2.llm_analysis is None

    def test_alert_default_values(self):
        a = Alert()
        assert a.alert_id  # uuid
        assert a.status == AlertStatus.PENDING
        assert a.llm_analysis is None
        assert a.video_clip_path == ""
        assert a.notified_channels == []
        assert a.acknowledged_at == 0.0
        assert a.acknowledged_by == ""


# ─── CameraState ───────────────────────────────────────────────


class TestCameraState:
    """CameraState 创建、is_online、序列化"""

    def test_creation_defaults(self):
        cs = CameraState()
        assert cs.camera_id == ""
        assert cs.status == CameraStatus.CONNECTING
        assert cs.current_fps == 0.0
        assert cs.is_online is False

    def test_is_online_connected(self):
        cs = CameraState(status=CameraStatus.CONNECTED)
        assert cs.is_online is True

    def test_is_online_connecting(self):
        cs = CameraState(status=CameraStatus.CONNECTING)
        assert cs.is_online is False

    def test_is_online_disconnected(self):
        cs = CameraState(status=CameraStatus.DISCONNECTED)
        assert cs.is_online is False

    def test_is_online_error(self):
        cs = CameraState(status=CameraStatus.ERROR)
        assert cs.is_online is False

    def test_roundtrip(self):
        cs = CameraState(
            camera_id="cam-1",
            status=CameraStatus.CONNECTED,
            current_fps=25.0,
            gpu_latency_ms=12.5,
            queue_size=3,
            last_frame_time=1700000000.0,
            total_detections=120,
            total_alerts=5,
            uptime_seconds=3600.0,
            error_message="",
        )
        d = cs.to_dict()
        cs2 = CameraState.from_dict(d)

        assert cs2.camera_id == cs.camera_id
        assert cs2.status == CameraStatus.CONNECTED
        assert cs2.current_fps == pytest.approx(cs.current_fps)
        assert cs2.gpu_latency_ms == pytest.approx(cs.gpu_latency_ms)
        assert cs2.queue_size == cs.queue_size
        assert cs2.total_detections == cs.total_detections
        assert cs2.total_alerts == cs.total_alerts
        assert cs2.is_online is True

    def test_camera_status_enum_values(self):
        assert CameraStatus.CONNECTING.value == "connecting"
        assert CameraStatus.CONNECTED.value == "connected"
        assert CameraStatus.DISCONNECTED.value == "disconnected"
        assert CameraStatus.ERROR.value == "error"
