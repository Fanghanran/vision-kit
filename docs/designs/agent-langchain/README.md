# SentinelMind × LangChain — Agent 架构设计

> 状态：设计中 | 版本：v1 | 日期：2026-07-09

## 概述

为 SentinelMind 新增一个智能运维 Agent，基于 LangChain/LangGraph 框架。Agent 作为前端的自然语言补充入口，支持 CLI / Web Chat / REST API 三种交互方式，具备通用模式和专业模式双模式切换能力。

## 交互方式

| 方式 | 用途 | 技术 |
|---|---|---|
| CLI | 开发/运维人员终端使用 | typer + rich |
| Web Chat | 嵌在 Vue 前端右下角浮窗 | SSE 流式 |
| REST API | 外部系统/自动化脚本调度 | FastAPI `/api/agent/*` |

## 双模式设计

```
用户输入 → 意图识别（gpt-4o-mini） → 路由

"cam_02 为什么总告警"    →  专业模式
"Python GIL 锁怎么处理"  →  通用模式
"切换到专业模式"         →  手动锁定
```

| | 通用模式 | 专业模式 |
|---|---|---|
| System Prompt | "你是一个通用 AI 助手" | "你是 SentinelMind 运维专家" |
| 加载工具 | MCP 通用工具（文件/搜索/天气/浏览器） | SentinelMind API 工具（摄像头/告警/配置/规则） |
| 上下文 | 无特定约束 | 自动注入摄像头列表、告警数、系统状态快照 |

## 整体架构

```
┌─────────────────────────────────────────────────────────┐
│                      交互层                               │
│     CLI (rich)      Web Chat (SSE)      REST API          │
├─────────────────────────────────────────────────────────┤
│                    模式路由                               │
│   RunnableBranch(意图识别) → general_agent | pro_agent   │
├─────────────────────────────────────────────────────────┤
│                LangGraph Agent 核心                       │
│  ┌──────────────────────────────────────────────────┐   │
│  │              StateGraph(AgentState)                │   │
│  │  node: call_model → should_continue? → tool_node  │   │
│  │  checkpointer: SqliteSaver (自动持久化状态)        │   │
│  │  memory:  CompositeMemory (四层组合)               │   │
│  └──────────────────────────────────────────────────┘   │
├─────────────────────────────────────────────────────────┤
│                    工具注册表                             │
│  ┌───────────────┐  ┌──────────────────────────────┐    │
│  │ 通用 MCP 工具   │  │ SentinelMind 专业工具          │    │
│  │ MultiServer    │  │ StructuredTool.from_function │    │
│  │ MCPClient      │  │ 摄像头/告警/配置/规则/系统     │    │
│  └───────────────┘  └──────────────────────────────┘    │
├─────────────────────────────────────────────────────────┤
│                    调度器                                 │
│  ScheduledTask(APScheduler) → 巡检 StateGraph            │
│  巡检 → 发现问题 → 推送通知 → 人决策 → 回调恢复           │
└─────────────────────────────────────────────────────────┘
```

## 项目结构

```
src/sentinelmind/
├── ... (现有模块不变)
│
└── agent/                          # ← 新增
    ├── __init__.py
    │
    ├── core/
    │   ├── __init__.py
    │   ├── state.py                # AgentState 定义
    │   ├── graph.py                # 双模式 StateGraph 构建
    │   └── router.py               # 意图识别 → 模式路由
    │
    ├── memory/
    │   ├── __init__.py
    │   ├── checkpoint.py           # SqliteSaver 配置
    │   ├── buffer.py               # ConversationBufferWindow
    │   ├── summary.py              # ConversationSummaryMemory
    │   └── long_term.py            # 用户偏好 + 历史规律
    │
    ├── adapters/
    │   ├── sentinelmind/          # REST API 适配器
    │   │   ├── __init__.py
    │   │   ├── camera_tools.py    # 摄像头 CRUD → GET/POST /api/cameras
    │   │   ├── alert_tools.py     # 告警查询/确认 → GET/PUT /api/alerts
    │   │   ├── system_tools.py    # 健康检查/统计/配置 → GET /health /api/stats
    │   │   └── rule_tools.py      # 规则管理 → GET/POST/DELETE /api/rules（待新增）
    │   └── mcp/
    │       └── general_tools.py   # MCP 通用工具加载
    │
    ├── skills/
    │   ├── __init__.py
    │   ├── patrol.py               # /巡检 — 全系统健康检查
    │   ├── diagnose.py             # /排障 — 单摄像头深度分析
    │   ├── daily_report.py         # /日报 — 日终汇总
    │   └── rule_manage.py          # /规则 — 规则 CRUD
    │
    ├── prompts/
    │   ├── __init__.py
    │   ├── general.py              # 通用模式 system prompt
    │   └── professional.py         # 专业模式 system prompt
    │
    ├── scheduler.py                # 主动巡检调度器 (APScheduler)
    ├── web.py                       # FastAPI 路由挂载
    └── models.py                   # Agent 相关的 Pydantic 模型
```

## 关键设计决策

### 1. Agent 通过 REST API 对接 SentinelMind，不 import 内部模块

