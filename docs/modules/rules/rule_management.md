# 规则管理模块设计

> 状态：设计中 | 版本：v1 | 日期：2026-07-09

## 一、现状分析

### 已有的

| 模块 | 位置 | 状态 |
|---|---|---|
| 规则引擎核心 | `rules/engine.py` | ✅ 完整 |
| 规则加载（YAML → 实例） | `RuleEngine.load_rules()` | ✅ |
| 规则评估（每帧运行） | `RuleEngine.evaluate()` | ✅ |
| 三层防线（去重+冷却+时间窗） | `DefenseFilter` | ✅ |
| 热重载（文件 mtime 监控） | `RuleEngine._hot_reload_loop()` | ✅ |
| 内置评估器（3 种类型） | `ObjectInZoneRule` / `CountLineRule` / `ZoneEmptyRule` | ✅ |
| Python 扩展规则 | `_load_python_rule()` 动态 import | ✅ |
| 内部查询 | `list_rules()` / `get_rule()` / `unload_rule()` | ✅ |

### 缺失的

| 模块 | 状态 |
|---|---|
| REST API（增删改查） | ❌ web/api/app.py 无任何 rule 端点 |
| YAML 文件管理器 | ❌ 无程序化写 YAML 的能力 |
| 前端规则管理页面 | ❌ 无 Rules.vue |
| 前端 API 客户端 | ❌ 无 api/rules.ts |
| 规则模板/示例 | ❌ configs/rules/ 目录为空 |
| 规则校验（保存前） | ❌ 无 |

### 当前规则管理方式

```
用户手动编辑 configs/rules/xxx.yaml → 保存文件 → 5秒后热重载自动生效
```

开发者/运维人员需要：
- 记住 YAML 格式
- 手动编辑文件
- 不知道规则是否写错（无校验）
- 无法从前端操作

## 二、设计目标

```
┌─────────────────────────────────────────────────────┐
│                 规则管理方式                           │
│                                                       │
│  方式1：REST API（程序化）                             │
│    GET/POST/PUT/DELETE /api/rules                   │
│    → 自动写 YAML → 自动热加载                         │
│                                                       │
│  方式2：前端页面（可视化）                              │
│    Rules.vue → 表单创建/编辑/删除                     │
│                                                       │
│  方式3：YAML 文件（保留兼容）                           │
│    手动编辑 configs/rules/*.yaml → 热重载自动生效      │
│                                                       │
│  方式4：Agent（未来）                                  │
│    "创建一个区域闯入规则" → Agent 调 REST API          │
└─────────────────────────────────────────────────────┘
```

三种方式共享同一个 YAML 文件存储，互不冲突。

## 三、后端新增

### 3.1 新增文件

```
src/sentinelmind/
├── rules/
│   ├── __init__.py
│   ├── engine.py          # 现有，不动
│   └── manager.py         # ← 新增：规则文件管理器
│
└── web/api/
    ├── __init__.py
    ├── app.py             # 现有，新增 rule 路由挂载
    └── rules.py           # ← 新增：规则 REST API
```

### 3.2 规则文件管理器

