# Skills 设计（Agno Workflow 版）

## LangChain StateGraph vs Agno Workflow

| | LangChain | Agno |
|---|---|---|
| 定义方式 | `StateGraph` + 手动 `add_node` + `add_edge` | 继承 `Workflow` + 写普通方法 |
| 条件路由 | `add_conditional_edges("node", fn, {...})` | 方法内 `if/else` |
| 状态管理 | `TypedDict` 手动定义 | 实例属性 `self.xxx` |
| 学习成本 | 需理解节点/边/条件路由 | 写普通 Python 类 |

## 四个 Skills

| Skill | 触发词 | 权限 | 功能 |
|---|---|---|---|
| `/巡检` | /patrol, /巡检, "巡检" | viewer+ | 全系统健康检查 → 报告 |
| `/排障` | /diagnose, /排障, "为什么总告警" | viewer+ | 单摄像头深度分析 |
| `/日报` | /report, /日报, "生成日报" | viewer+ | 汇总今日数据 → 日报 |
| `/规则` | /rule, /规则, "创建规则" | operator+ | 规则 CRUD 对话式操作 |

---

## 一、巡检 Skill

```python
# agent_agno/skills/patrol.py
from agno.workflow import Workflow
from agno import Agent
from agent_agno.adapters.sentinelmind.camera_tools import list_cameras
from agent_agno.adapters.sentinelmind.alert_tools import query_alerts
from agent_agno.adapters.sentinelmind.system_tools import system_health, get_system_stats

class PatrolWorkflow(Workflow):
    """
    系统巡检 Workflow。

    执行顺序：
    1. 健康检查 → 检查所有摄像头 + 系统健康
    2. 告警扫描 → 检查待处理告警
    3. 汇总 → 生成巡检报告

    用法：
    result = await PatrolWorkflow().run(token="xxx")
    """

    description: str = "每5分钟巡检：摄像头状态 + 系统健康 + 待处理告警"

    async def run(self, token: str = "", *args, **kwargs):
        # 步骤1：摄像头状态
        cameras = await list_cameras(token=token)
        health = await system_health()
        alerts = await query_alerts(token=token, status="pending", limit=50)
        stats = await get_system_stats(token=token, period="today")

        # 步骤2：用 Agent 分析巡检结果
        # 这里只做数据采集，分析交给 Agent 的 LLM
        return {
            "cameras": cameras,
            "health": health,
            "pending_alerts": alerts,
            "today_stats": stats,
            "timestamp": datetime.now().isoformat(),
        }
```

对比 LangChain 版——Agno 的 Workflow 就是一个带 `run` 方法的类，不需要定义 State、节点、边、条件路由。

---

## 二、排障 Skill

```python
# agent_agno/skills/diagnose.py
from agno.workflow import Workflow

class DiagnoseWorkflow(Workflow):
    """单摄像头深度排障"""

    description: str = "针对指定摄像头执行深度诊断，综合告警历史和配置信息给出根因判断"

    async def run(self, token: str, camera_id: str, *args, **kwargs):
        # 列出所有摄像头，找到目标
        cameras_resp = await list_cameras(token=token)

        # 查最近告警
        recent_alerts = await query_alerts(
            token=token,
            camera_id=camera_id,
            limit=50,
        )

        # 查7天趋势
        week_alerts = await query_alerts(
            token=token,
            camera_id=camera_id,
            limit=1000,
        )

        return {
            "camera_id": camera_id,
            "recent_alerts": recent_alerts,
            "week_alert_count": week_alerts,
            "timestamp": datetime.now().isoformat(),
        }
```

---

## 三、日报 Skill

```python
# agent_agno/skills/daily_report.py
from agno.workflow import Workflow

class DailyReportWorkflow(Workflow):
    """每日报告生成"""

    description: str = "每天9:00汇总昨日数据，生成 Markdown 日报"

    async def run(self, token: str, *args, **kwargs):
        stats = await get_system_stats(token=token, period="today")
        pending = await query_alerts(token=token, status="pending", limit=1000)
        all_alerts = await query_alerts(token=token, limit=1000)

        return {
            "stats": stats,
            "pending_count": len(pending) if isinstance(pending, list) else 0,
            "total_alerts": all_alerts,
            "timestamp": datetime.now().isoformat(),
        }
```

---

## 四、规则管理 Skill

```python
# agent_agno/skills/rule_manage.py
from agno.workflow import Workflow

class RuleManageWorkflow(Workflow):
    """规则管理 — 待 SentinelMind 新增 /api/rules 端点后可用"""

    description: str = "对话式规则管理：创建/删除/查看检测规则"

    async def run(self, token: str, action: str, rule_name: str = "",
                  rule_type: str = "", camera_ids: str = "", *args, **kwargs):
        if action == "list":
            return await list_rules(token=token)
        elif action == "create":
            return await create_rule(
                token=token, name=rule_name,
                rule_type=rule_type, camera_ids=camera_ids,
            )
        elif action == "delete":
            return await delete_rule(token=token, name=rule_name)
        return "请指定 action: list / create / delete"
```

---

## Skills 与 Agent 的集成

```python
# agent_agno/app.py（节选）

from agent_agno.skills.patrol import PatrolWorkflow
from agent_agno.skills.diagnose import DiagnoseWorkflow
from agent_agno.skills.daily_report import DailyReportWorkflow

# 注册 skills
SKILL_TRIGGERS = {
    "patrol": {"trigger": ["/巡检", "/patrol", "巡检"], "workflow": PatrolWorkflow()},
    "diagnose": {"trigger": ["/排障", "/diagnose", "排障"], "workflow": DiagnoseWorkflow()},
    "daily_report": {"trigger": ["/日报", "/report", "日报"], "workflow": DailyReportWorkflow()},
}

def detect_skill(user_input: str) -> tuple[str, object] | None:
    """检测用户输入是否触发某个 skill，返回 (名称, Workflow)"""
    for name, skill in SKILL_TRIGGERS.items():
        for t in skill["trigger"]:
            if t in user_input:
                return (name, skill["workflow"])
    return None

# 在路由入口中：
# skill = detect_skill(user_input)
# if skill:
#     result = await skill["workflow"].run(token=token)
#     将 result 作为上下文送给 Agent 做分析
```

---

## LangChain vs Agno 代码量对比

以巡检 Skill 为例：

| | LangChain | Agno |
|---|---|---|
| 定义 State | `class AgentState(TypedDict)` 10行 | 不需要 |
| 定义节点 | `async def patrol_node(state)` 5行 | `async def run(self, ...)` |
| 定义工具节点 | `ToolNode(PATROL_TOOLS)` 1行 | 不需要（Agent 内部处理） |
| 定义边 | `add_edge` + `add_conditional_edges` 8行 | 不需要 |
| 编译 | `graph.compile()` | 不需要 |
| **总计** | ~35 行 | ~15 行 |
