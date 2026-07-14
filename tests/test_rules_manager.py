"""规则管理器单元测试 — CRUD、校验、干跑测试（适配单文件 rules.yaml）"""

from pathlib import Path

import pytest
import yaml

from sentinelmind.rules.manager import RuleManager


# ─── 辅助函数 ──────────────────────────────────────────────

def _valid_config(name: str = "test_rule", **overrides) -> dict:
    config = {
        "name": name,
        "conditions": [{"type": "object_in_zone", "params": {
            "zone": [[0, 0], [100, 0], [100, 100], [0, 100]],
            "target_classes": ["person"],
        }}],
        "severity": "warning",
        "cooldown": 300,
        "window_size": 5,
        "camera_ids": ["cam-1"],
        "actions": [{"type": "notify"}],
        "enabled": True,
        "description": "测试规则",
    }
    config.update(overrides)
    return config


def _write_rules_yaml(path: Path, rules: list[dict]) -> None:
    data = {"rule_items": rules}
    path.write_text(yaml.dump(data, allow_unicode=True, default_flow_style=False, sort_keys=False), encoding="utf-8")


# ─── Fixtures ──────────────────────────────────────────────

@pytest.fixture
def rules_file(tmp_path):
    return str(tmp_path / "rules.yaml")


@pytest.fixture
def manager(rules_file):
    return RuleManager(rules_file=rules_file)


# ─── 列出规则 ─────────────────────────────────────────────

class TestListRulesFromTemplates:

    @pytest.fixture
    def template_file(self, tmp_path):
        """创建包含三条模板规则的 rules.yaml"""
        path = tmp_path / "rules.yaml"
        _write_rules_yaml(path, [
            {
                "name": "example_zone_intrusion",
                "description": "区域闯入检测模板",
                "conditions": [{"type": "object_in_zone", "params": {
                    "zone": [[100, 200], [300, 200], [300, 400], [100, 400]],
                    "target_classes": ["person"],
                }}],
                "camera_ids": ["cam_01"], "severity": "warning",
                "cooldown": 300, "window_size": 5,
                "actions": [{"type": "record_clip"}, {"type": "llm_analyze"}, {"type": "notify"}],
                "enabled": False,
            },
            {
                "name": "example_count_line",
                "description": "计数线模板",
                "conditions": [{"type": "count_line", "params": {
                    "line_start": [0, 100], "line_end": [200, 100],
                }}],
                "camera_ids": None, "severity": "info",
                "actions": [{"type": "notify"}],
                "enabled": True,
            },
            {
                "name": "example_zone_empty",
                "description": "区域清空模板",
                "conditions": [{"type": "zone_empty", "params": {
                    "zone": [[0, 0], [100, 0], [100, 100], [0, 100]],
                }}],
                "camera_ids": ["cam_02"], "severity": "critical",
                "actions": [], "enabled": True,
            },
        ])
        return str(path)

    def test_list_rules_from_templates(self, template_file):
        mgr = RuleManager(rules_file=template_file)
        rules = mgr.list_rules()
        assert len(rules) == 3
        names = {r["name"] for r in rules}
        assert "example_zone_intrusion" in names
        assert "example_count_line" in names
        assert "example_zone_empty" in names

    def test_list_rules_summary_fields(self, template_file):
        mgr = RuleManager(rules_file=template_file)
        rules = mgr.list_rules()
        r = rules[0]
        for key in ("name", "type", "severity", "enabled", "actions"):
            assert key in r, f"Missing field: {key}"


class TestListRulesEmpty:
    def test_list_empty_dir(self, manager):
        rules = manager.list_rules()
        assert rules == []


# ─── 创建 ─────────────────────────────────────────────────

class TestCreateRule:
    def test_create_rule_file_exists(self, manager, rules_file):
        manager.create_rule("zone_check", _valid_config("zone_check"))
        assert Path(rules_file).exists()
        data = yaml.safe_load(Path(rules_file).read_text(encoding="utf-8"))
        assert len(data["rule_items"]) == 1
        assert data["rule_items"][0]["name"] == "zone_check"

    def test_create_rule_content_correct(self, manager, rules_file):
        manager.create_rule("zone_check", _valid_config("zone_check"))
        data = yaml.safe_load(Path(rules_file).read_text(encoding="utf-8"))
        rule = data["rule_items"][0]
        assert rule["conditions"][0]["type"] == "object_in_zone"

    def test_create_rule_returns_path(self, manager):
        path = manager.create_rule("r1", _valid_config("r1"))
        assert path.endswith("rules.yaml")

    def test_create_rule_sanitizes_filename(self, manager, rules_file):
        manager.create_rule("test-rule", _valid_config("test-rule"))
        data = yaml.safe_load(Path(rules_file).read_text(encoding="utf-8"))
        assert data["rule_items"][0]["name"] == "test-rule"


# ─── 更新 ─────────────────────────────────────────────────

class TestUpdateRule:
    def test_update_rule_changes_content(self, manager, rules_file):
        manager.create_rule("r1", _valid_config("r1", cooldown=300))
        manager.update_rule("r1", _valid_config("r1", cooldown=999))
        detail = manager.get_rule("r1")
        assert detail["config"]["cooldown"] == 999

    def test_update_rule_overwrites(self, manager, rules_file):
        manager.create_rule("r1", _valid_config("r1", camera_ids=["cam-1"]))
        manager.update_rule("r1", _valid_config("r1", camera_ids=["cam-2", "cam-3"]))
        detail = manager.get_rule("r1")
        assert detail["config"]["camera_ids"] == ["cam-2", "cam-3"]

    def test_update_rule_returns_path(self, manager):
        manager.create_rule("r1", _valid_config("r1"))
        path = manager.update_rule("r1", _valid_config("r1", cooldown=500))
        assert path.endswith("rules.yaml")


