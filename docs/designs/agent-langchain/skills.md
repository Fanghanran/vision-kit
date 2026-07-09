# Skills 设计

## 什么是 Skill

Skill 是一个**受限子 StateGraph**——有独立的 system prompt 和受限的工具集，执行后结果写回主 Agent 的 messages。

```
主 Agent 收到 "/巡检"
    ↓
识别为 skill 调用 → 激活 patrol_skill 子图
    ↓
子图：独立 prompt + 受限工具集
    ↓
子图执行完毕 → 结果写回主 graph 的 messages
    ↓
主 Agent 继续服务（可以基于巡检结果回答追问）
```

## 四个 Skills

| Skill | 触发词 | 权限 | 功能 |
|---|---|---|---|
| `/巡检` | /patrol, /巡检, "巡检" | viewer+ | 全系统健康检查 → 报告 |
| `/排障` | /diagnose, /排障, "为什么总告警" | viewer+ | 单摄像头深度分析 |
| `/日报` | /report, /日报, "生成日报" | viewer+ | 汇总今日数据 → 日报 |
| `/规则` | /rule, /规则, "创建规则" | operator+ | 规则 CRUD 对话式操作 |

---

## 一、巡检 Skill（patrol.py）

```
触发：/巡检 或 "执行一次巡检" 或 "检查系统状态"

Prompt 约束：
  你是系统巡检专家。请按以下步骤逐一检查：
  1. list_cameras — 检查所有摄像头状态
  2. system_health — 检查系统健康
  3. query_alerts(status='pending') — 检查待处理告警
  4. get_system_stats(period='today') — 今日统计

  对每个异常：描述问题 + 评估严重程度 + 给出建议
  全部正常 → "✅ 系统正常"

可用工具（受限）：
  - list_cameras
  - system_health
  - query_alerts
  - get_system_stats

输出格式：
  ## 巡检报告 — {timestamp}
  ### 摄像头状态（逐路列出）
  ### 系统健康（关键指标）
  ### 待处理告警（如有）
  ### 异常汇总（问题 + 建议）
```

**实现**：

```python
# agent/skills/patrol.py
from langgraph.graph import StateGraph, START, END
from langgraph.prebuilt import ToolNode
from langchain_core.messages import SystemMessage
from agent.core.state import AgentState
from agent.adapters.vision_agent.camera_tools import list_cameras as list_cameras_tool
from agent.adapters.vision_agent.alert_tools import query_alerts as query_alerts_tool
from agent.adapters.vision_agent.system_tools import system_health as system_health_tool, get_system_stats as get_system_stats_tool

PATROL_PROMPT = """你正在执行系统巡检。...（如上）"""

PATROL_TOOLS = [
    list_cameras_tool,
    system_health_tool,
    query_alerts_tool,
    get_system_stats_tool,
]

def create_patrol_skill(llm) -> StateGraph:
    graph = StateGraph(AgentState)

    llm_with_tools = llm.bind_tools(PATROL_TOOLS)
    tool_node = ToolNode(PATROL_TOOLS)

    async def patrol_node(state: AgentState):
        messages = [SystemMessage(content=PATROL_PROMPT)] + list(state["messages"])
        response = await llm_with_tools.ainvoke(messages)
        return {"messages": [response]}

    def should_continue(state: AgentState) -> str:
        last = state["messages"][-1]
        if hasattr(last, "tool_calls") and last.tool_calls:
            return "tools"
        return END

    graph.add_node("patrol", patrol_node)
    graph.add_node("tools", tool_node)
    graph.add_edge(START, "patrol")
    graph.add_conditional_edges("patrol", should_continue, {
        "tools": "tools",
        "__end__": END,
    })
    graph.add_edge("tools", "patrol")

    return graph.compile()
```

---

## 二、排障 Skill（diagnose.py）

```
触发：/排障 cam_02 或 "cam_02 为什么总告警"

Prompt 约束：
  你是故障排查专家。针对指定摄像头执行深度诊断：
  1. get_camera_detail(camera_id) — 配置和运行指标
  2. query_alerts(camera_id=..., limit=50) — 最近告警
  3. alert_stats(period='7d') 并关注该摄像头 — 7天趋势
  4. 交叉分析：告警类型分布、时间规律、是否误报

  最终给出：
  - 根因判断（网络/配置/环境/误报）
  - 建议操作
  - 需要人工确认的事项

可用工具：同上 + get_camera_detail
```

---

## 三、日报 Skill（daily_report.py）

```
触发：/日报 或 "生成今天的日报"

Prompt 约束：
  生成每日运维报告：
  1. get_system_stats(period='today') — 今日数据
  2. query_alerts 汇总 — 今日所有告警
  3. 生成 Markdown 日报：
     - 总告警数 / 已处理 / 待处理 / 误报
     - 告警最多的摄像头顶 3
     - 系统运行状态
     - 与昨日对比（如有）
     - 建议关注事项

可用工具：
  - get_system_stats
  - query_alerts
  - system_health

输出可直接复制到钉钉/企微推送。
```

---

## 四、规则管理 Skill（rule_manage.py）

```
触发：/规则 或 "创建一个检测规则" 或 "删除门口那个规则"

Prompt 约束：
  对话式规则管理：
  1. 如果用户想创建规则，先询问必要信息（名称/类型/摄像头/区域）
  2. list_rules 展示已有规则
  3. create_rule 创建 → 自动热加载
  4. delete_rule 删除 → 自动清理

可用工具：
  - list_rules
  - get_rule
  - create_rule
  - delete_rule

权限：operator 可查看，admin 可修改
```

**对话示例**：

```
用户：/规则 我要在门口加一个人闯入检测
Agent：好的。请确认以下信息：
       - 规则名称：门口闯入检测
       - 类型：object_in_zone
       - 摄像头：您想监控哪个摄像头？（当前在线：cam_01 正门, cam_03 后门）
       - 区域：门口的大致坐标？

用户：cam_01，区域是门框那一块
Agent：我需要具体的多边形坐标。您可以：
       1. 在前端 Monitor 页面拖拽画区域，把坐标发给我
       2. 直接告诉我坐标，格式：[[x1,y1],[x2,y2],...]
       3. 我先创建一个默认覆盖全画面的规则，您之后在前端调整

用户：先创建默认的吧
Agent：✅ 规则 '门口闯入检测' 已创建（object_in_zone, cam_01, 全画面）。
       将在 5 秒内自动热加载。您可以在前端 Monitor 页面调整检测区域。
```

---

## Skills 和主 Agent 的集成

```python
# agent/core/graph.py（节选）

# 注册所有 skills
SKILLS = {
    "patrol": {"graph": create_patrol_skill(llm), "trigger": ["/巡检", "/patrol", "巡检"]},
    "diagnose": {"graph": create_diagnose_skill(llm), "trigger": ["/排障", "/diagnose", "排障"]},
    "daily_report": {"graph": create_daily_report_skill(llm), "trigger": ["/日报", "/report", "日报"]},
    "rule_manage": {"graph": create_rule_manage_skill(llm), "trigger": ["/规则", "/rule", "规则"]},
}

def detect_skill_activation(user_input: str) -> str | None:
    """检测用户输入是否触发某个 skill"""
    for name, skill in SKILLS.items():
        for trigger in skill["trigger"]:
            if trigger in user_input:
                return name
    return None

async def entry_node(state: AgentState) -> AgentState:
    user_input = state["messages"][-1].content
    skill_name = detect_skill_activation(user_input)

    if skill_name:
        state["active_skill"] = skill_name
        # 将触发词替换为专业 prompt
        ...
    return state
```
