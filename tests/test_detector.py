"""单元测试 — core/detector.py

所有 Ultralytics YOLO 和 torch.cuda 均用 unittest.mock 模拟，
不需要真实模型文件或 GPU。
"""

from __future__ import annotations

import numpy as np
import pytest
from unittest.mock import MagicMock, patch, PropertyMock

from vision_agent.core.detector import DetectorConfig, DetectorProtocol, YOLODetector
from vision_agent.core.types import Detection, BoundingBox


# ─── 辅助：构造 mock YOLO 对象 ────────────────────────────────

def _make_mock_yolo(class_names: dict[int, str] | None = None):
    """构造一个模拟的 ultralytics.YOLO 实例。"""
    if class_names is None:
        class_names = {0: "person", 1: "car", 2: "bicycle"}

    mock_yolo = MagicMock()
    mock_yolo.names = class_names
    mock_yolo.overrides = {}
    mock_yolo.half = MagicMock()
    return mock_yolo


def _make_mock_results(num_boxes: int = 2):
    """构造一个模拟的 Ultralytics Results 对象，含 num_boxes 个检测框。"""
    mock_results = MagicMock()
    mock_boxes = MagicMock()

    # 每个 box 的类别、置信度、坐标
    mock_boxes.__len__ = MagicMock(return_value=num_boxes)
    mock_boxes.cls = [0, 1][:num_boxes]
    mock_boxes.conf = [0.95, 0.80][:num_boxes]

    # xyxy 返回 tensor-like 对象（支持 .cpu().numpy()）
    coords = []
    for i in range(num_boxes):
        tensor_mock = MagicMock()
        tensor_mock.cpu.return_value.numpy.return_value = np.array(
            [10.0 * i, 20.0 * i, 100.0 + 10 * i, 200.0 + 20 * i], dtype=np.float32
        )
        coords.append(tensor_mock)
    mock_boxes.xyxy = coords

    mock_results.boxes = mock_boxes
    return mock_results


def _create_detector(
    model_path: str = "dummy_model.pt",
    **kwargs,
) -> YOLODetector:
    """在 mock 环境下构造一个 YOLODetector（跳过文件检查）。"""
    config = DetectorConfig(model_path=model_path, **kwargs)
    with patch("pathlib.Path.exists", return_value=True), patch(
        "ultralytics.YOLO", return_value=_make_mock_yolo()
    ), patch("torch.cuda.is_available", return_value=False):
        detector = YOLODetector(config, device="cpu")
    return detector


# ═══════════════════════════════════════════════════════════════
# 1. DetectorConfig 创建和默认值
# ═══════════════════════════════════════════════════════════════


class TestDetectorConfig:
    def test_required_field(self):
        cfg = DetectorConfig(model_path="test.pt")
        assert cfg.model_path == "test.pt"

    def test_default_confidence(self):
        cfg = DetectorConfig(model_path="test.pt")
        assert cfg.confidence == 0.5

    def test_default_iou_threshold(self):
        cfg = DetectorConfig(model_path="test.pt")
        assert cfg.iou_threshold == 0.45

    def test_default_batch_size(self):
        cfg = DetectorConfig(model_path="test.pt")
        assert cfg.batch_size == 8

    def test_default_batch_timeout_ms(self):
        cfg = DetectorConfig(model_path="test.pt")
        assert cfg.batch_timeout_ms == 50

    def test_default_input_size(self):
        cfg = DetectorConfig(model_path="test.pt")
        assert cfg.input_size == 640

    def test_default_fp16(self):
        cfg = DetectorConfig(model_path="test.pt")
        assert cfg.fp16 is True

    def test_default_classes_filter(self):
        cfg = DetectorConfig(model_path="test.pt")
        assert cfg.classes_filter is None

    def test_default_max_det(self):
        cfg = DetectorConfig(model_path="test.pt")
        assert cfg.max_det == 300

    def test_custom_values(self):
        cfg = DetectorConfig(
            model_path="yolo11n.engine",
            confidence=0.3,
            iou_threshold=0.6,
            batch_size=16,
            input_size=1280,
            fp16=False,
            classes_filter=["person"],
            max_det=100,
        )
        assert cfg.model_path == "yolo11n.engine"
        assert cfg.confidence == 0.3
        assert cfg.iou_threshold == 0.6
        assert cfg.batch_size == 16
        assert cfg.input_size == 1280
        assert cfg.fp16 is False
        assert cfg.classes_filter == ["person"]
        assert cfg.max_det == 100


