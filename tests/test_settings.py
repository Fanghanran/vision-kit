"""配置系统测试 — 加载/校验/合并/热加载"""

from pathlib import Path

import pytest
import yaml

from vision_agent.config.settings import (
    ConfigLoadError,
    ConfigManager,
    deep_merge,
    load_yaml,
    substitute_env_vars,
    validate_camera_config,
    validate_global_config,
)


class TestYAML:
    def test_load_valid(self, tmp_path):
        p = tmp_path / "cfg.yaml"
        p.write_text("key: value\nnum: 42", encoding="utf-8")
        assert load_yaml(p) == {"key": "value", "num": 42}

    def test_load_not_found(self):
        with pytest.raises(ConfigLoadError):
            load_yaml("/nonexistent/file.yaml")


class TestDeepMerge:
    def test_nested_merge(self):
        base = {"a": {"x": 1, "y": 2}, "b": 3}
        override = {"a": {"y": 20}}
        result = deep_merge(base, override)
        assert result["a"]["x"] == 1
        assert result["a"]["y"] == 20


class TestEnvSubstitution:
    def test_default_value(self, monkeypatch):
        monkeypatch.delenv("NOT_SET_VAR", raising=False)
        result = substitute_env_vars({"k": "${NOT_SET_VAR:-default}"})
        assert result["k"] == "default"


class TestGlobalValidation:
    def test_missing_version(self):
        r = validate_global_config({})
        assert not r.is_valid

    def test_valid_minimal(self):
        cfg = {
            "version": 1,
            "detector": {"model_path": __file__},
            "gpu": {"device_id": 0, "batch_size": 4},
            "web": {"port": 8080},
        }
        r = validate_global_config(cfg)
        assert r.is_valid


class TestCameraValidation:
    def test_valid_camera(self):
        cfg = {"camera": {"id": "cam_01", "name": "测试", "source_type": "test"}}
        r = validate_camera_config(cfg, set())
        assert r.is_valid

    def test_missing_id(self):
        r = validate_camera_config({"camera": {"name": "无ID"}}, set())
        assert len(r.errors) > 0

    def test_duplicate_id(self):
        r = validate_camera_config({"camera": {"id": "dup", "name": "x"}}, {"dup"})
        assert "重复" in " ".join(r.errors)

    def test_rtsp_missing_url(self):
        cfg = {"camera": {"id": "cam_r", "name": "r", "source_type": "rtsp"}}
        r = validate_camera_config(cfg, set())
        assert not r.is_valid

    def test_video_missing_path(self):
        cfg = {"camera": {"id": "cam_v", "name": "v", "source_type": "video"}}
        r = validate_camera_config(cfg, set())
        assert not r.is_valid


class TestConfigManager:
    @pytest.fixture
    def config_dir(self, tmp_path):
        s = tmp_path / "settings.yaml"
        s.write_text(
            yaml.dump({
                "version": 1,
                "detector": {"model_path": __file__},
                "web": {"port": 8080},
                "system": {"data_dir": str(tmp_path / "data"), "log_dir": str(tmp_path / "logs")},
            }),
            encoding="utf-8",
        )
        (tmp_path / "cameras").mkdir()
        return tmp_path

    def test_load_and_get(self, config_dir):
        mgr = ConfigManager(config_dir / "settings.yaml")
        mgr.load()
        assert mgr.get("web.port") == 8080

    def test_get_fallback(self, config_dir):
        mgr = ConfigManager(config_dir / "settings.yaml")
        mgr.load()
        assert mgr.get("nonexistent", "fb") == "fb"
