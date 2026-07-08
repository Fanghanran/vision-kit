# 优化清单

> 记录优化方向及完成状态，按优先级排序。

## 1. 全局配置选择性热加载

**状态**：✅ 已完成
**优先级**：中
**影响**：运维体验

### 实现

- ConfigManager 新增 `_GLOBAL_HOT_RELOADABLE` 白名单（`system.log_level` / `llm.*` / `notification.*` / `rules.*`）
- ConfigManager 新增 `_GLOBAL_IMMUTABLE_WHEN_CHANGED` 不可变字段集合
- `_hot_reload_loop` 中新增 `_check_global_config_changes`：mtime 检测 → 安全字段直接热更新 → 不可变字段记录 WARNING 提示重启
- 新增 `_set_nested()` 按点分路径写入字典

---

## 2. 视频监控面板

**状态**：✅ 已完成
**优先级**：高
**影响**：核心功能

### 实现

- 后端 `/ws/video/{camera_id}`：WebSocket JPEG 实时帧推送（8 字节帧头 + JPEG）
- 后端 `/api/cameras/{id}/replay`：历史录像 MP4 查询
- 后端 `/api/cameras/{id}/timeline`：某天有录像的时间段
- CameraThread 帧订阅机制 `subscribe_frames()` / `unsubscribe_frames()`
- 前端 Monitor.vue：布局选择（1×1/2×2/3×3/4×4）+ 视频网格 + 右键菜单 + 全屏
- 前端 VideoPlayer.vue：WebSocket 接收 JPEG 渲染 + 信息栏 + FPS 显示
- 前端 useVideoStream.ts：WebSocket 连接 + 断线自动重连（指数退避 1s→10s）

---

## 3. 用户角色系统

**状态**：✅ 已完成
**优先级**：高
**影响**：安全

### 实现

- auth/models.py：Role 枚举（admin/operator/viewer）、UserStatus（0=正常/1=禁用）、User 数据模型
- auth/manager.py：AuthManager（SQLite 持久化，PBKDF2-SHA256 密码哈希）
- Token 认证：随机 urlsafe Token，24h 过期，常数时间比较
- 登录限流：5 次失败锁定 5 分钟
- API：`/api/auth/login` / `logout` / `me` / `change-password` / `profile`
- API：`/api/users` CRUD（admin only）
- 前端 Login.vue：全屏独立登录页，左右分栏品牌展示
- 前端 Profile.vue：独立全屏个人设置页（头像色、邮箱、改密码）
- 前端 Users.vue：用户管理（列表 + 详情 + 编辑 + 删除 + 状态开关），仅 admin 可见
- 路由守卫：未登录跳转 /login，非 admin 拦截 /users

### 权限矩阵

| 操作 | admin | operator | viewer |
|------|-------|----------|--------|
| 查看告警 | ✅ | ✅ | ✅ |
| 确认/标记误报 | ✅ | ✅ | ❌ |
| 修改配置 | ✅ | ❌ | ❌ |
| 管理用户 | ✅ | ❌ | ❌ |
| 查看摄像头 | ✅ | ✅ | ✅ |
| 控制摄像头 | ✅ | ✅ | ❌ |

---

## 4. WebSocket 端点兼容性修复

**状态**：✅ 已完成
**优先级**：中
**影响**：实时推送功能

### 根因

`from __future__ import annotations` 导致 `WebSocket` 注解变成字符串 `'WebSocket'`，FastAPI 在模块全局中找不到该类型，端点注册失败返回 403。

### 修复

- 注入 `_module.WebSocket = WebSocket` 到模块全局
- 路径白名单中间件改为 ASGI 原始中间件（兼容 WebSocket）

---

## ~~5. Docker 部署~~（暂缓）

---

## 6. 性能优化

**状态**：✅ 基础完成
**优先级**：低
**影响**：大规模部署

### 已完成

- 数据库索引已完备（`idx_alerts_created_at` / `camera_status` / `event_type` / `severity`）
- 推理延迟 P50/P99 保留两位小数

---

## 7. FPS 计算精度

**状态**：✅ 已完成
**优先级**：低
**影响**：显示准确性

### 实现

- `_calculate_fps` 改为 1 秒滑动窗口实时帧率（`_update_fps_window` ± 丢弃无意义累计平均）
- 录像模式自动检测视频原始帧率（`_resolve_fps`：test→25fps, video→cv2.CAP_PROP_FPS, rtsp→15fps）

---

## 8. 前端卡顿

**状态**：✅ 已完成
**优先级**：高
**影响**：用户体验

### 实现

- **图表优化**：`setOption({...}, { notMerge: false })` 增量更新，不重建 ECharts
- **轮询优化**：Dashboard 轮询 10s→30s，页面隐藏（`visibilitychange`）暂停轮询
- **WebSocket 防抖**：`requestAnimationFrame` 合并同帧消息 + 去重

---

## 9. 摄像头总帧数计算异常

**状态**：✅ 已完成
**优先级**：中
**影响**：数据准确性

### 实现

- 移除无意义的累计帧数 `total_frames`（前端不再显示）
- 重连时重置全部计数器（`_frame_seq` / `_total_frames` / `_total_detections` / `_total_alerts` / `_start_time`）
- FPS 窗口参数重连时归零

---

## 10. 摄像头管理功能

**状态**：✅ 已完成
**优先级**：高
**影响**：核心功能

### 实现

- API：`POST /api/cameras/{id}/toggle`（开关摄像头）
- API：`POST /api/cameras`（添加摄像头）
- API：`DELETE /api/cameras/{id}`（删除摄像头）
- 前端 Cameras.vue：启停按钮 + 删除按钮 + 添加弹窗（来源类型/帧率/分辨率）
- pipeline：`get_camera_thread` / `get_camera_states` 加锁防并发
- camera_id 正则校验防路径遍历
