# 个人设置模块设计

> 版本: v2 | 日期: 2026-07-09

## 一、现状

Profile.vue 功能单一：头像颜色选择 + 邮箱编辑 + 改密码。缺少：

| 缺失 | 说明 |
|---|---|
| 通知偏好 | 不能设置哪些事件推送、推送到哪个渠道 |
| 安全概览 | 看不到最后登录时间/IP、活跃会话 |
| 登录历史 | 不知道自己账号有没有被盗用 |
| 分栏布局 | 所有内容堆在一个窄卡片里 |

## 二、目标布局

```
┌─────────────────────────────────────────────────────┐
│  ← 返回                 个人设置                     │
├─────────────────────────────────────────────────────┤
│                                                     │
│  ┌──────────┐                                       │
│  │          │  用户名  admin                        │
│  │  头像    │  角色    管理员                        │
│  │  (80px)  │  邮箱    admin@example.com  [编辑]    │
│  │          │  注册时间 2026-01-01                   │
│  └──────────┘                                       │
│                                                     │
│  [个人信息] [通知设置] [安全设置]                      │
│  ───────────────────────────────────────────────     │
│                                                     │
│  ◉ 个人信息                                          │
│  ├─ 头像颜色  [■][■][■][■][■][■][■][■]             │
│  ├─ 邮箱      [________________]  [保存]             │
│  └─ 显示语言   [中文 ▼] （预留）                      │
│                                                     │
│  ◉ 通知设置                                          │
│  ├─ 告警推送  [Webhook] [Email]  ☑                  │
│  ├─ 系统通知  [Webhook] [Email]  ☐                  │
│  └─ 日报推送  [Webhook] [Email]  ☐                  │
│                                                     │
│  ◉ 安全设置                                          │
│  ├─ 最后登录  2026-07-09 14:30  IP 192.168.1.100    │
│  ├─ 活跃会话  2 个  [查看详情]                        │
│  ├─ 修改密码  [旧密码] [新密码] [确认]  [保存]         │
│  └─ 登录历史  [查看全部]  ← 时间线                    │
│                                                     │
└─────────────────────────────────────────────────────┘
```

## 三、实施计划

### 3.1 新增后端 API

| 端点 | 权限 | 说明 |
|---|---|---|
| GET /api/auth/me/detail | 本人 | 返回完整个人信息（含统计） |
| PUT /api/auth/preferences | 本人 | 保存通知偏好 |

### 3.2 notifications 表

```sql
CREATE TABLE IF NOT EXISTS user_preferences (
    username   TEXT PRIMARY KEY,
    notify_alert_enabled   INTEGER NOT NULL DEFAULT 1,
    notify_alert_channels  TEXT NOT NULL DEFAULT '["webhook"]',
    notify_system_enabled  INTEGER NOT NULL DEFAULT 1,
    notify_system_channels TEXT NOT NULL DEFAULT '["webhook"]',
    notify_daily_enabled   INTEGER NOT NULL DEFAULT 0,
    notify_daily_channels  TEXT NOT NULL DEFAULT '["webhook"]',
    updated_at REAL NOT NULL,
    FOREIGN KEY (username) REFERENCES users(username) ON DELETE CASCADE
)
```

### 3.3 修改文件清单

| 文件 | 操作 | 说明 |
|---|---|---|
| auth/manager.py | 修改 | 新增 preferences 表 + get/set 方法 |
| web/api/app.py | 修改 | 新增 GET /api/auth/me/detail、PUT /api/auth/preferences |
| frontend/src/stores/auth.ts | 修改 | 新增 API 调用 |
| frontend/src/views/Profile.vue | 重写 | 分栏布局 + 通知设置 + 安全概览 |
| docs/modules/auth/profile_settings.md | 新增 | 本文档 |
