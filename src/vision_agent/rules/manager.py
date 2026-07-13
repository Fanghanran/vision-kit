"""
规则文件管理器 — 读写 configs/rules.yaml

职责：
- 读写 configs/rules.yaml（单文件，所有规则集中管理）
- 规则名称 → 规则索引映射
- 保存前校验规则配置
- 与 RuleEngine 的热重载协同（只写文件，不操作引擎）

设计来源：docs/modules/rules/rule_management.md
"""

from __future__ import annotations

import logging
import os as _os
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

# 合法规则类型
_VALID_TYPES = {"object_in_zone", "count_line", "zone_empty"}
# 合法严重级别
_VALID_SEVERITY = {"critical", "warning", "info"}
# 合法动作类型
_VALID_ACTIONS = {"notify", "record_clip", "llm_analyze"}


class RuleManager:
    """规则文件管理器 — 读写 configs/rules.yaml"""

    def __init__(self, rules_file: str = "configs/rules.yaml") -> None:
        self._rules_file = Path(rules_file)
        self._rules_file.parent.mkdir(parents=True, exist_ok=True)

    # ─── 查询 ────────────────────────────────────────────────

    def list_rules(self) -> list[dict[str, Any]]:
        """列出所有规则的摘要信息"""
        data = self._read_rules_file()
        rule_list = data.get("rule_items", []) if isinstance(data, dict) else []
        return [
            self._summarize(rule if isinstance(rule, dict) else {})
            for rule in rule_list
        ]

    def get_rule(self, name: str) -> dict[str, Any] | None:
        """获取单条规则完整配置（含 YAML 原文）"""
        data = self._read_rules_file()
        rule_list = data.get("rule_items", []) if isinstance(data, dict) else []

        for i, rule in enumerate(rule_list):
            if isinstance(rule, dict) and rule.get("name") == name:
                raw = ""
                try:
                    raw = self._rules_file.read_text(encoding="utf-8")
                except OSError:
                    pass
                return {
                    "filename": self._rules_file.name,
                    "filepath": str(self._rules_file),
                    "config": rule,
                    "yaml_raw": raw,
                }
        return None

    # ─── 写入 ────────────────────────────────────────────────

    def create_rule(self, name: str, config: dict[str, Any]) -> str:
        """创建新规则 → 写 rules.yaml → 返回文件路径

        Raises:
            ValueError: 配置校验失败
            FileExistsError: 同名规则已存在
        """
        config["name"] = name
        self._validate(config)

        data = self._read_rules_file()
        rule_list = data.get("rule_items", []) if isinstance(data, dict) else []

        # 检查重名
        for rule in rule_list:
            if isinstance(rule, dict) and rule.get("name") == name:
                raise FileExistsError(f"同名规则已存在: {name}")

        rule_list.append(config)
        self._write_rules_file(data)
        logger.info("rule_created name=%s", name)
        return str(self._rules_file)

    def update_rule(self, name: str, config: dict[str, Any]) -> str:
        """更新规则 → 覆盖写 rules.yaml

        Raises:
            ValueError: 配置校验失败
            FileNotFoundError: 规则不存在
        """
        config["name"] = name
        self._validate(config)

        data = self._read_rules_file()
        rule_list = data.get("rule_items", []) if isinstance(data, dict) else []

        found = False
        for i, rule in enumerate(rule_list):
            if isinstance(rule, dict) and rule.get("name") == name:
                rule_list[i] = config
                found = True
                break

        if not found:
            raise FileNotFoundError(f"规则不存在: {name}")

        self._write_rules_file(data)
        logger.info("rule_updated name=%s", name)
        return str(self._rules_file)

    def delete_rule(self, name: str) -> bool:
        """删除规则 → 从 rules.yaml 移除"""
        data = self._read_rules_file()
        rule_list = data.get("rule_items", []) if isinstance(data, dict) else []

        new_list = [r for r in rule_list if not (isinstance(r, dict) and r.get("name") == name)]
        if len(new_list) == len(rule_list):
            return False

        data["rule_items"] = new_list
        self._write_rules_file(data)
        logger.info("rule_deleted name=%s", name)
        return True

    # ─── 测试 ────────────────────────────────────────────────

    def test_rule(self, config: dict[str, Any]) -> dict[str, Any]:
        """测试规则：校验 + 参数解析（不写文件）"""
        result: dict[str, Any] = {"valid": True, "errors": [], "rule_type": "", "params": {}}

        _name = config.get("name", "(未命名)")

        try:
            self._validate(config)
        except ValueError as e:
            result["valid"] = False
            result["errors"] = str(e).split("; ")
            return result

        conditions = config.get("conditions", [])
        cond = conditions[0] if conditions else {}
        cond_type = cond.get("type", "")
        params = cond.get("params", {})

        result["rule_type"] = cond_type

        if cond_type == "object_in_zone":
            zone = params.get("zone", [])
            result["params"] = {
                "zone_vertices": len(zone),
                "target_classes": params.get("target_classes"),
                "camera_ids": config.get("camera_ids"),
                "severity": config.get("severity", "warning"),
                "cooldown": config.get("cooldown", 300),
                "window_size": config.get("window_size", 5),
            }
        elif cond_type == "count_line":
            result["params"] = {
                "line_start": params.get("line_start"),
                "line_end": params.get("line_end"),
                "threshold": params.get("threshold", 1),
                "camera_ids": config.get("camera_ids"),
            }
        elif cond_type == "zone_empty":
            zone = params.get("zone", [])
            result["params"] = {
                "zone_vertices": len(zone),
                "target_classes": params.get("target_classes"),
                "camera_ids": config.get("camera_ids"),
            }

        actions = [a.get("type", "") for a in config.get("actions", [])]
        result["actions"] = actions
        return result

    # ─── 校验 ────────────────────────────────────────────────

    def _validate(self, config: dict[str, Any]) -> None:
        """校验规则配置，不合法抛 ValueError"""
        errors: list[str] = []

        name = config.get("name", "")
        if not name or not name.strip():
            errors.append("name 不能为空")
        if len(name) > 64:
            errors.append("name 不能超过 64 个字符")

        conditions = config.get("conditions", [])
        if not conditions:
            errors.append("conditions 不能为空，至少需要一个条件")
        else:
            cond = conditions[0]
            if not isinstance(cond, dict):
                errors.append("conditions[0] 必须是字典对象")
            else:
                cond_type = cond.get("type", "")
                if cond_type not in _VALID_TYPES:
                    errors.append(
                        f"type 无效: '{cond_type}'，可选: {', '.join(sorted(_VALID_TYPES))}"
                    )
                params = cond.get("params", {})
                if cond_type in ("object_in_zone", "zone_empty"):
                    zone = params.get("zone", [])
                    if not zone or len(zone) < 3:
                        errors.append("zone 至少需要 3 个顶点坐标（多边形）")
                if cond_type == "count_line":
                    if "line_start" not in params or "line_end" not in params:
                        errors.append("count_line 需要 line_start 和 line_end")

        severity = config.get("severity", "warning")
        if severity not in _VALID_SEVERITY:
            errors.append(f"severity 无效: '{severity}'，可选: {', '.join(sorted(_VALID_SEVERITY))}")

        cooldown = config.get("cooldown")
        if cooldown is not None and (not isinstance(cooldown, int) or cooldown < 0):
            errors.append("cooldown 必须是非负整数")

        window_size = config.get("window_size")
        if window_size is not None and (not isinstance(window_size, int) or window_size < 1):
            errors.append("window_size 必须是正整数")

        actions = config.get("actions", [])
        for a in actions:
            atype = a.get("type", "")
            if atype not in _VALID_ACTIONS:
                errors.append(f"actions.type 无效: '{atype}'，可选: {', '.join(sorted(_VALID_ACTIONS))}")

        enabled = config.get("enabled", True)
        if not isinstance(enabled, bool):
            errors.append("enabled 必须是布尔值 (true/false)")

        if errors:
            raise ValueError("; ".join(errors))

    # ─── 内部 ────────────────────────────────────────────────

    def _read_rules_file(self) -> dict[str, Any]:
        if yaml is None:
            return {"rule_items": []}
        if not self._rules_file.exists():
            return {"rule_items": []}
        try:
            data = yaml.safe_load(self._rules_file.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {"rules": []}
        except Exception as e:
            logger.warning("rules_file_read_error error=%s", e)
            return {"rule_items": []}

    def _write_rules_file(self, data: dict[str, Any]) -> None:
        """原子写入 rules.yaml"""
        if yaml is None:
            raise RuntimeError("PyYAML not installed")
        output = yaml.dump(data, allow_unicode=True, default_flow_style=False, sort_keys=False)
        tmp_path = self._rules_file.with_suffix(".tmp")
        tmp_path.write_text(output, encoding="utf-8")
        _os.replace(str(tmp_path), str(self._rules_file))

    def _summarize(self, config: dict[str, Any]) -> dict[str, Any]:
        conditions = config.get("conditions", [])
        first_cond = conditions[0] if conditions else None
        cond_type = first_cond.get("type", "") if isinstance(first_cond, dict) else ""
        return {
            "name": config.get("name", ""),
            "type": cond_type,
            "camera_ids": config.get("camera_ids"),
            "severity": config.get("severity", "warning"),
            "cooldown": config.get("cooldown"),
            "window_size": config.get("window_size"),
            "enabled": config.get("enabled", True),
            "actions": [a.get("type", "") for a in config.get("actions", [])],
            "description": config.get("description", ""),
        }
