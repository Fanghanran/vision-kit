"""单元测试 — vision_agent.config.settings 配置管理模块

覆盖范围：
- load_yaml(): 正常加载、文件不存在、YAML 语法错误、根元素非字典
- substitute_env_vars(): 正常替换、默认值语法、敏感字段、嵌套结构、深拷贝
- deep_merge(): 标量覆盖、列表替换、字典递归合并
- validate_global_config(): 必填字段缺失、类型错误、范围校验
- validate_camera_config(): rtsp_url 格式、ID 唯一性、fps 范围
- ConfigManager.load(): 正常加载、配置文件不存在
- ConfigManager.get(): 点分路径、嵌套路径、不存在路径返回默认值
- ConfigManager.get_camera(): 摄像头配置合并、不存在的摄像头
- ConfigManager.reload_camera(): 重新加载指定摄像头
- ConfigManager.watch(): 回调注册和通知
"""

from __future__ import annotations

import pytest
import yaml

from vision_agent.config.settings import (
    ConfigLoadError,
    ConfigManager,
    ConfigValidationError,
    ValidationResult,
    deep_merge,
    load_yaml,
    substitute_env_vars,
    validate_camera_config,
    validate_global_config,
)


# ─── Fixtures ──────────────────────────────────────────────────


@pytest.fixture()
def minimal_settings(tmp_path):
    """创建最小的合法 settings.yaml 并返回路径"""
    model_file = tmp_path / "model.onnx"
    model_file.touch()
    settings = {
        "version": 1,
        "system": {
            "data_dir": str(tmp_path / "data"),
            "log_dir": str(tmp_path / "logs"),
            "log_level": "INFO",
        },
        "gpu": {"device_id": 0, "batch_size": 8},
        "detector": {"model_path": str(model_file), "confidence": 0.5, "iou": 0.45},
        "storage": {"type": "sqlite"},
        "web": {"port": 8080},
    }
    path = tmp_path / "settings.yaml"
    path.write_text(yaml.dump(settings, allow_unicode=True), encoding="utf-8")
    return path


@pytest.fixture()
def config_manager(tmp_path, minimal_settings):
    """创建已加载的 ConfigManager 实例，附带 cameras/ 目录"""
    cameras_dir = tmp_path / "cameras"
    cameras_dir.mkdir()
    cam = {
        "camera": {
            "id": "cam_01",
            "name": "测试摄像头",
            "rtsp_url": "rtsp://192.168.1.1:554/stream",
            "fps": 10,
        }
    }
    cam_file = cameras_dir / "cam_01.yaml"
    cam_file.write_text(yaml.dump(cam, allow_unicode=True), encoding="utf-8")

    mgr = ConfigManager(minimal_settings)
    mgr.load()
    return mgr


# ─── load_yaml ─────────────────────────────────────────────────


class TestLoadYaml:
    """load_yaml() 函数测试"""

    def test_normal_load(self, tmp_path):
        """正常加载 YAML 文件"""
        data = {"key": "value", "nested": {"a": 1}}
        path = tmp_path / "config.yaml"
        path.write_text(yaml.dump(data), encoding="utf-8")
        result = load_yaml(path)
        assert result == data

    def test_file_not_exists(self, tmp_path):
        """文件不存在时抛出 ConfigLoadError"""
        path = tmp_path / "missing.yaml"
        with pytest.raises(ConfigLoadError, match="配置文件不存在"):
            load_yaml(path)

    def test_yaml_syntax_error(self, tmp_path):
        """YAML 语法错误时抛出 ConfigLoadError"""
        path = tmp_path / "bad.yaml"
        path.write_text("{{invalid yaml", encoding="utf-8")
        with pytest.raises(ConfigLoadError, match="YAML 解析失败"):
            load_yaml(path)

    def test_root_not_dict(self, tmp_path):
        """根元素不是字典时抛出 ConfigLoadError"""
        path = tmp_path / "list.yaml"
        path.write_text("- item1\n- item2\n", encoding="utf-8")
        with pytest.raises(ConfigLoadError, match="根元素必须是字典"):
            load_yaml(path)


