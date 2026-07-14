# SentinelMind × LangChain — Agent 详细设计书

> 状态：设计中 | 版本：v1 | 日期：2026-07-14

---

## 一、项目关系

- Agent 项目始于 SentinelMind 的运维思考
- **目前**：放在一起（`src/sentinelmind/agent/`），便于开发和测试
- **后续**：独立仓库，通过 REST API 对接 SentinelMind
- Agent 只认 HTTP + MCP 两种协议，不 import SentinelMind 内部代码

---

## 二、Token 生命周期管理

### 2.1 问题

设计书说"透传 Bearer Token"，但 Token 24h 过期。Agent 的定时任务（巡检每 5 分钟、日报每天）在用户关闭浏览器后会连续 401。

### 2.2 方案：Service Token

Agent 使用独立的 Service Token，不依赖用户 Token。

**配置**：
```yaml
# configs/agent.yaml
auth:
  mode: "service"  # service | user
  service_token: "${AGENT_SERVICE_TOKEN}"  # Agent 专用 token
  sentinelmind_url: "http://localhost:8080"
```

**SentinelMind 侧**：
- 新增 Service Token 概念：`configs/settings.yaml` 中配置 `web.service_tokens`
- Service Token 不过期，权限固定为 admin（或可配置）
- Service Token 与用户 Token 使用相同的 Bearer 格式

**实现**：
```python
# agent/adapters/sentinelmind/client.py
class VisionAgentClient:
    def __init__(self, base_url: str, token: str):
        self.base_url = base_url
        self.token = token  # service_token 或 user_token

    async def request(self, method: str, path: str, **kwargs):
        headers = {"Authorization": f"Bearer {self.token}"}
        async with httpx.AsyncClient(base_url=self.base_url, timeout=10.0) as client:
            resp = await client.request(method, path, headers=headers, **kwargs)
            if resp.status_code == 401:
                raise TokenExpiredError("Token 已过期，请重新登录或刷新 service token")
            resp.raise_for_status()
            return resp.json()
```

### 2.3 用户交互场景

Web Chat 中用户用自己的 Token（从前端透传），Agent 用 Service Token 调用 SentinelMind API。

```
用户 → Web Chat (Bearer Token) → Agent → SentinelMind (Service Token)
```

---

## 三、错误处理策略

### 3.1 错误分类

| HTTP 状态码 | 含义 | Agent 处理 |
|------------|------|-----------|
| 200 | 成功 | 正常返回 |
| 400 | 请求参数错误 | 告诉用户"参数错误" |
| 401 | Token 过期/无效 | 告诉用户"请重新登录" |
| 403 | 权限不足 | 告诉用户"权限不足，需要 {role}" |
| 404 | 资源不存在 | 告诉用户"资源不存在" |
| 429 | 限流 | 等待 Retry-After 后重试 |
| 5xx | 服务端错误 | 重试 1 次，仍失败则告诉用户"系统暂时不可用" |
| 超时 | 网络超时 | 重试 1 次，仍失败则告诉用户"连接超时" |

### 3.2 统一错误处理

```python
# agent/adapters/sentinelmind/client.py
class VisionAgentError(Exception):
    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail

class TokenExpiredError(VisionAgentError):
    pass

class PermissionDeniedError(VisionAgentError):
    pass

async def request_with_retry(client, method, path, max_retries=1, **kwargs):
    """统一的请求+重试+错误处理"""
    for attempt in range(max_retries + 1):
        try:
            resp = await client.request(method, path, **kwargs)
            return resp
        except httpx.TimeoutException:
            if attempt < max_retries:
                await asyncio.sleep(1)
                continue
            raise VisionAgentError(0, "连接超时，请检查网络")
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 500 and attempt < max_retries:
                await asyncio.sleep(2)
                continue
            if e.response.status_code == 401:
                raise TokenExpiredError(401, "Token 已过期，请重新登录")
            if e.response.status_code == 403:
                raise PermissionDeniedError(403, "权限不足")
            raise VisionAgentError(e.response.status_code, e.response.text)
```

### 3.3 Skill 层错误处理

