"""热加载功能测试 — 运行时配置变更推送"""

from unittest.mock import MagicMock, patch

import pytest

from sentinelmind.core.camera import CameraConfig
from sentinelmind.core.detector import DetectorConfig, YOLODetector
from sentinelmind.core.pipeline import CameraConfigItem, VisionAgent
from sentinelmind.core.recorder import ClipRecorder, RecorderConfig
from sentinelmind.core.tracker import TrackerConfig


# ─── YOLODetector 运行时参数 ─────────────────────────────────


class TestYOLODetectorHotReload:
    """YOLODetector.set_confidence / set_iou_threshold 运行时调整"""

    @pytest.fixture
    def detector(self):
        """创建 YOLODetector，mock _load_model 避免加载真实模型文件"""
        config = DetectorConfig(
            model_path="models/test.pt",
            confidence=0.5,
            iou_threshold=0.45,
        )
        with patch.object(YOLODetector, "_load_model", return_value=None):
            det = YOLODetector(config, device="cpu")
        return det

    def test_set_confidence_runtime(self, detector):
        """set_confidence 运行时改置信度，config 对象立即反映变更"""
        assert detector.config.confidence == 0.5
        detector.set_confidence(0.3)
        assert detector.config.confidence == 0.3

    def test_set_iou_threshold_runtime(self, detector):
        """set_iou_threshold 运行时改 IoU，config 对象立即反映变更"""
        assert detector.config.iou_threshold == 0.45
        detector.set_iou_threshold(0.6)
        assert detector.config.iou_threshold == 0.6


# ─── ClipRecorder 运行时配置 ─────────────────────────────────


class TestClipRecorderHotReload:
    """ClipRecorder.hot_update_config 运行时更新录制配置"""

    @pytest.fixture
    def recorder(self, tmp_path):
        """创建 ClipRecorder 实例（tmp_path 作为输出目录）"""
        config = RecorderConfig(
            retention_days=7,
            snapshot_retention_days=30,
            output_dir=str(tmp_path / "clips"),
            snapshot_dir=str(tmp_path / "snapshots"),
        )
        return ClipRecorder(config, fps=15.0)

    def test_recorder_hot_update(self, recorder):
        """hot_update_config 更新 retention_days"""
        assert recorder._config.retention_days == 7
        recorder.hot_update_config({"retention_days": 30})
        assert recorder._config.retention_days == 30

    def test_recorder_hot_update_unknown_field(self, recorder):
        """不认识的字段不抛异常，原值保持不变"""
        original = recorder._config.retention_days
        recorder.hot_update_config({"nonexistent_field": 999})
        assert recorder._config.retention_days == original


# ─── VisionAgent 热加载路由 ──────────────────────────────────


class TestVisionAgentHotReload:
    """VisionAgent.hot_update 路径路由 + _set_config_reference"""

    @pytest.fixture
    def agent(self, tmp_path):
        """创建最小 VisionAgent，detector/recorder 均为 Mock 便于断言路由"""
        camera_config = CameraConfig(
            camera_id="cam_test",
            camera_name="测试",
            source_type="test",
        )
        recorder_config = RecorderConfig(
            output_dir=str(tmp_path / "clips"),
            snapshot_dir=str(tmp_path / "snapshots"),
        )
        agent = VisionAgent(
            camera_configs=[CameraConfigItem(camera_config=camera_config, fps=15.0)],
            detector=MagicMock(),
            tracker_config=TrackerConfig(),
            recorder_config=recorder_config,
        )
        # 替换 recorder 为 mock，便于断言路由分发
        agent._recorder = MagicMock()
        return agent

    def test_pipeline_hot_update_confidence(self, agent):
        """hot_update("detector.confidence", 0.3) 推送到 detector.set_confidence"""
        agent.hot_update("detector.confidence", 0.3)
        agent._detector.set_confidence.assert_called_once_with(0.3)

    def test_pipeline_hot_update_iou(self, agent):
        """hot_update("detector.iou_threshold", 0.6) 推送到 detector.set_iou_threshold"""
        agent.hot_update("detector.iou_threshold", 0.6)
        agent._detector.set_iou_threshold.assert_called_once_with(0.6)

    def test_pipeline_hot_update_recorder(self, agent):
        """hot_update("recording.retention_days", 30) 推送到 recorder.hot_update_config"""
        agent.hot_update("recording.retention_days", 30)
        agent._recorder.hot_update_config.assert_called_once_with(
            {"retention_days": 30}
        )

    def test_pipeline_hot_update_recorder_snapshot(self, agent):
        """hot_update("recording.snapshot_retention_days", 60) 正确提取字段名"""
        agent.hot_update("recording.snapshot_retention_days", 60)
        agent._recorder.hot_update_config.assert_called_once_with(
            {"snapshot_retention_days": 60}
        )

    def test_pipeline_set_config_reference(self, agent):
        """_set_config_reference 注入后 self._global_config 不为 None"""
        # __init__ 未初始化 _global_config，注入前属性不存在
        assert not hasattr(agent, "_global_config")
        test_config = {"key": "value"}
        agent._set_config_reference(test_config)
        assert agent._global_config is not None
        assert agent._global_config is test_config
        assert agent._global_config["key"] == "value"

    def test_hot_update_unknown_path(self, agent):
        """不认识的路径不抛异常，也不会调用任何组件方法"""
        agent.hot_update("unknown.path", "value")
        agent._detector.set_confidence.assert_not_called()
        agent._detector.set_iou_threshold.assert_not_called()
        agent._recorder.hot_update_config.assert_not_called()
