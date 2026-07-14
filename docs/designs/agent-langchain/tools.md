# 工具设计（低耦合版）

## 设计原则

1. **Agent 不 import SentinelMind 内部模块** — 所有专业工具通过 REST API 调用，Agent 只知道 URL 和 Token，不知道内部实现
2. **SentinelMind 只是第一个适配的后端** — 换一个视频监控系统，只需换一套 tools 实现，Agent 核心不动
3. **权限透传，不在 Agent 侧校验** — Agent 透传用户的 Bearer Token，SentinelMind 自己判断 403

## 耦合对比

```
之前（紧耦合）：                      现在（低耦合）：
agent/tools/camera.py                agent/adapters/sentinelmind/camera.py
  import sentinelmind.core.camera      POST http://localhost:8080/api/cameras
  pipeline.get_camera_states()         Authorization: Bearer {token}
  ↑ 代码级依赖                         ↑ 协议级依赖
```

专业工具从 4 组缩减到 3 组（规则管理待 SentinelMind 新增端点），MCP 通用工具独立。

## 架构

```
┌─────────────────────────────────────────────┐
│                  Agent                        │
│                                               │
│  core / memory / skills / scheduler / web     │
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
│  REST API (现有，不动)                         │
│  /api/cameras  /api/alerts  /api/stats  ...   │
│  自身完成 RBAC 校验，403 原样返回               │
└──────────────────────────────────────────────┘
```

## 适配器配置

```yaml
# configs/agent.yaml
adapters:
  sentinelmind:
    base_url: "http://localhost:8080"
    timeout: 10
```

Agent 启动时读取配置，所有工具指向这个 URL。要对接另一个实例（测试/生产），改配置即切换。

---

## 一、摄像头工具（camera_tools.py）

```python
# agent/adapters/sentinelmind/camera_tools.py
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field
import httpx
import os

BASE_URL = os.getenv("VISION_AGENT_URL", "http://localhost:8080")

def _get_client(token: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=BASE_URL,
        headers={"Authorization": f"Bearer {token}"},
        timeout=10.0,
    )
```

### list_cameras

```
功能：列出所有摄像头及状态
HTTP：GET /api/cameras
权限：透传 token，SentinelMind 校验
```

### toggle_camera

```
功能：启用或停用摄像头
HTTP：POST /api/cameras/{camera_id}/toggle
权限：透传 token，403 → Agent 提示"权限不足，需要 operator+"
```

### add_camera / update_camera / delete_camera

```
功能：摄像头 CRUD
HTTP：POST|PUT|DELETE /api/cameras[/{id}]
权限：透传 token，403 → Agent 提示"权限不足，需要 admin"
```

### get_camera_detail

```
功能：单路摄像头详细信息
HTTP：GET /api/cameras/{camera_id}（需要 SentinelMind 新增此端点）
权限：viewer+
```

---

## 二、告警工具（alert_tools.py）

### query_alerts

```
功能：查询告警列表
HTTP：GET /api/alerts?status=...&camera_id=...&severity=...&limit=...
权限：透传 token
```

### alert_detail

```
功能：查看单条告警完整详情，含 LLM 分析
HTTP：GET /api/alerts/{id}
权限：透传 token
```

### acknowledge_alert / resolve_alert / reject_alert

```
功能：状态流转
HTTP：PUT /api/alerts/{id}/status → {status: "acknowledged", acknowledged_by: "..."}
权限：透传 token，403 → 提示权限不足
```

---

## 三、系统工具（system_tools.py）

### system_health

```
功能：系统健康状态
HTTP：GET /health
权限：无需 token（现有实现无认证）
```

### get_system_stats

```
功能：系统统计数据
HTTP：GET /api/stats?period=today|7d|30d
权限：透传 token
```

### get_config（脱敏）

```
功能：查看脱敏配置
HTTP：GET /api/config
权限：透传 token，403 → 提示需要 admin
```

---

## 四、规则工具（待 SentinelMind 新增 REST 端点）

规则管理目前只有内部 YAML 文件操作，没有 REST API。需要先在 SentinelMind 侧新增：