```python
# src/sentinelmind/rules/manager.py
"""
规则文件管理器 — YAML 文件的读写删操作

职责：
- 读写 configs/rules/ 目录下的 YAML 规则文件
- 规则名称 → 文件名映射
- 保存前校验规则配置
- 与 RuleEngine 的热重载协同
"""

from pathlib import Path
import yaml
import re
import logging
from typing import Any

logger = logging.getLogger(__name__)

# 规则名称→合法文件名：只保留字母数字下划线连字符
_FILENAME_RE = re.compile(r'[^\w\-]')

class RuleManager:
    """规则文件管理器"""

    def __init__(self, rules_dir: str = "configs/rules"):
        self._rules_dir = Path(rules_dir)
        self._rules_dir.mkdir(parents=True, exist_ok=True)

    # ─── 文件操作 ────────────────────────────────────

    def list_rules(self) -> list[dict[str, Any]]:
        """列出所有规则文件的内容摘要"""
        rules = []
        for yaml_file in sorted(self._rules_dir.glob("*.yaml")):
            config = self._read_yaml(yaml_file)
            if config:
                rules.append(self._summarize(yaml_file.stem, config))
        for yaml_file in sorted(self._rules_dir.glob("*.yml")):
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
        return {
            "filename": filepath.name,
            "filepath": str(filepath),
            "config": config,
            "yaml_raw": filepath.read_text(encoding="utf-8"),
        }

    def create_rule(self, name: str, config: dict[str, Any]) -> str:
        """创建新规则 → 写 YAML 文件 → 返回文件路径"""
        config["name"] = name
        self._validate(config)

        filename = _FILENAME_RE.sub('_', name) + ".yaml"
        filepath = self._rules_dir / filename

        if filepath.exists():
            raise FileExistsError(f"规则文件已存在: {filename}")

        self._write_yaml(filepath, config)
        logger.info("rule_created name=%s file=%s", name, filename)
        return str(filepath)

    def update_rule(self, name: str, config: dict[str, Any]) -> str:
        """更新规则 → 覆盖写 YAML 文件"""
        config["name"] = name

        filepath = self._name_to_path(name)
        if not filepath:
            raise FileNotFoundError(f"规则不存在: {name}")

        self._validate(config)
        self._write_yaml(filepath, config)
        logger.info("rule_updated name=%s file=%s", name, filepath.name)
        return str(filepath)

    def delete_rule(self, name: str) -> bool:
        """删除规则 → 删除 YAML 文件"""
        filepath = self._name_to_path(name)
        if not filepath:
            return False
        filepath.unlink()
        logger.info("rule_deleted name=%s file=%s", name, filepath.name)
        return True

    # ─── 校验 ────────────────────────────────────────

    def _validate(self, config: dict[str, Any]) -> None:
        """校验规则配置，不合法抛 ValueError"""
        errors = []

        # name 必填
        if not config.get("name"):
            errors.append("name 不能为空")

        # conditions 必填，且至少一个
        conditions = config.get("conditions", [])
        if not conditions:
            errors.append("conditions 不能为空，至少需要一个条件")
        else:
            cond_type = conditions[0].get("type", "")
            VALID_TYPES = {"object_in_zone", "count_line", "zone_empty"}
            if cond_type not in VALID_TYPES:
                errors.append(f"type 无效: {cond_type}，可选: {', '.join(sorted(VALID_TYPES))}")

            params = conditions[0].get("params", {})

            if cond_type in ("object_in_zone", "zone_empty"):
                zone = params.get("zone", [])
                if not zone or len(zone) < 3:
                    errors.append("zone 至少需要 3 个顶点坐标")

            if cond_type == "count_line":
                if "line_start" not in params or "line_end" not in params:
                    errors.append("count_line 需要 line_start 和 line_end")

        # severity 校验
        severity = config.get("severity", "warning")
        if severity not in ("critical", "warning", "info"):
            errors.append(f"severity 无效: {severity}，可选: critical/warning/info")

        # cooldown 范围
        cooldown = config.get("cooldown")
        if cooldown is not None:
            if not isinstance(cooldown, int) or cooldown < 0:
                errors.append("cooldown 必须是非负整数")

        # actions 校验
        VALID_ACTIONS = {"notify", "record_clip", "llm_analyze"}
        actions = config.get("actions", [])
        for a in actions:
            if a.get("type") not in VALID_ACTIONS:
                errors.append(f"actions.type 无效: {a.get('type')}，可选: {', '.join(sorted(VALID_ACTIONS))}")

        if errors:
            raise ValueError("; ".join(errors))

    # ─── 内部工具方法 ────────────────────────────────

    def _name_to_path(self, name: str) -> Path | None:
        """通过规则名称查找对应的 YAML 文件"""
        # 先精确匹配文件名
        expected = _FILENAME_RE.sub('_', name)
        for ext in (".yaml", ".yml"):
            filepath = self._rules_dir / f"{expected}{ext}"
            if filepath.exists():
                return filepath
        # 再遍历所有文件匹配 name 字段
        for yaml_file in list(self._rules_dir.glob("*.yaml")) + list(self._rules_dir.glob("*.yml")):
            config = self._read_yaml(yaml_file)
            if config and config.get("name") == name:
                return yaml_file
        return None

    def _read_yaml(self, filepath: Path) -> dict[str, Any] | None:
        try:
            with open(filepath, encoding="utf-8") as f:
                return yaml.safe_load(f)
        except Exception as e:
            logger.warning("yaml_read_error file=%s error=%s", filepath, e)
            return None

    def _write_yaml(self, filepath: Path, config: dict[str, Any]) -> None:
        """写 YAML 文件，保持可读格式"""
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(_format_yaml(config))

    def _summarize(self, filename: str, config: dict) -> dict:
        """从 config 中提取摘要信息"""
        conditions = config.get("conditions", [])
        cond_type = conditions[0].get("type", "unknown") if conditions else "unknown"
        return {
            "name": config.get("name", filename),
            "filename": filename,
            "type": cond_type,
            "camera_ids": config.get("camera_ids"),
            "severity": config.get("severity", "warning"),
            "cooldown": config.get("cooldown"),
            "enabled": config.get("enabled", True),
            "actions": [a.get("type") for a in config.get("actions", [])],
        }


def _format_yaml(config: dict) -> str:
    """格式化输出 YAML，保持中文字符友好的可读性"""
    import yaml

    class _LiteralStr(str):
        pass

    def _repr(dumper, data):
        if isinstance(data, _LiteralStr):
            return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
        return dumper.represent_str(data)

    # 使用 safe_dump 但保持顺序
    return yaml.dump(config, allow_unicode=True, default_flow_style=False, sort_keys=False)
```