```python
# agent/skills/base.py
async def execute_skill(skill_graph, input_message, config):
    """Skill 执行包装器，统一处理错误"""
    try:
        result = await skill_graph.ainvoke(
            {"messages": [HumanMessage(content=input_message)]},
            config=config,
        )
        return result["messages"][-1].content
    except TokenExpiredError as e:
        return f"❌ {e.detail}"
    except PermissionDeniedError as e:
        return f"❌ {e.detail}"
    except VisionAgentError as e:
        return f"❌ 系统错误：{e.detail}"
    except Exception as e:
        logger.error("skill_execution_failed error=%s", str(e))
        return "❌ 系统暂时不可用，请稍后重试"
```

---

## 四、Skills 与主 Agent 的状态传递

### 4.1 Skill 输出格式

Skill 子图执行完毕后，将结果作为 `HumanMessage` 注入主 graph 的 messages：

```python
# agent/core/graph.py
async def skill_node(state: AgentState) -> dict:
    """执行 Skill 子图，将结果写回主 graph"""
    skill_name = state.get("active_skill")
    skill_graph = SKILLS[skill_name]["graph"]

    result = await execute_skill(
        skill_graph,
        state["messages"][-1].content,
        config={"configurable": {"thread_id": f"skill_{skill_name}_{uuid4().hex[:8]}"}},
    )

    # 将 Skill 结果作为 HumanMessage 注入主 graph
    return {
        "messages": [HumanMessage(content=f"[Skill 结果] {result}")],
        "active_skill": None,  # 清除 skill 激活状态
    }
```

### 4.2 Skill 参数传递

Skill 参数从 `state["skill_params"]` 读取：

```python
# 排障 Skill 示例
async def diagnose_node(state: AgentState) -> dict:
    camera_id = state.get("skill_params", {}).get("camera_id")
    if not camera_id:
        # 从用户消息中提取
        user_input = state["messages"][-1].content
        camera_id = extract_camera_id(user_input)

    # 执行诊断...
```

### 4.3 Skill 结果格式规范

每个 Skill 必须返回结构化的 Markdown：

```markdown
## {Skill 名称} — {timestamp}

### {检查项 1}
- 状态：✅ 正常 / ⚠️ 警告 / ❌ 异常
- 详情：...

### {检查项 2}
...

### 总结
{一句话总结}

### 建议
1. {建议 1}
2. {建议 2}
```

---

## 五、调度器决策队列持久化

### 5.1 问题

调度器的 `_enqueue_decision` / `_dequeue_decision` 没有持久化。Agent 重启后待决策队列丢失。

### 5.2 方案

决策队列存入 `agent_memory.db`（与 checkpointer 同库）：

```sql
CREATE TABLE IF NOT EXISTS pending_decisions (
    token       TEXT PRIMARY KEY,
    issue_json  TEXT NOT NULL,
    created_at  REAL NOT NULL,
    expires_at  REAL NOT NULL,
    status      TEXT DEFAULT 'pending'  -- pending / confirmed / rejected / expired
);
```

### 5.3 实现

```python
# agent/scheduler.py
import sqlite3
import json
import time

class DecisionQueue:
    """持久化的决策队列"""

    def __init__(self, db_path: str = "data/agent_memory.db"):
        self.conn = sqlite3.connect(db_path)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS pending_decisions (
                token TEXT PRIMARY KEY,
                issue_json TEXT NOT NULL,
                created_at REAL NOT NULL,
                expires_at REAL NOT NULL,
                status TEXT DEFAULT 'pending'
            )
        """)
        self.conn.commit()

    def enqueue(self, token: str, issue: dict, ttl: int = 3600) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO pending_decisions (token, issue_json, created_at, expires_at, status) VALUES (?, ?, ?, ?, ?)",
            (token, json.dumps(issue), time.time(), time.time() + ttl, "pending"),
        )
        self.conn.commit()

    def dequeue(self, token: str) -> dict | None:
        row = self.conn.execute(
            "SELECT issue_json FROM pending_decisions WHERE token = ? AND status = 'pending' AND expires_at > ?",
            (token, time.time()),
        ).fetchone()
        if row:
            self.conn.execute(
                "UPDATE pending_decisions SET status = 'confirmed' WHERE token = ?",
                (token,),
            )
            self.conn.commit()
            return json.loads(row[0])
        return None

    def cleanup_expired(self) -> int:
        cur = self.conn.execute(
            "DELETE FROM pending_decisions WHERE expires_at < ?",
            (time.time(),),
        )
        self.conn.commit()
        return cur.rowcount
```

