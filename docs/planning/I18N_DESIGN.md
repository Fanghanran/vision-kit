# Vision Agent 国际化（i18n）设计书

> 状态：待实现  
> 优先级：P3（项目完善后）  
> 目标：前端 UI + 后端错误消息全部中英双语可切换

---

## 一、整体架构

```
┌──────────────────────────────────────────────────────────┐
│                    用户切换语言                           │
│          localStorage.setItem("lang", "zh|en")            │
└─────────────────┬────────────────────────────────────────┘
                  │
    ┌─────────────┴─────────────┐
    │                           │
    ▼                           ▼
┌─────────────┐          ┌─────────────┐
│   前端       │          │   后端       │
│ i18n/index  │          │ i18n/index  │
│ zh.ts/en.ts │          │ zh.py/en.py │
│             │          │             │
│ Vue 组件     │          │ FastAPI      │
│ ElMessage    │          │ HTTPException│
│ element-plus │          │ 验证消息      │
└─────────────┘          └─────────────┘
```

---

## 二、前端设计

### 2.1 文件结构

```
frontend/src/
  i18n/
    index.ts          # 入口：导出 t() 函数
    zh.ts             # 中文消息表
    en.ts             # 英文消息表
    element-plus.ts   # Element Plus 组件库语言包切换
  composables/
    useLocale.ts      # 语言切换 composable
  App.vue             # 调用 setupLocale() 初始化语言
  components/layout/
    LocaleSwitch.vue  # 顶栏语言切换按钮（中/EN）
```

### 2.2 消息表格式

```typescript
// i18n/zh.ts
export default {
  // ─── 通用 ───
  common: {
    save: "保存",
    cancel: "取消",
    delete: "删除",
    confirm: "确认",
    back: "返回",
    refresh: "刷新",
  },

  // ─── 错误消息 ───
  error: {
    E1001: "用户名或密码错误",
    E1002: "摄像头不存在",
    E1003: "用户已被禁用",
    E1004: "摄像头ID已存在",
    E1005: "告警不存在",
    E1006: "请求过于频繁，请稍后再试",
    // ...
  },

  // ─── 认证 ───
  auth: {
    loginTitle: "欢迎回来",
    loginSubtitle: "请登录您的账户以继续",
    username: "用户名",
    password: "密码",
    loginBtn: "登录",
    loggingIn: "登录中...",
    logout: "退出登录",
    profile: "个人设置",
    changePassword: "修改密码",
    oldPassword: "旧密码",
    newPassword: "新密码",
    confirmPassword: "确认新密码",
    passwordChanged: "密码已修改",
    loginHint: "默认账户：admin / admin123",
  },

  // ─── 导航 ───
  nav: {
    dashboard: "仪表盘",
    alerts: "告警",
    cameras: "摄像头",
    monitor: "监控",
    system: "系统",
    userManagement: "用户管理",
    darkMode: "切换暗色模式",
  },

  // ─── 仪表盘 ───
  dashboard: {
    todayAlerts: "今日告警",
    onlineCameras: "在线摄像头",
    gpuUsage: "GPU 使用率",
    inferenceLatency: "推理延迟 P50",
    alertTrend: "告警趋势",
    realtimeAlerts: "实时告警",
    alertDistribution: "告警类型分布",
    cameraAlertRanking: "摄像头告警排行",
  },

  // ─── 摄像头 ───
  camera: {
    status: "摄像头状态",
    add: "添加",
    edit: "编辑",
    start: "启动",
    stop: "停止",
    online: "在线",
    offline: "离线",
    connecting: "连接中",
    error: "错误",
    fps: "FPS",
    alerts: "告警",
    queue: "队列",
    sourceType: "来源类型",
    rtsp: "RTSP 流",
    video: "视频文件",
    test: "测试图案",
    resolution: "分辨率",
    autoDetect: "自动检测",
    addCamera: "添加摄像头",
    editCamera: "编辑摄像头",
    notExist: "摄像头不存在，可能已被删除",
    idExists: "摄像头 ID 已存在",
  },

  // ─── 告警 ───
  alert: {
    list: "告警列表",
    detail: "告警详情",
    pending: "待处理",
    acknowledged: "已确认",
    rejected: "误报",
    resolved: "已解决",
    severity: "级别",
    critical: "紧急",
    warning: "警告",
    info: "信息",
    type: "类型",
    camera: "摄像头",
    time: "时间",
    acknowledge: "确认",
    reject: "标记误报",
    acknowledgedBy: "确认人",
    noData: "暂无告警",
    notExist: "告警不存在",
  },

  // ─── 监控面板 ───
  monitor: {
    title: "视频监控",
    layout: "布局",
    overlay: "叠加",
    waiting: "等待画面...",
    connecting: "连接中...",
    selectCamera: "点击选择摄像头",
    replace: "替换",
    remove: "移除",
    fullscreen: "全屏",
    timeline: "时间轴",
    speed: "速度",
  },

  // ─── 系统 ───
  system: {
    gpuStatus: "GPU 状态",
    runningStatus: "运行状态",
    inferencePerformance: "推理性能",
    uptime: "运行时间",
    systemConfig: "系统配置",
    highLoad: "高负载",
    medium: "中等",
    normal: "正常",
    disabled: "禁用",
    enabled: "启用",
    none: "无",
  },

  // ─── 用户管理 ───
  user: {
    username: "用户名",
    email: "邮箱",
    role: "角色",
    status: "状态",
    active: "正常",
    disabled: "禁用",
    admin: "管理员",
    operator: "操作员",
    viewer: "观察者",
    createdAt: "注册时间",
    updatedAt: "最后修改",
    addUser: "添加用户",
    editUser: "编辑用户",
    userDetail: "用户详情",
    leaveBlank: "留空不修改",
    deleteConfirm: "确定删除此用户？",
  },
}
```

