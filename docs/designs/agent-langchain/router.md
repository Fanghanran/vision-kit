# 双模式路由

## 设计目标

同一个 Agent 核心支持两种模式，用户无需手动切换。通过一次轻量 LLM 调用（gpt-4o-mini）自动判断意图并路由。

## 路由规则

```python
# agent/core/router.py
from langchain_core.prompts import ChatPromptTemplate

class ModeRouter:
    """
    意图识别用轻量模型（gpt-4o-mini），只判断"走哪条路"。
    不做复杂推理，成本极低。
    """

    ROUTER_PROMPT = ChatPromptTemplate.from_messages([
        ("system", """你是一个意图路由器。分析用户输入，判断应该使用哪个 Agent 模式。

判断规则：
- professional：涉及摄像头、告警、系统配置、规则、巡检、日报、系统状态、视频监控
- general：所有其他问题（编程、常识、闲聊、文档查询等）
- 如果用户明确说"/专业模式"、"切换到专业模式"、"用专业模式" → professional
- 如果用户明确说"/通用模式"、"切换到通用模式"、"退出专业模式" → general

只输出一个词，不要任何解释。

示例：
"cam_02 为什么离线了" → professional
"Python 怎么处理 GIL" → general
"帮我写一个 Dockerfile" → general
"今天有多少告警" → professional
"切换到通用模式" → general
"/巡检" → professional"""),
        ("human", "{input}"),
    ])

    def __init__(self, llm):
        """
        llm: 轻量模型即可，如 gpt-4o-mini 或 claude-haiku。
        路由不需要推理能力，只需要分类。
        """
        self.llm = llm
        self.chain = self.ROUTER_PROMPT | self.llm

    async def route(self, user_input: str) -> str:
        resp = await self.chain.ainvoke({"input": user_input})
        mode = resp.content.strip().lower()
        if mode not in ("professional", "general"):
            mode = "general"  # fallback
        return mode
```

## 模式锁定

支持用户手动锁定模式：

| 用户输入 | 行为 |
|---|---|
| `/专业` 或 `/professional` | 锁定专业模式，后续对话不走路由 |
| `/通用` 或 `/general` | 锁定通用模式 |
| `/自动` 或 `/auto` | 解除锁定，恢复自动路由 |
| 正常聊天（未锁定时） | 自动判断意图 |

## 完整路由流程

```python
# agent/core/graph.py
from langgraph.graph import StateGraph, START, END
from langgraph.prebuilt import ToolNode, create_react_agent
from agent.core.state import AgentState
from agent.core.router import ModeRouter

def build_dual_mode_graph(
    llm: "BaseChatModel",
    router_llm: "BaseChatModel",  # gpt-4o-mini
    professional_tools: list,
    general_tools: list,
    checkpointer,
) -> "CompiledGraph":
    """
    构建双模式 Agent Graph。

    两个模式各自有独立的 create_react_agent 子图，
    区别仅在于 tools 和 system_prompt 不同。
    """

    # ─── 预构建两个 Agent 子图 ───
    professional_agent = create_react_agent(
        model=llm,
        tools=professional_tools,
        prompt=PROFESSIONAL_SYSTEM_PROMPT,
    )

    general_agent = create_react_agent(
        model=llm,
        tools=general_tools,
        prompt=GENERAL_SYSTEM_PROMPT,
    )

    # ─── 入口路由 ───
    router = ModeRouter(llm=router_llm)

    async def entry_node(state: AgentState) -> dict:
        user_input = state["messages"][-1].content

        # 检测模式锁定命令
        if any(cmd in user_input for cmd in ["/专业", "/professional"]):
            return {"mode": "professional", "mode_locked": True}
        if any(cmd in user_input for cmd in ["/通用", "/general"]):
            return {"mode": "general", "mode_locked": True}
        if any(cmd in user_input for cmd in ["/自动", "/auto"]):
            return {"mode_locked": False}

        # 已锁定 → 直接用锁定模式
        if state.get("mode_locked") and state.get("mode"):
            return {}

        # 未锁定 → 路由判断
        mode = await router.route(user_input)

        # 专业模式：注入系统快照
        if mode == "professional":
            snapshot = await capture_system_snapshot()
            return {
                "mode": mode,
                "system_snapshot": snapshot,
                "last_snapshot_time": time.time(),
                "user_id": state.get("user_id"),
                "user_role": state.get("user_role"),
            }

        return {"mode": mode}

    def route_to_subgraph(state: AgentState) -> str:
        return state.get("mode", "professional")

    # ─── 组装 Graph ───
    graph = StateGraph(AgentState)

    graph.add_node("entry", entry_node)
    graph.add_node("professional", professional_agent)
    graph.add_node("general", general_agent)

    graph.add_edge(START, "entry")
    graph.add_conditional_edges(
        "entry",
        route_to_subgraph,
        {"professional": "professional", "general": "general"},
    )

    return graph.compile(checkpointer=checkpointer)
```