### 5.4 调度器集成

```python
class PatrolScheduler:
    def __init__(self, patrol_skill, report_skill, notifier, db_path="data/agent_memory.db"):
        self.decision_queue = DecisionQueue(db_path)
        # ...

    async def _notify_issues(self, issues: list[dict], issue_type: str):
        for issue in issues:
            token = f"{issue_type}_{uuid4().hex[:8]}"
            self.decision_queue.enqueue(token, issue)
            issue["decision_token"] = token
        await self.notifier.send_patrol_alert(issues)
```

---

## 六、部署架构

### 6.1 部署模式

Agent 独立进程，通过 REST API 对接 SentinelMind：

```
┌─────────────────────────────────┐
│         Agent 进程               │
│  FastAPI (port 8081)            │
│  + LangGraph Agent              │
│  + APScheduler                  │
│  + agent_memory.db              │
└───────────┬─────────────────────┘
            │ HTTP (Bearer Token)
            ▼
┌─────────────────────────────────┐
│     SentinelMind 进程            │
│  FastAPI (port 8080)            │
│  + Pipeline + 规则引擎           │
│  + sentinelmind.db              │
└─────────────────────────────────┘
```

### 6.2 端口分配

| 服务 | 端口 | 说明 |
|------|------|------|
| SentinelMind | 8080 | 主服务 |
| Agent | 8081 | Agent 服务 |

### 6.3 启动顺序

1. 启动 SentinelMind（`python -m sentinelmind`）
2. 启动 Agent（`python -m agent`）

Agent 启动时会尝试连接 SentinelMind，连接失败则进入降级模式（仅通用模式可用）。

### 6.4 systemd 配置

```ini
# /etc/systemd/system/sentinelmind-agent.service
[Unit]
Description=SentinelMind - LangChain Agent
After=network.target sentinelmind.service

[Service]
Type=simple
User=vagent
WorkingDirectory=/opt/sentinelmind
ExecStart=/opt/sentinelmind/venv/bin/python -m agent --config configs/agent.yaml
Restart=always
RestartSec=5
Environment=AGENT_SERVICE_TOKEN=xxx

[Install]
WantedBy=multi-user.target
```

---

## 七、配置管理

### 7.1 Agent 配置文件

```yaml
# configs/agent.yaml
version: 1

# LLM 配置（Agent 自己的，不依赖 SentinelMind）
llm:
  model: "gpt-4o"
  api_key: "${AGENT_LLM_API_KEY}"
  base_url: "https://api.openai.com/v1"
  temperature: 0.3

# 路由 LLM（轻量模型，用于意图识别）
router:
  model: "gpt-4o-mini"
  api_key: "${AGENT_LLM_API_KEY}"

# SentinelMind 适配器
adapters:
  sentinelmind:
    base_url: "http://localhost:8080"
    timeout: 10
    service_token: "${AGENT_SERVICE_TOKEN}"

# 记忆配置
memory:
  db_path: "data/agent_memory.db"
  buffer_window: 20
  summary_max_tokens: 300

# 调度器配置
scheduler:
  patrol_interval: 300      # 5 分钟
  trend_interval: 3600      # 1 小时
  daily_report_time: "09:00"
  decision_ttl: 3600        # 决策队列过期时间

# 通知配置（Agent 自己的，可复用 SentinelMind 的 webhook）
notification:
  webhook:
    enabled: true
    url: "${AGENT_WEBHOOK_URL}"

# MCP 通用工具配置
mcp:
  servers:
    weather:
      command: "python"
      args: ["-m", "mcp_server_weather"]
```

### 7.2 与 SentinelMind 配置的关系

| 配置 | SentinelMind | Agent | 说明 |
|------|-------------|-------|------|
| LLM API Key | `llm.api_key` | `llm.api_key` | 可复用同一个 key |
| Webhook URL | `notification.webhook.url` | `notification.webhook.url` | 可复用同一个 webhook |
| 数据库 | `storage.sqlite.path` | `memory.db_path` | 各自独立 |
| 端口 | `web.port` | `web.port` | 不同端口 |

---

## 八、Web Chat 接口

