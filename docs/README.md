# SentinelMind 文档

> 最后更新: 2026-07-14

---

## 目录结构

```
docs/
├── README.md                    ← 本文件（索引）
├── architecture.md              ← 系统架构设计（17章）
│
├── modules/                     ← 模块设计书（按模块分组）
│   ├── README.md                ← 模块索引
│   ├── core/                    ← 核心模块
│   ├── rules/                   ← 规则引擎
│   ├── llm/                     ← LLM 集成
│   ├── storage/                 ← 存储层
│   ├── actions/                 ← 行动执行
│   ├── web/                     ← Web 服务
│   ├── config/                  ← 配置管理
│   └── auth/                    ← 认证模块
│
├── frontend/                    ← 前端设计
│   ├── DESIGN.md
│   └── MONITOR_PANEL.md
│
├── designs/                     ← 设计方案（Nexus Agent）
│   └── agent-langchain/         ← LangChain 框架版
│
├── planning/                    ← 项目管理
│   ├── PROGRESS.md
│   ├── TODO.md
│   ├── V2_PLAN.md
│   ├── V2_DEPLOYMENT_DESIGN.md
│   ├── OPTIMIZATION.md
│   └── I18N_DESIGN.md
│
├── security/                    ← 安全相关
│   ├── GAP_FIX_PLAN.md
│   └── PENDING_FIXES.md
│
├── reference/                   ← 参考资料
│   ├── KEY_LEARNINGS.md
│   └── TEST_PLAN.md
│
└── memory/                      ← Claude 记忆文件（迁移用）
```

---

## 核心文档

| 文档 | 说明 |
|------|------|
| [architecture.md](architecture.md) | 系统架构设计（17章） |
| [PROGRESS.md](planning/PROGRESS.md) | 开发进度 |
| [V2_DEPLOYMENT_DESIGN.md](planning/V2_DEPLOYMENT_DESIGN.md) | V2 部署设计 |
| [detailed-design.md](designs/agent-langchain/detailed-design.md) | Nexus Agent 详细设计 |
| [GAP_FIX_PLAN.md](security/GAP_FIX_PLAN.md) | 差距修复方案 |

---

## 文档规范

### 存放位置

| 文档类型 | 存放位置 |
|----------|---------|
| 系统架构 | `docs/architecture.md` |
| 模块设计书 | `docs/modules/{模块名}/` |
| 前端设计 | `docs/frontend/` |
| 设计方案 | `docs/designs/` |
| 项目管理 | `docs/planning/` |
| 安全文档 | `docs/security/` |
| 参考资料 | `docs/reference/` |
| 记忆文件 | `docs/memory/` |

### 命名规范

- 模块设计书：`{module_name}.md` 小写下划线
- 设计方案：大写下划线或中划线
- 规划文档：大写下划线

---

| | |
|---|---|
| **文档版本** | v1.0 |
| **最后更新** | 2026-07-14 |