# ═══════════════════════════════════════════════════════════════
# 2. YOLODetector：model_name 属性
# ═══════════════════════════════════════════════════════════════


class TestModelName:
    def test_model_name_returns_filename(self):
        detector = _create_detector(model_path="/models/yolo11n.pt")
        assert detector.model_name == "yolo11n.pt"

    def test_model_name_different_extension(self):
        detector = _create_detector(model_path="/models/yolov8.engine")
        assert detector.model_name == "yolov8.engine"


# ═══════════════════════════════════════════════════════════════
# 3. YOLODetector：classes 属性
# ═══════════════════════════════════════════════════════════════


class TestClasses:
    def test_classes_returns_class_names(self):
        class_names = {0: "person", 1: "car", 2: "bicycle"}
        config = DetectorConfig(model_path="dummy.pt")
        mock_yolo = _make_mock_yolo(class_names)
        with patch("pathlib.Path.exists", return_value=True), patch(
            "ultralytics.YOLO", return_value=mock_yolo
        ), patch("torch.cuda.is_available", return_value=False):
            detector = YOLODetector(config, device="cpu")
        assert detector.classes == ["person", "car", "bicycle"]

    def test_classes_empty_when_no_classes(self):
        config = DetectorConfig(model_path="dummy.pt")
        mock_yolo = _make_mock_yolo({})
        with patch("pathlib.Path.exists", return_value=True), patch(
            "ultralytics.YOLO", return_value=mock_yolo
        ), patch("torch.cuda.is_available", return_value=False):
            detector = YOLODetector(config, device="cpu")
        assert detector.classes == []


# ═══════════════════════════════════════════════════════════════
# 4. YOLODetector：set_confidence 方法
# ═══════════════════════════════════════════════════════════════


class TestSetConfidence:
    def test_set_confidence_updates_config(self):
        detector = _create_detector()
        detector.set_confidence(0.8)
        assert detector.config.confidence == 0.8

    def test_set_confidence_to_zero(self):
        detector = _create_detector()
        detector.set_confidence(0.0)
        assert detector.config.confidence == 0.0

    def test_set_confidence_to_one(self):
        detector = _create_detector()
        detector.set_confidence(1.0)
        assert detector.config.confidence == 1.0


# ═══════════════════════════════════════════════════════════════
# 5. YOLODetector：detect 空输入返回空列表
# ═══════════════════════════════════════════════════════════════


class TestDetectEmpty:
    def test_detect_empty_list_returns_empty(self):
        detector = _create_detector()
        assert detector.detect([]) == []

    def test_detect_single_with_empty_detect_returns_empty(self):
        """detect_single 通过 detect 间接测试空帧返回。"""
        detector = _create_detector()
        # 直接 mock detect 返回 [[]]，验证 detect_single 取 results[0]
        with patch.object(detector, "detect", return_value=[[]]):
            result = detector.detect_single(np.zeros((100, 100, 3), dtype=np.uint8))
            assert result == []


# ═══════════════════════════════════════════════════════════════
# 6. YOLODetector：detect_single 便捷方法
# ═══════════════════════════════════════════════════════════════