### 8.1 API 设计

```
POST /api/chat
  - 请求：{ "message": "...", "thread_id": "..." }
  - 响应：SSE 流式

GET /api/chat/threads
  - 列出所有对话线程

DELETE /api/chat/threads/{thread_id}
  - 删除对话线程
```

### 8.2 SSE 流式响应格式

```
data: {"type": "token", "content": "正在"}
data: {"type": "token", "content": "分析"}
data: {"type": "token", "content": "..."}
data: {"type": "tool_call", "name": "list_cameras", "args": {}}
data: {"type": "tool_result", "name": "list_cameras", "result": "..."}
data: {"type": "message", "content": "完整回复内容"}
data: [DONE]
```

### 8.3 FastAPI 实现

```python
# agent/web.py
from fastapi import FastAPI, Depends
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage
import json

app = FastAPI(title="SentinelMind Chat API")

@app.post("/api/chat")
async def chat(request: ChatRequest, user=Depends(get_current_user)):
    """SSE 流式聊天"""

    async def generate():
        config = {"configurable": {"thread_id": request.thread_id}}

        async for event in graph.astream_events(
            {"messages": [HumanMessage(content=request.message)]},
            config=config,
            version="v2",
        ):
            if event["event"] == "on_chat_model_stream":
                token = event["data"]["chunk"].content
                yield f"data: {json.dumps({'type': 'token', 'content': token})}\n\n"
            elif event["event"] == "on_tool_start":
                yield f"data: {json.dumps({'type': 'tool_call', 'name': event['name'], 'args': event['data'].get('input', {})})}\n\n"
            elif event["event"] == "on_tool_end":
                yield f"data: {json.dumps({'type': 'tool_result', 'name': event['name'], 'result': str(event['data'].get('output', ''))[:500]})}\n\n"

        yield "data: [DONE]\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")
```

### 8.4 前端集成

**文件结构**（预留工具模块）：
```
frontend/src/
├── api/
│   ├── chat.ts              # Chat API（SSE 流式）
│   └── tools.ts             # 工具 API（列表/测试/配置）
│
├── composables/
│   ├── useChat.ts           # Chat 组合式函数
│   └── useTools.ts          # 工具管理组合式函数
│
├── stores/
│   └── chat.ts              # Chat 状态管理
│
├── components/
│   ├── chat/                # 聊天组件
│   │   ├── ChatWindow.vue   # 聊天浮窗
│   │   ├── MessageBubble.vue # 消息气泡
│   │   └── ToolCallCard.vue # 工具调用卡片
│   │
│   └── tools/               # 工具模块组件（预留）
│       ├── ToolList.vue     # 工具列表
│       ├── ToolDetail.vue   # 工具详情
│       ├── ToolTest.vue     # 工具测试/调试
│       └── MCPConfig.vue    # MCP 服务器配置
│
└── views/
    └── agent/               # Agent 页面（预留）
        ├── Chat.vue         # 聊天页面
        └── Tools.vue        # 工具管理页面
```

### 8.5 工具模块设计（预留）

**功能定位**：Agent 工具的可视化管理和调试界面。

**页面结构**：
```
┌─────────────────────────────────────────────────────┐
│ 工具管理                                             │
├─────────────────────────────────────────────────────┤
│                                                     │
│ ┌─ 专业工具（SentinelMind API）────────────────────┐ │
│ │ 📷 list_cameras      GET /api/cameras    ✅ 正常  │ │
│ │ 🚨 query_alerts      GET /api/alerts    ✅ 正常  │ │
│ │ 🏥 system_health     GET /health         ✅ 正常  │ │
│ │ 📊 get_system_stats  GET /api/stats      ✅ 正常  │ │
│ │ ⚙️ get_config        GET /api/config     ⚠️ 403  │ │
│ │ 📋 list_rules        GET /api/rules      ✅ 正常  │ │
│ └──────────────────────────────────────────────────┘ │
│                                                     │
│ ┌─ 通用工具（MCP）────────────────────────────────┐ │
│ │ 🌤️ weather           MCP Server          ✅ 正常  │ │
│ │ 🌐 puppeteer         MCP Server          ⚠️ 离线  │ │
│ │ 📁 filesystem        MCP Server          ✅ 正常  │ │
│ └──────────────────────────────────────────────────┘ │
│                                                     │
│ [测试选中工具]  [刷新状态]  [MCP 配置]                │
└─────────────────────────────────────────────────────┘
```

