---
name: agent-framework-design
description: Vision Agent 的 Agent 是通用 AI Agent 框架，不是专用运维助手
metadata: 
  node_type: memory
  type: project
  originSessionId: a44998c4-778d-42f2-b4c3-58b9c24bcb0c
---

## Agent 定位

**项目名称：Nexus**（AI Agent 枢纽）

Vision Agent 的 Agent 是一个**通用 AI Agent 框架**，Vision Agent 只是其中一个专业场景。

### 双模式架构

- **通用模式**：MCP 工具（文件/搜索/天气/浏览器等）
- **专业模式**：Vision Agent API 工具（摄像头/告警/配置/规则/系统）

### 四种 Skills

| Skill | 功能 |
|-------|------|
| `/巡检` | 全系统健康检查 → 报告 |
| `/排障` | 单摄像头深度分析 |
| `/日报` | 汇总今日数据 → 日报 |
| `/规则` | 规则 CRUD 对话式操作 |

### 核心设计

- **工具层**：适配器层（adapters/）隔离框架，LangChain/Agno 版复用同一套工具函数
- **扩展性**：MCP 协议接入任意工具
- **交互**：CLI + Web Chat（SSE 流式）+ REST API
- **记忆**：四层组合（Buffer + Summary + SQLite + UserPrefs）
- **前端工具模块**：预留 tools/ 组件目录，支持工具列表、详情、测试、MCP配置

### 设计文档位置

- `docs/designs/agent-agno/` — Agno 框架版（6 个文件）
- `docs/designs/agent-langchain/` — LangChain 框架版（6 个文件）
- `docs/designs/agent-langchain/detailed-design.md` — **详细设计书**（13章，含基础设施：用户管理/审计日志/系统监控/配置管理/前端管理页面）
- `docs/designs/three-agent-workflow.md` — 三 Agent 开发工作流

### 选定方案

**LangChain 版**（用户确认）

### 两种方案对比

| | LangChain 版 | Agno 版 |
|---|---|---|
| 代码量 | ~1800 行（21 文件） | ~1200 行（15 文件） |
| 学习曲线 | 陡峭 | 平缓 |
| 灵活性 | 高，每层可替换 | 中，框架做决策 |

**Why:** 用户强调 Agent 不是专用运维助手，而是通用框架，Vision Agent 是其中一个应用场景
**How to:** 后续讨论 Agent 时，从通用框架角度思考，不要局限于运维场景

### 项目关系

- Agent 项目始于 Vision Agent 的运维思考
- **已分离** — Nexus 独立项目，位于 `d:/nexus/`
- 设计文档已迁移到 `d:/nexus/docs/designs/`
- **后续**：通过 REST API 对接 Vision Agent（`http://localhost:8080`）
- Nexus 只认 HTTP + MCP 两种协议，不 import Vision Agent 内部代码

### 当前状态

- 设计文档：✅ 完成（13章详细设计书）
- 代码：⏳ 待实现
- 前端：⏳ 待实现
- 测试：⏳ 待实现

### 项目结构（已分离）

```
d:/nexus/                       # Nexus 项目根目录
├── README.md                   # 项目说明
├── requirements.txt            # Python 依赖
├── src/nexus/                  # 源码（待实现）
│   ├── __init__.py
│   ├── __main__.py             # CLI 入口
│   ├── core/                   # 核心逻辑
│   ├── memory/                 # 记忆体系
│   ├── adapters/               # 适配器层
│   │   ├── vision_agent/       # Vision Agent REST API
│   │   └── mcp/                # MCP 通用工具
│   ├── skills/                 # Skills 编排
│   ├── scheduler.py            # 调度器
│   └── web.py                  # FastAPI 路由
├── frontend/                   # 前端（待实现）
├── configs/                    # 配置文件
│   ├── nexus.yaml              # 主配置（待创建）
│   └── mcp_servers.yaml        # MCP 服务器配置（待创建）
├── tests/                      # 测试（待实现）
└── docs/                       # 文档
    └── designs/                # 设计书（已从 vision_agent 迁移）
        ├── agent-agno/         # Agno 框架版
        ├── agent-langchain/    # LangChain 框架版（选定方案）
        │   └── detailed-design.md  # 详细设计书（13章）
        └── three-agent-workflow.md
```
