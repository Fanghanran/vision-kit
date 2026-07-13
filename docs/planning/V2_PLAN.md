# Vision Agent v2 — 产品规划

> 基于 v1 现有功能的分析，列出 v2 需要解决的问题和设计方案。
> ✅ = 已完成  ⏳ = 进行中  📋 = 设计完成待实现

---

## v2 已完成项摘要（2026-07-14）

### 工程架构
- 📋 配置文件拆分（1→11个文件）
- ✅ 摄像头内嵌 detector 配置
- ✅ 通知偏好后端落地
- ✅ 配置热加载增强（detector/recording/notification 运行时更新）
- ✅ 摄像头 enabled 字段（热加载）
- ✅ 视频标注框画帧同步

### 业务模块
- ✅ 规则管理模块（RuleManager + REST API + Rules.vue）
- ✅ 用户管理 v2（登录历史、活跃会话、邮箱校验、统计）
- ✅ 个人设置 v2（通知偏好、安全概览、登录历史、换色环）
- ✅ 摄像头管理 v2（统计卡片、搜索筛选、详情抽屉、RTSP脱敏）

### 测试
- 231 个 pytest（auth + rules + camera + pipeline + overlay）
- ✅ 三 Agent 开发工作流（写/查/测）

---

## 1. 摄像头配置持久化 ✅

## 2. 摄像头配置变更 ✅

## 3. 测试覆盖 ✅

## 4. Docker 部署 ✅（已删除，后续重做）

## 5. CI/CD ⏳（待后续）

## 6. API 文档 ✅

## 7. 前端日志 / 错误追踪 ✅

## 8. Session / Token 管理增强 ✅

## 9. test/video 模式按需出帧 ✅

---

## v2 新增待办

| 优先级 | 项目 | 工作量 | 状态 |
|--------|------|--------|------|
| P0 | YOLO 模型训练（安全帽 3 类） | 8h | 📋 设计完成 → docs/modules/core/model_training.md |
| P0 | GPU 推理加速 | 1h | 📋 换 GPU 后改 gpu.yaml |
| P1 | Agent 代码落地 | 16h | 📋 LangChain/Agno 双版设计文档完成 |
| P1 | RAG 知识库 | 8h | 📋 接口预留，待实现 |
| P2 | Docker 部署重做 | 3h | 📋 待模型训练完成后 |
| P2 | CI/CD（测试+部署） | 2h | ⏳ |
| P2 | 国际化（i18n） | 4h | 📋 设计完成 |
| P3 | README 架构文档 | 2h | 📋 |

---

## 当前进度

```
✅ Phase 1: 稳定性 → 全部完成
✅ Phase 2: 工程化 → 全部完成
✅ Phase 3: 体验 → 基本完成（7/8 项）
⏳ Phase 4: AI 功能 → 模型设计完成，待训练
⏳ Phase 5: Agent → 设计完成，待实现
⏳ Phase 6: 部署 → 待模型落地后重做
```

## 下一步

1. **换 GPU 硬件** → 推理帧率从 1 FPS 提到 25 FPS
2. **训练 3 类安全帽模型** → 替换 helmet.pt
3. **Agent 代码落地** → 选 LangChain 或 Agno
4. **录演示视频** → 面试用