**API 设计**：
```
GET /api/agent/tools              # 列出所有工具及状态
GET /api/agent/tools/{name}       # 工具详情（参数 schema、描述）
POST /api/agent/tools/{name}/test # 测试工具调用
GET /api/agent/mcp/servers        # MCP 服务器列表
PUT /api/agent/mcp/servers/{name} # 更新 MCP 服务器配置
```

**工具列表数据结构**：
```typescript
interface ToolInfo {
  name: string              // 工具名称
  description: string       // 工具描述
  type: 'sentinelmind' | 'mcp'  // 工具类型
  status: 'online' | 'offline' | 'error'
  parameters: object        // JSON Schema
  last_used?: number        // 最后使用时间
  call_count?: number       // 调用次数
}
```

**前端组件**：
```vue
<!-- frontend/src/components/tools/ToolList.vue -->
<template>
  <div class="tool-list">
    <el-card v-for="tool in tools" :key="tool.name" shadow="hover">
      <div class="tool-header">
        <span class="tool-icon">{{ toolIcon(tool.type) }}</span>
        <span class="tool-name">{{ tool.name }}</span>
        <el-tag :type="tool.status === 'online' ? 'success' : 'danger'" size="small">
          {{ tool.status }}
        </el-tag>
      </div>
      <div class="tool-desc">{{ tool.description }}</div>
      <div class="tool-actions">
        <el-button size="small" @click="testTool(tool)">测试</el-button>
        <el-button size="small" @click="viewDetail(tool)">详情</el-button>
      </div>
    </el-card>
  </div>
</template>
```

---

## 九、CLI 入口

### 9.1 命令结构

```bash
python -m agent [command] [options]

Commands:
  chat        交互式聊天（默认）
  patrol      执行一次巡检
  report      生成日报
  diagnose    排障指定摄像头
  serve       启动 Web 服务（REST API + Web Chat）

Options:
  --config    配置文件路径（默认 configs/agent.yaml）
  --mode      模式：auto | professional | general（默认 auto）
  --token     SentinelMind Token（覆盖配置文件）
```

### 9.2 实现

```python
# agent/__main__.py
import typer
from rich.console import Console
from rich.markdown import Markdown

app = typer.Typer(help="SentinelMind 智能助手")
console = Console()

@app.command()
def chat(
    config: str = typer.Option("configs/agent.yaml", help="配置文件路径"),
    mode: str = typer.Option("auto", help="模式：auto/professional/general"),
):
    """交互式聊天"""
    agent = create_agent(config)
    console.print("[bold green]SentinelMind 智能助手[/bold green]")
    console.print("输入 /help 查看帮助，/quit 退出\n")

    while True:
        user_input = console.input("[bold blue]You:[/bold blue] ")
        if user_input.strip() == "/quit":
            break

        result = agent.invoke(user_input, mode=mode)
        console.print(Markdown(result))

@app.command()
def patrol(config: str = typer.Option("configs/agent.yaml")):
    """执行一次巡检"""
    agent = create_agent(config)
    result = agent.invoke("/巡检", mode="professional")
    console.print(Markdown(result))

@app.command()
def serve(
    config: str = typer.Option("configs/agent.yaml"),
    port: int = typer.Option(8081, help="服务端口"),
):
    """启动 Web 服务"""
    import uvicorn
    from agent.web import create_app
    app = create_app(config)
    uvicorn.run(app, host="0.0.0.0", port=port)
```

---

## 十、测试策略

### 10.1 测试分层

| 层级 | 测试内容 | Mock 策略 |
|------|---------|----------|
| 单元测试 | 工具函数、记忆管理、路由逻辑 | Mock httpx、Mock LLM |
| 集成测试 | Skill 执行、Graph 流程 | Mock SentinelMind API |
| 端到端测试 | 完整对话流程 | 真实 LLM + Mock API |

### 10.2 Mock LLM

```python
# tests/conftest.py
from langchain_core.language_models import FakeListChatModel

@pytest.fixture
def mock_llm():
    return FakeListChatModel(responses=[
        "professional",  # 路由结果
        "cam_01 在线，cam_02 离线",  # 巡检结果
    ])
```