class TestDetectSingle:
    def test_detect_single_returns_first_frame_results(self):
        detector = _create_detector()
        expected = [Detection(
            frame_id=0, class_id=0, class_name="person",
            confidence=0.9, bbox=BoundingBox(0, 0, 100, 100),
        )]
        with patch.object(detector, "detect", return_value=[expected]):
            result = detector.detect_single(np.zeros((100, 100, 3), dtype=np.uint8))
            assert result == expected

    def test_detect_single_wraps_single_frame_in_list(self):
        """验证 detect_single 确实将单帧包装为 [frame] 传给 detect。"""
        detector = _create_detector()
        frame = np.zeros((100, 100, 3), dtype=np.uint8)
        with patch.object(detector, "detect", return_value=[[]]) as mock_detect:
            detector.detect_single(frame)
            args = mock_detect.call_args[0][0]
            assert len(args) == 1
            np.testing.assert_array_equal(args[0], frame)

    def test_detect_single_returns_empty_when_detect_returns_empty(self):
        """detect 返回空列表时，detect_single 返回空列表。"""
        detector = _create_detector()
        with patch.object(detector, "detect", return_value=[]):
            result = detector.detect_single(np.zeros((100, 100, 3), dtype=np.uint8))
            assert result == []


# ═══════════════════════════════════════════════════════════════
# 7. YOLODetector：_parse_results 正确解析 mock Results
# ═══════════════════════════════════════════════════════════════


class TestParseResults:
    def test_parse_results_single_detection(self):
        detector = _create_detector()
        mock_results = MagicMock()
        mock_boxes = MagicMock()
        mock_boxes.__len__ = MagicMock(return_value=1)
        mock_boxes.cls = [0]
        mock_boxes.conf = [0.95]
        tensor = MagicMock()
        tensor.cpu.return_value.numpy.return_value = np.array(
            [10.0, 20.0, 110.0, 220.0], dtype=np.float32
        )
        mock_boxes.xyxy = [tensor]
        mock_results.boxes = mock_boxes

        detections = detector._parse_results(mock_results, frame_id=5)

        assert len(detections) == 1
        d = detections[0]
        assert d.frame_id == 5
        assert d.class_id == 0
        assert d.class_name == "person"
        assert d.confidence == pytest.approx(0.95)
        assert d.bbox.x1 == pytest.approx(10.0)
        assert d.bbox.y1 == pytest.approx(20.0)
        assert d.bbox.x2 == pytest.approx(110.0)
        assert d.bbox.y2 == pytest.approx(220.0)

    def test_parse_results_multiple_detections(self):
        detector = _create_detector()
        mock_results = _make_mock_results(num_boxes=2)

        detections = detector._parse_results(mock_results, frame_id=0)

        assert len(detections) == 2
        assert detections[0].class_id == 0
        assert detections[0].class_name == "person"
        assert detections[1].class_id == 1
        assert detections[1].class_name == "car"

    def test_parse_results_empty_boxes(self):
        detector = _create_detector()
        mock_results = MagicMock()
        mock_results.boxes = None

        detections = detector._parse_results(mock_results, frame_id=0)
        assert detections == []

    def test_parse_results_class_filter(self):
        """只保留 classes_filter 指定的类别。"""
        config = DetectorConfig(
            model_path="dummy.pt",
            classes_filter=["car"],
        )
        mock_yolo = _make_mock_yolo({0: "person", 1: "car", 2: "bicycle"})
        with patch("pathlib.Path.exists", return_value=True), patch(
            "ultralytics.YOLO", return_value=mock_yolo
        ), patch("torch.cuda.is_available", return_value=False):
            detector = YOLODetector(config, device="cpu")

        mock_results = _make_mock_results(num_boxes=2)
        detections = detector._parse_results(mock_results, frame_id=0)

        # 只有 class_id=1 ("car") 应保留
        assert len(detections) == 1
        assert detections[0].class_name == "car"

    def test_parse_results_unknown_class_fallback(self):
        """未知 class_id 使用 fallback 命名 class_{id}。"""
        detector = _create_detector()
        mock_results = MagicMock()
        mock_boxes = MagicMock()
        mock_boxes.__len__ = MagicMock(return_value=1)
        mock_boxes.cls = [99]
        mock_boxes.conf = [0.5]
        tensor = MagicMock()
        tensor.cpu.return_value.numpy.return_value = np.array(
            [0, 0, 10, 10], dtype=np.float32
        )
        mock_boxes.xyxy = [tensor]
        mock_results.boxes = mock_boxes

        detections = detector._parse_results(mock_results, frame_id=0)
        assert detections[0].class_name == "class_99"


