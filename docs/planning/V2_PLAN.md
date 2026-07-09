# Vision Agent v2 — 产品规划

> 基于 v1 现有功能的分析，列出 v2 需要解决的问题和设计方案。
> 每个方案需明确：问题 → 方案 → 涉及文件 → 验收标准。

---

## 1. 摄像头配置持久化

**问题**：通过 API 添加的摄像头只存在内存中，服务重启后丢失。

**方案**：
- `POST /api/cameras` 创建摄像头时，同步写 YAML 文件到 `configs/cameras/{id}.yaml`
- 调用 `yaml.dump()` 写入摄像头配置，参考 `camera_01.yaml.example` 格式
- 添加失败时不写入；写入前校验配置合法性
- `DELETE /api/cameras/{id}` 删除时同步删除对应的 YAML 文件

**涉及文件**：
- `src/vision_agent/web/api/app.py`（create_camera / delete_camera）
- `src/vision_agent/core/pipeline.py`（add_camera / remove_camera 可选增强）

**验收**：API 添加摄像头 → 检查 `configs/cameras/` 下生成 YAML → 重启服务 → 摄像头仍在列表中

---

## 2. 摄像头配置变更持久化

**问题**：修改摄像头来源/帧率/分辨率等配置后，YAML 文件不更新。

**方案**：
- 新增 `PUT /api/cameras/{id}` 端点
- 更新内存中的 CameraThread（停止旧线程 → 创建新线程 → 启动）
- 同步写回 YAML 文件
- 支持字段：`name` / `rtsp_url` / `video_path` / `source_type` / `fps` / `resolution`

**涉及文件**：
- `src/vision_agent/web/api/app.py`
- `src/vision_agent/core/pipeline.py`

**验收**：API 修改摄像头帧率 → YAML 文件更新 → 重启后新帧率生效

---

## 3. 测试覆盖

**问题**：auth、监控面板、摄像头管理等新模块没有测试。

**方案**：
- 测试 AuthManager：用户 CRUD + 密码验证 + Token 签发/验证/过期 + 登录限流
- 测试认证 API：login/logout/me/change-password/profile + 权限控制
- 测试摄像头管理 API：toggle/create/delete + 并发安全
- 测试监控面板 WebSocket 端点：帧订阅/JPEG 编码/断线清理

**涉及文件**：
- `tests/test_auth.py`（新增）
- `tests/test_camera_api.py`（新增）
- `tests/test_video_ws.py`（新增）

**目标覆盖率**：>85%

---

## 4. Docker 部署

**问题**：无容器化方案，环境依赖复杂。

**方案**：
- `Dockerfile`：Python 3.10 + 后端依赖 + uvicorn 启动
- `Dockerfile.frontend`：Node 18 构建 → nginx 静态服务
- `docker-compose.yml`：backend + frontend + auth.db 持久卷
- 环境变量配置（`VISION_AGENT_CONFIG` / `VISION_AGENT_LOG_LEVEL`）

**涉及文件**：
- `Dockerfile`
- `Dockerfile.frontend`
- `docker-compose.yml`
- `.dockerignore`

**验收**：`docker-compose up` → 服务可访问 → 登录 → 摄像头状态正常显示

---

## 5. CI/CD

**问题**：无自动化测试和质量检查。

**方案**：
- GitHub Actions workflow
- 触发：push to master / PR
- 步骤：checkout → setup python → install dependencies → ruff lint → pytest
- 前端：setup node → install → typecheck → build
- 可选：Docker image build + push

**涉及文件**：
- `.github/workflows/ci.yml`（新增）

**验收**：push 代码 → CI 自动运行 → 839 测试通过 → status badge 绿色

---

## 6. API 文档

**问题**：FastAPI 自动生成的 `/docs` 缺少中文描述和分组标签。

