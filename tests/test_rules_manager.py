"""规则管理器单元测试 — CRUD、校验、干跑测试"""

from pathlib import Path

import pytest
import yaml

from vision_agent.rules.manager import RuleManager


# ─── 辅助函数 ──────────────────────────────────────────────

def _valid_config(name: str = "test_rule", **overrides) -> dict:
    """构造合法的规则配置"""
    config = {
        "name": name,
        "conditions": [
            {
                "type": "object_in_zone",
                "params": {
                    "zone": [[0, 0], [100, 0], [100, 100], [0, 100]],
                    "target_classes": ["person"],
                },
            }
        ],
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


def _write_yaml(path: Path, config: dict) -> None:
    """写入 YAML 文件到指定路径"""
    content = yaml.dump(config, allow_unicode=True, default_flow_style=False, sort_keys=False)
    path.write_text(content, encoding="utf-8")


# ─── Fixtures ──────────────────────────────────────────────

@pytest.fixture
def rules_dir(tmp_path):
    """使用临时目录作为规则目录"""
    d = tmp_path / "rules"
    d.mkdir()
    return str(d)


@pytest.fixture
def manager(rules_dir):
    """创建 RuleManager 实例"""
    return RuleManager(rules_dir=rules_dir)


# ─── 列出规则 ─────────────────────────────────────────────

class TestListRulesFromTemplates:
    """列出模板规则（预填充 YAML 文件）"""

    @pytest.fixture
    def template_dir(self, tmp_path):
        """创建包含模板文件的临时目录"""
        d = tmp_path / "rules_with_templates"
        d.mkdir()
        # 写入三条模板规则
        _write_yaml(d / "example_zone_intrusion.yaml", {
            "name": "example_zone_intrusion",
            "description": "区域闯入检测模板",
            "conditions": [{"type": "object_in_zone", "params": {
                "zone": [[100, 200], [300, 200], [300, 400], [100, 400]],
                "target_classes": ["person"],
            }}],
            "camera_ids": ["cam_01"],
            "severity": "warning",
            "cooldown": 300,
            "window_size": 5,
            "actions": [{"type": "record_clip"}, {"type": "llm_analyze"}, {"type": "notify"}],
            "enabled": False,
        })
        _write_yaml(d / "example_count_line.yaml", {
            "name": "example_count_line",
            "description": "计数线模板",
            "conditions": [{"type": "count_line", "params": {
                "line_start": [0, 100],
                "line_end": [200, 100],
            }}],
            "camera_ids": None,
            "severity": "info",
            "actions": [{"type": "notify"}],
            "enabled": True,
        })
        _write_yaml(d / "example_zone_empty.yaml", {
            "name": "example_zone_empty",
            "description": "区域清空模板",
            "conditions": [{"type": "zone_empty", "params": {
                "zone": [[0, 0], [100, 0], [100, 100], [0, 100]],
            }}],
            "camera_ids": ["cam_02"],
            "severity": "critical",
            "actions": [],
            "enabled": True,
        })
        return str(d)

    def test_list_rules_from_templates(self, template_dir):
        """列出模板目录中的所有规则"""
        mgr = RuleManager(rules_dir=template_dir)
        rules = mgr.list_rules()
        assert len(rules) == 3
        names = {r["name"] for r in rules}
        assert "example_zone_intrusion" in names
        assert "example_count_line" in names
        assert "example_zone_empty" in names

    def test_list_rules_summary_fields(self, template_dir):
        """列表返回的摘要字段完整"""
        mgr = RuleManager(rules_dir=template_dir)
        rules = mgr.list_rules()
        rule = next(r for r in rules if r["name"] == "example_zone_intrusion")
        assert rule["type"] == "object_in_zone"
        assert rule["severity"] == "warning"
        assert rule["cooldown"] == 300
        assert rule["window_size"] == 5
        assert "record_clip" in rule["actions"]


class TestListRulesEmpty:
    def test_list_empty_dir(self, manager):
        """空目录返回空列表"""
        rules = manager.list_rules()
        assert rules == []


# ─── 创建规则 ─────────────────────────────────────────────

class TestCreateRule:
    def test_create_rule_file_exists(self, manager, rules_dir):
        """创建规则 → 文件存在"""
        manager.create_rule("my_rule", _valid_config("my_rule"))
        filepath = Path(rules_dir) / "my_rule.yaml"
        assert filepath.exists()
        assert filepath.is_file()

    def test_create_rule_content_correct(self, manager, rules_dir):
        """创建规则 → 读取文件内容正确"""
        manager.create_rule("zone_check", _valid_config(
            "zone_check",
            severity="critical",
            conditions=[{
                "type": "count_line",
                "params": {"line_start": [0, 0], "line_end": [100, 100], "threshold": 5},
            }],
        ))
        filepath = Path(rules_dir) / "zone_check.yaml"
        content = yaml.safe_load(filepath.read_text(encoding="utf-8"))
        assert content["name"] == "zone_check"
        assert content["severity"] == "critical"
        assert content["conditions"][0]["type"] == "count_line"

    def test_create_rule_returns_path(self, manager):
        """create_rule 返回创建的文件路径"""
        path = manager.create_rule("return_test", _valid_config("return_test"))
        assert path.endswith("return_test.yaml")

    def test_create_rule_sanitizes_filename(self, manager, rules_dir):
        """特殊字符在文件名中被替换为下划线"""
        manager.create_rule("my rule/with:chars", _valid_config(name="my rule/with:chars"))
        # 文件名中不应出现特殊字符
        files = list(Path(rules_dir).glob("*.yaml"))
        assert len(files) == 1
        assert " " not in files[0].name


# ─── 更新规则 ─────────────────────────────────────────────

class TestUpdateRule:
    def test_update_rule_changes_content(self, manager, rules_dir):
        """创建 → 更新 → 校验变化"""
        name = "update_me"
        manager.create_rule(name, _valid_config(name, severity="warning"))

        # 更新严重级别
        manager.update_rule(name, _valid_config(name, severity="critical"))
        filepath = Path(rules_dir) / "update_me.yaml"
        content = yaml.safe_load(filepath.read_text(encoding="utf-8"))
        assert content["severity"] == "critical"

    def test_update_rule_returns_path(self, manager):
        """update_rule 返回文件路径"""
        manager.create_rule("update_path", _valid_config("update_path"))
        path = manager.update_rule("update_path", _valid_config("update_path", cooldown=60))
        assert path.endswith("update_path.yaml")

    def test_update_rule_overwrites(self, manager, rules_dir):
        """更新后文件只有新内容，旧字段被覆盖"""
        name = "overwrite_test"
        manager.create_rule(name, _valid_config(name, description="旧描述"))
        manager.update_rule(name, _valid_config(name, description="新描述"))
        filepath = Path(rules_dir) / "overwrite_test.yaml"
        content = yaml.safe_load(filepath.read_text(encoding="utf-8"))
        assert content["description"] == "新描述"


# ─── 删除规则 ─────────────────────────────────────────────

class TestDeleteRule:
    def test_delete_rule_removes_file(self, manager, rules_dir):
        """创建 → 删除 → 确认文件不存在"""
        manager.create_rule("to_delete", _valid_config("to_delete"))
        filepath = Path(rules_dir) / "to_delete.yaml"
        assert filepath.exists()

        result = manager.delete_rule("to_delete")
        assert result is True
        assert not filepath.exists()

    def test_delete_nonexistent_returns_false(self, manager):
        """删除不存在的规则返回 False"""
        result = manager.delete_rule("no_such_rule")
        assert result is False


# ─── 查询规则 ─────────────────────────────────────────────

class TestGetRule:
    def test_get_rule_returns_full_config(self, manager):
        """查询已创建的规则返回完整配置"""
        cfg = _valid_config("full_rule")
        manager.create_rule("full_rule", cfg)
        rule = manager.get_rule("full_rule")
        assert rule is not None
        assert rule["config"]["name"] == "full_rule"
        assert "yaml_raw" in rule
        assert "filepath" in rule
        assert "filename" in rule

    def test_get_rule_not_found(self, manager):
        """查询不存在的规则返回 None"""
        rule = manager.get_rule("no_such_rule")
        assert rule is None

    def test_list_rules_filters_correctly(self, manager):
        """列表仅返回存在的规则"""
        manager.create_rule("rule_a", _valid_config("rule_a"))
        manager.create_rule("rule_b", _valid_config("rule_b"))
        rules = manager.list_rules()
        assert len(rules) == 2


# ─── 重复检查 ─────────────────────────────────────────────

class TestCreateDuplicate:
    def test_create_duplicate_raises(self, manager):
        """创建重名规则应抛 FileExistsError"""
        manager.create_rule("dup", _valid_config("dup"))
        with pytest.raises(FileExistsError):
            manager.create_rule("dup", _valid_config("dup"))

    def test_create_duplicate_case_sensitive(self, manager):
        """同名但大小写不同的文件名，由 sanitize 统一处理"""
        manager.create_rule("MyRule", _valid_config(name="MyRule"))
        # "MyRule" 和 "MyRule" 完全相同，重名应抛错
        with pytest.raises(FileExistsError):
            manager.create_rule("MyRule", _valid_config(name="MyRule"))


# ─── 校验：空名称 ─────────────────────────────────────────

class TestValidateEmptyName:
    def test_empty_name_raises(self, manager):
        """空字符串名称应抛 ValueError"""
        with pytest.raises(ValueError) as exc:
            manager.create_rule("", _valid_config(name=""))
        assert "name" in str(exc.value)

    def test_whitespace_name_raises(self, manager):
        """纯空白名称应抛 ValueError"""
        with pytest.raises(ValueError) as exc:
            manager.create_rule("   ", _valid_config(name="   "))
        assert "name" in str(exc.value)

    def test_test_rule_rejects_empty_name_in_config(self, manager):
        """test_rule 对 config 中 name 为空也返回 invalid"""
        result = manager.test_rule(_valid_config(name=""))
        assert result["valid"] is False
        assert any("name" in e.lower() for e in result["errors"])


# ─── 校验：非法类型 ──────────────────────────────────────

class TestValidateInvalidType:
    def test_invalid_condition_type_raises(self, manager):
        """非法条件类型应抛 ValueError"""
        with pytest.raises(ValueError) as exc:
            manager.create_rule("bad_type", _valid_config("bad_type", conditions=[{
                "type": "invalid_type",
                "params": {},
            }]))
        assert "type" in str(exc.value).lower()


# ─── 校验：非法严重级别 ──────────────────────────────────

class TestValidateInvalidSeverity:
    def test_invalid_severity_raises(self, manager):
        """非法严重级别应抛 ValueError"""
        with pytest.raises(ValueError) as exc:
            manager.create_rule("bad_sev", _valid_config("bad_sev", severity="fatal"))
        assert "severity" in str(exc.value).lower()

    def test_valid_severities_are_accepted(self, manager, rules_dir):
        """合法的严重级别都应被接受"""
        for sev in ("critical", "warning", "info"):
            name = f"sev_{sev}"
            path = manager.create_rule(name, _valid_config(name, severity=sev))
            content = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
            assert content["severity"] == sev


# ─── 校验：其他边界 ──────────────────────────────────────

class TestValidateBoundary:
    def test_invalid_action_type_raises(self, manager):
        """非法动作类型应抛 ValueError"""
        with pytest.raises(ValueError) as exc:
            manager.create_rule("bad_action", _valid_config("bad_action", actions=[
                {"type": "unknown_action"},
            ]))
        assert "actions" in str(exc.value).lower()

    def test_negative_cooldown_raises(self, manager):
        """负冷却时间应抛 ValueError"""
        with pytest.raises(ValueError):
            manager.create_rule("bad_cd", _valid_config("bad_cd", cooldown=-1))

    def test_zero_window_size_raises(self, manager):
        """零窗口大小应抛 ValueError"""
        with pytest.raises(ValueError):
            manager.create_rule("bad_ws", _valid_config("bad_ws", window_size=0))

    def test_missing_conditions_raises(self, manager):
        """缺少 conditions 应抛 ValueError"""
        with pytest.raises(ValueError):
            manager.create_rule("no_cond", {"name": "no_cond"})


# ─── test_rule 干跑校验 ──────────────────────────────────

class TestTestRuleValid:
    def test_test_rule_valid_object_in_zone(self, manager):
        """test_rule 干跑校验 object_in_zone 通过"""
        result = manager.test_rule(_valid_config("dry_run_zone"))
        assert result["valid"] is True
        assert result["rule_type"] == "object_in_zone"
        assert result["params"]["zone_vertices"] == 4
        assert result["actions"] == ["notify"]

    def test_test_rule_valid_count_line(self, manager):
        """test_rule 干跑校验 count_line 通过"""
        result = manager.test_rule(_valid_config("dry_run_line", conditions=[{
            "type": "count_line",
            "params": {"line_start": [0, 0], "line_end": [100, 0], "threshold": 3},
        }]))
        assert result["valid"] is True
        assert result["rule_type"] == "count_line"
        assert result["params"]["threshold"] == 3

    def test_test_rule_valid_zone_empty(self, manager):
        """test_rule 干跑校验 zone_empty 通过"""
        result = manager.test_rule(_valid_config("dry_run_empty", conditions=[{
            "type": "zone_empty",
            "params": {"zone": [[0, 0], [10, 0], [10, 10], [0, 10]]},
        }]))
        assert result["valid"] is True
        assert result["rule_type"] == "zone_empty"
        assert result["params"]["zone_vertices"] == 4

    def test_test_rule_returns_actions_list(self, manager):
        """test_rule 返回动作列表"""
        cfg = _valid_config("multi_action", actions=[
            {"type": "notify"}, {"type": "record_clip"}, {"type": "llm_analyze"},
        ])
        result = manager.test_rule(cfg)
        assert result["valid"] is True
        assert len(result["actions"]) == 3
        assert "llm_analyze" in result["actions"]


class TestTestRuleInvalid:
    def test_test_rule_invalid_type(self, manager):
        """test_rule 干跑校验失败 — 非法类型"""
        result = manager.test_rule(_valid_config("bad", conditions=[{
            "type": "nonexistent",
            "params": {},
        }]))
        assert result["valid"] is False
        assert len(result["errors"]) > 0

    def test_test_rule_missing_name(self, manager):
        """test_rule 干跑校验失败 — 缺少名称"""
        result = manager.test_rule({"conditions": [{"type": "object_in_zone", "params": {
            "zone": [[0, 0], [100, 0], [100, 100]],
        }}]})
        assert result["valid"] is False
        assert any("name" in e.lower() for e in result["errors"])

    def test_test_rule_zone_too_few_vertices(self, manager):
        """test_rule 干跑校验失败 — 多边形顶点不足"""
        result = manager.test_rule(_valid_config("few_v", conditions=[{
            "type": "object_in_zone",
            "params": {"zone": [[0, 0], [100, 0]]},  # 仅 2 个点
        }]))
        assert result["valid"] is False

    def test_test_rule_count_line_missing_params(self, manager):
        """test_rule 干跑校验失败 — count_line 缺少参数"""
        result = manager.test_rule(_valid_config("no_line", conditions=[{
            "type": "count_line",
            "params": {},
        }]))
        assert result["valid"] is False