# ─── substitute_env_vars ───────────────────────────────────────


class TestSubstituteEnvVars:
    """substitute_env_vars() 函数测试"""

    def test_normal_replacement(self, monkeypatch):
        """正常替换已设置的环境变量"""
        monkeypatch.setenv("MY_VAR", "hello")
        config = {"key": "${MY_VAR}"}
        result = substitute_env_vars(config)
        assert result["key"] == "hello"

    def test_default_value_syntax(self):
        """${VAR:-default} 使用默认值"""
        config = {"key": "${UNDEFINED_VAR_XYZ:-fallback}"}
        result = substitute_env_vars(config)
        assert result["key"] == "fallback"

    def test_default_value_overridden_by_env(self, monkeypatch):
        """环境变量存在时 ${VAR:-default} 使用环境变量值"""
        monkeypatch.setenv("MY_VAR2", "real_value")
        config = {"key": "${MY_VAR2:-fallback}"}
        result = substitute_env_vars(config)
        assert result["key"] == "real_value"

    def test_sensitive_field_missing_returns_empty(self):
        """敏感字段缺失时替换为空字符串"""
        config = {"password": "${MISSING_SECRET_PASS}"}
        result = substitute_env_vars(config)
        assert result["password"] == ""

    def test_non_sensitive_field_missing_keeps_original(self):
        """非敏感字段缺失时保留原始 ${VAR} 文本"""
        config = {"name": "${MISSING_NON_SENSITIVE}"}
        result = substitute_env_vars(config)
        assert result["name"] == "${MISSING_NON_SENSITIVE}"

    def test_nested_dict(self, monkeypatch):
        """嵌套字典中的环境变量替换"""
        monkeypatch.setenv("NESTED_VAR", "replaced")
        config = {"level1": {"level2": "${NESTED_VAR}"}}
        result = substitute_env_vars(config)
        assert result["level1"]["level2"] == "replaced"

    def test_list_values(self, monkeypatch):
        """列表中的环境变量替换"""
        monkeypatch.setenv("LIST_VAR", "val")
        config = {"items": ["${LIST_VAR}", "literal"]}
        result = substitute_env_vars(config)
        assert result["items"] == ["val", "literal"]

    def test_deep_copy_not_modify_original(self, monkeypatch):
        """替换结果是深拷贝，不修改原始字典"""
        monkeypatch.setenv("COPY_VAR", "new")
        config = {"key": "${COPY_VAR}"}
        result = substitute_env_vars(config)
        assert config["key"] == "${COPY_VAR}"
        assert result["key"] == "new"

    def test_non_string_values_unchanged(self):
        """非字符串值不受影响"""
        config = {"num": 42, "flag": True, "none_val": None}
        result = substitute_env_vars(config)
        assert result == {"num": 42, "flag": True, "none_val": None}

    def test_multiple_vars_in_one_string(self, monkeypatch):
        """单个字符串中包含多个环境变量"""
        monkeypatch.setenv("VAR_A", "aaa")
        monkeypatch.setenv("VAR_B", "bbb")
        config = {"key": "${VAR_A}-${VAR_B}"}
        result = substitute_env_vars(config)
        assert result["key"] == "aaa-bbb"

    def test_api_key_path_is_sensitive(self):
        """嵌套路径中包含 api_key 关键字时按敏感字段处理"""
        config = {"llm": {"api_key": "${MISSING_LLM_KEY}"}}
        result = substitute_env_vars(config)
        assert result["llm"]["api_key"] == ""


# ─── deep_merge ────────────────────────────────────────────────


