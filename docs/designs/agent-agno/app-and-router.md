# 应用入口与路由（Agno 版）

## 应用入口

```python
# agent_agno/app.py
from agno import Agent
from agno.models.openai import OpenAIChat
from agent_agno.memory import create_agent_memory
from agent_agno import prompts
from agent_agno.adapters.vision_agent.camera_tools import (
    list_cameras, toggle_camera, add_camera,
)
from agent_agno.adapters.vision_agent.alert_tools import (
    query_alerts, acknowledge_alert, alert_detail,
)
from agent_agno.adapters.vision_agent.system_tools import (
    system_health, get_system_stats, get_config,
)
from agent_agno.scheduler import PatrolScheduler
from agent_agno.skills.patrol import PatrolWorkflow
from agent_agno.skills.daily_report import DailyReportWorkflow

class AgentApp:
    """Agent 应用 — 独立于 Vision Agent 运行"""

    def __init__(self, config: dict):
        self.config = config
        self.llm = self._create_llm(config)
        self.memory = create_agent_memory(config.get("memory", {}).get("db_path", "data/agent_memory.db"))

        # ─── 工具 ───
        professional_tools = [
            list_cameras, toggle_camera, add_camera,
            query_alerts, acknowledge_alert, alert_detail,
            system_health, get_system_stats, get_config,
        ]

        # ─── 两个 Agent 实例 ───
        self.general_agent = Agent(
            model=self.llm,
            tools=[],  # MCP 工具延迟加载
            system_prompt=prompts.GENERAL,
            memory=self.memory,
            add_history_to_context=True,
            num_history_runs=10,
        )

        self.professional_agent = Agent(
            model=self.llm,
            tools=professional_tools,
            system_prompt=prompts.PROFESSIONAL,
            memory=self.memory,
            add_history_to_context=True,
            num_history_runs=10,
        )

        # ─── 分析 Agent（用于巡检/排障/日报）───
        self.analysis_agent = Agent(
            model=OpenAIChat(id="gpt-4o"),  # 分析可以用更强的模型
            tools=professional_tools,
            system_prompt="你是视频监控运维分析专家，负责分析巡检数据、告警趋势，给出建议。",
            memory=self.memory,
        )

        # ─── 调度器 ───
        self.scheduler = PatrolScheduler(
            patrol_workflow=PatrolWorkflow(),
            report_workflow=DailyReportWorkflow(),
            analysis_agent=self.analysis_agent,
            notifier=self._create_notifier(config.get("notification")),
        )

    async def start(self):
        self.scheduler.start()

    async def stop(self):
        self.scheduler.stop()

    def _create_llm(self, config: dict):
        provider = config.get("llm", {})
        return OpenAIChat(
            id=provider.get("model", "gpt-4o"),
            api_key=provider.get("api_key"),
            base_url=provider.get("base_url"),
        )

    def _create_notifier(self, notif_config: dict | None):
        if not notif_config:
            return None
        from agent_agno.adapters.notification import WebhookNotifier
        return WebhookNotifier(notif_config)
```

---

## 路由 Agent

```python
# agent_agno/router.py
from agno import Agent
from agno.models.openai import OpenAIChat

class ModeRouter:
    """
    用轻量 Agent 做意图路由。

    对比 LangChain 版：
    - LangChain：自定义 RunnableBranch + ChatPromptTemplate
    - Agno：直接用 Agent 跑一次推理
    """

    ROUTER_PROMPT = """你是一个意图路由器。分析用户输入，判断应该用哪个 Agent 模式。

判断规则：
- professional：涉及摄像头、告警、系统配置、规则、巡检、日报、系统状态、视频监控
- general：所有其他问题（编程、常识、闲聊等）
- 如果用户明确说"/专业模式"、"切换到专业模式" → 锁定 professional
- 如果用户明确说"/通用模式"、"切换到通用模式" → 锁定 general
- 如果用户明确说"/自动"、"退出专业模式" → 取消锁定

只输出一个词，不要解释。"""

    def __init__(self):
        self.router_agent = Agent(
            model=OpenAIChat(id="gpt-4o-mini"),  # 路由用便宜模型
            system_prompt=self.ROUTER_PROMPT,
        )
        self.mode_locked = False
        self.locked_mode = None

    async def route(self, user_input: str) -> str:
        # 检测锁定/解锁命令
        if any(cmd in user_input for cmd in ["/专业", "/professional"]):
            self.mode_locked = True
            self.locked_mode = "professional"
            return "professional"
        if any(cmd in user_input for cmd in ["/通用", "/general"]):
            self.mode_locked = True
            self.locked_mode = "general"
            return "general"
        if any(cmd in user_input for cmd in ["/自动", "/auto"]):
            self.mode_locked = False
            self.locked_mode = None

        # 已锁定 → 直接返回
        if self.mode_locked and self.locked_mode:
            return self.locked_mode

        # 路由判断
        resp = await self.router_agent.arun(user_input)
        mode = resp.content.strip().lower()
        return mode if mode in ("professional", "general") else "general"
```

