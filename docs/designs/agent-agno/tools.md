# 工具设计（Agno 版）

## 说明

**适配器层（`adapters/sentinelmind/`）的代码 LangChain 版和 Agno 版完全复用。** 工具函数只依赖 `httpx` + Pydantic，不依赖任何 Agent 框架。

两个版本唯一的区别是**工具注册方式**：

```python
# LangChain
from langchain_core.tools import StructuredTool
tool = StructuredTool.from_function(coroutine=fn, name="...", description="...")

# Agno
from agno.tools import tool

@tool
async def fn(...) -> str:
    ...
```

## 架构

```
┌─────────────────────────────────────────────┐
│                  Agent (Agno)                 │
│                                               │
│  ┌──────────────────────────────────────┐    │
│  │         adapters/ (适配器层)           │    │
│  │                                       │    │
│  │  ┌─────────────────┐  ┌────────────┐ │    │
│  │  │ sentinelmind/    │  │ mcp/       │ │    │
│  │  │ (REST API 调用)  │  │ (MCP 协议) │ │    │
│  │  └────────┬────────┘  └────────────┘ │    │
│  └───────────┼──────────────────────────┘    │
└──────────────┼───────────────────────────────┘
               │ HTTP (Bearer Token 透传)
               ▼
┌──────────────────────────────────────────────┐
│              SentinelMind                     │
│  REST API（自身完成 RBAC 校验）                 │
└──────────────────────────────────────────────┘
```

## 一、摄像头工具

```python
# agent_agno/adapters/sentinelmind/camera_tools.py
from agno.tools import tool
from pydantic import BaseModel, Field
import httpx
import os

BASE_URL = os.getenv("VISION_AGENT_URL", "http://localhost:8080")

class ToggleCameraInput(BaseModel):
    camera_id: str = Field(..., description="摄像头 ID")
    action: str = Field(..., description="'on' 启用 或 'off' 停用")

class AddCameraInput(BaseModel):
    id: str = Field(..., description="摄像头 ID")
    name: str = Field(..., description="摄像头名称")
    rtsp_url: str = Field(..., description="RTSP 流地址")
    fps: int = Field(default=0, description="帧率，0 表示自动")

@tool
async def list_cameras(token: str = "") -> str:
    """列出所有摄像头及其在线状态、FPS、今日告警数。"""
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=10.0) as client:
        headers = {"Authorization": f"Bearer {token}"}
        resp = await client.get("/api/cameras", headers=headers)
        resp.raise_for_status()
        data = resp.json()

    if not data:
        return "当前没有配置任何摄像头。使用 add_camera 添加。"

    lines = []
    for c in data:
        icon = "🟢" if c["status"] == "online" else "🔴" if c["status"] == "offline" else "🟡"
        lines.append(
            f"{icon} **{c['id']}** ({c.get('name', '')}) | {c['status']} | "
            f"{c.get('fps', 0)}fps | 今日告警{c.get('alerts_today', 0)}条"
        )
    return "\n".join(lines)

@tool
async def toggle_camera(token: str, camera_id: str, action: str) -> str:
    """启用或停用摄像头。action 为 'on' 或 'off'。需要 operator 以上权限。"""
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=10.0) as client:
        headers = {"Authorization": f"Bearer {token}"}
        resp = await client.post(f"/api/cameras/{camera_id}/toggle", headers=headers)
        if resp.status_code == 403:
            return "❌ 权限不足，需要 operator 以上权限"
        resp.raise_for_status()
    return f"{'✅' if action == 'on' else '⏸️'} {camera_id} 已{'启动' if action == 'on' else '停止'}"

@tool
async def add_camera(token: str, id: str, name: str, rtsp_url: str,
                     fps: int = 0, width: int = 1920, height: int = 1080) -> str:
    """添加新摄像头。需要 admin 权限。"""
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=10.0) as client:
        headers = {"Authorization": f"Bearer {token}"}
        resp = await client.post("/api/cameras", headers=headers, json={
            "id": id, "name": name, "rtsp_url": rtsp_url,
            "fps": fps, "width": width, "height": height,
        })
        if resp.status_code == 403:
            return "❌ 权限不足，需要 admin 权限"
        resp.raise_for_status()
    return f"✅ 摄像头 {id} ({name}) 已添加"
```

## 二、告警工具

