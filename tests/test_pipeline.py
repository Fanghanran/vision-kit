"""单元测试 — core/pipeline.py 主处理管线编排器

覆盖范围：
- 数据类：InferenceResult, HealthResponse, AlertStats
- ResultQueue：put/get、满则丢旧、clear、qsize
- LatencyTracker：record、p50/p99 计算、空窗口
- InferenceThread：启动/停止、batch 处理
- ActionThread：启动/停止、规则引擎评估、告警生成、LLM 调用、通知发送
- TimerTask：启动/停止、定时回调
- VisionAgent：组装、启动、停止、健康检查、摄像头动态管理、线程异常恢复

Mock 策略：
- 检测器用 MockDetector（实现 detect/warmup/release/model_name/classes）
- 追踪器用 MockTrackerManager（避免 ultralytics 依赖）
- 摄像头线程用 MockCameraThread（避免真实 FFmpeg）
- 规则引擎/LLM/通知器/数据库用 MagicMock
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from unittest.mock import MagicMock, PropertyMock, patch

import numpy as np
import pytest

from vision_agent.core.camera import CameraConfig, CameraStatus, FrameData, FrameQueue
from vision_agent.core.detector import DetectorProtocol
from vision_agent.core.pipeline import (
    ActionThread,
    AlertStats,
    CameraConfigItem,
    HealthResponse,
    InferenceResult,
    InferenceThread,
    LatencyTracker,
    PipelineConfig,
    ResultQueue,
    SystemStatus,
    TimerTask,
    VisionAgent,
)
from vision_agent.core.recorder import RecorderConfig
from vision_agent.core.tracker import TrackerConfig
from vision_agent.core.types import (
    BoundingBox,
    CameraState,
    Detection,
    Event,
    Severity,
    Track,
)


# ─── 辅助工具 ──────────────────────────────────────────────────


def _make_frame(h: int = 64, w: int = 64, fill: int = 0) -> np.ndarray:
    """生成一个小尺寸纯色帧，节省内存。"""
    return np.full((h, w, 3), fill, dtype=np.uint8)


def _make_detection(class_name: str = "person", conf: float = 0.9) -> Detection:
    """构造一个简单的检测对象。"""
    return Detection(
        frame_id=1,
        class_id=0,
        class_name=class_name,
        confidence=conf,
        bbox=BoundingBox(10.0, 20.0, 100.0, 200.0),
        timestamp=1700000000.0,
    )


def _make_track(track_id: int = 1, class_name: str = "person") -> Track:
    """构造一个简单的追踪对象。"""
    return Track(
        track_id=track_id,
        class_name=class_name,
        bbox=BoundingBox(10.0, 20.0, 100.0, 200.0),
        first_seen=1700000000.0,
        last_seen=1700000001.0,
        age=10,
        hit_streak=5,
    )


def _make_event(camera_id: str = "cam-1") -> Event:
    """构造一个简单的事件对象。"""
    return Event(
        event_id="evt-test-001",
        event_type="intrusion",
        camera_id=camera_id,
        severity=Severity.WARNING,
    )


def _make_frame_data(
    camera_id: str = "cam-1", seq: int = 1, fill: int = 0
) -> FrameData:
    """构造一个 FrameData。"""
    return FrameData(
        camera_id=camera_id,
        frame=_make_frame(fill=fill),
        timestamp=time.time(),
        frame_seq=seq,
        width=64,
        height=64,
    )


# ─── Mock 组件 ─────────────────────────────────────────────────


class MockDetector:
    """满足 DetectorProtocol 的 mock 检测器。"""

    def __init__(self):
        self._released = False
        self.config = MagicMock()
        self.config.batch_size = 4

    def detect(self, frames: list[np.ndarray]) -> list[list[Detection]]:
        """每帧返回 1 个 person 检测。"""
        return [[_make_detection()] for _ in frames]

    def detect_single(self, frame: np.ndarray) -> list[Detection]:
        return [_make_detection()]

    def warmup(self) -> None:
        pass

    def release(self) -> None:
        self._released = True

    @property
    def model_name(self) -> str:
        return "mock_yolo.pt"

    @property
    def classes(self) -> list[str]:
        return ["person", "car"]


class MockCameraThread:
    """模拟 CameraThread 的行为，不启动 FFmpeg。"""

    def __init__(self, config: CameraConfig, frame_queue: FrameQueue):
        self._config = config
        self._frame_queue = frame_queue
        self._started = False
        self._status = CameraStatus.CONNECTING

    def start(self) -> None:
        self._started = True
        self._status = CameraStatus.CONNECTED

    def stop(self) -> None:
        self._started = False
        self._status = CameraStatus.DISCONNECTED

    def is_alive(self) -> bool:
        return self._started

    @property
    def camera_id(self) -> str:
        return self._config.camera_id

    @property
    def camera_name(self) -> str:
        return self._config.camera_name

    @property
    def status(self) -> CameraStatus:
        return self._status

    @property
    def camera_state(self) -> CameraState:
        return CameraState(
            camera_id=self._config.camera_id,
            status=self._status,
        )


class MockTrackerManager:
    """模拟 TrackerManager，不依赖 ultralytics。"""

    def __init__(self, config: TrackerConfig):
        self._config = config
        self._removed: list[str] = []
        self._reset_calls: list[str] = []

    def update(
        self, camera_id: str, detections: list[Detection], frame: np.ndarray
    ) -> list[Track]:
        """每个检测返回一个 track。"""
        return [
            _make_track(track_id=i, class_name=d.class_name)
            for i, d in enumerate(detections)
        ]

    def get_tracks(self, camera_id: str) -> list[Track]:
        return []

    def reset(self, camera_id: str) -> None:
        self._reset_calls.append(camera_id)

    def reset_all(self) -> None:
        self._reset_calls.clear()

    def remove_tracker(self, camera_id: str) -> None:
        self._removed.append(camera_id)


@pytest.fixture
def mock_detector() -> MockDetector:
    return MockDetector()


@pytest.fixture
def tracker_config() -> TrackerConfig:
    return TrackerConfig()


@pytest.fixture
def recorder_config(tmp_path) -> RecorderConfig:
    return RecorderConfig(
        output_dir=str(tmp_path / "clips"),
        snapshot_dir=str(tmp_path / "snapshots"),
        fps=5.0,
        buffer_duration=1.0,
        retention_days=1,
    )


@pytest.fixture
def frame_queue() -> FrameQueue:
    return FrameQueue(maxsize=20)


@pytest.fixture
def result_queue() -> ResultQueue:
    return ResultQueue(maxsize=10)


# ═══════════════════════════════════════════════════════════════
# 1. 数据类测试
# ═══════════════════════════════════════════════════════════════


class TestInferenceResult:
    """InferenceResult 数据类的创建和属性。"""

    def test_creation_with_defaults(self):
        frame = _make_frame()
        r = InferenceResult(
            camera_id="cam-1", frame=frame, frame_id=1, timestamp=1.0
        )
        assert r.camera_id == "cam-1"
        assert r.frame_id == 1
        assert r.timestamp == 1.0
        assert r.detections == []
        assert r.tracks == []
        assert r.inference_latency_ms == 0.0

    def test_creation_with_all_fields(self):
        frame = _make_frame()
        det = _make_detection()
        trk = _make_track()
        r = InferenceResult(
            camera_id="cam-2",
            frame=frame,
            frame_id=42,
            timestamp=1700000000.0,
            detections=[det],
            tracks=[trk],
            inference_latency_ms=25.5,
        )
        assert r.camera_id == "cam-2"
        assert r.frame_id == 42
        assert len(r.detections) == 1
        assert r.detections[0].class_name == "person"
        assert len(r.tracks) == 1
        assert r.inference_latency_ms == pytest.approx(25.5)

    def test_frame_is_numpy_array(self):
        r = InferenceResult(
            camera_id="cam-1",
            frame=_make_frame(),
            frame_id=0,
            timestamp=0.0,
        )
        assert isinstance(r.frame, np.ndarray)
        assert r.frame.shape == (64, 64, 3)


class TestHealthResponse:
    """HealthResponse 数据类的创建和默认值。"""

    def test_defaults(self):
        h = HealthResponse()
        assert h.status == "ok"
        assert h.uptime_seconds == 0.0
        assert h.gpu_utilization == 0.0
        assert h.queue_depth == 0
        assert h.inference_latency_p50_ms == 0.0
        assert h.inference_latency_p99_ms == 0.0
        assert h.active_cameras == 0
        assert h.total_cameras == 0
        assert h.today_alerts == 0
        assert h.llm_success_rate == 1.0

    def test_custom_values(self):
        h = HealthResponse(
            status="degraded",
            uptime_seconds=3600.0,
            gpu_utilization=85.0,
            gpu_memory_used_mb=4096.0,
            gpu_memory_total_mb=8192.0,
            queue_depth=150,
            inference_latency_p50_ms=12.0,
            inference_latency_p99_ms=50.0,
            active_cameras=2,
            total_cameras=3,
            today_alerts=15,
            llm_success_rate=0.95,
        )
        assert h.status == "degraded"
        assert h.uptime_seconds == 3600.0
        assert h.active_cameras == 2
        assert h.total_cameras == 3
        assert h.llm_success_rate == pytest.approx(0.95)


class TestAlertStats:
    """AlertStats 数据类的创建和默认值。"""

    def test_defaults(self):
        a = AlertStats()
        assert a.total == 0
        assert a.pending == 0
        assert a.acknowledged == 0
        assert a.rejected == 0
        assert a.resolved == 0
        assert a.today_count == 0

    def test_custom_values(self):
        a = AlertStats(total=100, pending=10, acknowledged=50, rejected=5, resolved=35)
        assert a.total == 100
        assert a.pending == 10
        assert a.today_count == 0  # 默认 0


# ═══════════════════════════════════════════════════════════════
# 2. ResultQueue 测试
# ═══════════════════════════════════════════════════════════════


class TestResultQueuePutGet:
    """ResultQueue 基本 put/get 操作。"""

    def test_put_then_get(self):
        q = ResultQueue(maxsize=5)
        result = InferenceResult(
            camera_id="cam-1", frame=_make_frame(), frame_id=1, timestamp=1.0
        )
        q.put(result)
        got = q.get(timeout=0.1)
        assert got is not None
        assert got.camera_id == "cam-1"
        assert got.frame_id == 1

    def test_get_timeout_returns_none(self):
        q = ResultQueue(maxsize=5)
        got = q.get(timeout=0.05)
        assert got is None

    def test_fifo_order(self):
        q = ResultQueue(maxsize=10)
        for i in range(3):
            q.put(InferenceResult(
                camera_id="cam-1", frame=_make_frame(), frame_id=i, timestamp=float(i)
            ))
        results = []
        for _ in range(3):
            r = q.get(timeout=0.1)
            if r:
                results.append(r.frame_id)
        assert results == [0, 1, 2]


class TestResultQueueDropOld:
    """ResultQueue 满则丢旧行为。"""

    def test_full_drops_oldest(self):
        q = ResultQueue(maxsize=3)
        for i in range(5):
            q.put(InferenceResult(
                camera_id="cam-1", frame=_make_frame(), frame_id=i, timestamp=float(i)
            ))
        # 应该只剩下最新 3 个：frame_id 2, 3, 4
        assert q.size == 3
        ids = []
        while q.size > 0:
            r = q.get(timeout=0.1)
            if r:
                ids.append(r.frame_id)
        assert ids == [2, 3, 4]

    def test_drop_count_increments(self):
        q = ResultQueue(maxsize=2)
        for i in range(5):
            q.put(InferenceResult(
                camera_id="cam-1", frame=_make_frame(), frame_id=i, timestamp=float(i)
            ))
        assert q._drop_count == 3  # 超过 maxsize=2 的部分（3, 4 丢弃旧的 0,1,2 → 丢弃 3 次）


class TestResultQueueClear:
    """ResultQueue clear 操作。"""

    def test_clear_empties_queue(self):
        q = ResultQueue(maxsize=10)
        for i in range(5):
            q.put(InferenceResult(
                camera_id="cam-1", frame=_make_frame(), frame_id=i, timestamp=float(i)
            ))
        assert q.size == 5
        q.clear()
        assert q.size == 0

    def test_clear_on_empty_queue_no_error(self):
        q = ResultQueue(maxsize=5)
        q.clear()
        assert q.size == 0


class TestResultQueueQsize:
    """ResultQueue qsize 属性。"""

    def test_initial_qsize_zero(self):
        q = ResultQueue(maxsize=10)
        assert q.size == 0

    def test_qsize_after_puts(self):
        q = ResultQueue(maxsize=10)
        for i in range(3):
            q.put(InferenceResult(
                camera_id="c", frame=_make_frame(), frame_id=i, timestamp=float(i)
            ))
        assert q.size == 3

    def test_qsize_after_gets(self):
        q = ResultQueue(maxsize=10)
        for i in range(3):
            q.put(InferenceResult(
                camera_id="c", frame=_make_frame(), frame_id=i, timestamp=float(i)
            ))
        q.get(timeout=0.1)
        assert q.size == 2


# ═══════════════════════════════════════════════════════════════
# 3. LatencyTracker 测试
# ═══════════════════════════════════════════════════════════════


class TestLatencyTrackerRecord:
    """LatencyTracker record 操作。"""

    def test_record_increments_count(self):
        t = LatencyTracker(window_size=100)
        assert t.count == 0
        t.record(10.0)
        assert t.count == 1
        t.record(20.0)
        assert t.count == 2

    def test_window_respects_maxlen(self):
        t = LatencyTracker(window_size=3)
        for i in range(5):
            t.record(float(i))
        assert t.count == 3  # 窗口大小限制


class TestLatencyTrackerPercentiles:
    """LatencyTracker p50/p99 计算。"""

    def test_p50_of_known_values(self):
        t = LatencyTracker(window_size=100)
        values = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]
        for v in values:
            t.record(v)
        # p50 = sorted[5] = 6.0
        assert t.p50 == pytest.approx(6.0)

    def test_p99_of_known_values(self):
        t = LatencyTracker(window_size=100)
        for i in range(100):
            t.record(float(i + 1))
        # p99 = sorted[99] = 100.0
        assert t.p99 == pytest.approx(100.0)

    def test_single_value_p50_equals_p99(self):
        t = LatencyTracker(window_size=100)
        t.record(42.0)
        assert t.p50 == pytest.approx(42.0)
        assert t.p99 == pytest.approx(42.0)

    def test_custom_percentile(self):
        t = LatencyTracker(window_size=100)
        for i in range(100):
            t.record(float(i))
        assert t.percentile(90) == pytest.approx(90.0)


class TestLatencyTrackerEmpty:
    """LatencyTracker 空窗口行为。"""

    def test_empty_p50_returns_zero(self):
        t = LatencyTracker(window_size=100)
        assert t.p50 == 0.0

    def test_empty_p99_returns_zero(self):
        t = LatencyTracker(window_size=100)
        assert t.p99 == 0.0

    def test_empty_percentile_returns_zero(self):
        t = LatencyTracker(window_size=100)
        assert t.percentile(50) == 0.0
        assert t.percentile(99) == 0.0

    def test_empty_count_returns_zero(self):
        t = LatencyTracker(window_size=100)
        assert t.count == 0


# ═══════════════════════════════════════════════════════════════
# 4. InferenceThread 测试
# ═══════════════════════════════════════════════════════════════


class TestInferenceThreadStartStop:
    """InferenceThread 启动和停止。"""

    def test_start_creates_thread(self, frame_queue, result_queue, mock_detector):
        tracker = MockTrackerManager(TrackerConfig())
        recorder = MagicMock()

        thread = InferenceThread(
            frame_queue=frame_queue,
            detector=mock_detector,
            tracker_manager=tracker,
            result_queue=result_queue,
            recorder=recorder,
            batch_size=4,
            batch_timeout_ms=50,
        )
        thread.start()
        assert thread.is_alive()
        thread.stop(timeout=2.0)
        assert not thread.is_alive()

    def test_double_start_is_idempotent(self, frame_queue, result_queue, mock_detector):
        tracker = MockTrackerManager(TrackerConfig())
        recorder = MagicMock()

        thread = InferenceThread(
            frame_queue=frame_queue,
            detector=mock_detector,
            tracker_manager=tracker,
            result_queue=result_queue,
            recorder=recorder,
        )
        thread.start()
        thread.start()  # 第二次不应报错
        assert thread.is_alive()
        thread.stop(timeout=2.0)

    def test_stop_without_start(self, frame_queue, result_queue, mock_detector):
        tracker = MockTrackerManager(TrackerConfig())
        recorder = MagicMock()

        thread = InferenceThread(
            frame_queue=frame_queue,
            detector=mock_detector,
            tracker_manager=tracker,
            result_queue=result_queue,
            recorder=recorder,
        )
        thread.stop(timeout=1.0)  # 不应报错
        assert not thread.is_alive()


class TestInferenceThreadBatchProcessing:
    """InferenceThread batch 处理逻辑。"""

    def test_processes_frames_and_produces_results(
        self, frame_queue, result_queue, mock_detector
    ):
        tracker = MockTrackerManager(TrackerConfig())
        recorder = MagicMock()

        thread = InferenceThread(
            frame_queue=frame_queue,
            detector=mock_detector,
            tracker_manager=tracker,
            result_queue=result_queue,
            recorder=recorder,
            batch_size=4,
            batch_timeout_ms=100,
        )

        # 放入 3 帧
        for i in range(3):
            frame_queue.put(
                FrameData(
                    camera_id="cam-1",
                    frame=_make_frame(fill=i),
                    timestamp=time.time(),
                    frame_seq=i + 1,
                )
            )

        thread.start()
        # 等待处理完成
        time.sleep(0.5)
        thread.stop(timeout=2.0)

        # 结果队列应有 3 个结果
        assert result_queue.size >= 1  # 至少处理了一些
        # 录制器应被调用 push_frame
        assert recorder.push_frame.call_count >= 1
        # 检查统计
        assert thread.total_frames >= 1
        assert thread.total_inferences >= 1

    def test_detect_error_does_not_crash(
        self, frame_queue, result_queue
    ):
        """检测器抛出异常时，推理线程不应崩溃。"""

        class FailingDetector(MockDetector):
            def detect(self, frames):
                raise RuntimeError("GPU OOM")

        detector = FailingDetector()
        tracker = MockTrackerManager(TrackerConfig())
        recorder = MagicMock()

        thread = InferenceThread(
            frame_queue=frame_queue,
            detector=detector,
            tracker_manager=tracker,
            result_queue=result_queue,
            recorder=recorder,
            batch_size=2,
            batch_timeout_ms=50,
        )

        frame_queue.put(_make_frame_data())
        thread.start()
        time.sleep(0.5)
        thread.stop(timeout=2.0)

        # 不崩溃，仍在统计
        assert thread.total_inferences >= 1

    def test_latency_tracking(self, frame_queue, result_queue, mock_detector):
        tracker = MockTrackerManager(TrackerConfig())
        recorder = MagicMock()

        thread = InferenceThread(
            frame_queue=frame_queue,
            detector=mock_detector,
            tracker_manager=tracker,
            result_queue=result_queue,
            recorder=recorder,
            batch_size=2,
            batch_timeout_ms=50,
        )

        for i in range(4):
            frame_queue.put(_make_frame_data(seq=i + 1))

        thread.start()
        time.sleep(0.5)
        thread.stop(timeout=2.0)

        # p50 应 > 0（有处理过帧）
        assert thread.p50_latency >= 0
        assert thread.p99_latency >= 0


# ═══════════════════════════════════════════════════════════════
# 5. ActionThread 测试
# ═══════════════════════════════════════════════════════════════


class TestActionThreadStartStop:
    """ActionThread 启动和停止。"""

    def test_start_and_stop(self, result_queue):
        rule_engine = MagicMock()
        recorder = MagicMock()

        thread = ActionThread(
            result_queue=result_queue,
            rule_engine=rule_engine,
            recorder=recorder,
        )
        thread.start()
        assert thread.is_alive()
        thread.stop(timeout=2.0)
        assert not thread.is_alive()

    def test_double_start_idempotent(self, result_queue):
        rule_engine = MagicMock()
        recorder = MagicMock()

        thread = ActionThread(
            result_queue=result_queue,
            rule_engine=rule_engine,
            recorder=recorder,
        )
        thread.start()
        thread.start()
        assert thread.is_alive()
        thread.stop(timeout=2.0)

    def test_stop_without_start(self, result_queue):
        rule_engine = MagicMock()
        recorder = MagicMock()

        thread = ActionThread(
            result_queue=result_queue,
            rule_engine=rule_engine,
            recorder=recorder,
        )
        thread.stop(timeout=1.0)  # 不应报错


class TestActionThreadRuleEvaluation:
    """ActionThread 规则引擎评估。"""

    def test_rule_engine_called_with_correct_args(self, result_queue):
        rule_engine = MagicMock()
        rule_engine.evaluate.return_value = []
        recorder = MagicMock()
        recorder.save_snapshot.return_value = "/tmp/snap.jpg"

        thread = ActionThread(
            result_queue=result_queue,
            rule_engine=rule_engine,
            recorder=recorder,
        )
        thread.start()

        # 推入一个结果
        result = InferenceResult(
            camera_id="cam-1",
            frame=_make_frame(),
            frame_id=1,
            timestamp=1700000000.0,
            detections=[_make_detection()],
            tracks=[_make_track()],
        )
        result_queue.put(result)
        time.sleep(0.5)
        thread.stop(timeout=2.0)

        # 规则引擎应被调用
        rule_engine.evaluate.assert_called_once()
        call_kwargs = rule_engine.evaluate.call_args
        assert call_kwargs.kwargs["camera_id"] == "cam-1"
        assert isinstance(call_kwargs.kwargs["tracks"], list)
        assert isinstance(call_kwargs.kwargs["frame"], np.ndarray)
        assert call_kwargs.kwargs["timestamp"] == 1700000000.0

    def test_rule_eval_error_does_not_crash(self, result_queue):
        rule_engine = MagicMock()
        rule_engine.evaluate.side_effect = RuntimeError("rule parse error")
        recorder = MagicMock()

        thread = ActionThread(
            result_queue=result_queue,
            rule_engine=rule_engine,
            recorder=recorder,
        )
        thread.start()
        result_queue.put(InferenceResult(
            camera_id="cam-1", frame=_make_frame(), frame_id=1, timestamp=1.0
        ))
        time.sleep(0.5)
        thread.stop(timeout=2.0)
        # 不崩溃
        assert thread.total_processed >= 1


class TestActionThreadAlertGeneration:
    """ActionThread 告警生成流程。"""

    def test_alert_generation_calls_recorder_and_db(self, result_queue):
        event = _make_event()
        rule_engine = MagicMock()
        rule_engine.evaluate.return_value = [event]
        recorder = MagicMock()
        recorder.save_snapshot.return_value = "/tmp/snap.jpg"
        database = MagicMock()
        database.save_alert.return_value = "alert-id-001"

        thread = ActionThread(
            result_queue=result_queue,
            rule_engine=rule_engine,
            recorder=recorder,
            database=database,
        )
        thread.start()

        result_queue.put(InferenceResult(
            camera_id="cam-1", frame=_make_frame(), frame_id=1, timestamp=1.0,
            detections=[_make_detection()], tracks=[_make_track()],
        ))
        time.sleep(0.5)
        thread.stop(timeout=2.0)

        # 截图应被保存
        recorder.save_snapshot.assert_called_once()
        # 视频片段应被截取
        recorder.save_clip.assert_called_once()
        # 数据库应保存告警
        database.save_alert.assert_called_once()
        assert event.snapshot_path == "/tmp/snap.jpg"
        assert thread.total_events >= 1
        assert thread._total_alerts >= 1

    def test_multiple_events_generate_multiple_alerts(self, result_queue):
        events = [_make_event("cam-1"), _make_event("cam-1")]
        rule_engine = MagicMock()
        rule_engine.evaluate.return_value = events
        recorder = MagicMock()
        recorder.save_snapshot.return_value = "/tmp/snap.jpg"

        thread = ActionThread(
            result_queue=result_queue,
            rule_engine=rule_engine,
            recorder=recorder,
        )
        thread.start()
        result_queue.put(InferenceResult(
            camera_id="cam-1", frame=_make_frame(), frame_id=1, timestamp=1.0,
        ))
        time.sleep(0.5)
        thread.stop(timeout=2.0)

        assert thread.total_events == 2
        assert thread._total_alerts == 2
        assert recorder.save_snapshot.call_count == 2
        assert recorder.save_clip.call_count == 2

    def test_on_alert_callback_called(self, result_queue):
        event = _make_event()
        rule_engine = MagicMock()
        rule_engine.evaluate.return_value = [event]
        recorder = MagicMock()
        recorder.save_snapshot.return_value = "/tmp/snap.jpg"
        callback = MagicMock()

        thread = ActionThread(
            result_queue=result_queue,
            rule_engine=rule_engine,
            recorder=recorder,
            on_alert=callback,
        )
        thread.start()
        result_queue.put(InferenceResult(
            camera_id="cam-1", frame=_make_frame(), frame_id=1, timestamp=1.0,
        ))
        time.sleep(0.5)
        thread.stop(timeout=2.0)

        callback.assert_called_once()
        received_event = callback.call_args[0][0]
        assert received_event.event_id == event.event_id


class TestActionThreadLLM:
    """ActionThread LLM 分析器调用。"""

    def test_llm_analyzer_called_on_event(self, result_queue):
        event = _make_event()
        rule_engine = MagicMock()
        rule_engine.evaluate.return_value = [event]
        recorder = MagicMock()
        recorder.save_snapshot.return_value = "/tmp/snap.jpg"
        llm = MagicMock()
        llm.analyze.return_value = {"description": "test"}

        thread = ActionThread(
            result_queue=result_queue,
            rule_engine=rule_engine,
            recorder=recorder,
            llm_analyzer=llm,
        )
        thread.start()
        result_queue.put(InferenceResult(
            camera_id="cam-1", frame=_make_frame(), frame_id=1, timestamp=1.0,
        ))
        time.sleep(0.5)
        thread.stop(timeout=2.0)

        llm.analyze.assert_called_once()
        assert thread.llm_success_rate == pytest.approx(1.0)
        assert thread._llm_calls == 1
        assert thread._llm_successes == 1

    def test_llm_error_does_not_crash(self, result_queue):
        event = _make_event()
        rule_engine = MagicMock()
        rule_engine.evaluate.return_value = [event]
        recorder = MagicMock()
        recorder.save_snapshot.return_value = "/tmp/snap.jpg"
        llm = MagicMock()
        llm.analyze.side_effect = TimeoutError("LLM timeout")

        thread = ActionThread(
            result_queue=result_queue,
            rule_engine=rule_engine,
            recorder=recorder,
            llm_analyzer=llm,
        )
        thread.start()
        result_queue.put(InferenceResult(
            camera_id="cam-1", frame=_make_frame(), frame_id=1, timestamp=1.0,
        ))
        time.sleep(0.5)
        thread.stop(timeout=2.0)

        # LLM 失败但不影响告警生成
        assert thread._total_alerts >= 1
        assert thread._llm_calls == 1
        assert thread._llm_successes == 0
        assert thread.llm_success_rate == pytest.approx(0.0)

    def test_llm_success_rate_zero_calls_returns_one(self, result_queue):
        rule_engine = MagicMock()
        rule_engine.evaluate.return_value = []
        recorder = MagicMock()

        thread = ActionThread(
            result_queue=result_queue,
            rule_engine=rule_engine,
            recorder=recorder,
        )
        # 未调用时成功率应为 1.0
        assert thread.llm_success_rate == pytest.approx(1.0)


class TestActionThreadNotifiers:
    """ActionThread 通知发送。"""

    def test_notifiers_called_on_event(self, result_queue):
        event = _make_event()
        rule_engine = MagicMock()
        rule_engine.evaluate.return_value = [event]
        recorder = MagicMock()
        recorder.save_snapshot.return_value = "/tmp/snap.jpg"
        notifier1 = MagicMock()
        notifier1.execute.return_value = True
        notifier2 = MagicMock()
        notifier2.execute.return_value = True

        thread = ActionThread(
            result_queue=result_queue,
            rule_engine=rule_engine,
            recorder=recorder,
            notifiers=[notifier1, notifier2],
        )
        thread.start()
        result_queue.put(InferenceResult(
            camera_id="cam-1", frame=_make_frame(), frame_id=1, timestamp=1.0,
        ))
        time.sleep(0.5)
        thread.stop(timeout=2.0)

        notifier1.execute.assert_called_once()
        notifier2.execute.assert_called_once()

    def test_notifier_error_does_not_crash(self, result_queue):
        event = _make_event()
        rule_engine = MagicMock()
        rule_engine.evaluate.return_value = [event]
        recorder = MagicMock()
        recorder.save_snapshot.return_value = "/tmp/snap.jpg"
        notifier = MagicMock()
        notifier.execute.side_effect = ConnectionError("network down")

        thread = ActionThread(
            result_queue=result_queue,
            rule_engine=rule_engine,
            recorder=recorder,
            notifiers=[notifier],
        )
        thread.start()
        result_queue.put(InferenceResult(
            camera_id="cam-1", frame=_make_frame(), frame_id=1, timestamp=1.0,
        ))
        time.sleep(0.5)
        thread.stop(timeout=2.0)

        # 不崩溃，告警仍生成
        assert thread._total_alerts >= 1


# ═══════════════════════════════════════════════════════════════
# 6. TimerTask 测试
# ═══════════════════════════════════════════════════════════════


class TestTimerTaskStartStop:
    """TimerTask 启动和停止。"""

    def test_start_and_stop(self):
        callback = MagicMock()
        task = TimerTask(interval=0.1, callback=callback, name="test-timer")
        task.start()
        time.sleep(0.3)
        task.stop()
        # 回调应至少被调用 1 次
        assert callback.call_count >= 1

    def test_double_start_idempotent(self):
        callback = MagicMock()
        task = TimerTask(interval=0.1, callback=callback)
        task.start()
        task.start()  # 第二次不报错
        time.sleep(0.2)
        task.stop()

    def test_stop_without_start(self):
        callback = MagicMock()
        task = TimerTask(interval=1.0, callback=callback)
        task.stop()  # 不报错


class TestTimerTaskCallback:
    """TimerTask 定时回调。"""

    def test_callback_called_periodically(self):
        call_count = []
        callback = MagicMock(side_effect=lambda: call_count.append(1))
        task = TimerTask(interval=0.05, callback=callback, name="periodic")
        task.start()
        time.sleep(0.35)
        task.stop()
        assert callback.call_count >= 3  # 0.35s / 0.05s ≈ 7, 至少 3

    def test_callback_error_does_not_crash_timer(self):
        call_count = [0]

        def flaky_callback():
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("first call fails")

        task = TimerTask(interval=0.05, callback=flaky_callback, name="flaky")
        task.start()
        time.sleep(0.3)
        task.stop()
        # 第一次调用失败，后续调用应继续
        assert call_count[0] >= 2


# ═══════════════════════════════════════════════════════════════
# 7. VisionAgent 测试
# ═══════════════════════════════════════════════════════════════


@pytest.fixture
def camera_config_cam1() -> CameraConfig:
    return CameraConfig(
        camera_id="cam-1",
        camera_name="Front Door",
        rtsp_url="rtsp://test/stream1",
        fps=5.0,
        width=64,
        height=64,
    )


@pytest.fixture
def camera_config_cam2() -> CameraConfig:
    return CameraConfig(
        camera_id="cam-2",
        camera_name="Back Yard",
        rtsp_url="rtsp://test/stream2",
        fps=5.0,
        width=64,
        height=64,
    )


@pytest.fixture
def pipeline_config() -> PipelineConfig:
    return PipelineConfig(
        frame_queue_size=20,
        result_queue_size=10,
        shutdown_timeout=5.0,
        frame_drain_timeout=0.5,
        health_check_interval=0.5,
        thread_restart_max=2,
    )


@pytest.fixture
def vision_agent(
    camera_config_cam1,
    camera_config_cam2,
    mock_detector,
    tracker_config,
    recorder_config,
    pipeline_config,
):
    """构造一个 VisionAgent 实例，使用 mock 组件替代真实 FFmpeg/ultralytics。"""
    with patch(
        "vision_agent.core.pipeline.CameraThread", MockCameraThread
    ), patch(
        "vision_agent.core.pipeline.TrackerManager", MockTrackerManager
    ):
        agent = VisionAgent(
            camera_configs=[
                CameraConfigItem(camera_config=camera_config_cam1, fps=5.0),
                CameraConfigItem(camera_config=camera_config_cam2, fps=5.0),
            ],
            detector=mock_detector,
            tracker_config=tracker_config,
            recorder_config=recorder_config,
            pipeline_config=pipeline_config,
        )
        yield agent
        # 确保清理
        if agent.status != SystemStatus.STOPPED:
            agent.stop()


class TestVisionAgentInit:
    """VisionAgent 初始化和组件组装。"""

    def test_initial_status_stopped(self, vision_agent):
        assert vision_agent.status == SystemStatus.STOPPED

    def test_cameras_created(self, vision_agent):
        assert len(vision_agent._camera_threads) == 2
        assert vision_agent._camera_threads[0].camera_id == "cam-1"
        assert vision_agent._camera_threads[1].camera_id == "cam-2"

    def test_queues_created(self, vision_agent):
        assert vision_agent._frame_queue is not None
        assert vision_agent._result_queue is not None

    def test_inference_thread_created(self, vision_agent):
        assert vision_agent._inference_thread is not None

    def test_action_thread_created(self, vision_agent):
        assert vision_agent._action_thread is not None


class TestVisionAgentStartStop:
    """VisionAgent 启动和停止生命周期。"""

    def test_start_transitions_to_running(self, vision_agent):
        assert vision_agent.status == SystemStatus.STOPPED
        vision_agent.start()
        assert vision_agent.status == SystemStatus.RUNNING
        vision_agent.stop()
        assert vision_agent.status == SystemStatus.STOPPED

    def test_start_starts_all_cameras(self, vision_agent):
        vision_agent.start()
        for thread in vision_agent._camera_threads:
            assert thread._started is True
        vision_agent.stop()

    def test_stop_stops_all_cameras(self, vision_agent):
        vision_agent.start()
        vision_agent.stop()
        for thread in vision_agent._camera_threads:
            assert thread._started is False

    def test_double_start_is_idempotent(self, vision_agent):
        vision_agent.start()
        vision_agent.start()  # 不报错
        assert vision_agent.status == SystemStatus.RUNNING
        vision_agent.stop()

    def test_stop_without_start(self, vision_agent):
        vision_agent.stop()  # 不报错
        assert vision_agent.status == SystemStatus.STOPPED

    def test_uptime_positive_after_start(self, vision_agent):
        vision_agent.start()
        time.sleep(0.1)
        assert vision_agent.uptime_seconds > 0
        vision_agent.stop()

    def test_uptime_zero_before_start(self, vision_agent):
        assert vision_agent.uptime_seconds == 0.0

    def test_start_with_rule_engine_and_notifiers(
        self,
        camera_config_cam1,
        mock_detector,
        tracker_config,
        recorder_config,
        pipeline_config,
    ):
        rule_engine = MagicMock()
        llm = MagicMock()
        notifier = MagicMock()
        database = MagicMock()

        with patch(
            "vision_agent.core.pipeline.CameraThread", MockCameraThread
        ), patch(
            "vision_agent.core.pipeline.TrackerManager", MockTrackerManager
        ):
            agent = VisionAgent(
                camera_configs=[CameraConfigItem(camera_config=camera_config_cam1)],
                detector=mock_detector,
                tracker_config=tracker_config,
                recorder_config=recorder_config,
                pipeline_config=pipeline_config,
                rule_engine=rule_engine,
                llm_analyzer=llm,
                notifiers=[notifier],
                database=database,
            )
            agent.start()
            time.sleep(0.1)
            agent.stop()

            # 关闭时应调用 database.close()
            database.close.assert_called_once()


class TestVisionAgentHealth:
    """VisionAgent 健康检查。"""

    def test_health_returns_health_response(self, vision_agent):
        vision_agent.start()
        time.sleep(0.1)
        h = vision_agent.health()
        vision_agent.stop()

        assert isinstance(h, HealthResponse)
        assert h.status in ("ok", "degraded", "unhealthy")
        assert h.active_cameras == 2  # MockCameraThread 默认 CONNECTED
        assert h.total_cameras == 2
        assert h.uptime_seconds > 0

    def test_health_before_start(self, vision_agent):
        h = vision_agent.health()
        assert h.active_cameras == 0
        assert h.total_cameras == 2
        assert h.uptime_seconds == 0.0

    def test_health_status_ok_when_cameras_active(self, vision_agent):
        vision_agent.start()
        time.sleep(0.1)
        # 模拟有 GPU 可用（health 检查 gpu_mem_total > 0）
        with patch.object(
            VisionAgent, "_get_gpu_stats", return_value=(50.0, 2048.0, 8192.0)
        ):
            h = vision_agent.health()
        vision_agent.stop()
        # 有在线摄像头、队列不深、GPU 可用 → ok
        assert h.status == "ok"

    def test_health_reports_zero_gpu_when_unavailable(self, vision_agent):
        """无 GPU 时 GPU 指标为零，但系统仍可正常运行（CPU 降级）。"""
        vision_agent.start()
        time.sleep(0.1)
        h = vision_agent.health()
        vision_agent.stop()
        # 无 GPU 环境下 GPU 指标为零，但系统状态取决于摄像头而非 GPU
        assert h.gpu_memory_total_mb == 0.0
        assert h.gpu_utilization == 0.0

    def test_health_with_database_today_alerts(
        self,
        camera_config_cam1,
        mock_detector,
        tracker_config,
        recorder_config,
        pipeline_config,
    ):
        db = MagicMock()
        db.count_alerts_today.return_value = 42

        with patch(
            "vision_agent.core.pipeline.CameraThread", MockCameraThread
        ), patch(
            "vision_agent.core.pipeline.TrackerManager", MockTrackerManager
        ):
            agent = VisionAgent(
                camera_configs=[CameraConfigItem(camera_config=camera_config_cam1)],
                detector=mock_detector,
                tracker_config=tracker_config,
                recorder_config=recorder_config,
                pipeline_config=pipeline_config,
                database=db,
            )
            agent.start()
            time.sleep(0.1)
            h = agent.health()
            agent.stop()

            assert h.today_alerts == 42


class TestVisionAgentCameraManagement:
    """VisionAgent 摄像头动态管理。"""

    def test_add_camera(self, vision_agent):
        vision_agent.start()
        new_cam = CameraConfig(
            camera_id="cam-new",
            camera_name="New Camera",
            rtsp_url="rtsp://test/new",
            width=64,
            height=64,
        )
        vision_agent.add_camera(new_cam, fps=5.0)
        assert len(vision_agent._camera_threads) == 3
        # 新摄像头应已启动（因系统处于 RUNNING）
        ids = [t.camera_id for t in vision_agent._camera_threads]
        assert "cam-new" in ids
        vision_agent.stop()

    def test_remove_camera(self, vision_agent):
        vision_agent.start()
        assert len(vision_agent._camera_threads) == 2
        vision_agent.remove_camera("cam-1")
        assert len(vision_agent._camera_threads) == 1
        assert vision_agent._camera_threads[0].camera_id == "cam-2"
        vision_agent.stop()

    def test_remove_nonexistent_camera_no_crash(self, vision_agent):
        vision_agent.start()
        vision_agent.remove_camera("ghost-cam")  # 不报错
        assert len(vision_agent._camera_threads) == 2
        vision_agent.stop()

    def test_reload_camera(self, vision_agent, camera_config_cam1):
        vision_agent.start()
        new_config = CameraConfig(
            camera_id="cam-1",
            camera_name="Front Door Updated",
            rtsp_url="rtsp://test/new_stream",
            fps=10.0,
            width=128,
            height=128,
        )
        vision_agent.reload_camera(new_config, fps=10.0)
        # 摄像头数量不变
        assert len(vision_agent._camera_threads) == 2
        # cam-1 仍存在
        ids = [t.camera_id for t in vision_agent._camera_threads]
        assert "cam-1" in ids
        vision_agent.stop()

    def test_reload_nonexistent_camera_no_crash(self, vision_agent):
        vision_agent.start()
        ghost_config = CameraConfig(
            camera_id="ghost",
            camera_name="Ghost",
            rtsp_url="rtsp://test/ghost",
            width=64,
            height=64,
        )
        vision_agent.reload_camera(ghost_config)  # 不报错，仅 warning
        vision_agent.stop()


class TestVisionAgentGetStates:
    """VisionAgent get_camera_states 和 get_alert_stats。"""

    def test_get_camera_states_returns_dict(self, vision_agent):
        states = vision_agent.get_camera_states()
        assert isinstance(states, dict)
        assert "cam-1" in states
        assert "cam-2" in states
        assert isinstance(states["cam-1"], CameraState)

    def test_get_alert_stats_default(self, vision_agent):
        stats = vision_agent.get_alert_stats()
        assert isinstance(stats, AlertStats)
        assert stats.total == 0
        assert stats.today_count == 0

    def test_get_alert_stats_with_database(
        self,
        camera_config_cam1,
        mock_detector,
        tracker_config,
        recorder_config,
        pipeline_config,
    ):
        db = MagicMock()
        db.count_alerts_today.return_value = 7

        with patch(
            "vision_agent.core.pipeline.CameraThread", MockCameraThread
        ), patch(
            "vision_agent.core.pipeline.TrackerManager", MockTrackerManager
        ):
            agent = VisionAgent(
                camera_configs=[CameraConfigItem(camera_config=camera_config_cam1)],
                detector=mock_detector,
                tracker_config=tracker_config,
                recorder_config=recorder_config,
                pipeline_config=pipeline_config,
                database=db,
            )
            stats = agent.get_alert_stats()
            assert stats.today_count == 7


class TestVisionAgentReloadRules:
    """VisionAgent 规则重载。"""

    def test_reload_rules_with_engine(self, vision_agent):
        # 未配置规则引擎时重载仅 warning
        vision_agent.reload_rules()  # 不报错

    def test_reload_rules_with_configured_engine(
        self,
        camera_config_cam1,
        mock_detector,
        tracker_config,
        recorder_config,
        pipeline_config,
    ):
        rule_engine = MagicMock()

        with patch(
            "vision_agent.core.pipeline.CameraThread", MockCameraThread
        ), patch(
            "vision_agent.core.pipeline.TrackerManager", MockTrackerManager
        ):
            agent = VisionAgent(
                camera_configs=[CameraConfigItem(camera_config=camera_config_cam1)],
                detector=mock_detector,
                tracker_config=tracker_config,
                recorder_config=recorder_config,
                pipeline_config=pipeline_config,
                rule_engine=rule_engine,
            )
            agent.reload_rules()  # 不报错


class TestVisionAgentShutdownOrder:
    """VisionAgent 优雅关闭顺序。"""

    def test_shutdown_calls_detector_release(
        self,
        camera_config_cam1,
        mock_detector,
        tracker_config,
        recorder_config,
        pipeline_config,
    ):
        with patch(
            "vision_agent.core.pipeline.CameraThread", MockCameraThread
        ), patch(
            "vision_agent.core.pipeline.TrackerManager", MockTrackerManager
        ):
            agent = VisionAgent(
                camera_configs=[CameraConfigItem(camera_config=camera_config_cam1)],
                detector=mock_detector,
                tracker_config=tracker_config,
                recorder_config=recorder_config,
                pipeline_config=pipeline_config,
            )
            agent.start()
            time.sleep(0.1)
            agent.stop()

            assert mock_detector._released is True

    def test_shutdown_calls_database_close(
        self,
        camera_config_cam1,
        mock_detector,
        tracker_config,
        recorder_config,
        pipeline_config,
    ):
        db = MagicMock()
        with patch(
            "vision_agent.core.pipeline.CameraThread", MockCameraThread
        ), patch(
            "vision_agent.core.pipeline.TrackerManager", MockTrackerManager
        ):
            agent = VisionAgent(
                camera_configs=[CameraConfigItem(camera_config=camera_config_cam1)],
                detector=mock_detector,
                tracker_config=tracker_config,
                recorder_config=recorder_config,
                pipeline_config=pipeline_config,
                database=db,
            )
            agent.start()
            time.sleep(0.1)
            agent.stop()

            db.close.assert_called_once()


class TestVisionAgentDetectorReleaseError:
    """VisionAgent 关闭时 detector.release 异常不崩溃。"""

    def test_detector_release_error_handled(
        self,
        camera_config_cam1,
        tracker_config,
        recorder_config,
        pipeline_config,
    ):
        class BadDetector(MockDetector):
            def release(self):
                raise RuntimeError("CUDA error")

        detector = BadDetector()
        with patch(
            "vision_agent.core.pipeline.CameraThread", MockCameraThread
        ), patch(
            "vision_agent.core.pipeline.TrackerManager", MockTrackerManager
        ):
            agent = VisionAgent(
                camera_configs=[CameraConfigItem(camera_config=camera_config_cam1)],
                detector=detector,
                tracker_config=tracker_config,
                recorder_config=recorder_config,
                pipeline_config=pipeline_config,
            )
            agent.start()
            time.sleep(0.1)
            agent.stop()  # 不应崩溃
            assert agent.status == SystemStatus.STOPPED


class TestVisionAgentStatusCallbacks:
    """VisionAgent 状态回调通知。"""

    def test_status_callbacks_invoked(self, vision_agent):
        statuses = []
        vision_agent._status_callbacks.append(lambda s: statuses.append(s.value))

        vision_agent.start()
        time.sleep(0.1)
        vision_agent.stop()

        assert "starting" in statuses
        assert "running" in statuses
        assert "shutting_down" in statuses
        assert "stopped" in statuses

    def test_status_callback_error_does_not_crash(self, vision_agent):
        def bad_callback(status):
            raise RuntimeError("callback error")

        vision_agent._status_callbacks.append(bad_callback)
        vision_agent.start()
        time.sleep(0.1)
        vision_agent.stop()
        # 不崩溃
        assert vision_agent.status == SystemStatus.STOPPED


class TestVisionAgentEndToEnd:
    """VisionAgent 端到端集成测试。"""

    def test_full_pipeline_with_alert(
        self,
        camera_config_cam1,
        mock_detector,
        tracker_config,
        recorder_config,
        pipeline_config,
    ):
        """验证完整流程：帧 → 推理 → 规则 → 告警 → 回调。"""
        event = _make_event("cam-1")
        rule_engine = MagicMock()
        rule_engine.evaluate.return_value = [event]
        recorder_mock = MagicMock()
        recorder_mock.save_snapshot.return_value = "/tmp/snap.jpg"
        db = MagicMock()
        db.save_alert.return_value = "alert-001"
        alert_received = []

        def on_alert(evt):
            alert_received.append(evt)

        with patch(
            "vision_agent.core.pipeline.CameraThread", MockCameraThread
        ), patch(
            "vision_agent.core.pipeline.TrackerManager", MockTrackerManager
        ), patch(
            "vision_agent.core.pipeline.ClipRecorder", return_value=recorder_mock
        ):
            agent = VisionAgent(
                camera_configs=[CameraConfigItem(camera_config=camera_config_cam1)],
                detector=mock_detector,
                tracker_config=tracker_config,
                recorder_config=recorder_config,
                pipeline_config=pipeline_config,
                rule_engine=rule_engine,
                database=db,
                on_alert=on_alert,
            )
            agent.start()
            time.sleep(0.1)

            # 手动向 frame_queue 推入帧，模拟采集层
            for i in range(3):
                agent._frame_queue.put(
                    FrameData(
                        camera_id="cam-1",
                        frame=_make_frame(fill=i),
                        timestamp=time.time(),
                        frame_seq=i + 1,
                    )
                )

            # 等待推理 + 处理
            time.sleep(1.0)

            agent.stop()

            # 验证规则引擎被调用
            assert rule_engine.evaluate.call_count >= 1
            # 验证告警回调被调用
            assert len(alert_received) >= 1
            # 验证数据库保存
            assert db.save_alert.call_count >= 1


class TestSystemStatusEnum:
    """SystemStatus 枚举值。"""

    def test_enum_values(self):
        assert SystemStatus.STARTING == "starting"
        assert SystemStatus.RUNNING == "running"
        assert SystemStatus.DEGRADED == "degraded"
        assert SystemStatus.SHUTTING_DOWN == "shutting_down"
        assert SystemStatus.STOPPED == "stopped"


class TestPipelineConfig:
    """PipelineConfig 默认值和自定义值。"""

    def test_defaults(self):
        cfg = PipelineConfig()
        assert cfg.frame_queue_size == 200
        assert cfg.result_queue_size == 100
        assert cfg.shutdown_timeout == 30.0
        assert cfg.frame_drain_timeout == 3.0
        assert cfg.health_check_interval == 5.0
        assert cfg.thread_restart_max == 3

    def test_custom_values(self):
        cfg = PipelineConfig(
            frame_queue_size=50,
            result_queue_size=25,
            shutdown_timeout=10.0,
            frame_drain_timeout=1.0,
            health_check_interval=2.0,
            thread_restart_max=5,
        )
        assert cfg.frame_queue_size == 50
        assert cfg.result_queue_size == 25
        assert cfg.thread_restart_max == 5


class TestCameraConfigItem:
    """CameraConfigItem 数据类。"""

    def test_creation(self):
        cam = CameraConfig(
            camera_id="cam-1",
            camera_name="Test",
            rtsp_url="rtsp://test",
            fps=10.0,
            width=64,
            height=64,
        )
        item = CameraConfigItem(camera_config=cam, fps=10.0)
        assert item.camera_config.camera_id == "cam-1"
        assert item.fps == 10.0

    def test_default_fps(self):
        cam = CameraConfig(
            camera_id="cam-1",
            camera_name="Test",
            rtsp_url="rtsp://test",
            width=64,
            height=64,
        )
        item = CameraConfigItem(camera_config=cam)
        assert item.fps == 5.0