### 10.3 Mock SentinelMind API

```python
# tests/conftest.py
import pytest
from unittest.mock import AsyncMock, patch

@pytest.fixture
def mock_sentinelmind_api():
    with patch("agent.adapters.sentinelmind.client.VisionAgentClient") as mock:
        mock.return_value.list_cameras = AsyncMock(return_value=[
            {"camera_id": "cam_01", "status": "connected"},
            {"camera_id": "cam_02", "status": "disconnected"},
        ])
        mock.return_value.system_health = AsyncMock(return_value={
            "status": "ok", "gpu_utilization": 0.3,
        })
        yield mock
```

### 10.4 测试用例

```python
# tests/test_patrol_skill.py
import pytest
from agent.skills.patrol import create_patrol_skill

@pytest.mark.asyncio
async def test_patrol_normal(mock_llm, mock_sentinelmind_api):
    """正常巡检：所有系统正常"""
    skill = create_patrol_skill(mock_llm)
    result = await skill.ainvoke({"messages": [HumanMessage(content="执行巡检")]})
    assert "✅" in result["messages"][-1].content

@pytest.mark.asyncio
async def test_patrol_with_issues(mock_llm, mock_sentinelmind_api):
    """巡检发现问题：cam_02 离线"""
    mock_sentinelmind_api.return_value.list_cameras = AsyncMock(return_value=[
        {"camera_id": "cam_01", "status": "connected"},
        {"camera_id": "cam_02", "status": "disconnected"},
    ])
    skill = create_patrol_skill(mock_llm)
    result = await skill.ainvoke({"messages": [HumanMessage(content="执行巡检")]})
    assert "cam_02" in result["messages"][-1].content
    assert "离线" in result["messages"][-1].content
```

---

## 十一、日志设计

### 11.1 日志格式

```
2026-07-14 10:30:15 INFO  [agent] chat_started thread_id=abc123 mode=professional
2026-07-14 10:30:16 INFO  [agent] tool_call name=list_cameras duration_ms=45
2026-07-14 10:30:17 INFO  [agent] chat_completed thread_id=abc123 tokens=150
2026-07-14 10:35:00 INFO  [scheduler] patrol_started
2026-07-14 10:35:02 INFO  [scheduler] patrol_completed issues=0
2026-07-14 10:35:00 ERROR [scheduler] patrol_failed error=connection_timeout
```

### 11.2 日志级别

| 级别 | 用途 |
|------|------|
| DEBUG | 工具调用参数、LLM 原始响应 |
| INFO | 对话开始/结束、Skill 执行、调度任务 |
| WARNING | 工具调用重试、Token 即将过期 |
| ERROR | 工具调用失败、Skill 执行异常 |

### 11.3 日志配置

```python
# agent/__init__.py
import logging

logger = logging.getLogger("agent")
logger.setLevel(logging.INFO)

# 文件 handler
handler = RotatingFileHandler(
    "logs/agent.log",
    maxBytes=50 * 1024 * 1024,
    backupCount=5,
)
handler.setFormatter(logging.Formatter(
    "%(asctime)s %(levelname)-5s [%(name)s] %(message)s"
))
logger.addHandler(handler)
```

---

## 十二、基础设施设计

### 12.1 用户管理

**原则**：Agent 不维护自己的用户系统，复用 SentinelMind 的用户认证。

**Token 透传**：
```
用户登录 SentinelMind → 获得 Bearer Token
用户访问 Agent Web Chat → 携带同一个 Token
Agent 调用 SentinelMind API → 透传用户 Token（或使用 Service Token）
```

**权限映射**：

| SentinelMind 角色 | Agent 权限 | 可用 Skills |
|------------------|-----------|------------|
| admin | 全部 | /巡检 /排障 /日报 /规则 |
| operator | 操作 | /巡检 /排障 /日报 |
| viewer | 只读 | /巡检 /日报（只读） |