# ─── 删除 ─────────────────────────────────────────────────

class TestDeleteRule:
    def test_delete_rule_removes_file(self, manager, rules_file):
        manager.create_rule("r1", _valid_config("r1"))
        manager.create_rule("r2", _valid_config("r2"))
        manager.delete_rule("r1")
        data = yaml.safe_load(Path(rules_file).read_text(encoding="utf-8"))
        names = [r["name"] for r in data["rule_items"]]
        assert "r1" not in names
        assert "r2" in names

    def test_delete_nonexistent_returns_false(self, manager):
        assert manager.delete_rule("ghost") is False


# ─── 查询 ─────────────────────────────────────────────────

class TestGetRule:
    def test_get_rule_returns_full_config(self, manager):
        manager.create_rule("r1", _valid_config("r1"))
        detail = manager.get_rule("r1")
        assert detail["config"]["name"] == "r1"
        assert "yaml_raw" in detail

    def test_get_rule_not_found(self, manager):
        assert manager.get_rule("ghost") is None

    def test_list_rules_filters_correctly(self, manager):
        manager.create_rule("r1", _valid_config("r1"))
        manager.create_rule("r2", _valid_config("r2"))
        rules = manager.list_rules()
        assert len(rules) == 2


# ─── 重复 ─────────────────────────────────────────────────

class TestCreateDuplicate:
    def test_create_duplicate_raises(self, manager):
        manager.create_rule("dup", _valid_config("dup"))
        with pytest.raises((FileExistsError, ValueError)):
            manager.create_rule("dup", _valid_config("dup"))


# ─── 校验 ─────────────────────────────────────────────────

class TestValidateEmptyName:
    def test_empty_name_raises(self, manager):
        with pytest.raises(ValueError, match="name"):
            manager.create_rule("", _valid_config(""))

    def test_whitespace_name_raises(self, manager):
        with pytest.raises(ValueError, match="name"):
            manager.create_rule("   ", _valid_config("   "))

    def test_test_rule_rejects_empty_name_in_config(self, manager):
        result = manager.test_rule(_valid_config(""))
        assert result["valid"] is False


class TestValidateInvalidType:
    def test_invalid_condition_type_raises(self, manager):
        with pytest.raises(ValueError, match="type"):
            manager.create_rule("bad", _valid_config("bad", conditions=[{"type": "unknown", "params": {}}]))


class TestValidateInvalidSeverity:
    def test_invalid_severity_raises(self, manager):
        with pytest.raises(ValueError, match="severity"):
            manager.create_rule("bad", _valid_config("bad", severity="super_critical"))

    def test_valid_severities_are_accepted(self, manager):
        for sev in ("critical", "warning", "info"):
            manager.create_rule(f"rule_{sev}", _valid_config(f"rule_{sev}", severity=sev))
        rules = manager.list_rules()
        assert len(rules) == 3


class TestValidateBoundary:
    def test_invalid_action_type_raises(self, manager):
        with pytest.raises(ValueError, match="actions"):
            manager.create_rule("bad", _valid_config("bad", actions=[{"type": "sms"}]))

    def test_negative_cooldown_raises(self, manager):
        with pytest.raises(ValueError, match="cooldown"):
            manager.create_rule("bad", _valid_config("bad", cooldown=-1))

    def test_zero_window_size_raises(self, manager):
        with pytest.raises(ValueError, match="window_size"):
            manager.create_rule("bad", _valid_config("bad", window_size=0))

    def test_missing_conditions_raises(self, manager):
        with pytest.raises(ValueError, match="conditions"):
            manager.create_rule("bad", _valid_config("bad", conditions=[]))


# ─── 干跑 ─────────────────────────────────────────────────

class TestTestRuleValid:
    def test_test_rule_valid_object_in_zone(self, manager):
        result = manager.test_rule(_valid_config("t1"))
        assert result["valid"] is True
        assert result["rule_type"] == "object_in_zone"

    def test_test_rule_valid_count_line(self, manager):
        result = manager.test_rule(_valid_config("t2", conditions=[{
            "type": "count_line",
            "params": {"line_start": [0, 0], "line_end": [100, 100]},
        }]))
        assert result["valid"] is True
        assert result["rule_type"] == "count_line"

    def test_test_rule_valid_zone_empty(self, manager):
        result = manager.test_rule(_valid_config("t3", conditions=[{
            "type": "zone_empty",
            "params": {"zone": [[0, 0], [100, 0], [100, 100], [0, 100]]},
        }]))
        assert result["valid"] is True
        assert result["rule_type"] == "zone_empty"

    def test_test_rule_returns_actions_list(self, manager):
        result = manager.test_rule(_valid_config("t4", actions=[{"type": "notify"}, {"type": "llm_analyze"}]))
        assert result["actions"] == ["notify", "llm_analyze"]


class TestTestRuleInvalid:
    def test_test_rule_invalid_type(self, manager):
        result = manager.test_rule(_valid_config("bad", conditions=[{"type": "unknown", "params": {}}]))
        assert result["valid"] is False

    def test_test_rule_missing_name(self, manager):
        result = manager.test_rule(_valid_config(""))
        assert result["valid"] is False

    def test_test_rule_zone_too_few_vertices(self, manager):
        result = manager.test_rule(_valid_config("bad", conditions=[{
            "type": "object_in_zone",
            "params": {"zone": [[0, 0], [100, 100]]},
        }]))
        assert result["valid"] is False

    def test_test_rule_count_line_missing_params(self, manager):
        result = manager.test_rule(_valid_config("bad", conditions=[{
            "type": "count_line",
            "params": {},
        }]))
        assert result["valid"] is False