class TestDeepMerge:
    """deep_merge() 函数测试"""

    def test_scalar_override(self):
        """标量值直接覆盖"""
        base = {"a": 1, "b": 2}
        override = {"a": 100}
        result = deep_merge(base, override)
        assert result == {"a": 100, "b": 2}

    def test_list_replacement(self):
        """列表完全替换而非追加"""
        base = {"items": [1, 2, 3]}
        override = {"items": [4, 5]}
        result = deep_merge(base, override)
        assert result["items"] == [4, 5]

    def test_dict_recursive_merge(self):
        """字典递归合并"""
        base = {"section": {"a": 1, "b": 2}}
        override = {"section": {"b": 20, "c": 30}}
        result = deep_merge(base, override)
        assert result["section"] == {"a": 1, "b": 20, "c": 30}

    def test_new_key_from_override(self):
        """override 中的全新键被添加"""
        base = {"a": 1}
        override = {"b": 2}
        result = deep_merge(base, override)
        assert result == {"a": 1, "b": 2}

    def test_does_not_modify_inputs(self):
        """不修改原始输入字典"""
        base = {"a": {"x": 1}}
        override = {"a": {"y": 2}}
        deep_merge(base, override)
        assert base == {"a": {"x": 1}}
        assert override == {"a": {"y": 2}}

    def test_nested_deep_copy(self):
        """嵌套结构是深拷贝"""
        base = {"a": {"x": [1, 2]}}
        override = {"a": {"x": [3]}}
        result = deep_merge(base, override)
        # 修改结果不影响原始数据
        result["a"]["x"].append(99)
        assert base["a"]["x"] == [1, 2]


# ─── validate_global_config ────────────────────────────────────


