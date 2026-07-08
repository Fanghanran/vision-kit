# Vision Agent — 开发进度

> 本文件记录当前开发状态，供新会话快速了解上下文。

## 项目概况

- **定位**：多路视频智能分析框架（看懂→想明白→做决定）
- **仓库**：https://github.com/Fanghanran/vision-kit.git
- **作者**：方瀚然
- **技术栈**：Python + FastAPI + YOLO + Vue 3
- **测试**：839 个全部通过

## 文档状态：✅ 全部完成

| 文档 | 路径 | 状态 |
|------|------|------|
| 架构设计 | docs/architecture.md (1200+行，17章) | ✅ |
| 模块设计书×15 | docs/modules/ | ✅ |
| 前端设计书 | docs/frontend/DESIGN.md | ✅ |
| 监控面板设计书 | docs/frontend/MONITOR_PANEL.md | ✅ |
| 优化清单 | docs/OPTIMIZATION.md | ✅ |
| 进度文件 | docs/PROGRESS.md | ✅ |
| README | README.md | ✅ |

## 代码状态

### 后端模块（18 个）

| 模块 | 状态 |
|------|------|
| core/types.py | ✅ |
| core/camera.py | ✅ |
| core/detector.py | ✅ |
| core/tracker.py | ✅ |
| core/recorder.py | ✅ |
| core/pipeline.py | ✅ |
| core/exceptions.py | ✅ |
| config/settings.py | ✅ |
| rules/engine.py | ✅ |
| rules/builtin/*.py | ✅ |
| storage/database.py | ✅ |
| storage/cache.py | ✅ |
| llm/analyzer.py | ✅ |
| llm/provider.py | ✅ |
| actions/notifier.py | ✅ |
| web/api/app.py | ✅ |
| auth/models.py | ✅ 新增 |
| auth/manager.py | ✅ 新增 |
| __main__.py | ✅ |

### 前端（Vue 3）

| 页面 | 状态 |
|------|------|
| Dashboard | ✅ |
| AlertList / AlertDetail | ✅ |
| Cameras | ✅ |
| Monitor | ✅ 新增（视频监控面板） |
| System | ✅ |
| Login | ✅ 新增（登录页面） |
| Profile | ✅ 新增（个人设置） |
| Users | ✅ 新增（用户管理，仅 admin） |
| 状态管理（5 个 stores） | ✅ |
| API 层（5 个） | ✅ |
| WebSocket | ✅ |

## 优化清单状态

| 项目 | 状态 |
|------|------|
| WebSocket 403 | ✅ 已修复 |
| 视频监控面板 | ✅ 已完成 |
| 用户角色系统 | ✅ 已完成 |
| 摄像头管理 | ✅ 已完成 |
| 前端卡顿优化 | ✅ 已完成 |
| 全局配置热加载 | ✅ 已完成 |
| FPS 精度 | ✅ 已完成 |
| 帧数异常 | ✅ 已完成 |
| 帧率自动检测 | ✅ 已完成 |
| Docker 部署 | ⏳ 暂缓 |
| 性能优化 | ✅ 基础完成 |

## 已知设计决策

- **Protocol 依赖注入**：所有核心接口用 Python Protocol 定义
- **三层队列**：采集→推理→处理，有界队列满则丢旧帧
- **用户认证**：SQLite 持久化，PBKDF2-SHA256 密码哈希，Token 24h 过期
- **权限**：admin / operator / viewer 三级角色
- **端-云预留**：camera/detector/pipeline 设计书已包含端-云扩展章节
- **安全**：API Token 认证、路径白名单 ASGI 中间件、日志脱敏

## 工作流

```
1. 写代码（读设计书 → 写实现）
2. Review agent（审阅）
3. Test agent（测试）
4. 根据 Review 修代码
5. 确认测试通过
6. git commit + push（等用户指示）
```
