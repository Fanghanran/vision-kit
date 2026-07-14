# SentinelMind × Agno — Agent 架构设计

> 状态：设计中 | 版本：v1 | 日期：2026-07-09

## 概述

为 SentinelMind 新增一个智能运维 Agent，基于 Agno 框架（原 Phidata）。这是与 LangChain 版并列的设计方案，用于对比选型。Agent 作为前端的自然语言补充入口，支持 CLI / Web Chat / REST API 三种交互方式，具备通用模式和专业模式双模式切换能力。

## 与 LangChain 版的核心差异

| | LangChain 版 | Agno 版 |
|---|---|---|
| Agent 构建 | `create_react_agent(llm, tools, prompt, checkpointer)` | `Agent(model=..., tools=..., memory=...)` |
| 记忆管理 | 四层手动组合：Buffer + Summary + SqliteSaver + UserPrefs | `AgentMemory(db=..., create_user_memories=True)` 一行 |
| RAG 知识库 | 手动搭 VectorStoreRetriever | `KnowledgeBase(vector_db=ChromaDb(...))` 开箱自带 |
| 工作流 | `StateGraph` 手动定义节点+边+条件 | `Workflow` 继承类，写普通 Python 方法 |
| MCP 支持 | `MultiServerMCPClient` 原生 | 需 `agno-mcp` 插件 或 手动实现 |
| 代码量 | ~1800 行（21 文件） | ~1200 行（15 文件） |
| 学习曲线 | 陡峭，概念多 | 平缓，API 直觉化 |
| 灵活性 | 高，每层可替换 | 中，框架帮你做了大部分决策 |

## 交互方式

| 方式 | 用途 | 技术 |
|---|---|---|
| CLI | 开发/运维人员终端使用 | typer + rich |
| Web Chat | 嵌在 Vue 前端右下角浮窗 | SSE 流式 |
| REST API | 外部系统/自动化脚本调度 | FastAPI `/api/agent/*` |

## 双模式设计

```
用户输入 → 路由 Agent（gpt-4o-mini） → 分发

"cam_02 为什么总告警"    →  专业 Agent
"Python GIL 锁怎么处理"  →  通用 Agent
"切换到专业模式"         →  手动锁定
```

Agno 版用**两个独立的 Agent 实例**代替 LangChain 的子图：

```python
# 两个 Agent 实例，各自独立的 tools + prompt + memory
general_agent = Agent(model=llm, tools=mcp_tools, system_prompt=GENERAL_PROMPT)
professional_agent = Agent(model=llm, tools=va_tools, system_prompt=PROFESSIONAL_PROMPT)
```

| | 通用模式 | 专业模式 |
|---|---|---|
| System Prompt | "你是一个通用 AI 助手" | "你是 SentinelMind 运维专家" |
| 加载工具 | MCP 通用工具 | SentinelMind REST API 工具 |
| 上下文 | 无特定约束 | 自动注入系统状态快照 |

## 整体架构

```
┌─────────────────────────────────────────────────────────┐
│                      交互层                               │
│     CLI (rich)      Web Chat (SSE)      REST API          │
├─────────────────────────────────────────────────────────┤
│                    路由 Agent                             │
│  Agent(model=gpt-4o-mini, prompt="判断意图") → 分发       │
├─────────────────────────────────────────────────────────┤
│                Agno Agent 核心                            │
│  ┌──────────────────────────────────────────────────┐   │
│  │  Agent(model=llm, tools=..., memory=AgentMemory)  │   │
│  │  记忆：一行 AgentMemory(db, create_user_memories)  │   │
│  │  知识：KnowledgeBase 开箱自带 ChomaDB             │   │
│  │  会话：session_id 自动持久化                       │   │
│  └──────────────────────────────────────────────────┘   │
├─────────────────────────────────────────────────────────┤
│                    工具注册表                             │
│  ┌───────────────┐  ┌──────────────────────────────┐    │
│  │ 通用 MCP 工具   │  │ SentinelMind REST API 工具    │    │
│  │ 文件/搜索/天气  │  │ 摄像头/告警/配置/规则         │    │
│  └───────────────┘  └──────────────────────────────┘    │
├─────────────────────────────────────────────────────────┤
│                    调度器                                 │
│  APScheduler → 巡检 Workflow                             │
│  巡检 → 发现问题 → 推送通知 → 人决策 → 回调恢复           │
└─────────────────────────────────────────────────────────┘
```

## 项目结构