```typescript
// i18n/en.ts（结构完全相同，英文值）
export default {
  common: {
    save: "Save",
    cancel: "Cancel",
    // ...
  },
  error: {
    E1001: "Invalid username or password",
    E1002: "Camera not found",
    // ...
  },
  // ...
}
```

### 2.3 翻译函数 t()

```typescript
// i18n/index.ts
import zh from "./zh"
import en from "./en"

const messages = { zh, en }
type Lang = "zh" | "en"

let currentLang: Lang = (localStorage.getItem("lang") as Lang) || "zh"

export function setLang(lang: Lang) {
  currentLang = lang
  localStorage.setItem("lang", lang)
}

export function getLang(): Lang {
  return currentLang
}

/**
 * t("auth.loginTitle") → "欢迎回来"
 * t("error.E1002")    → "摄像头不存在"
 */
export function t(path: string): string {
  const keys = path.split(".")
  let result: any = messages[currentLang]
  for (const key of keys) {
    result = result?.[key]
  }
  return result ?? path
}
```

### 2.4 在组件中使用

```vue
<template>
  <!-- 替换硬编码文字 -->
  <el-button>{{ t("common.save") }}</el-button>
  <span>{{ t("camera.status") }}</span>
</template>

<script setup>
import { t } from "@/i18n"
</script>
```

### 2.5 Element Plus 组件库语言

```typescript
// composables/useLocale.ts
import { ref } from "vue"
import zhCn from "element-plus/es/locale/lang/zh-cn"
import en from "element-plus/es/locale/lang/en"
import { getLang } from "@/i18n"

export function useLocale() {
  const locale = ref(getLang() === "en" ? en : zhCn)

  function switchLocale(lang: "zh" | "en") {
    locale.value = lang === "en" ? en : zhCn
  }

  return { locale, switchLocale }
}
```

### 2.6 语言切换按钮

```vue
<!-- LocaleSwitch.vue -->
<template>
  <el-button text @click="toggle">
    {{ getLang() === "zh" ? "EN" : "中" }}
  </el-button>
</template>

<script setup>
import { setLang, getLang } from "@/i18n"
import { useLocale } from "@/composables/useLocale"

const { switchLocale } = useLocale()

function toggle() {
  const next = getLang() === "zh" ? "en" : "zh"
  setLang(next)
  switchLocale(next)
  window.location.reload()
}
</script>
```

---

## 三、后端设计

### 3.1 文件结构

```
src/vision_agent/
  i18n/
    __init__.py       # 导出 get_message() 函数
    messages.py       # MESSAGES 字典（zh + en）
    middleware.py      # 语言检测 ASGI 中间件
```

### 3.2 消息表格式

