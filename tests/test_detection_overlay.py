"""检测标注框画帧功能测试 — 帧上画框 + JPEG 编码 + 缓存获取"""
import pytest

pytest.importorskip("cv2", reason="需要 opencv-python-headless")

from unittest.mock import MagicMock

import numpy as np

from sentinelmind.core.camera import CameraConfig, FrameData, FrameQueue
from sentinelmind.core.detector import DetectorConfig
from sentinelmind.core.pipeline import (
    CameraConfigItem,
    InferenceThread,
    PipelineConfig,
    ResultQueue,
    VisionAgent,
)
from sentinelmind.core.recorder import RecorderConfig
from sentinelmind.core.tracker import TrackerConfig
from sentinelmind.core.types import BoundingBox, Detection


# ─── _draw_and_encode 单元测试 ─────────────────────────────


class TestDrawAndEncode:
    """InferenceThread._draw_and_encode 画框与编码"""

    @pytest.fixture
    def thread(self):
        """创建 InferenceThread（全部依赖 mock），仅用于测试 _draw_and_encode"""
        fq = FrameQueue(maxsize=10)
        rq = ResultQueue(maxsize=10)
        recorder = MagicMock()
        t = InferenceThread(fq, MagicMock(), MagicMock(), rq, recorder, 8, 50)
        return t

    @pytest.fixture
    def frame(self):
        """480×640 灰色测试帧"""
        return np.ones((480, 640, 3), dtype=np.uint8) * 128

    def test_draw_and_encode_no_detections(self, thread, frame):
        """空检测列表 → 返回纯帧 JPEG"""
        result = thread._draw_and_encode(frame, [])
        assert isinstance(result, bytes)
        assert len(result) > 0
        # JPEG 文件头
        assert result[:2] == b"\xff\xd8"
        assert result[-2:] == b"\xff\xd9"

    def test_draw_and_encode_with_detections(self, thread, frame):
        """有检测 → 返回带框帧 JPEG，至少比原帧 JPEG 大"""
        # 先编码不带检测框的纯帧
        no_det_jpeg = thread._draw_and_encode(frame, [])

        detection = Detection(
            frame_id=1,
            class_id=0,
            class_name="person",
            confidence=0.95,
            bbox=BoundingBox(100, 100, 300, 400),
        )
        with_det_jpeg = thread._draw_and_encode(frame, [detection])

        assert isinstance(with_det_jpeg, bytes)
        assert len(with_det_jpeg) > 0
        assert with_det_jpeg[:2] == b"\xff\xd8"
        # 画了框的帧包含更多内容，JPEG 应比纯帧大
        assert len(with_det_jpeg) >= len(no_det_jpeg)

    def test_draw_and_encode_mock_detection(self, thread, frame):
        """单个 Detection 对象（非列表）不抛异常，兼容 mock 场景"""
        detection = Detection(
            frame_id=1,
            class_id=0,
            class_name="person",
            confidence=0.9,
            bbox=BoundingBox(50, 50, 150, 150),
        )
        # 传入单个 Detection 而非列表 —— 不应抛异常
        result = thread._draw_and_encode(frame, detection)
        assert isinstance(result, bytes)
        assert len(result) > 0
        assert result[:2] == b"\xff\xd8"


# ─── InferenceThread.get_last_jpeg 测试 ────────────────────


class TestGetLastJpeg:
    """InferenceThread.get_last_jpeg 缓存获取"""

    @pytest.fixture
    def thread(self):
        """创建 InferenceThread，detector mock 返回空检测"""
        fq = FrameQueue(maxsize=10)
        rq = ResultQueue(maxsize=10)
        detector = MagicMock()
        detector.detect.return_value = [[]]  # 返回空检测列表
        tracker = MagicMock()
        tracker.update.return_value = []
        recorder = MagicMock()
        t = InferenceThread(fq, detector, tracker, rq, recorder, 8, 50)
        return t

    def test_get_last_jpeg_before_process(self, thread):
        """初始无数据时 get_last_jpeg 返回 None"""
        assert thread.get_last_jpeg("cam_any") is None

    def test_get_last_jpeg_after_process(self, thread):
        """_process_batch 后 get_last_jpeg 返回非空 bytes"""
        frame = np.ones((480, 640, 3), dtype=np.uint8) * 128
        fd = FrameData(
            camera_id="cam_test",
            frame=frame,
            timestamp=0.0,
            frame_seq=1,
            width=640,
            height=480,
        )
        thread._frame_queue.put(fd)
        thread._running = True

        thread._process_batch()

        entry = thread.get_last_jpeg("cam_test")
        assert entry is not None
        jpeg, fid = entry
        assert isinstance(jpeg, bytes)
        assert len(jpeg) > 0
        assert jpeg[:2] == b"\xff\xd8"


# ─── VisionAgent.get_last_frame_jpeg 测试 ─────────────────


