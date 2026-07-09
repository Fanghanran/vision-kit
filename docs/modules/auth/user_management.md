# 用户管理模块设计

> 版本: v2 | 日期: 2026-07-09

## 一、现状

已有完整的后端认证系统（`auth/models.py` + `auth/manager.py`）：PBKDF2 密码哈希、Bearer Token、RBAC 三级权限、登录限流。前端只有一个基础表格 + 增删改弹窗，缺乏以下能力：

| 缺失 | 说明 |
|---|---|
| 登录历史 | 不知道谁什么时候登录、从哪个 IP |
| 活跃会话 | 不能查看/撤销在线用户 |
| 统计卡片 | 没有总用户数、角色分布、在线状态概览 |
| 搜索筛选 | 用户多了只能翻一页页找 |
| 权限可视化 | 看不到每个角色具体能做什么 |
| 操作审计 | 谁改了谁的权限、谁启用了谁 |

## 二、新增内容

### 2.1 后端新增

```
auth/
├── models.py      # 现有，新增 LoginHistory 模型
├── manager.py     # 现有，新增登录历史写入、会话查询
└── __init__.py
```

#### LoginHistory 表

```sql
CREATE TABLE IF NOT EXISTS login_history (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    username   TEXT NOT NULL,
    ip         TEXT DEFAULT '',
    success    INTEGER NOT NULL DEFAULT 1,
    reason     TEXT DEFAULT '',        -- 失败原因（仅失败时记录）
    created_at REAL NOT NULL,
    FOREIGN KEY (username) REFERENCES users(username) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_login_history_username ON login_history(username);
CREATE INDEX IF NOT EXISTS idx_login_history_created_at ON login_history(created_at);
```

#### 新增 AuthManager 方法

| 方法 | 功能 |
|---|---|
| `record_login(username, ip, success, reason)` | 写登录历史 |
| `get_login_history(username, limit)` | 查某用户登录历史 |
| `list_active_sessions()` | 列出所有活跃 Token（用户名+IP+剩余时间） |
| `revoke_session(username)` | 撤销某用户的所有 Token |

#### 新增 REST API

| 端点 | 权限 | 功能 |
|---|---|---|
| GET /api/users/stats | admin | 用户统计：总数、角色分布、启用/禁用数 |
| GET /api/users/{username}/sessions | admin/本人 | 用户活跃会话 |
| DELETE /api/users/{username}/sessions | admin | 强制下线 |
| GET /api/users/{username}/login-history | admin/本人 | 登录历史 |

### 2.2 前端改造 Users.vue

```
┌─────────────────────────────────────────────────┐
│  用户管理                           [+ 添加用户] │
├─────────────────────────────────────────────────┤
│  ┌───────┐ ┌───────┐ ┌───────┐ ┌───────┐      │
│  │ 总用户 │ │ 管理员 │ │ 操作员 │ │ 观察者 │      │
│  │   5   │ │   1   │ │   2   │ │   2   │      │
│  └───────┘ └───────┘ └───────┘ └───────┘      │
├─────────────────────────────────────────────────┤
│  🔍 搜索用户名/邮箱...    [角色▼] [状态▼]        │
├─────────────────────────────────────────────────┤
│  用户名 │ 邮箱  │ 角色 │ 状态 │ 最后登录 │ 操作  │
│  admin  │ ...   │ 管理员│ 正常 │ 今天 14:30│ ...  │
│  op1    │ ...   │ 操作员│ 正常 │ 昨天      │ ...  │
│  viewer1│ ...   │ 观察者│ 禁用 │ 从未登录  │ ...  │
└─────────────────────────────────────────────────┘
```

点击行展开 → 权限矩阵 + 登录历史时间线。

## 三、实施文件清单

| 文件 | 操作 | 说明 |
|---|---|---|
| auth/models.py | 修改 | 新增 LoginHistory |
| auth/manager.py | 修改 | 新增登录历史+会话管理 |
| web/api/app.py | 修改 | 新增 4 个用户管理 API 端点 |
| frontend/src/views/Users.vue | 重写 | 统计卡片+搜索+权限矩阵 |
| frontend/src/stores/auth.ts | 修改 | 新增 API 调用 |
| docs/modules/auth/user_management.md | 新增 | 本文档 |
