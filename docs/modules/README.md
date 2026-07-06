# 模块设计书

按模块分类的详细设计文档。

## 目录

### core/ — 核心管线
- [camera.md](core/camera.md) — 摄像头管理（FFmpeg采集、断线重连）
- [detector.md](core/detector.md) — 检测器（YOLO封装、batch推理）
- [tracker.md](core/tracker.md) — 追踪器（BoT-SORT、轨迹管理）
- [recorder.md](core/recorder.md) — 录制器（环形缓冲、告警截取）
- [pipeline.md](core/pipeline.md) — 主处理管线（三层线程编排）

### rules/ — 规则引擎
- [rule_engine.md](rules/rule_engine.md) — 规则引擎（三层防线、YAML加载）
- [builtin_rules.md](rules/builtin_rules.md) — 内置规则（闯入/离岗/聚集/遗留物/计数）

### config/ — 配置管理
- [config.md](config/config.md) — 配置管理（YAML加载、环境变量、校验）

### storage/ — 存储层
- [database.md](storage/database.md) — 数据库（SQLite/PostgreSQL、告警CRUD）
- [cache.md](storage/cache.md) — 缓存（Redis/内存降级）

### llm/ — LLM 集成
- [llm_analyzer.md](llm/llm_analyzer.md) — LLM分析器（Prompt构造、RAG预留）
- [llm_provider.md](llm/llm_provider.md) — LLM提供者（断路器、重试、预算）

### actions/ — 行动执行
- [notifier.md](actions/notifier.md) — 通知器（Webhook、邮件）

### web/ — Web 界面
- [web_api.md](web/web_api.md) — Web API（REST端点、WebSocket、认证）

### 根目录
- [main.md](main.md) — 主入口（启动流程、信号处理、优雅关闭）