**方案**：
- 所有端点添加 `tags` 参数分组（系统/告警/摄像头/认证/监控）
- 添加 `summary` 和 `description` 中文描述
- 响应模型添加 `responses` 文档（401/403/404 等标准错误）
- `create_app` 的 `FastAPI()` 参数加 `docs_url` / `redoc_url` 控制

**涉及文件**：
- `src/vision_agent/web/api/app.py`

**验收**：访问 `/docs` → 端点按标签分组 → 中文描述可读 → Try it out 可正常调用

---

## 7. 前端日志 / 错误追踪

**问题**：用户操作失败时控制台只有 `console.error`，没有结构化日志。

**方案**：
- 前端统一错误处理：所有 `ElMessage.error` 复用同一个格式化函数
- 可选：接入 Sentry（`@sentry/vue`）用于生产环境错误追踪

**涉及文件**：
- `frontend/src/utils/error.ts`（新增）
- `frontend/src/api/client.ts`

**验收**：操作失败 → 统一格式的 ElMessage + console 日志包含时间戳和请求信息

---

## 8. Session / Token 管理增强

**问题**：
- 当前 Token 存在内存中，服务重启后所有 Token 失效
- 不支持查看/撤销其他用户的会话

**方案**：
- Token 表持久化到 `auth.db`（`CREATE TABLE tokens`）
- `GET /api/users/{username}/sessions`：admin 查看用户会话
- `DELETE /api/users/{username}/sessions`：admin 撤销所有会话
- `POST /api/auth/logout-all`：用户退出所有设备

**涉及文件**：
- `src/vision_agent/auth/manager.py`
- `src/vision_agent/web/api/app.py`

**验收**：重启服务 → Token 仍然有效 → admin 可查看/撤销会话

---

## 9. test/video 模式按需出帧

**问题**：test 和 video 模式摄像头开机即出帧入队列进推理，无人观看时空耗 CPU/内存。

**方案**：
- CameraThread 帧生成循环中检查 `_frame_subscribers` 是否为空
- 无订阅者 → 只 sleep 不生成帧不推队列
- 有订阅者（WebSocket 视频流连接） → 正常出帧
- RTSP 来源不适用此逻辑——需要持续检测告警 + 录制历史录像

**涉及文件**：
- `src/vision_agent/core/camera.py`（`_run_test_loop` / `_run_video_loop`）

**验收**：无监控页面连接时 test/video 摄像头帧序列不变，CPU 降低；打开监控页面后 200ms 内恢复出帧

---

## 优先级排序

| 优先级 | 项目 | 工作量 | 状态 |
|--------|------|--------|------|
| P0 | 摄像头配置持久化 | 2h | ✅ 已完成 |
| P0 | 摄像头配置变更 | 2h | ✅ 已完成 |
| P0 | 测试覆盖（839→105） | 4h | ✅ 已完成 |
| P1 | Docker 部署 | 3h | ✅ 已完成 |
| P2 | CI/CD | 1h | ✅ 已完成 |
| P2 | 前端错误追踪 + 中文化 | 1h | ✅ 已完成 |
| P2 | API 文档 | 1h | ✅ 已完成 |
| P3 | Session 持久化 | 2h | ⏳ |
| P3 | 国际化（i18n） | 4h | 📋 设计完成，待实现 |
| P3 | test/video 按需出帧 | 0.5h | 📋 待实现 |

## v2 开发顺序

```
Phase 1: 稳定性 ✅ 全部完成
  ├── 摄像头配置持久化
  ├── 摄像头配置变更
  ├── 测试覆盖（839→105）
  └── 前端错误追踪优化 + 全项目中文化

Phase 2: 工程化 ✅ 全部完成
  ├── CI/CD
  └── Ruff 代码检查

Phase 3: 体验
  ├── API 文档 ✅
  ├── Docker 部署 ✅
  ├── test/video 按需出帧 ✅
  ├── Session 持久化 ⏳
  └── 国际化（i18n）📋 设计完成→ 详见 docs/planning/I18N_DESIGN.md
```