**实现**：
```python
# agent/auth.py
async def get_current_user(request: Request) -> User:
    """从请求中提取 Token 并验证"""
    token = extract_bearer_token(request)
    if not token:
        raise HTTPException(401, "请先登录")

    # 方式1：透传给 SentinelMind 验证
    user_info = await sentinelmind_client.verify_token(token)
    if not user_info:
        raise HTTPException(401, "Token 无效或已过期")

    return User(
        username=user_info["username"],
        role=user_info["role"],
        token=token,
    )

def require_role(min_role: str):
    """权限检查装饰器"""
    role_order = {"viewer": 1, "operator": 2, "admin": 3}
    def checker(user: User = Depends(get_current_user)):
        if role_order.get(user.role, 0) < role_order.get(min_role, 0):
            raise HTTPException(403, f"需要 {min_role} 或更高权限")
        return user
    return checker
```

### 12.2 审计日志

**原则**：Agent 的所有操作记录到审计日志，与 SentinelMind 共享审计表结构。

**记录内容**：

| 操作 | 记录字段 |
|------|---------|
| Skill 执行 | username, action="skill.execute", resource="{skill_name}" |
| 工具调用 | username, action="tool.call", resource="{tool_name}", details="{args}" |
| 配置变更 | username, action="config.update", resource="{key}", details="{old}→{new}" |
| MCP 服务器操作 | username, action="mcp.{add\|remove\|update}", resource="{server_name}" |

**实现**：
```python
# agent/audit.py
from datetime import datetime
import sqlite3
import json

class AgentAuditLogger:
    """Agent 审计日志（写入 SentinelMind 的 audit_logs 表）"""

    def __init__(self, db_path: str = "data/agent_memory.db"):
        self.conn = sqlite3.connect(db_path)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                role TEXT DEFAULT '',
                action TEXT NOT NULL,
                resource TEXT DEFAULT '',
                details TEXT DEFAULT '',
                ip TEXT DEFAULT '',
                created_at REAL NOT NULL
            )
        """)
        self.conn.commit()

    def log(self, username: str, role: str, action: str,
            resource: str = "", details: str = "", ip: str = ""):
        self.conn.execute(
            "INSERT INTO audit_logs (username, role, action, resource, details, ip, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (username, role, action, resource, details, ip, datetime.now().timestamp()),
        )
        self.conn.commit()
```

**Skill 执行审计**：
```python
# agent/skills/base.py
async def execute_skill(skill_graph, input_message, config, audit_logger, user):
    """Skill 执行包装器，记录审计日志"""
    audit_logger.log(
        username=user.username,
        role=user.role,
        action="skill.execute",
        resource=skill_name,
        details=f"input={input_message[:100]}",
    )
    result = await skill_graph.ainvoke(...)
    audit_logger.log(
        username=user.username,
        role=user.role,
        action="skill.complete",
        resource=skill_name,
        details=f"result_length={len(result)}",
    )
    return result
```

### 12.3 系统监控

**Agent 健康检查**：`GET /health`

```json
{
  "status": "ok",
  "version": "1.0.0",
  "uptime_seconds": 3600,
  "active_threads": 3,
  "llm_status": {
    "model": "gpt-4o",
    "last_call_ms": 1200,
    "calls_today": 45,
    "errors_today": 2
  },
  "scheduler": {
    "patrol_next_run": "2026-07-14T10:35:00",
    "report_next_run": "2026-07-15T09:00:00",
    "pending_decisions": 2
  },
  "memory": {
    "db_size_mb": 12.5,
    "active_threads": 5,
    "buffer_window": 20
  },
  "adapters": {
    "sentinelmind": {
      "status": "connected",
      "latency_ms": 45,
      "last_error": null
    },
    "mcp_servers": {
      "weather": "online",
      "puppeteer": "offline"
    }
  }
}
```

**实现**：
```python
# agent/web.py
@app.get("/health")
async def health():
    return {
        "status": "ok",
        "version": __version__,
        "uptime_seconds": time.time() - start_time,
        "llm_status": get_llm_stats(),
        "scheduler": get_scheduler_stats(),
        "memory": get_memory_stats(),
        "adapters": get_adapter_stats(),
    }
```

### 12.4 配置管理

**配置热重载**：Agent 配置支持热重载（不重启进程）。