### 3.3 REST API

```python
# src/sentinelmind/web/api/rules.py
"""
规则管理 REST API

端点：
- GET    /api/rules            列出所有规则
- GET    /api/rules/{name}     查看规则详情（含 YAML 原文）
- POST   /api/rules            创建规则
- PUT    /api/rules/{name}     更新规则
- DELETE /api/rules/{name}     删除规则
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from typing import Any

from sentinelmind.web.api.app import _require_auth, _require_role
from sentinelmind.rules.manager import RuleManager

router = APIRouter(prefix="/api/rules", tags=["rules"])

# ─── 请求/响应模型 ───────────────────────────────────

class ConditionParams(BaseModel):
    zone: list[list[float]] | None = Field(default=None, description="多边形顶点")
    line_start: list[float] | None = Field(default=None)
    line_end: list[float] | None = Field(default=None)
    threshold: int | None = Field(default=None)
    target_classes: list[str] | None = Field(default=None)

class RuleCondition(BaseModel):
    type: str = Field(..., description="object_in_zone / count_line / zone_empty")
    params: ConditionParams = Field(default_factory=ConditionParams)

class RuleAction(BaseModel):
    type: str = Field(..., description="notify / record_clip / llm_analyze")

class CreateRuleRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    conditions: list[RuleCondition] = Field(..., min_length=1)
    camera_ids: list[str] | None = Field(default=None)
    severity: str = Field(default="warning", pattern="^(critical|warning|info)$")
    cooldown: int | None = Field(default=None, ge=0)
    window_size: int | None = Field(default=None, ge=1)
    time_windows: list[dict] | None = Field(default=None)
    actions: list[RuleAction] = Field(default_factory=list)
    enabled: bool = Field(default=True)

class UpdateRuleRequest(CreateRuleRequest):
    pass  # 与创建相同

# ─── 端点 ────────────────────────────────────────────

@router.get("")
async def list_rules(request: Request):
    """列出所有规则"""
    _require_auth(request)
    manager = _get_manager()
    return manager.list_rules()


@router.get("/{name}")
async def get_rule(name: str, request: Request):
    """查看规则详情（含 YAML 原文）"""
    _require_auth(request)
    manager = _get_manager()
    rule = manager.get_rule(name)
    if not rule:
        raise HTTPException(404, f"规则不存在: {name}")
    return rule


@router.post("", status_code=201)
async def create_rule(body: CreateRuleRequest, request: Request):
    """创建新规则 → 写 YAML → 5 秒内自动热加载"""
    _require_auth(request)
    _require_role(request, "admin")

    manager = _get_manager()

    try:
        filepath = manager.create_rule(body.name, body.model_dump(exclude_none=True))
        return {"message": f"规则 '{body.name}' 已创建", "filepath": filepath}
    except FileExistsError as e:
        raise HTTPException(409, str(e))
    except ValueError as e:
        raise HTTPException(422, str(e))


@router.put("/{name}")
async def update_rule(name: str, body: UpdateRuleRequest, request: Request):
    """更新规则 → 覆盖写 YAML → 5 秒内自动热加载"""
    _require_auth(request)
    _require_role(request, "admin")

    manager = _get_manager()

    try:
        filepath = manager.update_rule(name, body.model_dump(exclude_none=True))
        return {"message": f"规则 '{name}' 已更新", "filepath": filepath}
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(422, str(e))


@router.delete("/{name}")
async def delete_rule(name: str, request: Request):
    """删除规则 → 删 YAML → 5 秒内热重载感知"""
    _require_auth(request)
    _require_role(request, "admin")

    manager = _get_manager()
    ok = manager.delete_rule(name)
    if not ok:
        raise HTTPException(404, f"规则不存在: {name}")
    return {"message": f"规则 '{name}' 已删除"}


# ─── 内部 ────────────────────────────────────────────

_rule_manager: RuleManager | None = None

def _get_manager() -> RuleManager:
    global _rule_manager
    if _rule_manager is None:
        from sentinelmind.config.settings import get_config
        config = get_config()
        rules_dir = config.get("rules", {}).get("dir", "configs/rules")
        _rule_manager = RuleManager(rules_dir)
    return _rule_manager
```