```python
# i18n/messages.py
MESSAGES = {
    # ─── 通用 ───
    "E0001": {"zh": "系统未启动", "en": "System not available"},
    "E0002": {"zh": "日期格式不正确", "en": "Invalid date format"},

    # ─── 认证 ───
    "E1001": {"zh": "用户名或密码错误",   "en": "Invalid username or password"},
    "E1002": {"zh": "用户已被禁用",       "en": "User is disabled"},
    "E1003": {"zh": "请先登录",           "en": "Please login first"},
    "E1004": {"zh": "权限不足",           "en": "Permission denied"},
    "E1005": {"zh": "请输入用户名和密码",  "en": "Username and password required"},
    "E1006": {"zh": "旧密码错误",         "en": "Old password is incorrect"},
    "E1007": {"zh": "请求过于频繁，请稍后", "en": "Too many attempts, try later"},

    # ─── 摄像头 ───
    "E2001": {"zh": "摄像头不存在",               "en": "Camera not found"},
    "E2002": {"zh": "摄像头ID不合法",              "en": "Invalid camera ID"},
    "E2003": {"zh": "摄像头已存在",                "en": "Camera already exists"},
    "E2004": {"zh": "缺少摄像头ID",                "en": "Camera ID is required"},
    "E2005": {"zh": "ID格式不合法（仅允许字母数字-_）", "en": "Invalid ID format (alphanumeric, -, _)"},
    "E2006": {"zh": "不能从 {0} 转为 {1}",         "en": "Cannot transition from {0} to {1}"},
    "E2007": {"zh": "没有找到录像文件",             "en": "No recordings found"},
    "E2008": {"zh": "指定时间范围内没有录像",        "en": "No recording in specified time range"},

    # ─── 告警 ───
    "E3001": {"zh": "告警不存在",     "en": "Alert not found"},
    "E3002": {"zh": "缺少状态字段",   "en": "Missing status field"},
    "E3003": {"zh": "截图不存在",     "en": "Snapshot not found"},
    "E3004": {"zh": "视频片段不存在",  "en": "Video clip not found"},

    # ─── 用户管理 ───
    "E4001": {"zh": "用户 {} 已存在",   "en": "User {} already exists"},
    "E4002": {"zh": "用户 {} 不存在",   "en": "User {} not found"},
    "E4003": {"zh": "不能删除默认管理员", "en": "Cannot delete default admin"},
    "E4004": {"zh": "请输入用户名和密码", "en": "Username and password required"},
}
```

### 3.3 翻译函数

```python
# i18n/__init__.py
from .messages import MESSAGES

def get_message(code: str, lang: str = "zh", **kwargs) -> str:
    """获取翻译消息，支持参数填充"""
    entry = MESSAGES.get(code, {})
    msg = entry.get(lang, code)
    if kwargs:
        msg = msg.format(**kwargs)
    return msg
```

### 3.4 语言检测中间件

```python
# i18n/middleware.py
from starlette.types import ASGIApp, Receive, Scope, Send

class I18nMiddleware:
    """从请求头 Accept-Language 检测语言，注入到 scope"""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] == "http":
            headers = dict(scope.get("headers", []))
            al = headers.get(b"accept-language", b"zh").decode()
            scope["lang"] = "en" if al.startswith("en") else "zh"
        else:
            scope["lang"] = "zh"
        await self.app(scope, receive, send)
```

### 3.5 在端点中使用

```python
from vision_agent.i18n import get_message

# 在 create_app 中注册中间件
app.add_middleware(I18nMiddleware)

# 在端点中
@app.delete("/api/cameras/{camera_id}")
async def delete_camera(camera_id: str, request: Request):
    if not pipeline.get_camera_thread(camera_id):
        lang = request.scope.get("lang", "zh")
        raise HTTPException(status_code=404, detail=get_message("E2001", lang))
```

---

## 四、实现计划

### Phase 1：后端（1h）

1. 创建 `src/vision_agent/i18n/` 模块（`__init__.py` + `messages.py`）
2. 创建 `I18nMiddleware`
3. 替换 `app.py` 中所有硬编码 `detail=` 为 `get_message("Exxxx", lang)`
4. 替换 `auth/manager.py` 中 `ValueError` 为 `get_message("Exxxx")`
5. 运行 105 测试确保无回归

### Phase 2：前端（3h）

1. 创建 `i18n/zh.ts` + `i18n/en.ts` 消息表
2. 创建 `i18n/index.ts` 翻译函数
3. 改造所有 `.vue` 文件（~15 个），硬编码文字替换为 `t("xx.xx")`
4. 改造所有 `.ts` store 文件，`ElMessage.error` 用 `t("error.Exxxx")`
5. 顶栏加语言切换按钮
6. Element Plus 组件库语言包联动
7. 开发环境实测中英切换

### Phase 3：优化（0.5h）

1. 清除所有残留硬编码文字
2. 验证登录页/仪表盘/告警/摄像头/监控/用户管理 所有页面中英切换正常
3. 文档更新

---

## 五、代码量估算

| 阶段 | 新增行数 | 修改行数 |
|------|---------|---------|
| 后端 | ~150 | ~50 |
| 前端 | ~500 | ~300 |
| 总计 | ~650 | ~350 |

---

## 六、使用示例

```python
# 后端 — 修改前
raise HTTPException(status_code=404, detail="Camera cam_01 not found")

# 后端 — 修改后
raise HTTPException(status_code=404, detail=get_message("E2001", lang))

# 响应 body（浏览器语言 zh）→ {"detail": "摄像头不存在"}
# 响应 body（浏览器语言 en）→ {"detail": "Camera not found"}
```

```vue
<!-- 前端 — 修改前 -->
<el-button>添加</el-button>
<el-tag>在线</el-tag>

<!-- 前端 — 修改后 -->
<el-button>{{ t("camera.add") }}</el-button>
<el-tag>{{ t("camera.online") }}</el-tag>
```
