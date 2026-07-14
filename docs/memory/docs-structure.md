---
name: docs-structure
description: SentinelMind 文档目录规范，写文档时必须遵守
metadata: 
  node_type: memory
  type: feedback
  originSessionId: a44998c4-778d-42f2-b4c3-58b9c24bcb0c
---

## 文档存放规范

### 目录结构

```
docs/
├── architecture.md              ← 系统架构（唯一）
├── modules/                     ← 模块设计书（按模块分组）
├── frontend/                    ← 前端设计
├── designs/                     ← 设计方案（如 Nexus Agent）
├── planning/                    ← 项目管理（进度/待办/计划）
├── security/                    ← 安全相关（漏洞/修复方案）
├── reference/                   ← 参考资料（经验/测试计划）
└── memory/                      ← Claude 记忆文件（迁移用）
```

### 存放规则

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

**Why:** 用户要求文档规范化，避免杂乱和重复
**How to:** 写文档时先确认类型，放到对应目录，不要放根目录