class TestValidateGlobalConfig:
    """validate_global_config() 函数测试"""

    def _valid_config(self, tmp_path) -> dict:
        """返回一份合法的全局配置（基于默认值）"""
        data_dir = tmp_path / "data"
        log_dir = tmp_path / "logs"
        model_file = tmp_path / "model.onnx"
        data_dir.mkdir(exist_ok=True)
        log_dir.mkdir(exist_ok=True)
        model_file.touch(exist_ok=True)
        return {
            "version": 1,
            "system": {
                "data_dir": str(data_dir),
                "log_dir": str(log_dir),
                "log_level": "INFO",
            },
            "gpu": {"device_id": 0, "batch_size": 8},
            "detector": {"model_path": str(model_file), "confidence": 0.5, "iou": 0.45},
            "storage": {"type": "sqlite"},
            "web": {"port": 8080},
        }

    def test_valid_config(self, tmp_path):
        """合法配置校验通过"""
        config = self._valid_config(tmp_path)
        result = validate_global_config(config)
        assert result.is_valid

    def test_missing_version(self, tmp_path):
        """缺少 version 字段"""
        config = self._valid_config(tmp_path)
        del config["version"]
        result = validate_global_config(config)
        assert not result.is_valid
        assert any("version" in e.lower() for e in result.errors)

    def test_wrong_version(self, tmp_path):
        """version 不匹配"""
        config = self._valid_config(tmp_path)
        config["version"] = 999
        result = validate_global_config(config)
        assert not result.is_valid
        assert any("version" in e for e in result.errors)

    def test_invalid_log_level(self, tmp_path):
        """无效的 log_level"""
        config = self._valid_config(tmp_path)
        config["system"]["log_level"] = "VERBOSE"
        result = validate_global_config(config)
        assert not result.is_valid
        assert any("log_level" in e for e in result.errors)

    def test_negative_device_id(self, tmp_path):
        """gpu.device_id 为负数"""
        config = self._valid_config(tmp_path)
        config["gpu"]["device_id"] = -1
        result = validate_global_config(config)
        assert not result.is_valid
        assert any("device_id" in e for e in result.errors)

    def test_batch_size_out_of_range(self, tmp_path):
        """gpu.batch_size 超出 1-64 范围"""
        config = self._valid_config(tmp_path)
        config["gpu"]["batch_size"] = 100
        result = validate_global_config(config)
        assert not result.is_valid
        assert any("batch_size" in e for e in result.errors)

    def test_batch_size_zero(self, tmp_path):
        """gpu.batch_size 为 0"""
        config = self._valid_config(tmp_path)
        config["gpu"]["batch_size"] = 0
        result = validate_global_config(config)
        assert not result.is_valid

    def test_empty_model_path(self, tmp_path):
        """detector.model_path 为空"""
        config = self._valid_config(tmp_path)
        config["detector"]["model_path"] = ""
        result = validate_global_config(config)
        assert not result.is_valid
        assert any("model_path" in e for e in result.errors)

    def test_model_path_not_exists(self, tmp_path):
        """detector.model_path 文件不存在"""
        config = self._valid_config(tmp_path)
        config["detector"]["model_path"] = "/nonexistent/model.onnx"
        result = validate_global_config(config)
        assert not result.is_valid
        assert any("model_path" in e for e in result.errors)

    def test_confidence_out_of_range(self, tmp_path):
        """detector.confidence 超出 0.0-1.0 范围"""
        config = self._valid_config(tmp_path)
        config["detector"]["confidence"] = 1.5
        result = validate_global_config(config)
        assert not result.is_valid
        assert any("confidence" in e for e in result.errors)

    def test_iou_out_of_range(self, tmp_path):
        """detector.iou 超出 0.0-1.0 范围"""
        config = self._valid_config(tmp_path)
        config["detector"]["iou"] = -0.1
        result = validate_global_config(config)
        assert not result.is_valid
        assert any("iou" in e for e in result.errors)

    def test_invalid_storage_type(self, tmp_path):
        """storage.type 无效"""
        config = self._valid_config(tmp_path)
        config["storage"]["type"] = "mongodb"
        result = validate_global_config(config)
        assert not result.is_valid
        assert any("storage" in e for e in result.errors)

    def test_web_port_out_of_range(self, tmp_path):
        """web.port 超出 1-65535 范围"""
        config = self._valid_config(tmp_path)
        config["web"]["port"] = 70000
        result = validate_global_config(config)
        assert not result.is_valid
        assert any("port" in e for e in result.errors)

    def test_web_port_zero(self, tmp_path):
        """web.port 为 0"""
        config = self._valid_config(tmp_path)
        config["web"]["port"] = 0
        result = validate_global_config(config)
        assert not result.is_valid

    def test_llm_enabled_no_api_key_warning(self, tmp_path):
        """llm.enabled=true 但 api_key 为空时产生 warning"""
        config = self._valid_config(tmp_path)
        config["llm"] = {"enabled": True, "api_key": ""}
        result = validate_global_config(config)
        assert result.is_valid  # warning，不是 error
        assert len(result.warnings) > 0
        assert any("api_key" in w for w in result.warnings)

    def test_multiple_errors(self, tmp_path):
        """多个校验错误同时报告"""
        config = self._valid_config(tmp_path)
        config["version"] = 999
        config["gpu"]["device_id"] = -1
        config["web"]["port"] = 0
        result = validate_global_config(config)
        assert not result.is_valid
        assert len(result.errors) >= 3


# ─── validate_camera_config ────────────────────────────────────