```
src/sentinelmind/
├── ... (现有模块不变)
│
└── agent_agno/                      # ← 新增（与 agent/ 并列）
    ├── __init__.py
    │
    ├── app.py                       # AgentApp 入口 + 双 Agent 构建
    ├── router.py                    # 意图路由 Agent
    │
    ├── memory/
    │   ├── __init__.py              # AgentMemory 一行配置
    │   └── knowledge.py             # KnowledgeBase 知识库配置
    │
    ├── adapters/
    │   ├── sentinelmind/            # REST API 适配器（与 LangChain 版完全复用）
    │   │   ├── __init__.py
    │   │   ├── camera_tools.py
    │   │   ├── alert_tools.py
    │   │   ├── system_tools.py
    │   │   └── rule_tools.py
    │   └── mcp/
    │       └── general_tools.py     # MCP 通用工具加载
    │
    ├── skills/
    │   ├── __init__.py
    │   ├── patrol.py                # /巡检 — Agno Workflow
    │   ├── diagnose.py              # /排障 — Agno Workflow
    │   ├── daily_report.py          # /日报 — Agno Workflow
    │   └── rule_manage.py           # /规则 — Agno Workflow
    │
    ├── prompts/
    │   ├── __init__.py
    │   ├── general.py
    │   └── professional.py
    │
    ├── scheduler.py                 # 主动巡检调度器
    └── web.py                       # FastAPI 路由挂载
```

## 关键设计决策

### 1. Agent 通过 REST API 对接 SentinelMind

与 LangChain 版一样低耦合。适配器代码（`adapters/sentinelmind/`）两个版本完全复用——都只是 `httpx` + REST API 调用，不依赖框架。

### 2. LLM 分工：SentinelMind 做真伪判断，Agent 做深度分析

```
SentinelMind LLM                  Agent LLM
─────────────────────────────    ───────────────────────────
 粒度：单条告警 + 单张截图         粒度：聚合多条告警
 问题："真告警还是误报？"          问题："有什么规律和趋势？"
 输出：true/false + 一句话原因      输出：趋势分析、排障建议、日报
 时机：实时                        时机：巡检/排障/日报
```

### 3. 记忆管理：一行搞定

Agno 的 `AgentMemory` 内部自动处理短期/长期/摘要的切换。不需要像 LangChain 那样手动组合四层。

```python
Agent(
    memory=AgentMemory(
        db=SqliteStorage(table_name="agent_memory", db_file="data/agent_memory.db"),
        create_user_memories=True,         # 自动提取并记住用户偏好
        update_user_memories_after_run=True,
    ),
    add_history_to_context=True,           # 自动注入历史对话
    num_history_runs=10,                   # 最近 N 轮
)
```

### 4. Skills 用 Workflow 类实现，不用 StateGraph

Agno 的 Workflow 更像写普通 Python 函数，不需要理解节点/边/条件路由。

### 5. 适配器层两个版本完全复用

```
agent/adapters/sentinelmind/   ← LangChain 版
agent_agno/adapters/           → 复制过来直接用
```

工具函数只依赖 `httpx`，不依赖任何 Agent 框架。两个版本唯一不同是工具注册方式：

```python
# LangChain：StructuredTool.from_function(coroutine=fn, ...)
# Agno：@tool 装饰器直接用在函数上
```

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
└──────────────────────┬────────────────────────────┘
                       │ REST API
┌──────────────────────┴────────────────────────────┐
│               Agent (Agno)                         │
│                                                    │
│  巡检(每5分钟) / 排障(按需) / 日报(每天9:00)         │
│         ↓                                          │
│  GET /api/alerts 拉取告警列表                       │
│  GET /api/alerts/{id}/snapshot 拉截图               │
│         ↓                                          │
│  ┌────── LLM (深度) ──────────────────────────┐    │
│  │ 聚合分析、趋势判断、根因推断、输出建议        │    │
│  └───────────────────────────────────────────┘    │
│         ↓                                          │
│  推送汇总报告给人                                    │
└───────────────────────────────────────────────────┘
```

## 依赖新增

```
agno>=1.0.0
openai>=1.0.0          # Agno 的 LLM 后端
chromadb>=0.5.0        # 知识库
apscheduler>=3.10.0    # 调度器
httpx>=0.27.0          # REST API 调用
```

## 子文档

- [记忆与知识库](memory-and-knowledge.md)
- [工具设计](tools.md)
- [Skills 设计 (Agno Workflow)](skills.md)
- [主动巡检调度器](scheduler.md)
- [应用入口与路由](app-and-router.md)