Agent 是通用 Agent，SentinelMind 只是第一个适配的后端。专业工具全部通过 HTTP REST API 调用，Agent 只知道 URL 和 Token。低耦合，成熟后可独立拆分。

### 2. LLM 分工：SentinelMind 做实时真伪判断，Agent 做深度分析

```
SentinelMind LLM                  Agent LLM
─────────────────────────────    ───────────────────────────
 粒度：单条告警 + 单张截图         粒度：聚合多条告警
 问题："这是真告警还是误报？"       问题："这批告警有什么规律？"
 输出：true/false + 一句话原因      输出：趋势分析、排障建议、日报
 时机：告警触发时，实时判断         时机：巡检/排障/日报时，批量分析
```

SentinelMind 的 LLM 只保留一个极简调用——判断告警是真还是假，不做复杂分析。这样既保证了实时性（紧急告警立即推送），又避免了重复分析。所有深度分析（聚合、归类、趋势、根因）由 Agent 在巡检时统一完成。

### 3. Skills = 受限子 StateGraph

每个 skill 是一个独立的 StateGraph，有自己的 system prompt 和受限工具集。执行完毕后结果写回主 graph 的 messages。

### 4. 调度器和 Agent 分离

APScheduler 管"什么时候执行"，LangGraph 管"单次执行的流程"。两者职责清晰，不耦合。

### 5. Agent 有自己的存储，不依赖 SentinelMind 内部 DB

- Agent 记忆 → 独立的 `agent_memory.db`（SqliteSaver + 用户偏好表）
- 通知渠道 → Agent 自己的通知配置（可对接相同的钉钉/企微 webhook）
- LLM 后端 → Agent 自己的 LLM 配置（可复用相同的 API key）
- 权限 → 透传 Bearer Token 给 SentinelMind 校验，Agent 不做权限判断

## Agent 与 SentinelMind 的对接

```
Agent (adapters/sentinelmind/)        SentinelMind (REST API)
─────────────────────────────────     ──────────────────────────
list_cameras()          ──GET──→     /api/cameras
toggle_camera(id)       ──POST──→    /api/cameras/{id}/toggle
add_camera(config)      ──POST──→    /api/cameras
query_alerts(filters)   ──GET──→     /api/alerts
acknowledge_alert(id)   ──PUT──→     /api/alerts/{id}/status
system_health()         ──GET──→     /health
get_system_stats(p)     ──GET──→     /api/stats?period=...
get_config()            ──GET──→     /api/config
list_rules()            ──GET──→     /api/rules （待新增）
create_rule(...)        ──POST──→    /api/rules （待新增）
```

Agent 只认 HTTP + MCP 两种协议，不认任何 SentinelMind 内部代码。

## 两个系统的数据流

```
┌─────────────── SentinelMind ──────────────────────┐
│                                                     │
│  Camera → YOLO → Tracker → Rule Engine → Alert     │
│                                          │          │
│                                    告警 + 截图      │
│                                          ↓          │
│                              ┌─── LLM (极简) ───┐   │
│                              │  真 / 假告警？    │   │
│                              └──────────────────┘   │
│                                          │          │
│                                    存入 SQLite       │
│                                    推送简短通知      │
│                                    "cam_02 区域闯入" │
└──────────────────────┬────────────────────────────┘
                       │ REST API
┌──────────────────────┴────────────────────────────┐
│                     Agent                          │
│                                                    │
│  巡检(每5分钟) / 排障(按需) / 日报(每天9:00)         │
│         ↓                                          │
│  GET /api/alerts 拉取告警列表                       │
│  GET /api/alerts/{id}/snapshot 拉截图               │
│         ↓                                          │
│  ┌────── LLM (深度) ──────────────────────────┐    │
│  │ 聚合分析：同摄像头、同类型的告警一起看          │    │
│  │ 趋势判断：今天比昨天多？是否异常激增？         │    │
│  │ 根因推断：阳光反光？角度问题？确实有人闯入？    │    │
│  │ 输出建议：调灵敏度 / 调整摄像头角度 / 人工确认  │    │
│  └───────────────────────────────────────────┘    │
│         ↓                                          │
│  推送汇总报告给人：                                  │
│  "cam_02 下午14-16时告警23次（日常3次），           │
│   看截图 80% 是阳光反光误报，建议调低灵敏度"         │
└───────────────────────────────────────────────────┘
```


## 代码量估算

| 模块 | 文件数 | 预估行数 |
|---|---|---|
| core (state/graph/router) | 3 | ~300 |
| memory (4层) | 4 | ~250 |
| tools (6组) | 6 | ~500 |
| skills (4个) | 4 | ~300 |
| scheduler | 1 | ~200 |
| web | 1 | ~150 |
| prompts | 2 | ~80 |
| **总计** | **~21** | **~1800** |

## 依赖新增

```
langgraph>=0.2.0
langchain>=0.3.0
langchain-community>=0.3.0
langchain-mcp-adapters>=0.1.0
apscheduler>=3.10.0
```

## 子文档

- [State 与记忆设计](state-and-memory.md)
- [工具设计](tools.md)
- [Skills 设计](skills.md)
- [主动巡检调度器](scheduler.md)
- [双模式路由](router.md)