class TestValidateCameraConfig:
    """validate_camera_config() 函数测试"""

    def _valid_camera(self) -> dict:
        return {
            "camera": {
                "id": "cam_01",
                "name": "入口摄像头",
                "rtsp_url": "rtsp://192.168.1.1:554/stream",
                "fps": 10,
            }
        }

    def test_valid_camera(self):
        """合法摄像头配置校验通过"""
        result = validate_camera_config(self._valid_camera(), set())
        assert result.is_valid

    def test_empty_camera_id(self):
        """camera.id 为空"""
        cam = self._valid_camera()
        cam["camera"]["id"] = ""
        result = validate_camera_config(cam, set())
        assert not result.is_valid
        assert any("camera.id" in e for e in result.errors)

    def test_missing_camera_id(self):
        """camera.id 不存在"""
        cam = {"camera": {"name": "test", "rtsp_url": "rtsp://x"}}
        result = validate_camera_config(cam, set())
        assert not result.is_valid

    def test_invalid_camera_id_format(self):
        """camera.id 包含非法字符"""
        cam = self._valid_camera()
        cam["camera"]["id"] = "cam-01!"  # 连字符和感叹号非法
        result = validate_camera_config(cam, set())
        assert not result.is_valid
        assert any("camera.id" in e for e in result.errors)

    def test_duplicate_camera_id(self):
        """camera.id 重复"""
        cam = self._valid_camera()
        existing = {"cam_01"}
        result = validate_camera_config(cam, existing)
        assert not result.is_valid
        assert any("重复" in e for e in result.errors)

    def test_empty_rtsp_url(self):
        """camera.rtsp_url 为空"""
        cam = self._valid_camera()
        cam["camera"]["rtsp_url"] = ""
        result = validate_camera_config(cam, set())
        assert not result.is_valid
        assert any("rtsp_url" in e for e in result.errors)

    def test_invalid_rtsp_url_format(self):
        """camera.rtsp_url 不以 rtsp:// 开头"""
        cam = self._valid_camera()
        cam["camera"]["rtsp_url"] = "http://192.168.1.1/stream"
        result = validate_camera_config(cam, set())
        assert not result.is_valid
        assert any("rtsp://" in e for e in result.errors)

    def test_fps_out_of_range_warning(self):
        """camera.fps 超出 1-30 范围产生 warning 而非 error"""
        cam = self._valid_camera()
        cam["camera"]["fps"] = 50
        result = validate_camera_config(cam, set())
        assert result.is_valid  # warning，不是 error
        assert len(result.warnings) > 0
        assert any("fps" in w for w in result.warnings)

    def test_fps_zero_warning(self):
        """camera.fps 为 0 产生 warning"""
        cam = self._valid_camera()
        cam["camera"]["fps"] = 0
        result = validate_camera_config(cam, set())
        assert result.is_valid
        assert any("fps" in w for w in result.warnings)

    def test_empty_name(self):
        """camera.name 为空"""
        cam = self._valid_camera()
        cam["camera"]["name"] = ""
        result = validate_camera_config(cam, set())
        assert not result.is_valid
        assert any("camera.name" in e for e in result.errors)

    def test_id_with_underscores_valid(self):
        """camera.id 含下划线是合法的"""
        cam = self._valid_camera()
        cam["camera"]["id"] = "camera_main_entrance"
        result = validate_camera_config(cam, set())
        assert result.is_valid


# ─── ConfigManager.load ────────────────────────────────────────


class TestConfigManagerLoad:
    """ConfigManager.load() 测试"""

    def test_normal_load(self, minimal_settings):
        """正常加载配置"""
        mgr = ConfigManager(minimal_settings)
        mgr.load()
        assert mgr._loaded

    def test_config_file_not_exists(self, tmp_path):
        """配置文件不存在时抛出 ConfigLoadError"""
        mgr = ConfigManager(tmp_path / "missing.yaml")
        with pytest.raises(ConfigLoadError, match="配置文件不存在"):
            mgr.load()

    def test_validation_error_raises(self, tmp_path):
        """配置校验不通过时抛出 ConfigValidationError"""
        settings = {"version": 1}  # 缺少 model_path 等必要字段
        path = tmp_path / "settings.yaml"
        path.write_text(yaml.dump(settings), encoding="utf-8")
        mgr = ConfigManager(path)
        with pytest.raises(ConfigValidationError):
            mgr.load()


# ─── ConfigManager.get ─────────────────────────────────────────