---

## 对话处理

```python
# agent_agno/app.py（续）

async def chat(self, user_input: str, session_id: str, token: str = "") -> str:
    """
    处理一次对话。

    流程：
    1. 路由判断 → 选择 general 或 professional Agent
    2. 检测 Skill 激活
    3. 如果触发 Skill → Workflow 采集数据 → Agent 分析
    4. 否则 → Agent 直接对话
    """
    # 步骤1：路由
    mode = await self.router.route(user_input)

    # 步骤2：Skill 检测
    skill = detect_skill(user_input)

    if skill and mode == "professional":
        skill_name, workflow = skill
        # Workflow 采集数据
        data = await workflow.run(token=token)

        # Agent 分析数据
        prompt = self._build_skill_prompt(skill_name, data, user_input)
        agent = self.analysis_agent
    else:
        prompt = user_input
        agent = self.professional_agent if mode == "professional" else self.general_agent

    # 步骤3：执行
    # Agno 自动处理历史注入（add_history_to_context）
    response = await agent.arun(
        prompt,
        session_id=f"{mode}_{session_id}",  # 不同模式不同 session
    )

    return response.content

async def stream_chat(self, user_input: str, session_id: str, token: str = ""):
    """流式对话，用于 SSE"""
    mode = await self.router.route(user_input)
    agent = self.professional_agent if mode == "professional" else self.general_agent

    async for chunk in agent.arun(
        user_input,
        session_id=f"{mode}_{session_id}",
        stream=True,
    ):
        yield chunk
```

---

## Web 集成

```python
# agent_agno/web.py
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

agent_router = APIRouter(prefix="/api/agent", tags=["agent"])

class ChatRequest(BaseModel):
    message: str
    session_id: str = "default"

@agent_router.post("/chat")
async def chat(request: Request, payload: ChatRequest):
    """SSE 流式对话"""
    user_id = request.state.user_id
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    session_id = f"{user_id}_{payload.session_id}"

    agent_app = get_agent_app()

    async def event_stream():
        async for chunk in agent_app.stream_chat(payload.message, session_id, token):
            yield f"data: {json.dumps({'content': chunk})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")
```

---

## 启动流程

```python
# 在 Vision Agent 的 __main__.py 中可选择性启动 Agent

# 方式1：同进程启动（开发期间）
agent_app = AgentApp(config=agent_config)
await agent_app.start()

# 方式2：独立进程启动（生产/拆分后）
# uvicorn agent_agno.web:agent_app --port 8081
```

---

## 对话示例

```
=== 通用模式 ===

用户：Python 的 GIL 是什么
Agent：GIL（全局解释器锁）是 CPython 中的一个互斥锁...

用户：用专业模式看看我的摄像头
Agent：✅ 已切换到专业模式。

      当前系统状态：
      🟢 cam_01 (正门): online, 25fps
      🔴 cam_02 (仓库): offline, 0fps

      您的 cam_02 处于离线状态，需要排查吗？

=== 巡检触发 ===

用户：/巡检
Agent：执行系统巡检...

      1. 摄像头状态：
      🟢 cam_01 (正门): online
      🔴 cam_02 (仓库): offline 12 分钟

      2. 系统健康：正常
      P50=45ms, GPU=34%

      3. 待处理告警：2 条

      ⚠️ 异常汇总：
      - cam_02 离线 12 分钟，建议检查网络连接
      - 告警 #abc123 待确认超过 1 小时

      需要我帮您排查 cam_02 吗？
```
