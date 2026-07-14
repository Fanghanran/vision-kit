"""Pipeline 集成测试 — 采集-推理-处理主链路"""

import time
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from sentinelmind.core.camera import CameraConfig, CameraThread, FrameQueue
from sentinelmind.core.pipeline import (
    ActionThread,
    InferenceThread,
    PipelineConfig,
    ResultQueue,
    VisionAgent,
)
from sentinelmind.core.types import Detection


# ─── ResultQueue ──────────────────────────────────────────

class TestResultQueue:
    def test_put_and_get(self):
        q = ResultQueue(maxsize=10)
        r = {"camera_id": "x"}
        q.put(r)
        assert q.size == 1
        got = q.get(timeout=1)
        assert got is not None
        assert got["camera_id"] == "x"

    def test_full_drops_old(self):
        q = ResultQueue(maxsize=2)
        for i in range(3):
            q.put({"camera_id": str(i)})
        assert q.size == 2


# ─── InferenceThread ──────────────────────────────────────

class TestInferenceThread:
    @pytest.fixture
    def detector(self):
        from sentinelmind.core.types import BoundingBox
        det = MagicMock()
        det.detect.return_value = [Detection(frame_id=1, class_id=0, class_name="person",
                                              confidence=0.9, bbox=BoundingBox(1, 2, 3, 4))]
        return det

    @pytest.fixture
    def tracker(self):
        return MagicMock()

    @pytest.fixture
    def rule_engine(self):
        return MagicMock()

    @pytest.fixture
    def thread(self, detector, tracker, rule_engine):
        fq = FrameQueue(maxsize=10)
        rq = ResultQueue(maxsize=10)
        t = InferenceThread(fq, detector, tracker, rq, MagicMock(return_value=None), 8, 50)
        return t, fq

    def test_start_stop(self, thread):
        t, fq = thread
        t.start()
        assert t.is_alive()
        time.sleep(0.3)
        t.stop()
        time.sleep(0.2)
        assert not t.is_alive()

    def test_processes_frames(self, thread):
        from sentinelmind.core.camera import FrameData

        t, fq = thread
        t.start()
        frame = np.ones((640, 640, 3), dtype=np.uint8) * 128
        fq.put(FrameData(camera_id="c1", frame=frame, timestamp=time.time(), frame_seq=1))
        time.sleep(0.5)
        t.stop()
        assert t.total_frames >= 1


# ─── ActionThread ─────────────────────────────────────────

class TestActionThread:
    @pytest.fixture
    def thread(self):
        rq = ResultQueue(maxsize=10)
        t = ActionThread(rq, MagicMock(), MagicMock(), MagicMock(return_value=None), [])
        return t

    def test_start_stop(self, thread):
        thread.start()
        assert thread.is_alive()
        time.sleep(0.2)
        thread.stop()
        assert not thread.is_alive()


# ─── VisionAgent ─────────────────────────────────────────

class TestVisionAgent:
    @pytest.fixture
    def agent(self):
        from sentinelmind.core.camera import CameraConfig
        from sentinelmind.core.pipeline import CameraConfigItem
        from sentinelmind.core.recorder import RecorderConfig
        from sentinelmind.core.tracker import TrackerConfig

        detector = MagicMock()
        detector.detect.return_value = []
        detector.warmup = MagicMock()

        cfg = CameraConfigItem(
            camera_config=CameraConfig(camera_id="test_cam", camera_name="测试", source_type="test", fps=10),
            fps=10,
        )
        va = VisionAgent(
            camera_configs=[cfg],
            detector=detector,
            tracker_config=TrackerConfig(),
            recorder_config=RecorderConfig(enabled=False),
            pipeline_config=PipelineConfig(),
            rule_engine=None,
            llm_analyzer=None,
            notifiers=[],
            database=None,
        )
        return va

    def test_start_stop(self, agent):
        agent.start()
        time.sleep(0.5)
        states = agent.get_camera_states()
        assert "test_cam" in states
        agent.stop()

    def test_health(self, agent):
        agent.start()
        time.sleep(0.5)
        h = agent.health()
        assert h.status in ("ok", "degraded")
        assert h.total_cameras >= 1
        agent.stop()

    def test_get_camera_thread(self, agent):
        agent.start()
        time.sleep(0.5)
        t = agent.get_camera_thread("test_cam")
        assert t is not None
        assert t.camera_id == "test_cam"
        agent.stop()

    def test_add_remove_camera(self, agent):
        from sentinelmind.core.camera import CameraConfig

        agent.start()
        agent.add_camera(CameraConfig(camera_id="new_cam", camera_name="新", source_type="test", fps=5))
        assert agent.get_camera_thread("new_cam") is not None
        agent.remove_camera("new_cam")
        assert agent.get_camera_thread("new_cam") is None
        agent.stop()