class TestConfigManagerGet:
    """ConfigManager.get() 测试"""

    def test_dot_path(self, config_manager):
        """点分路径获取值"""
        assert config_manager.get("gpu.device_id") == 0
        assert config_manager.get("gpu.batch_size") == 8

    def test_nested_path(self, config_manager):
        """更深的嵌套路径"""
        assert config_manager.get("storage.type") == "sqlite"

    def test_nonexistent_path_returns_default(self, config_manager):
        """不存在的路径返回默认值"""
        assert config_manager.get("nonexistent.path") is None
        assert config_manager.get("nonexistent.path", "fallback") == "fallback"

    def test_top_level_key(self, config_manager):
        """顶层键"""
        assert config_manager.get("version") == 1

    def test_default_value_none(self, config_manager):
        """默认值为 None"""
        assert config_manager.get("no.such.key") is None


# ─── ConfigManager.get_camera ──────────────────────────────────


class TestConfigManagerGetCamera:
    """ConfigManager.get_camera() 测试"""

    def test_existing_camera(self, config_manager):
        """获取已存在的摄像头，配置已合并"""
        cam = config_manager.get_camera("cam_01")
        assert cam is not None
        assert cam["camera"]["id"] == "cam_01"
        assert cam["camera"]["rtsp_url"] == "rtsp://192.168.1.1:554/stream"

    def test_camera_has_global_sections(self, config_manager):
        """摄像头配置包含全局配置段"""
        cam = config_manager.get_camera("cam_01")
        assert cam is not None
        # detector 段应从全局合并
        assert "detector" in cam

    def test_nonexistent_camera(self, config_manager):
        """不存在的摄像头返回 None"""
        assert config_manager.get_camera("nonexistent") is None

    def test_camera_override_global(self, tmp_path, minimal_settings):
        """摄像头配置覆盖全局配置"""
        cameras_dir = tmp_path / "cameras"
        cameras_dir.mkdir()
        cam = {
            "camera": {
                "id": "cam_custom",
                "name": "自定义摄像头",
                "rtsp_url": "rtsp://10.0.0.1/stream",
            },
            "detector": {"confidence": 0.8},
        }
        (cameras_dir / "cam_custom.yaml").write_text(
            yaml.dump(cam, allow_unicode=True), encoding="utf-8"
        )
        mgr = ConfigManager(minimal_settings)
        mgr.load()
        merged = mgr.get_camera("cam_custom")
        assert merged is not None
        # 摄像头覆盖了全局的 confidence
        assert merged["detector"]["confidence"] == 0.8


# ─── ConfigManager.reload_camera ───────────────────────────────


class TestConfigManagerReloadCamera:
    """ConfigManager.reload_camera() 测试"""

    def test_reload_existing_camera(self, config_manager, tmp_path):
        """重新加载已存在的摄像头配置"""
        # 修改摄像头配置文件
        cam = {
            "camera": {
                "id": "cam_01",
                "name": "更新后的摄像头",
                "rtsp_url": "rtsp://192.168.1.1:554/stream2",
                "fps": 15,
            }
        }
        cam_file = tmp_path / "cameras" / "cam_01.yaml"
        cam_file.write_text(yaml.dump(cam, allow_unicode=True), encoding="utf-8")

        result = config_manager.reload_camera("cam_01")
        assert result is True

        reloaded = config_manager.get_camera("cam_01")
        assert reloaded is not None
        assert reloaded["camera"]["name"] == "更新后的摄像头"
        assert reloaded["camera"]["fps"] == 15

    def test_reload_nonexistent_camera(self, config_manager):
        """重新加载不存在的摄像头返回 False"""
        result = config_manager.reload_camera("nonexistent")
        assert result is False

    def test_reload_camera_no_cameras_dir(self, minimal_settings, tmp_path):
        """cameras/ 目录不存在时返回 False"""
        mgr = ConfigManager(minimal_settings)
        mgr.load()
        # cameras/ 目录不存在（minimal_settings fixture 不创建 cameras/）
        result = mgr.reload_camera("cam_01")
        assert result is False


# ─── ConfigManager.watch ───────────────────────────────────────


