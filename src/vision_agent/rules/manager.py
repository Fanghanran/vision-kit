"""
规则文件管理器 — YAML 文件的读写删操作

职责：
- 读写 configs/rules/ 目录下的 YAML 规则文件
- 规则名称 → 文件名映射
- 保存前校验规则配置
- 与 RuleEngine 的热重载协同（只写文件，不操作引擎）

设计来源：docs/modules/rules/rule_management.md
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

_FILENAME_RE = re.compile(r"[^\w\-]")

# 合法规则类型
_VALID_TYPES = {"object_in_zone", "count_line", "zone_empty"}
# 合法严重级别
_VALID_SEVERITY = {"critical", "warning", "info"}
# 合法动作类型
_VALID_ACTIONS = {"notify", "record_clip", "llm_analyze"}


class RuleManager:
    """规则文件管理器

    与 RuleEngine 的分工：
    - RuleManager：文件 CRUD + 校验（本模块）
    - RuleEngine：规则加载 + 评估 + 热重载（engine.py，不动）
    """

    def __init__(self, rules_dir: str = "configs/rules") -> None:
        self._rules_dir = Path(rules_dir)
        self._rules_dir.mkdir(parents=True, exist_ok=True)
        self._name_index: dict[str, Path] = {}  # name → filepath，O(1) 查找
        self._rebuild_index()

    # ─── 查询 ────────────────────────────────────────────────

    def list_rules(self) -> list[dict[str, Any]]:
        """列出所有规则文件的内容摘要"""
        rules: list[dict[str, Any]] = []
        for yaml_file in sorted(
            list(self._rules_dir.glob("*.yaml")) + list(self._rules_dir.glob("*.yml"))
        ):
            config = self._read_yaml(yaml_file)
            if config:
                rules.append(self._summarize(yaml_file.stem, config))
        return rules

    def get_rule(self, name: str) -> dict[str, Any] | None:
        """获取单条规则完整配置（含 YAML 原文）"""
        filepath = self._name_to_path(name)
        if not filepath:
            return None
        config = self._read_yaml(filepath)
        if not config:
            return None
        raw = ""
        try:
            raw = filepath.read_text(encoding="utf-8")
        except OSError:
            pass
        return {
            "filename": filepath.name,
            "filepath": str(filepath),
            "config": config,
            "yaml_raw": raw,
        }

    # ─── 写入 ────────────────────────────────────────────────

    def create_rule(self, name: str, config: dict[str, Any]) -> str:
        """创建新规则 → 写 YAML 文件 → 返回文件路径

        Raises:
            ValueError: 配置校验失败
            FileExistsError: 同名文件已存在
        """
        config["name"] = name
        self._validate(config)

        filename = _FILENAME_RE.sub("_", name) + ".yaml"
        filepath = self._rules_dir / filename

        if filepath.exists():
            raise FileExistsError(f"规则文件已存在: {filename}")

        self._write_yaml(filepath, config)
        self._name_index[name] = filepath
        logger.info("rule_created name=%s file=%s", name, filename)
        return str(filepath)

    def update_rule(self, name: str, config: dict[str, Any]) -> str:
        """更新规则 → 覆盖写 YAML 文件

        Raises:
            ValueError: 配置校验失败
            FileNotFoundError: 规则不存在
        """
        config["name"] = name

        filepath = self._name_to_path(name)
        if not filepath:
            raise FileNotFoundError(f"规则不存在: {name}")

        self._validate(config)
        self._write_yaml(filepath, config)
        logger.info("rule_updated name=%s file=%s", name, filepath.name)
        return str(filepath)

    def delete_rule(self, name: str) -> bool:
        """删除规则 → 删除 YAML 文件，返回是否成功"""
        filepath = self._name_to_path(name)
        if not filepath:
            return False
        try:
            filepath.unlink()
        except OSError:
            return False
        self._name_index.pop(name, None)
        logger.info("rule_deleted name=%s file=%s", name, filepath.name)
        return True

    # ─── 测试（干跑）────────────────────────────────────────────

    def test_rule(self, config: dict[str, Any]) -> dict[str, Any]:
        """测试规则：校验 + 尝试实例化 + 干跑（不写文件）

        返回：
        - valid: bool
        - errors: list[str]
        - rule_type: str
        - params_summary: 参数摘要
        """
        result: dict[str, Any] = {"valid": True, "errors": [], "rule_type": "", "params": {}}

        _name = config.get("name", "(未命名)")

        # 1. 校验
        try:
            self._validate(config)
        except ValueError as e:
            result["valid"] = False
            result["errors"] = str(e).split("; ")
            return result

        # 2. 提取规则类型和参数
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
                # 跳过后续条件校验，但继续校验其他字段
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
            errors.append(
                f"severity 无效: '{severity}'，可选: {', '.join(sorted(_VALID_SEVERITY))}"
            )

        cooldown = config.get("cooldown")
        if cooldown is not None:
            if not isinstance(cooldown, int) or cooldown < 0:
                errors.append("cooldown 必须是非负整数")

        window_size = config.get("window_size")
        if window_size is not None:
            if not isinstance(window_size, int) or window_size < 1:
                errors.append("window_size 必须是正整数")

        actions = config.get("actions", [])
        for a in actions:
            atype = a.get("type", "")
            if atype not in _VALID_ACTIONS:
                errors.append(
                    f"actions.type 无效: '{atype}'，可选: {', '.join(sorted(_VALID_ACTIONS))}"
                )

        enabled = config.get("enabled", True)
        if not isinstance(enabled, bool):
            errors.append("enabled 必须是布尔值 (true/false)")

        if errors:
            raise ValueError("; ".join(errors))

    # ─── 内部 ────────────────────────────────────────────────

    def _name_to_path(self, name: str) -> Path | None:
        """通过规则名称查找对应的 YAML 文件（O(1) 字典查找）"""
        # 先查内存索引
        filepath = self._name_index.get(name)
        if filepath and filepath.exists():
            return filepath
        # 索引过期：重建后重试一次
        self._rebuild_index()
        return self._name_index.get(name)

    def _rebuild_index(self) -> None:
        """重建 name→filepath 索引"""
        self._name_index.clear()
        for yaml_file in list(self._rules_dir.glob("*.yaml")) + list(
            self._rules_dir.glob("*.yml")
        ):
            config = self._read_yaml(yaml_file)
            if config and config.get("name"):
                self._name_index[config["name"]] = yaml_file

    def _read_yaml(self, filepath: Path) -> dict[str, Any] | None:
        if yaml is None:
            logger.error("pyyaml_not_installed")
            return None
        try:
            with open(filepath, encoding="utf-8") as f:
                return yaml.safe_load(f)
        except Exception as e:
            logger.warning("yaml_read_error file=%s error=%s", filepath, e)
            return None

    def _write_yaml(self, filepath: Path, config: dict[str, Any]) -> None:
        """写 YAML 文件，保持可读格式（原子写入，防热重载竞态）"""
        if yaml is None:
            raise RuntimeError("PyYAML not installed")
        import os as _os

        output = yaml.dump(
            config,
            allow_unicode=True,
            default_flow_style=False,
            sort_keys=False,
        )
        # 原子写入：先写临时文件，再 os.replace（跨平台原子 rename）
        # 消除引擎热重载扫描到半写文件的竞态窗口
        tmp_path = filepath.with_suffix(filepath.suffix + ".tmp")
        tmp_path.write_text(output, encoding="utf-8")
        _os.replace(str(tmp_path), str(filepath))

    def _summarize(self, filename: str, config: dict[str, Any]) -> dict[str, Any]:
        """从 config 中提取摘要信息"""
        conditions = config.get("conditions", [])
        first_cond = conditions[0] if conditions else None
        cond_type = first_cond.get("type", "") if isinstance(first_cond, dict) else ""
        return {
            "name": config.get("name", filename),
            "filename": filename,
            "type": cond_type,
            "camera_ids": config.get("camera_ids"),
            "severity": config.get("severity", "warning"),
            "cooldown": config.get("cooldown"),
            "window_size": config.get("window_size"),
            "enabled": config.get("enabled", True),
            "actions": [a.get("type", "") for a in config.get("actions", [])],
            "description": config.get("description", ""),
        }
