# Vision Agent — 开发进度

> 本文件记录当前开发状态，供新会话快速了解上下文。

## 项目概况

- **定位**：多路视频智能分析框架（看懂→想明白→做决定）
- **仓库**：https://github.com/Fanghanran/vision-kit.git（SSH: git@github.com:Fanghanran/vision-kit.git）
- **作者**：方瀚然
- **技术栈**：Python + FastAPI + YOLO + Vue 3

## 文档状态：✅ 全部完成

| 文档 | 路径 | 状态 |
|------|------|------|
| 架构设计 | docs/architecture.md (1200+行，17章) | ✅ |
| 模块设计书×15 | docs/modules/ (按模块分类) | ✅ |
| 端-云演进 | architecture.md 第16章 | ✅ |
| 安全设计 | architecture.md 第12章 | ✅ |
| README | README.md | ✅ |
| 进度文件 | docs/PROGRESS.md（本文件） | ✅ |

## 代码状态：6/15 模块完成

| 模块 | 文件 | 行数 | 测试 | Review | 状态 |
|------|------|------|------|--------|------|
| core/types.py | src/vision_agent/core/types.py | 415 | 43✅ | ✅ | 完成 |
| core/camera.py | src/vision_agent/core/camera.py | 395 | 31✅ | ✅ | 完成 |
| core/detector.py | src/vision_agent/core/detector.py | 415 | 35✅ | ✅ | 完成 |
| core/tracker.py | src/vision_agent/core/tracker.py | 457 | 26✅ | ✅ | 完成 |
| core/recorder.py | src/vision_agent/core/recorder.py | 338 | 26✅ | ✅ | 完成 |
| core/pipeline.py | src/vision_agent/core/pipeline.py | 1089 | 90✅ | ✅ | 完成 |
| config/settings.py | — | — | — | — | **下一个** |
| rules/engine.py | — | — | — | — | 待开发 |
| rules/builtin/*.py | — | — | — | — | 待开发 |
| storage/database.py | — | — | — | — | 待开发 |
| storage/cache.py | — | — | — | — | 待开发 |
| llm/analyzer.py | — | — | — | — | 待开发 |
| llm/provider.py | — | — | — | — | 待开发 |
| actions/notifier.py | — | — | — | — | 待开发 |
| web/api/*.py | — | — | — | — | 待开发 |
| __main__.py | — | — | — | — | 待开发 |

**总计**：代码 3109 行，测试 251 个，全部通过。

## 下一步开发顺序

```
core/pipeline.py      ← ✅ 完成（1089行，90测试，依赖 camera+detector+tracker+recorder）
config/settings.py    ← 现在开始（无依赖）
rules/engine.py       ← 依赖 types
rules/builtin/*.py    ← 依赖 engine
storage/database.py   ← 依赖 types
storage/cache.py      ← 无依赖
llm/analyzer.py       ← 依赖 types
llm/provider.py       ← 无依赖
actions/notifier.py   ← 依赖 types
web/api/*.py          ← 依赖全部
__main__.py           ← 依赖全部
```

## 工作流（每个模块三步走）

```
1. 我写代码（读设计书 → 写实现 → ruff check/format）
2. Review agent（读代码 + 设计书 → 5维度检查 → 输出报告）
3. Test agent（读代码 → 写 pytest → 运行 → 输出报告）
4. 根据 Review 修代码
5. 确认测试通过
6. git commit + push
```

## 已知的设计决策

- **Protocol 依赖注入**：所有核心接口用 Python Protocol 定义
- **三层队列**：采集→推理→处理，有界队列满则丢旧帧
- **错误处理**：每模块独立容错，LLM/Redis 可选增强
- **端-云预留**：camera/detector/pipeline 设计书已包含端-云扩展章节
- **安全**：API Token 认证、WebSocket 保护、日志脱敏、路径白名单

## 需要在新会话中告诉 Claude 的话

```
继续 Vision Agent 项目开发。项目在 d:/vision_agent/。
读 docs/PROGRESS.md 了解当前进度。
当前完成 6/15 模块（core/types, camera, detector, tracker, recorder, pipeline）。
下一个模块是 config/settings.py。
每个模块的工作流：写代码 → Review agent → Test agent → 修代码 → commit。
```


