# 通知偏好与 Pipeline 对接设计

> 版本: v1 | 日期: 2026-07-09

## 一、问题

`user_preferences` 表 + API 已经建好，但 pipeline 发通知时没有读偏好：

```
pipeline.py:550  for notifier in self._notifiers:
                    notifier.execute(alert)  ← 全渠道、无条件推送
```

## 二、当前约束

| 客观事实 | 影响 |
|---|---|
| Webhook URL 是全局的（一个群机器人） | 无法区分"发给谁" |
| Email 收件人列表在 `settings.yaml` 全局配 | 无法按用户过滤收件人 |
| preferences 是 per-user 的 | 跟全局通知模型不匹配 |

## 三、设计决策

**分两步走，不一步到位：**

### v1（当前落地）— 偏好作为系统级开关

preferences 存储在 **admin 用户**名下，控制整个系统的通知策略：

```
admin 的偏好 = 系统通知策略
  notify_alert:   { enabled: true,  channels: ["webhook", "email"] }
  notify_system:  { enabled: true,  channels: ["webhook"] }
  notify_daily:   { enabled: false, channels: ["webhook"] }
```

Pipeline 推送时读取 admin 的偏好，按偏好决定推不推、推哪个渠道。

### v2（未来）— 用户级通知

- Email 配置改为存储用户邮箱，支持按用户名查询
- Webhook 保持群机器人不变
- Pipeline 推送告警时查询所有启用通知的用户，逐个判断

## 四、v1 实现方案

### 4.1 Pipeline 改动

```python
# core/pipeline.py — ActionThread 通知段

# 原代码
for notifier in self._notifiers:
    notifier.execute(alert, snapshot_path)

# 改为
if self._should_notify("alert", "webhook"):
    self._webhook_notifier.execute(alert, snapshot_path)
if self._should_notify("alert", "email"):
    self._email_notifier.execute(alert, snapshot_path)
```

### 4.2 新增 `_should_notify` 方法

```python
# core/pipeline.py 新增
def _get_admin_preferences(self) -> dict:
    """读取 admin 用户的通知偏好（缓存 60 秒）"""
    now = time.time()
    if self._prefs_cache and (now - self._prefs_cache_time) < 60:
        return self._prefs_cache
    try:
        from vision_agent.auth.manager import get_auth_manager
        mgr = get_auth_manager()
        prefs = mgr.get_preferences("admin")
        self._prefs_cache = prefs
        self._prefs_cache_time = now
        return prefs
    except Exception:
        return {
            "notify_alert": {"enabled": True, "channels": ["webhook"]},
            "notify_system": {"enabled": True, "channels": ["webhook"]},
            "notify_daily": {"enabled": False, "channels": ["webhook"]},
        }

def _should_notify(self, notify_type: str, channel: str) -> bool:
    """检查某类通知是否应通过某渠道发送"""
    prefs = self._get_admin_preferences()
    cfg = prefs.get(f"notify_{notify_type}", {})
    return cfg.get("enabled", True) and channel in cfg.get("channels", [])
```

### 4.3 __main__.py 改动

原代码初始化 notifiers 为列表，改为分类存储：

```python
# __main__.py — assemble_components()
# 原
notifiers = [WebhookNotifier(...), EmailNotifier(...)]
# 改为
webhook_notifier = WebhookNotifier(wh_config) if webhook_enabled else None
email_notifier = EmailNotifier(em_config) if email_enabled else None
notifiers = [n for n in (webhook_notifier, email_notifier) if n is not None]

pipeline = VisionAgent(
    notifiers=notifiers,
    webhook_notifier=webhook_notifier,
    email_notifier=email_notifier,
    ...
)
```

### 4.4 Pipeline 构造函数改动

```python
class VisionAgent:
    def __init__(self, ...,
                 notifiers=None,          # 保持兼容
                 webhook_notifier=None,   # 新增
                 email_notifier=None,      # 新增
                 ):
        ...
        self._webhook_notifier = webhook_notifier
        self._email_notifier = email_notifier
        self._prefs_cache = None
        self._prefs_cache_time = 0.0
```

### 4.5 调度器中的通知（未来）

`PatrolScheduler`（Agent 模块）巡检和日报推送时，同样读取 admin 偏好决定是否推送。

## 五、通知类型映射

| 用户偏好 key | 对应场景 | pipeline 中触发点 |
|---|---|---|
| `notify_alert` | 告警推送 | ActionThread — 每次 Event 生成 |
| `notify_system` | 系统通知 | 健康监控线程 — 摄像头离线/GPU 异常 |
| `notify_daily` | 日报推送 | Agent 调度器 — 每天 9:00 |

## 六、改动文件清单

| 文件 | 操作 |
|---|---|
| core/pipeline.py | `_get_admin_preferences` + `_should_notify` + 构造函数 + 通知段改写 |
| __main__.py | notifiers 拆分为 webhook/email 独立变量 |
| docs/modules/core/notification_preferences.md | 新增（本文档） |

## 七、测试

- `test_pipeline_preferences.py`：mock AuthManager + 偏好缓存 + 不同偏好组合
- 验证 `_should_notify("alert", "email")` 在偏好关闭时返回 False
- 验证缓存 60 秒内不重复查询