```python
# agent_agno/adapters/sentinelmind/alert_tools.py
from agno.tools import tool

@tool
async def query_alerts(token: str, status: str = "pending", camera_id: str | None = None,
                       severity: str | None = None, limit: int = 20) -> str:
    """查询告警列表。可按状态/摄像头/严重级别筛选。limit 范围 1-100。"""
    params = {"status": status, "limit": limit}
    if camera_id: params["camera_id"] = camera_id
    if severity: params["severity"] = severity

    async with httpx.AsyncClient(base_url=BASE_URL, timeout=10.0) as client:
        headers = {"Authorization": f"Bearer {token}"}
        resp = await client.get("/api/alerts", headers=headers, params=params)
        resp.raise_for_status()
        data = resp.json()

    alerts = data.get("items", [])
    if not alerts:
        return "没有匹配的告警。"

    lines = [f"共 {data.get('total', len(alerts))} 条告警：\n"]
    for a in alerts:
        lines.append(
            f"• `{a['id'][:8]}` | {a['event_type']} | {a['camera_name']} | "
            f"{a['severity']} | {a['status']}"
        )
    return "\n".join(lines)

@tool
async def acknowledge_alert(token: str, alert_id: str, acknowledge_by: str = "operator") -> str:
    """确认一条待处理告警。需要 operator 以上权限。"""
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=10.0) as client:
        headers = {"Authorization": f"Bearer {token}"}
        resp = await client.put(
            f"/api/alerts/{alert_id}/status", headers=headers,
            json={"status": "acknowledged", "acknowledged_by": acknowledge_by},
        )
        if resp.status_code == 404:
            return f"❌ 告警 {alert_id} 不存在"
        if resp.status_code == 403:
            return "❌ 权限不足，需要 operator 以上权限"
        resp.raise_for_status()
    return f"✅ 告警 {alert_id[:8]} 已确认"

@tool
async def alert_detail(token: str, alert_id: str) -> str:
    """查看某条告警的完整详情，包含 LLM 分析结果和截图。"""
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=10.0) as client:
        headers = {"Authorization": f"Bearer {token}"}
        resp = await client.get(f"/api/alerts/{alert_id}", headers=headers)
        resp.raise_for_status()
        alert = resp.json()

    parts = [
        f"## 告警 {alert['id'][:8]}",
        f"- 类型：{alert['event_type']}",
        f"- 摄像头：{alert.get('camera_name', '-')}",
        f"- 规则：{alert.get('rule_name', '-')}",
        f"- 严重级别：{alert.get('severity', '-')}",
        f"- 状态：{alert.get('status', '-')}",
    ]
    llm = alert.get("llm_analysis")
    if llm:
        parts.append(f"\n### LLM 分析\n{llm.get('description', '')}")
        parts.append(f"风险等级：{llm.get('risk_level', '')}")
        parts.append(f"建议：{llm.get('suggestion', '')}")
    return "\n".join(parts)
```

## 三、系统工具

```python
# agent_agno/adapters/sentinelmind/system_tools.py
from agno.tools import tool

@tool
async def system_health() -> str:
    """获取系统完整健康状态：GPU、延迟、队列深度、运行状态。"""
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=10.0) as client:
        resp = await client.get("/health")
        health = resp.json()

    status_icon = "✅" if health.get("status") == "healthy" else "⚠️"
    parts = [f"## 系统健康 {status_icon}\n"]
    parts.append("| 指标 | 值 |")
    parts.append("|---|---|")
    for k, v in health.items():
        parts.append(f"| {k} | {v} |")
    return "\n".join(parts)

@tool
async def get_system_stats(token: str, period: str = "today") -> str:
    """获取系统统计数据。period 可选 today/7d/30d。"""
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=10.0) as client:
        headers = {"Authorization": f"Bearer {token}"}
        resp = await client.get("/api/stats", headers=headers, params={"period": period})
        resp.raise_for_status()
        stats = resp.json()

    parts = [f"## 统计 ({period})\n"]
    for k, v in stats.items():
        parts.append(f"- **{k}**: {v}")
    return "\n".join(parts)

@tool
async def get_config(token: str) -> str:
    """查看当前系统配置（密码/token 已脱敏）。需要 admin 权限。"""
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=10.0) as client:
        headers = {"Authorization": f"Bearer {token}"}
        resp = await client.get("/api/config", headers=headers)
        if resp.status_code == 403:
            return "❌ 权限不足，需要 admin 权限"
        resp.raise_for_status()
        import yaml
        return f"```yaml\n{yaml.dump(resp.json())}\n```"
```

## 四、规则工具（待 SentinelMind 新增 REST 端点）

| 端点 | 功能 | 状态 |
|---|---|---|
| GET /api/rules | 列出所有规则 | 待新增 |
| GET /api/rules/{name} | 查看规则详情 | 待新增 |
| POST /api/rules | 创建规则 | 待新增 |
| DELETE /api/rules/{name} | 删除规则 | 待新增 |

## 工具注册

```python
# agent_agno/app.py（节选）
from agent_agno.adapters.sentinelmind.camera_tools import list_cameras, toggle_camera, add_camera
from agent_agno.adapters.sentinelmind.alert_tools import query_alerts, acknowledge_alert, alert_detail
from agent_agno.adapters.sentinelmind.system_tools import system_health, get_system_stats, get_config

professional_tools = [
    list_cameras,
    toggle_camera,
    add_camera,
    query_alerts,
    acknowledge_alert,
    alert_detail,
    system_health,
    get_system_stats,
    get_config,
    # + rule_tools 待 SentinelMind 新增 /api/rules
]

# 通用模式 — MCP
# Agno 的 MCP 支持通过 agno-mcp 插件，或手动封装
general_tools = await load_mcp_tools()

# 构建 Agent
from agno import Agent

professional_agent = Agent(
    model=OpenAIChat(id="gpt-4o"),
    tools=professional_tools,
    system_prompt=PROFESSIONAL_PROMPT,
    memory=create_agent_memory(),
)

general_agent = Agent(
    model=OpenAIChat(id="gpt-4o"),
    tools=general_tools,
    system_prompt=GENERAL_PROMPT,
    memory=create_agent_memory(),
)
```