## 系统快照注入

专业模式下，每次激活时自动注入当前系统状态，让 Agent 一开始就知道"什么情况"。快照通过 REST API 获取，不 import SentinelMind 内部模块：

```python
async def capture_system_snapshot(token: str) -> dict:
    """通过 REST API 获取当前系统状态快照，注入专业模式 prompt"""
    import httpx

    async with httpx.AsyncClient(base_url=BASE_URL, timeout=10.0) as client:
        # 并行请求三个端点
        headers = {"Authorization": f"Bearer {token}"}

        # GET /api/cameras — 摄像头状态
        cameras_resp = await client.get("/api/cameras", headers=headers)
        cameras = cameras_resp.json() if cameras_resp.status_code == 200 else []

        # GET /health — 系统健康
        health_resp = await client.get("/health")
        health = health_resp.json() if health_resp.status_code == 200 else {}

        # GET /api/alerts?status=pending&limit=0 — 只看总数
        alerts_resp = await client.get("/api/alerts", headers=headers,
                                       params={"status": "pending", "limit": 0})
        pending = alerts_resp.json().get("total", 0) if alerts_resp.status_code == 200 else 0

    cam_summary = []
    for c in cameras:
        icon = "🟢" if c.get("status") == "online" else "🔴" if c.get("status") == "offline" else "🟡"
        cam_summary.append(f"{icon} {c['id']} ({c.get('name', '')}): {c.get('status', 'unknown')}")

    return {
        "cameras": cam_summary,
        "health": health,
        "pending_alerts": pending,
        "timestamp": datetime.now().isoformat(),
    }

def format_snapshot_for_prompt(snapshot: dict) -> str:
    """将快照格式化为 Markdown 注入 system prompt"""
    cam_lines = "\n".join(f"  - {c}" for c in snapshot["cameras"])
    h = snapshot["health"]
    return f"""当前系统状态（{snapshot['timestamp']}）：

摄像头：
{cam_lines}

系统健康：
  - 状态：{h['status']}
  - P50 延迟：{h['p50_latency_ms']}ms
  - P99 延迟：{h['p99_latency_ms']}ms
  - GPU 利用率：{h['gpu_util_percent']}%
  - GPU 显存：{h['gpu_memory']}

待处理告警：{snapshot['pending_alerts']} 条
"""
```

## 对话示例

```
=== 通用模式 ===

用户：Python 的 GIL 是什么
Agent：GIL（全局解释器锁）是 CPython 中的一个互斥锁...（通用知识）

用户：用专业模式看看我的摄像头
Agent：✅ 已切换到专业模式。

      当前系统状态：
      🟢 cam_01 (正门): online, 25fps
      🔴 cam_02 (仓库): offline, 0fps

      您的 cam_02 处于离线状态，需要排查吗？

用户：帮我查一下 cam_02
Agent：cam_02 目前离线，最后在线时间是 14:23（12 分钟前）。
      建议检查 RTSP 流地址和网络连接。
      需要我尝试重启该摄像头吗？

=== 自动路由 ===

用户：帮我写一个 shell 脚本
Agent：（自动识别为通用模式）好的，请问需要什么功能的脚本？

用户：今天系统跑了多少告警
Agent：（自动识别为专业模式）今天共触发 23 条告警，其中 18 条已处理，
      2 条待确认，3 条被标为误报。告警主要集中在 14:00-16:00...
```
