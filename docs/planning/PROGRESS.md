# Vision Agent — 开发进度

> 最后更新: 2026-07-14

## 项目概况

- **定位**：多路视频智能分析框架（看懂→想明白→做决定）
- **仓库**：https://github.com/Fanghanran/vision-kit.git
- **作者**：方瀚然
- **技术栈**：Python + FastAPI + YOLO + Vue 3 + SQLite
- **测试**：231 个全部通过（auth + rules + camera + pipeline + overlay + hot_reload + prefs）
- **模型**：helmet.pt (39MB, 2类) + yolov8n.pt (6MB) + 训练文档已完成

---

## 代码状态

### 后端模块（19 个）

| 模块 | 状态 | 版本 |
|------|------|------|
| core/types.py | ✅ | — |
| core/camera.py | ✅ | v2 — 新增 source_type/rtsp_url/width/height 属性 + enabled 字段 + _has_subscribers |
| core/detector.py | ✅ | v2 — 新增 set_confidence/set_iou_threshold/set_input_size 热加载setter |
| core/tracker.py | ✅ | — |
| core/recorder.py | ✅ | v2 — 新增 hot_update_config 热加载 |
| core/pipeline.py | ✅ | v2 — 新增通知偏好、hot_update、_rebuild_notifiers、_set_config_reference、_draw_and_encode、get_last_frame_jpeg |
| core/exceptions.py | ✅ | — |
| config/settings.py | ✅ | v2 — 配置拆分合并、摄像头热加载、enabled 字段、per-camera detector、热加载白名单 |
| rules/engine.py | ✅ | v2 — 已适配 rules.yaml 单文件加载 + rule_items 键名 + 热重载 |
| rules/manager.py | ✅ | **新增** — 规则文件 CRUD + 校验（读写 rules.yaml） |
| storage/database.py | ✅ | — |
| storage/cache.py | ✅ | — |
| llm/analyzer.py | ✅ | — RAG 接口预留 |
| llm/provider.py | ✅ | — |
| actions/notifier.py | ✅ | — |
| web/api/app.py | ✅ | v2 — 新增规则/摄像头统计/摄像头详情/用户统计/会话管理/登录历史/偏好设置/检测框推流 |
| web/api/rules.py | ✅ | **新增** — 规则 REST API（三模式：查/写/测） |
| auth/models.py | ✅ | v2 — 新增 LoginHistoryEntry、ActiveSession |
| auth/manager.py | ✅ | v2 — 新增登录历史、活跃会话、用户统计、邮箱唯一校验、通知偏好、用户详情 |
| __main__.py | ✅ | v2 — config watcher 回调、热加载线程启动、摄像头 enabled 过滤 |

### 前端（Vue 3）

| 页面 | 状态 | 版本 |
|------|------|------|
| Dashboard | ✅ | — |
| AlertList / AlertDetail | ✅ | — |
| Cameras | ✅ | **v2** — 统计卡片、搜索筛选、详情抽屉、自定义检测参数 |
| Monitor | ✅ | — |
| Rules | ✅ | **新增** — 三模式：查（列表+详情）/写（创建/编辑）/测（干跑校验） |
| System | ✅ | — |
| Login | ✅ | — |
| Profile | ✅ | **v2** — 通知设置/安全设置（会话+改密+登录历史）/头像换色环 |
| Users | ✅ | **v2** — 统计卡片、搜索筛选、右侧资料抽屉、权限详情、会话强制下线 |
| 状态管理（6 个 stores）| ✅ | auth/cameras/alerts/rules/system — cameras 新增 stats/fetchDetail |
| API 层（6 个） | ✅ | alerts/cameras/rules/system/auth — 新增 rules.ts |
| WebSocket | ✅ | v2 — 视频流改为轮询缓存（帧框同步），前端兼容 JSON 心跳 |

---

## 配置体系（v2 重构）

```
configs/
├── cameras.yaml      # 摄像头 + 全局 detector（每路可嵌入 detector 覆盖）
├── rules.yaml         # 规则列表（rule_items）
├── system.yaml        # 系统基础 / 日志 / 数据目录
├── gpu.yaml           # GPU 编号 / batch / FP16
├── llm.yaml           # LLM 服务商 / API / 预算 / RAG
├── notification.yaml  # Webhook / Email 通知渠道
├── server.yaml        # Web 端口 / CORS
├── storage.yaml       # SQLite + Redis
└── recording.yaml     # 录像保留策略
```

**热加载覆盖范围**：
- ✅ detector.confidence / iou_threshold / input_size → 运行时 setter
- ✅ recording.retention_days / snapshot_retention_days → recorder.hot_update_config
- ✅ notification.* → _rebuild_notifiers()
- ✅ cameras.yaml → 新增/删除/修改/启用/禁用（热重载）
- ✅ rules.yaml → RuleEngine 5秒扫描
- ✅ 通知偏好 → _get_admin_preferences() 60秒缓存

---

## 文档体系

| 目录 | 内容 |
|------|------|
| docs/architecture.md | 架构设计 |
| docs/modules/core/ | camera / detector / pipeline / recorder / tracker / exceptions / camera_management / model_training / notification_preferences / detection_overlay_plan |
| docs/modules/auth/ | user_management / profile_settings |
| docs/modules/rules/ | rule_engine / builtin_rules / rule_management |
| docs/modules/config/ | config.md / config_split / config_merge_detector |
| docs/modules/web/ | web_api |
| docs/modules/storage/ | database / cache |
| docs/modules/llm/ | llm_analyzer / llm_provider |
| docs/modules/actions/ | notifier |
| docs/designs/agent-langchain/ | 完整 Agent 架构（记忆/工具/Skills/调度/路由） |
| docs/designs/agent-agno/ | Agno 版 Agent 架构 |
| docs/designs/three-agent-workflow.md | 三 Agent 开发工作流 |
| docs/frontend/ | DESIGN / MONITOR_PANEL |
| docs/planning/ | V2_PLAN / TODO / PROGRESS / I18N_DESIGN |

---

## 关键架构决策

- **Protocol 依赖注入**：所有核心接口用 Python Protocol 定义
- **三层队列**：采集→推理→处理，有界队列满则丢旧帧
- **帧框同步**：InferenceThread 推理完成后在同一帧上画框编码JPEG，WS 从缓存取
- **通知偏好**：admin 用户偏好 = 系统级通知策略，60秒缓存
- **RBAC 三级**：admin / operator / viewer
- **配置文件拆分**：11 个 YAML，ConfigManager 自动合并
- **热加载**：detector 参数 / recorder 策略 / notification URL / 摄像头增删改 / 规则变更
- **三 Agent 开发流程**：写→查→测，各司其职

---

## 当前卡点

| 项目 | 说明 |
|------|------|
| ⏳ GPU 硬件 | CPU 跑 helmet.pt → ~1 FPS，GPU 可到 25 FPS |
| ⏳ 模型训练 | 3 类安全帽模型设计完成，待训练 |
| ⏳ Agent 代码 | LangChain/Agno 双版设计完成，待实现 |
| ⏳ Docker | 等模型落地后重做 |

---

## 工作流

```
1. 写代码（读设计书 → 写实现）
2. Review agent（审阅）
3. Test agent（测试）
4. 根据 Review 修代码
5. 确认测试通过
6. git commit + push（等用户指示）
```