### 3.4 挂载到 app.py

```python
# 在 create_app() 函数中添加：

from sentinelmind.web.api.rules import router as rules_router
app.include_router(rules_router)
```

## 四、YAML 规则文件格式

### 内置规则模板

```yaml
# configs/rules/example_zone_intrusion.yaml
# 区域闯入检测

name: "门口闯入检测"

conditions:
  - type: object_in_zone
    params:
      zone: [[100, 200], [300, 200], [300, 400], [100, 400]]
      target_classes: ["person"]

camera_ids: ["cam_01"]
severity: warning
cooldown: 300            # 冷却 5 分钟
window_size: 5           # 连续 5 帧才触发

actions:
  - type: record_clip
  - type: llm_analyze
  - type: notify
```

```yaml
# configs/rules/example_count_line.yaml
# 计数线检测

name: "入口客流计数"

conditions:
  - type: count_line
    params:
      line_start: [100, 300]
      line_end: [500, 300]
      threshold: 10       # 累计 10 人穿越触发
      target_classes: ["person"]

camera_ids: ["cam_01"]
severity: info
cooldown: 600

actions:
  - type: notify
```

```yaml
# configs/rules/example_zone_empty.yaml
# 区域清空检测（人员离岗）

name: "值班岗位离岗检测"

conditions:
  - type: zone_empty
    params:
      zone: [[200, 300], [400, 300], [400, 500], [200, 500]]
      target_classes: ["person"]

camera_ids: ["cam_02"]
severity: critical
cooldown: 60

time_windows:
  - start: "08:00"
    end: "18:00"
    days: [0, 1, 2, 3, 4]  # 仅工作日

actions:
  - type: record_clip
  - type: llm_analyze
  - type: notify
```

## 五、前端新增

### 5.1 新增文件

```
frontend/src/
├── api/
│   └── rules.ts            # ← 新增：规则 API 客户端
├── views/
│   └── Rules.vue           # ← 新增：规则管理页面
└── router/
    └── index.ts            # 现有，新增 /rules 路由
```

### 5.2 API 客户端

```typescript
// frontend/src/api/rules.ts
import client from './client'
import type { Rule } from './types'

export interface RuleSummary {
  name: string
  filename: string
  type: string
  camera_ids: string[] | null
  severity: string
  cooldown: number | null
  enabled: boolean
  actions: string[]
}

export interface RuleDetail {
  filename: string
  filepath: string
  config: RuleConfig
  yaml_raw: string
}

export interface RuleConfig {
  name: string
  conditions: {
    type: string
    params: Record<string, any>
  }[]
  camera_ids?: string[]
  severity?: string
  cooldown?: number
  window_size?: number
  time_windows?: { start: string; end: string; days: number[] }[]
  actions?: { type: string }[]
  enabled?: boolean
}

export const rulesApi = {
  list: () => client.get<RuleSummary[]>('/api/rules'),

  get: (name: string) => client.get<RuleDetail>(`/api/rules/${encodeURIComponent(name)}`),

  create: (config: RuleConfig) => client.post('/api/rules', config),

  update: (name: string, config: RuleConfig) =>
    client.put(`/api/rules/${encodeURIComponent(name)}`, config),

  delete: (name: string) => client.delete(`/api/rules/${encodeURIComponent(name)}`),
}
```

### 5.3 前端页面布局