| 配置项 | 热重载 | 说明 |
|--------|--------|------|
| `llm.model` | ❌ | 需要重建 Agent Graph |
| `llm.temperature` | ✅ | 下次调用生效 |
| `scheduler.patrol_interval` | ✅ | 调度器动态调整 |
| `adapters.sentinelmind.base_url` | ✅ | 适配器重新连接 |
| `mcp.servers` | ✅ | 重新加载 MCP 工具 |

**API**：
```
GET  /api/agent/config              # 获取 Agent 配置（脱敏）
PUT  /api/agent/config              # 更新配置（仅 admin）
POST /api/agent/config/reload       # 重新加载配置文件
```

### 12.5 Agent 管理页面（前端）

**侧边栏入口**：
```
📂 Agent 管理
  ├── 💬 聊天        ← /agent/chat
  ├── 🔧 工具管理    ← /agent/tools
  ├── ⏰ 调度任务    ← /agent/scheduler
  ├── 📋 审计日志    ← /agent/audit
  └── ⚙️ Agent 配置  ← /agent/config
```

**调度任务页面**：
```
┌─────────────────────────────────────────────────────┐
│ 调度任务                                            │
├─────────────────────────────────────────────────────┤
│                                                     │
│ 🏥 健康巡检        每 5 分钟    下次: 10:35:00     │
│    最近执行: 10:30:00 ✅ 正常                       │
│                                                     │
│ 📊 告警趋势分析    每小时       下次: 11:00:00      │
│    最近执行: 10:00:00 ✅ 无异常激增                  │
│                                                     │
│ 📋 日报            每天 09:00   下次: 明天 09:00     │
│    最近执行: 今天 09:00 ✅ 已推送                    │
│                                                     │
│ [暂停全部]  [立即执行巡检]                            │
└─────────────────────────────────────────────────────┘
```

**Agent 配置页面**：
```
┌─────────────────────────────────────────────────────┐
│ Agent 配置                                          │
├─────────────────────────────────────────────────────┤
│                                                     │
│ LLM 模型                                            │
│   模型: gpt-4o                    [修改]             │
│   温度: 0.3                       [修改]             │
│   今日调用: 45 次  错误: 2 次                       │
│                                                     │
│ 调度器                                              │
│   巡检间隔: 5 分钟                [修改]             │
│   日报时间: 09:00                 [修改]             │
│   待决策队列: 2 条                                  │
│                                                     │
│ SentinelMind 连接                                   │
│   地址: http://localhost:8080                       │
│   状态: ✅ 延迟 45ms                                │
│                                                     │
│ MCP 工具                                            │
│   weather: ✅ 在线                                  │
│   puppeteer: ❌ 离线                                │
│   filesystem: ✅ 在线                               │
│                                                     │
│ [保存配置]  [重载配置]  [查看审计日志]                │
└─────────────────────────────────────────────────────┘
```

---

## 十三、实施计划

| 阶段 | 内容 | 工作量 |
|------|------|--------|
| Phase 1 | 项目结构 + 核心 Graph + 路由 | 2 天 |
| Phase 2 | 记忆体系（4 层） | 1 天 |
| Phase 3 | 适配器层（SentinelMind API 工具） | 1 天 |
| Phase 4 | Skills（巡检 + 排障 + 日报 + 规则） | 2 天 |
| Phase 5 | 调度器 + 决策队列 | 1 天 |
| Phase 6 | 基础设施（用户管理 + 审计 + 监控 + 配置） | 1.5 天 |
| Phase 7 | Web Chat API + SSE | 1 天 |
| Phase 8 | 前端（Chat + 工具管理 + 调度任务 + 配置） | 2 天 |
| Phase 9 | CLI 入口 | 0.5 天 |
| Phase 10 | 测试 | 1 天 |
| Phase 6 | Web Chat API + SSE | 1 天 |
| Phase 7 | CLI 入口 | 0.5 天 |
| Phase 8 | 测试 | 1 天 |
| Phase 9 | 配置 + 部署 | 0.5 天 |

**总计**：约 10 天

---

## 十三、依赖清单

```
langgraph>=0.2.0
langchain>=0.3.0
langchain-community>=0.3.0
langchain-mcp-adapters>=0.1.0
apscheduler>=3.10.0
httpx>=0.27.0
pydantic>=2.0.0
typer>=0.9.0
rich>=13.0.0
uvicorn>=0.30.0
fastapi>=0.111.0
sse-starlette>=2.0.0
```