| 端点 | 功能 |
|---|---|
| GET /api/rules | 列出所有规则 |
| GET /api/rules/{name} | 查看规则详情 |
| POST /api/rules | 创建规则 → 写 YAML → 自动热加载 |
| DELETE /api/rules/{name} | 删除规则 → 删 YAML → 自动热加载 |

这些端点实现后，Agent 适配器对接即可。

---

## 五、MCP 通用工具（mcp_tools.py）

与 SentinelMind 完全无关，Agent 自己的通用能力：

```python
# agent/adapters/mcp/general_tools.py
from langchain_mcp_adapters.client import MultiServerMCPClient

async def load_mcp_general_tools(config_path: str = "configs/mcp_servers.yaml") -> list:
    import yaml
    with open(config_path) as f:
        servers = yaml.safe_load(f)
    client = MultiServerMCPClient(servers)
    return await client.get_tools()
```

```yaml
# configs/mcp_servers.yaml
weather:
  command: python
  args: ["-m", "mcp_server_weather"]
puppeteer:
  command: npx
  args: ["-y", "@modelcontextprotocol/server-puppeteer"]
filesystem:
  command: npx
  args: ["-y", "@modelcontextprotocol/server-filesystem", "/data"]
```

---

## 工具注册表

```python
# agent/tools.py（入口）
from agent.adapters.sentinelmind.camera_tools import camera_tools
from agent.adapters.sentinelmind.alert_tools import alert_tools
from agent.adapters.sentinelmind.system_tools import system_tools
from agent.adapters.mcp.general_tools import load_mcp_general_tools

# 专业模式 — REST API → SentinelMind
professional_tools = camera_tools + alert_tools + system_tools
# + rule_tools 待 SentinelMind 新增 /api/rules 端点

# 通用模式 — MCP
general_tools = await load_mcp_general_tools()
```

## Token 注入

工具函数需要用户 Bearer Token 做鉴权。Token 从 AgentState 注入，无需用户手动输入：

```python
# Agent graph 入口节点：
state["user_token"] = extract_token_from_request(request)

# 工具调用时从 RunnableConfig 中获取并自动传入
```

## 权限矩阵（在 SentinelMind 侧校验）

Agent 不自己做权限判断。透传 token，403 → 展示错误给用户。

| SentinelMind API | viewer | operator | admin |
|---|---|---|---|
| GET /api/cameras | ✅ | ✅ | ✅ |
| POST /api/cameras/{id}/toggle | ❌ | ✅ | ✅ |
| POST /api/cameras | ❌ | ❌ | ✅ |
| PUT /api/cameras/{id} | ❌ | ❌ | ✅ |
| DELETE /api/cameras/{id} | ❌ | ❌ | ✅ |
| GET /api/alerts | ✅ | ✅ | ✅ |
| GET /api/alerts/{id} | ✅ | ✅ | ✅ |
| PUT /api/alerts/{id}/status | ❌ | ✅ | ✅ |
| GET /health | ✅ | ✅ | ✅ |
| GET /api/stats | ✅ | ✅ | ✅ |
| GET /api/config | ❌ | ❌ | ✅ |

## 拆分后的形态

当 Agent 成熟后独立成仓库，零 Python 依赖 SentinelMind：

```
agent/
├── pyproject.toml          # 依赖: langgraph, httpx, apscheduler, langchain-mcp-adapters
│                           # 注意：没有 sentinelmind
├── src/agent/
│   ├── core/               # 纯 Agent 逻辑
│   ├── memory/             # 四层记忆
│   ├── adapters/
│   │   ├── sentinelmind/   # REST API 适配器 → 任何一个 SentinelMind 实例
│   │   └── mcp/            # MCP 通用工具
│   ├── skills/             # Skill 编排
│   ├── scheduler.py
│   └── web.py
└── configs/
    ├── agent.yaml           # adapters.sentinelmind.base_url = "http://..."
    └── mcp_servers.yaml
```

Agent 只认 HTTP 协议和 MCP 协议，不认识任何 SentinelMind 内部代码。