```
┌──────────────────────────────────────────────────────────────┐
│  规则管理                                         [+ 创建规则] │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │ 🔍 搜索规则...                      [类型▼] [状态▼]    │ │
│  └─────────────────────────────────────────────────────────┘ │
│                                                              │
│  ┌────────┬──────────┬──────────┬────────┬────────┬───────┐ │
│  │ 名称   │ 类型     │ 摄像头   │ 严重级别│ 冷却   │ 操作  │ │
│  ├────────┼──────────┼──────────┼────────┼────────┼───────┤ │
│  │ 门口闯入│ 区域闯入 │ cam_01   │ warning│ 300s   │ 编辑  │ │
│  │ 检测   │          │          │        │        │ 删除  │ │
│  ├────────┼──────────┼──────────┼────────┼────────┼───────┤ │
│  │ 客流统计│ 计数线   │ cam_01   │ info   │ 600s   │ 编辑  │ │
│  │        │          │          │        │        │ 删除  │ │
│  ├────────┼──────────┼──────────┼────────┼────────┼───────┤ │
│  │ 离岗检测│ 区域清空 │ cam_02   │ critical│ 60s   │ 编辑  │ │
│  │        │          │          │        │        │ 删除  │ │
│  └────────┴──────────┴──────────┴────────┴────────┴───────┘ │
│                                                              │
└──────────────────────────────────────────────────────────────┘

点击 [+ 创建规则] 或 [编辑] → 右侧抽屉：

┌─────────────────────────────────┐
│  创建规则                    [×] │
│                                 │
│  名称：    [________________]   │
│  类型：    [区域闯入 ▼]          │
│  摄像头：  [cam_01 ▼] [+添加]   │
│  严重级别：[warning ▼]           │
│                                 │
│  ── 条件参数 ──                  │
│  检测区域：                      │
│  ┌────────────────────┐        │
│  │                    │        │
│  │   (画布区域)        │        │
│  │   拖拽顶点设定区域   │        │
│  │                    │        │
│  └────────────────────┘        │
│  顶点坐标：[_____________]      │
│                                 │
│  目标类型：[person ▼] [+添加]    │
│                                 │
│  ── 防线配置 ──                  │
│  滑动窗口：[5] 帧                │
│  冷却时间：[300] 秒              │
│  时间窗口：[+] 添加              │
│                                 │
│  ── 动作 ──                      │
│  ☑ 录制视频片段                  │
│  ☑ LLM 智能分析                  │
│  ☑ 推送通知                      │
│                                 │
│  [取消]              [保存]      │
└─────────────────────────────────┘
```

## 六、规则模板文件

在 `configs/rules/` 下预置 3 个模板文件，方便用户快速上手：

```
configs/rules/
├── .gitkeep
├── example_zone_intrusion.yaml    # 区域闯入示例
├── example_count_line.yaml        # 计数线示例
└── example_zone_empty.yaml        # 区域清空示例
```

模板文件的 `enabled: false`，默认不生效。用户复制后改名为 `xxx.yaml` 并设为 `enabled: true` 即可激活。

## 七、数据流

```
                前端 Rules.vue
                      │
                      ▼
              REST API /api/rules
                      │
            ┌─────────┴─────────┐
            ▼                   ▼
      RuleManager           RuleEngine
      (写 YAML 文件)        (热重载监控)
            │                   │
            ▼                   ▼
    configs/rules/xxx.yaml  ──→ mtime 变化 ──→ _reload_single_file()
    (持久化到磁盘)              (5秒内自动生效)
```

## 八、与 Agent 的关系

规则管理 REST API 完成后，Agent 的规则工具可以直接对接：

```python
# Agent adapters/sentinelmind/rule_tools.py
# 之前受限于无 API → 现在直接 POST/GET/DELETE /api/rules
```

## 九、代码量估算

| 模块 | 文件 | 预估行数 |
|---|---|---|
| RuleManager | rules/manager.py | ~150 |
| REST API | web/api/rules.py | ~100 |
| API 客户端 | frontend/src/api/rules.ts | ~40 |
| 前端页面 | frontend/src/views/Rules.vue | ~350 |
| 路由 | router/index.ts (+5行) | ~5 |
| 规则模板 | configs/rules/*.yaml × 3 | ~60 |
| **总计** | **6 文件** | **~700** |

## 十、实施步骤

1. 实现 `rules/manager.py` — YAML 文件管理 + 校验
2. 实现 `web/api/rules.py` — REST 端点
3. 在 `app.py` 中挂载路由
4. 编写 3 个规则模板 YAML
5. 实现前端 `api/rules.ts`
6. 实现前端 `views/Rules.vue`
7. 路由 + 侧栏菜单注册
8. 手动测试：创建 → 编辑 → 删除 → 验证热重载