class TestConfigManagerWatch:
    """ConfigManager.watch() 测试"""

    def test_callback_registration(self, config_manager):
        """回调函数注册成功"""
        events: list[tuple] = []
        config_manager.watch(lambda ct, cid, oc, nc: events.append((ct, cid)))
        # 手动触发通知来验证回调注册
        config_manager._notify_watchers("camera_added", "test_cam", {}, {"new": True})
        assert len(events) == 1
        assert events[0] == ("camera_added", "test_cam")

    def test_multiple_callbacks(self, config_manager):
        """多个回调都收到通知"""
        calls_a: list[str] = []
        calls_b: list[str] = []
        config_manager.watch(lambda ct, *_: calls_a.append(ct))
        config_manager.watch(lambda ct, *_: calls_b.append(ct))
        config_manager._notify_watchers("camera_updated", "cam_01", {}, {})
        assert len(calls_a) == 1
        assert len(calls_b) == 1

    def test_callback_receives_correct_args(self, config_manager):
        """回调函数收到正确的参数"""
        received: list[tuple] = []
        config_manager.watch(
            lambda ct, cid, oc, nc: received.append((ct, cid, oc, nc))
        )
        old_cfg = {"camera": {"fps": 5}}
        new_cfg = {"camera": {"fps": 10}}
        config_manager._notify_watchers("camera_updated", "cam_01", old_cfg, new_cfg)
        assert len(received) == 1
        change_type, cam_id, old_c, new_c = received[0]
        assert change_type == "camera_updated"
        assert cam_id == "cam_01"
        assert old_c == old_cfg
        assert new_c == new_cfg

    def test_callback_exception_does_not_affect_others(self, config_manager):
        """一个回调抛出异常不影响其他回调"""
        calls: list[str] = []

        def bad_callback(*_args):
            raise RuntimeError("boom")

        def good_callback(*_args):
            calls.append("called")

        config_manager.watch(bad_callback)
        config_manager.watch(good_callback)
        # 不应抛出异常
        config_manager._notify_watchers("camera_added", "cam", {}, {})
        assert calls == ["called"]

    def test_watch_on_camera_reload(self, config_manager, tmp_path):
        """摄像头配置重载时触发 watch 回调"""
        events: list[tuple] = []
        config_manager.watch(lambda ct, cid, oc, nc: events.append((ct, cid)))

        # 修改摄像头文件
        cam = {
            "camera": {
                "id": "cam_01",
                "name": "变更摄像头",
                "rtsp_url": "rtsp://192.168.1.1:554/stream",
                "fps": 20,
            }
        }
        cam_file = tmp_path / "cameras" / "cam_01.yaml"
        cam_file.write_text(yaml.dump(cam, allow_unicode=True), encoding="utf-8")

        config_manager.reload_camera("cam_01")
        assert len(events) == 1
        assert events[0][0] == "camera_updated"
        assert events[0][1] == "cam_01"


# ─── ValidationResult ──────────────────────────────────────────


class TestValidationResult:
    """ValidationResult 数据类测试"""

    def test_is_valid_when_no_errors(self):
        result = ValidationResult()
        assert result.is_valid

    def test_is_invalid_when_errors_present(self):
        result = ValidationResult()
        result.add_error("something wrong")
        assert not result.is_valid

    def test_warnings_do_not_affect_is_valid(self):
        result = ValidationResult()
        result.add_warning("just a warning")
        assert result.is_valid

    def test_add_error_and_warning(self):
        result = ValidationResult()
        result.add_error("err1")
        result.add_error("err2")
        result.add_warning("warn1")
        assert result.errors == ["err1", "err2"]
        assert result.warnings == ["warn1"]


# ─── list_cameras / get_all_cameras ────────────────────────────


class TestListAndGetAllCameras:
    """list_cameras() 和 get_all_cameras() 测试"""

    def test_list_cameras(self, config_manager):
        cam_ids = config_manager.list_cameras()
        assert "cam_01" in cam_ids

    def test_get_all_cameras(self, config_manager):
        all_cams = config_manager.get_all_cameras()
        assert "cam_01" in all_cams
        assert all_cams["cam_01"]["camera"]["id"] == "cam_01"