# ═══════════════════════════════════════════════════════════════
# 8. YOLODetector：_handle_oom batch_size 减半
# ═══════════════════════════════════════════════════════════════


class TestHandleOOM:
    def test_handle_oom_halves_batch_size(self):
        detector = _create_detector(batch_size=8)
        assert detector._current_batch_size == 8

        # mock _infer_batch 使其在 _handle_oom 重试时不报错
        frames = [np.zeros((100, 100, 3), dtype=np.uint8) for _ in range(4)]
        with patch.object(detector, "_infer_batch", return_value=[[] for _ in frames]):
            detector._handle_oom(frames)

        assert detector._current_batch_size == 4

    def test_handle_oom_minimum_batch_size_is_one(self):
        """batch_size 减半不低于 1。"""
        detector = _create_detector(batch_size=2)
        frames = [np.zeros((100, 100, 3), dtype=np.uint8)]

        with patch.object(detector, "_infer_batch", return_value=[[]]):
            detector._handle_oom(frames)
        assert detector._current_batch_size == 1

        # 再次 OOM — batch_size 应保持为 1
        with patch.object(detector, "_infer_batch", return_value=[[]]):
            detector._handle_oom(frames)
        assert detector._current_batch_size == 1

    def test_handle_oom_retries_in_smaller_chunks(self):
        """OOM 重试应将帧分成更小的 chunk。"""
        detector = _create_detector(batch_size=4)
        frames = [np.zeros((100, 100, 3), dtype=np.uint8) for _ in range(4)]

        infer_calls = []

        def track_infer(chunk):
            infer_calls.append(len(chunk))
            return [[] for _ in chunk]

        with patch.object(detector, "_infer_batch", side_effect=track_infer):
            detector._handle_oom(frames)

        # batch_size 从 4 减为 2，所以分成两个 chunk，每个 2 帧
        assert infer_calls == [2, 2]


# ═══════════════════════════════════════════════════════════════
# 9. YOLODetector：release 调用后 model 为 None
# ═══════════════════════════════════════════════════════════════


class TestRelease:
    def test_release_sets_model_to_none(self):
        detector = _create_detector()
        assert detector._model is not None

        detector.release()
        assert detector._model is None

    def test_release_idempotent(self):
        """多次调用 release 不应报错。"""
        detector = _create_detector()
        detector.release()
        detector.release()  # 应无异常
        assert detector._model is None

    def test_release_calls_clear_cuda_cache(self):
        detector = _create_detector()
        with patch.object(detector, "_clear_cuda_cache") as mock_clear:
            detector.release()
            mock_clear.assert_called_once()


# ═══════════════════════════════════════════════════════════════
# 10. DetectorProtocol：确认 YOLODetector 实现了协议
# ═══════════════════════════════════════════════════════════════


class TestDetectorProtocol:
    def test_yolo_detector_satisfies_protocol(self):
        assert isinstance(YOLODetector, type)
        # runtime_checkable 协议检查
        detector = _create_detector()
        assert isinstance(detector, DetectorProtocol)

    def test_detector_has_required_methods(self):
        """YOLODetector 拥有 Protocol 要求的所有方法/属性。"""
        detector = _create_detector()
        assert hasattr(detector, "detect")
        assert hasattr(detector, "warmup")
        assert hasattr(detector, "release")
        assert hasattr(detector, "model_name")
        assert hasattr(detector, "classes")
        # 确认可调用
        assert callable(detector.detect)
        assert callable(detector.warmup)
        assert callable(detector.release)
        assert isinstance(type(detector).model_name, property)
        assert isinstance(type(detector).classes, property)
