"""摄像头配置测试 — cameras.yaml 加载、detector 合并、兼容性"""

from pathlib import Path

import pytest
import yaml

from sentinelmind.config.settings import ConfigManager


class TestCameraConfig:
    """新格式 cameras.yaml（camera + detector 分节）加载与合并测试"""

    @pytest.fixture
    def config_dir(self, tmp_path):
        """创建包含 settings.yaml 的临时配置目录。

        settings.yaml 提供全局默认 detector / tracker 等配置，
        并通过校验（__file__ 作为 model_path 确保路径存在）。
        """
        settings = {
            "version": 1,
            "detector": {
                "model_path": __file__,
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
            "web": {"port": 8080},
            "system": {
                "data_dir": str(tmp_path / "data"),
                "log_dir": str(tmp_path / "logs"),
            },
        }
        settings_file = tmp_path / "settings.yaml"
        settings_file.write_text(yaml.dump(settings), encoding="utf-8")
        return tmp_path

    # ── 新格式加载 ─────────────────────────────────────────────

    def test_load_new_format(self, config_dir):
        """新格式 cameras.yaml（camera + detector 分节）能被正确解析。

        验证：
        - camera 子节正确提取（id/name/source_type）
        - detector 子节出现在合并结果中
        - get_camera 返回非 None
        """
        cameras_yaml = config_dir / "cameras.yaml"
        cameras_yaml.write_text(
            yaml.dump({
                "cameras": {
                    "cam_01": {
                        "camera": {
                            "id": "cam_01",
                            "name": "测试摄像头",
                            "source_type": "test",
                        },
                        "detector": {
                            "model_path": "models/helmet.pt",
                            "confidence": 0.7,
                        },
                    }
                }
            }),
            encoding="utf-8",
        )

        mgr = ConfigManager(config_dir / "settings.yaml")
        mgr.load()

        cam = mgr.get_camera("cam_01")
        assert cam is not None
        assert cam["camera"]["id"] == "cam_01"
        assert cam["camera"]["name"] == "测试摄像头"
        assert cam["camera"]["source_type"] == "test"
        assert "detector" in cam

    # ── detector 合并 ──────────────────────────────────────────

    def test_detector_merge(self, config_dir):
        """全局 detector + 摄像头 detector 合并，全局字段被保留。

        验证：
        - 摄像头指定的字段取摄像头值
        - 摄像头未指定的字段保留全局默认值
        - 合并后的 detector 包含两部分来源的 key
        """
        cameras_yaml = config_dir / "cameras.yaml"
        cameras_yaml.write_text(
            yaml.dump({
                "cameras": {
                    "cam_01": {
                        "camera": {
                            "id": "cam_01",
                            "name": "测试",
                            "source_type": "test",
                        },
                        "detector": {
                            "confidence": 0.8,  # 仅覆盖 confidence
                        },
                    }
                }
            }),
            encoding="utf-8",
        )

        mgr = ConfigManager(config_dir / "settings.yaml")
        mgr.load()

        cam = mgr.get_camera("cam_01")
        det = cam["detector"]

        # 摄像头覆盖
        assert det["confidence"] == 0.8
        # 全局默认保留
        assert det["iou"] == 0.45
        assert det["model_name"] == "yolo11m"
        assert det["input_size"] == 640
        # model_path 来自 settings.yaml（__file__），未被摄像头覆盖
        assert det["model_path"] == __file__

    # ── detector 覆盖 ──────────────────────────────────────────

    def test_detector_override(self, config_dir):
        """摄像头 detector 字段完整覆盖全局同名字段。

        验证：
        - model_path / confidence / iou / input_size 均被摄像头值覆盖
        - 摄像头未设置的 model_name 保留全局值
        """
        cameras_yaml = config_dir / "cameras.yaml"
        cameras_yaml.write_text(
            yaml.dump({
                "cameras": {
                    "cam_01": {
                        "camera": {
                            "id": "cam_01",
                            "name": "测试",
                            "source_type": "test",
                        },
                        "detector": {
                            "model_path": "models/custom.pt",
                            "confidence": 0.9,
                            "iou": 0.6,
                            "input_size": 1280,
                        },
                    }
                }
            }),
            encoding="utf-8",
        )

        mgr = ConfigManager(config_dir / "settings.yaml")
        mgr.load()

        cam = mgr.get_camera("cam_01")
        det = cam["detector"]

        assert det["model_path"] == "models/custom.pt"
        assert det["confidence"] == 0.9
        assert det["iou"] == 0.6
        assert det["input_size"] == 1280
        # 未覆盖字段不受影响
        assert det["model_name"] == "yolo11m"
        assert det["classes"] is None

    # ── 旧格式兼容 ─────────────────────────────────────────────

    def test_old_format_compat(self, config_dir):
        """旧格式（无 camera 子节，摄像头节直接是 camera 字段）依然正常工作。

        旧格式 cam_entry 不包含 `camera` key，整个节就是 camera 配置。
        ConfigManager 检测到后自动包装为 {"camera": {...}}。
        """
        cameras_yaml = config_dir / "cameras.yaml"
        cameras_yaml.write_text(
            yaml.dump({
                "cameras": {
                    "old_cam": {
                        # 旧格式：id/name/source_type 直接放在节内
                        "id": "old_cam",
                        "name": "旧格式摄像头",
                        "source_type": "test",
                    }
                }
            }),
            encoding="utf-8",
        )

        mgr = ConfigManager(config_dir / "settings.yaml")
        mgr.load()

        cam = mgr.get_camera("old_cam")
        assert cam is not None
        assert cam["camera"]["id"] == "old_cam"
        assert cam["camera"]["name"] == "旧格式摄像头"
        assert cam["camera"]["source_type"] == "test"
        # 旧格式无 detector 段，合并结果使用全局 detector 默认
        assert "detector" in cam
        assert cam["detector"]["confidence"] == 0.5

    # ── 不存在 ─────────────────────────────────────────────────

    def test_get_camera_nonexistent(self, config_dir):
        """查询不存在的摄像头 ID 应返回 None。"""
        cameras_yaml = config_dir / "cameras.yaml"
        cameras_yaml.write_text(
            yaml.dump({
                "cameras": {
                    "cam_01": {
                        "camera": {
                            "id": "cam_01",
                            "name": "测试",
                            "source_type": "test",
                        },
                    }
                }
            }),
            encoding="utf-8",
        )

        mgr = ConfigManager(config_dir / "settings.yaml")
        mgr.load()

        assert mgr.get_camera("nonexistent") is None
        assert mgr.get_camera("") is None

    # ── 无 detector 回退 ───────────────────────────────────────

    def test_no_detector_fallback(self, config_dir):
        """摄像头无 detector 段时，get_camera 返回全局默认 detector。

        验证合并结果中 detector 完全使用全局默认值，不受影响。
        """
        cameras_yaml = config_dir / "cameras.yaml"
        cameras_yaml.write_text(
            yaml.dump({
                "cameras": {
                    "cam_no_det": {
                        "camera": {
                            "id": "cam_no_det",
                            "name": "无检测器配置的摄像头",
                            "source_type": "test",
                        },
                        # 无 detector 段
                    }
                }
            }),
            encoding="utf-8",
        )

        mgr = ConfigManager(config_dir / "settings.yaml")
        mgr.load()

        cam = mgr.get_camera("cam_no_det")
        assert cam is not None
        assert "detector" in cam
        det = cam["detector"]

        # 应完全使用全局默认值（来自 settings.yaml 中的 detector 节）
        assert det["confidence"] == 0.5
        assert det["iou"] == 0.45
        assert det["input_size"] == 640
        assert det["model_name"] == "yolo11m"
        assert det["classes"] is None

    # ── tracker 合并 ───────────────────────────────────────────

    def test_tracker_merge(self, config_dir):
        """摄像头也可以覆盖 tracker 配置（与 detector 合并逻辑一致）。"""
        cameras_yaml = config_dir / "cameras.yaml"
        cameras_yaml.write_text(
            yaml.dump({
                "cameras": {
                    "cam_01": {
                        "camera": {
                            "id": "cam_01",
                            "name": "测试",
                            "source_type": "test",
                        },
                        "tracker": {
                            "type": "bytetrack",
                            "track_buffer": 60,
                        },
                    }
                }
            }),
            encoding="utf-8",
        )

        mgr = ConfigManager(config_dir / "settings.yaml")
        mgr.load()

        cam = mgr.get_camera("cam_01")
        assert cam is not None
        trk = cam["tracker"]

        # 摄像头覆盖
        assert trk["type"] == "bytetrack"
        assert trk["track_buffer"] == 60
        # 全局默认保留
        assert trk["track_thresh"] == 0.5
