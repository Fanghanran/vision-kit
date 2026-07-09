# Vision Agent — 测试方案 v2

> 替换原有 839 个测试，精简为核心功能的集成测试 + 关键单元测试。
> 原则：只测暴露给用户的接口，不测内部实现细节。

---

## 测试文件清单

| 文件 | 覆盖模块 | 数量 | 理由 |
|------|---------|------|------|
| `tests/test_auth.py` | 认证体系 | ~20 | 用户登录/权限是新模块的核心 |
| `tests/test_camera_api.py` | 摄像头管理 API | ~15 | toggle/create/delete 是 v1 新增功能 |
| `tests/test_video_ws.py` | 监控面板 WebSocket | ~10 | 帧推送是核心体验 |
| `tests/test_pipeline.py` | 采集-推理-处理链路 | ~25 | 主流程，保留精简 |
| `tests/test_web_api.py` | REST API | ~30 | 告警/统计/配置/健康检查 |
| `tests/test_types.py` | 数据模型序列化 | ~15 | Alert/Event/CameraState 序列化 |
| `tests/test_settings.py` | 配置加载/校验/热加载 | ~20 | 配置是运维入口 |

**总计 ~135 个聚焦测试，替换原有 839 个。**

---

## 一、认证体系 `test_auth.py`（~20 个）

### AuthManager

- 创建用户成功 / 用户名重复报错
- 密码哈希与验证（正确/错误）
- Token 签发与验证
- Token 过期（修改系统时间快速模拟）
- 登录限流：5 次失败后锁定 5 分钟
- 禁用用户登录失败
- 更新用户信息（邮箱/角色/状态/头像色）
- 删除用户（默认 admin 不可删）

### Auth API

- `POST /api/auth/login` 正确返回 token + user
- `POST /api/auth/login` 错误密码返回 401
- `GET /api/auth/me` 带 token 返回用户
- `GET /api/auth/me` 无 token 返回 401
- `POST /api/auth/change-password` 修改后旧密码失效
- `PUT /api/auth/profile` 更新邮箱
- `GET /api/users` 普通用户返回 403
- `POST /api/users` admin 创建用户
- `DELETE /api/users/{name}` admin 删除用户
- 路由守卫：未登录访问受保护页面 → 401

---

## 二、摄像头管理 `test_camera_api.py`（~15 个）

- `GET /api/cameras` 返回列表
- `POST /api/cameras/{id}/toggle` 停止后再启动
- `POST /api/cameras` 添加 test 模式摄像头
- `POST /api/cameras` 重复 id 返回 409
- `POST /api/cameras` 无效 id 返回 400
- `DELETE /api/cameras/{id}` 删除后列表不包含
- `DELETE /api/cameras/{id}` 不存在返回 404
- pipeline `get_camera_thread` 加锁并发安全

---

## 三、监控面板 WebSocket `test_video_ws.py`（~10 个）

- WebSocket 连接 `/ws/video/cam_01` 成功
- 收到二进制帧（8 字节头 + JPEG）且数据合理
- 摄像头不存在返回 1008
- 断线时订阅队列清理
- 多客户端同时订阅不互相影响
- `subscribe_frames` / `unsubscribe_frames` 生命周期
- JPEG 编码 cv2 / PIL 双后端降级

---

## 四、Pipeline 主流程 `test_pipeline.py`（~25 个）

保留原有的核心集成测试：
- 摄像头启动/停止
- 帧从采集到推理队列
- 检测结果生产
- 优雅关闭顺序
- 健康检查字段齐全
- 队列满丢帧逻辑

砍掉：
- 内部实现细节（mock 过度）
- 工具函数单独测试

---

## 五、Web API `test_web_api.py`（~30 个）

- 健康检查 `/health`
- 告警列表分页/筛选
- 告警详情/截图/视频
- 告警状态流转
- 统计端点
- 配置脱敏
- 路径白名单：未知路径 404
- 回放 API `replay` / `timeline`
- CORS 响应头

---

## 六、数据模型 `test_types.py`（~15 个）

- Alert/Event/CameraState 等核心模型的 to_dict/from_dict 往返
- 枚举序列化/反序列化安全回退
- User 模型新字段序列化
- Alert 状态流转合法性

---

## 七、配置 `test_settings.py`（~20 个）

- settings.yaml 加载 + 默认值合并
- 环境变量替换
- 全局校验（必填/范围/路径）
- 摄像头配置校验
- **热加载**：修改安全字段 → 配置变化通知
- **热加载**：修改不可变字段 → WARNING 日志
- `deep_merge` 合并逻辑

---

## 对比

| 维度 | 旧方案 | 新方案 |
|------|--------|--------|
| 测试数量 | 839 | ~135 |
| 运行时间 | ~4min | ~30s |
| 覆盖范围 | 每模块明细 | 核心接口 + 集成 |
| 新增模块覆盖 | 无 | auth / camera API / video WS |
| 维护成本 | 高（mock 过多） | 低（测试接口契约） |