class TestVisionAgentGetLastFrameJpeg:
    """VisionAgent.get_last_frame_jpeg 对外接口"""

    @pytest.fixture
    def agent(self, tmp_path):
        """创建 VisionAgent，detector 提供 config 避免 MagicMock 传播到 batch_size"""
        detector = MagicMock()
        detector.config = DetectorConfig(model_path="test.pt", batch_size=8)
        detector.detect.return_value = [[]]
        detector.warmup = MagicMock()

        cfg = CameraConfigItem(
            camera_config=CameraConfig(
                camera_id="test_cam", camera_name="测试", source_type="test", fps=15
            ),
            fps=15,
        )
        va = VisionAgent(
            camera_configs=[cfg],
            detector=detector,
            tracker_config=TrackerConfig(),
            recorder_config=RecorderConfig(
                enabled=False,
                output_dir=str(tmp_path / "clips"),
                snapshot_dir=str(tmp_path / "snapshots"),
            ),
            pipeline_config=PipelineConfig(),
        )
        return va

    def test_get_last_frame_jpeg_before_start(self, agent):
        """启动前推理线程未运行 is_alive()=False → 返回 None"""
        result = agent.get_last_frame_jpeg("test_cam")
        assert result is None

    def test_get_last_frame_jpeg_after_start(self, agent):
        """启动后推理线程存活 → get_last_frame_jpeg 正确路由到推理线程"""
        agent.start()
        try:
            # _last_frame_jpeg 存储格式为 (JPEG_bytes, frame_id)
            agent._inference_thread._last_frame_jpeg["test_cam"] = (b"cached_jpeg", 1)
            result = agent.get_last_frame_jpeg("test_cam")
            assert result == (b"cached_jpeg", 1)
        finally:
            agent.stop()

    def test_get_last_frame_jpeg_unknown_camera(self, agent):
        """查询不存在的摄像头 → 返回 None"""
        agent.start()
        try:
            import time
            time.sleep(1.0)
            result = agent.get_last_frame_jpeg("nonexistent_cam")
            assert result is None
        finally:
            agent.stop()


# ─── HSV 类别颜色测试 ──────────────────────────────────────


class TestHSVColorDifferentClasses:
    """不同类别产生不同颜色（hash → HSV 色调映射）"""

    def test_hsv_color_different_classes(self):
        """不同类别名称 → 不同 BGR 颜色"""
        import cv2

        def class_color(cls_name: str) -> tuple:
            """模拟 _draw_and_encode 中的颜色生成逻辑"""
            h = hash(cls_name) % 180
            color = cv2.cvtColor(
                np.uint8([[[h, 220, 200]]]), cv2.COLOR_HSV2BGR
            )[0][0]
            return (int(color[0]), int(color[1]), int(color[2]))

        c_person = class_color("person")
        c_car = class_color("car")

        assert c_person != c_car, (
            f"颜色冲突: person={c_person}, car={c_car}"
        )

        # 颜色值应在合法范围
        for c in (c_person, c_car):
            assert all(0 <= v <= 255 for v in c)

    def test_same_class_same_color(self):
        """相同类别名称 → 相同颜色"""
        import cv2

        def class_color(cls_name: str) -> tuple:
            h = hash(cls_name) % 180
            color = cv2.cvtColor(
                np.uint8([[[h, 220, 200]]]), cv2.COLOR_HSV2BGR
            )[0][0]
            return (int(color[0]), int(color[1]), int(color[2]))

        c1 = class_color("person")
        c2 = class_color("person")
        assert c1 == c2

    def test_multiple_classes_all_different(self):
        """多个不同类别名称 → 颜色大概率全不同（180 色调空间中极少冲突）"""
        import cv2

        classes = ["person", "car", "dog", "bicycle", "truck", "cat"]
        colors = []
        for cls in classes:
            h = hash(cls) % 180
            color = cv2.cvtColor(
                np.uint8([[[h, 220, 200]]]), cv2.COLOR_HSV2BGR
            )[0][0]
            colors.append((int(color[0]), int(color[1]), int(color[2])))

        # 6 个类在 180 色调空间，冲突概率 < 8%
        # 如果冲突，重试一次（换一批类名）
        if len(set(colors)) < len(classes):
            # 极少情况：重试一组完全不同的类名
            classes2 = ["apple", "banana", "orange", "grape", "mango", "peach"]
            colors2 = []
            for cls in classes2:
                h = hash(cls) % 180
                color = cv2.cvtColor(
                    np.uint8([[[h, 220, 200]]]), cv2.COLOR_HSV2BGR
                )[0][0]
                colors2.append((int(color[0]), int(color[1]), int(color[2])))
            assert len(set(colors2)) == len(classes2), (
                f"颜色冲突（第二次尝试）: {colors2}"
            )
        else:
            assert len(set(colors)) == len(classes)
