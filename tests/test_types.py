"""数据模型序列化测试 — Alert/Event/CameraState/User to_dict ↔ from_dict"""

import time
import uuid

import pytest

from vision_agent.auth.models import Role, User, UserStatus
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


class TestBoundingBox:
    def test_create(self):
        box = BoundingBox(x1=10, y1=20, x2=100, y2=200)
        assert box.width == 90
        assert box.height == 180
        assert box.area == 90 * 180

    def test_to_dict_roundtrip(self):
        box = BoundingBox(x1=1.5, y1=2.5, x2=101.5, y2=202.5)
        b2 = BoundingBox.from_dict(box.to_dict())
        assert b2.x1 == pytest.approx(box.x1)
        assert b2.y2 == pytest.approx(box.y2)


class TestDetection:
    def test_to_dict_roundtrip(self):
        det = Detection(frame_id=42, class_id=0, class_name="person", confidence=0.95, bbox=BoundingBox(1, 2, 3, 4))
        assert "bbox" in det.to_dict()
        det2 = Detection.from_dict(det.to_dict())
        assert det2.class_name == "person"


class TestAlert:
    def test_state_flow(self):
        alert = Alert()
        assert alert.status == AlertStatus.PENDING
        alert.acknowledge("op1")
        assert alert.status == AlertStatus.ACKNOWLEDGED
        alert.resolve()
        assert alert.status == AlertStatus.RESOLVED

    def test_reject_flow(self):
        alert = Alert()
        alert.reject("op2")
        assert alert.status == AlertStatus.REJECTED

    def test_invalid_transition(self):
        alert = Alert()
        alert.acknowledge("op")
        with pytest.raises(ValueError):
            alert.reject("op")


class TestCameraState:
    def test_default(self):
        cs = CameraState()
        assert cs.is_online is False

    def test_to_dict_roundtrip(self):
        cs = CameraState(camera_id="cam-1", status=CameraStatus.CONNECTED, current_fps=25.0, total_alerts=5)
        d = cs.to_dict()
        cs2 = CameraState.from_dict(d)
        assert cs2.camera_id == cs.camera_id
        assert cs2.current_fps == pytest.approx(cs.current_fps)
        assert cs2.total_alerts == cs.total_alerts


class TestUserModel:
    def test_create(self):
        user = User(username="test", password_hash="hash", role=Role.ADMIN, email="t@test.com")
        assert user.is_admin
        assert user.is_active
        assert user.email == "t@test.com"

    def test_disabled(self):
        user = User(username="x", password_hash="h", status=UserStatus.DISABLED)
        assert not user.is_active

    def test_to_dict(self):
        user = User(id=1, username="u", password_hash="h", role=Role.OPERATOR, email="u@t.com", created_at=100.0, updated_at=200.0)
        d = user.to_dict()
        assert d["id"] == 1
        assert d["role"] == "operator"
        assert d["email"] == "u@t.com"
        assert d["status"] == 0


class TestSeverityEnum:
    def test_safe_enum(self):
        from vision_agent.core.types import _safe_enum
        assert _safe_enum(Severity, "critical", Severity.WARNING) == Severity.CRITICAL
        assert _safe_enum(Severity, "bogus", Severity.WARNING) == Severity.WARNING
