# Vision Agent

多路视频智能分析框架 — 让任何视频流拥有**看懂、想明白、做决定**的能力。

Vision Agent 不是又一个目标检测工具。它把 YOLO 的感知能力、规则引擎的判断能力、LLM 的理解能力串联起来，实现从"检测到异常"到"告诉人该怎么办"的完整链路。

```
摄像头RTSP流 → YOLO检测 → 多目标追踪 → 规则引擎 → LLM分析 → 通知
                                                          ↓
                                                  "3号仓库检测到人员闯入，
                                                   已持续2分钟，建议立即派人查看"
```

---

## 核心特性

**多路实时分析** — 同时接入多路 RTSP 摄像头，YOLO 检测 + BoT-SORT 追踪，16G GPU 可跑 8-10 路

**规则引擎** — YAML 声明式配置，内置 5 种规则（闯入/离岗/聚集/遗留物/计数），支持 Python 自定义扩展

**LLM 智能分析** — 告警触发后自动调用 LLM 分析截图，输出结构化的"发生了什么 + 风险等级 + 建议措施"

**RAG 知识检索（预留）** — 后期接入知识库，LLM 分析时参考历史案例、公司 SOP、处置规范

**弹性容错** — 摄像头断线自动重连，GPU 崩溃不拖垮系统，LLM/Redis 不可用时降级运行

**安全设计** — API 认证、WebSocket 保护、日志脱敏、路径白名单、截图访问控制

**插件化架构** — 检测器/规则/行动三个扩展点，Protocol 接口定义，YAML + Python 双模式扩展

---

## 快速开始

### 环境要求

- Python ≥ 3.10
- NVIDIA GPU（推荐 16GB 显存）
- CUDA ≥ 11.8
- FFmpeg

### 安装

```bash
git clone https://github.com/Fanghanran/vision-kit.git
cd vision-kit
pip install -r requirements.txt
pip install -e .
```

### 下载模型

```bash
# 创建模型目录
mkdir -p models

# 下载 YOLO 模型（约 6MB，推荐用于测试）
wget https://github.com/ultralytics/assets/releases/download/v8.2.0/yolov8n.pt -O models/yolov8n.pt

# 或使用更大的模型（需要更多显存）
# wget https://github.com/ultralytics/assets/releases/download/v8.2.0/yolov8m.pt -O models/yolov8m.pt
```

模型文件不提交到 Git（体积大），需手动下载。

### 配置

```bash
# 复制配置模板
cp configs/settings.yaml.example configs/settings.yaml
cp configs/cameras/camera_01.yaml.example configs/cameras/cam_01.yaml

# 设置环境变量
export LLM_API_KEY="your-api-key"
export API_TOKEN="your-api-token"
export WEBHOOK_URL="https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx"

# 编辑配置
# - configs/settings.yaml    → 全局配置（模型、LLM、通知）
# - configs/cameras/cam_01.yaml → 摄像头配置（RTSP 地址、规则）
```

### 启动

```bash
# 启动后端
python -m vision_agent --config configs/settings.yaml

# 新终端：启动前端
cd frontend
npm install
npm run dev
```

启动后：
- 后端 API：`http://localhost:8080`
- 前端界面：`http://localhost:3000`
- API 文档：`http://localhost:8080/docs`

---

## 架构概览

```
┌─────────── 采集层 ──────────┐
│ CameraThread × N (FFmpeg)   │
│ 每路独立线程，断线自动重连    │
└───────────┬─────────────────┘
            ↓ FrameQueue (有界队列，满则丢旧帧)
┌─────────── 推理层 ──────────┐
│ InferenceThread × 1         │
│ YOLO batch推理 + BoT-SORT   │
└───────────┬─────────────────┘
            ↓ ResultQueue
┌─────────── 处理层 ──────────┐
│ ActionThread × 1            │
│ 规则引擎 → 告警 → LLM分析   │
│ → 通知 → 存储               │
└─────────────────────────────┘
```

核心设计原则：
- **三层队列解耦**：采集、推理、处理各自独立，互不阻塞
- **有界队列丢旧帧**：宁可丢几帧也不能让延迟累积
- **任何单点故障不传播**：摄像头断线不影响其他路，GPU 崩溃自动降级

完整架构设计见 [docs/architecture.md](docs/architecture.md)。

---

## 规则配置

### YAML 声明式规则（推荐）

```yaml
# configs/rules/intrusion.yaml
name: 区域闯入
description: 有人进入禁止区域
camera_ids: ["cam_01", "cam_03"]

conditions:
  - type: object_in_zone
    classes: ["person"]
    zone: [[100,200], [500,200], [500,400], [100,400]]
    persist: 10s
    confidence: 0.6

actions:
  - type: notify
    channels: ["wechat"]
  - type: record_clip
    before: 15s
    after: 15s
  - type: llm_analyze
    prompt: "分析当前画面，判断闯入者的意图和危险等级"
```

### 内置规则

| 规则 | 条件 | 典型场景 |
|------|------|---------|
| 区域闯入 | 目标出现在禁止区域 + 持续 N 秒 | 仓库禁区有人进入 |
| 离岗检测 | 指定区域在工作时间无人 | 前台/门卫岗位无人 |
| 人员聚集 | 一个区域人数超过阈值 | 工厂门口人群聚集 |
| 遗留物 | 物体停留超过 N 分钟 | 行李/包裹无人认领 |
| 人数统计 | 经过某条计数线的人数 | 出入口客流统计 |

### 三层防线（防误报）

1. **滑动窗口去重** — 连续 N 帧检测到才算事件
2. **冷却时间** — 同一摄像头同一规则，冷却期内不重复告警
3. **时间窗口** — 可配置规则只在特定时间段生效

### Python 自定义规则

```python
# plugins/my_rule.py
from vision_agent.rules.engine import RuleProtocol
from vision_agent.core.types import Track, Event

class ForkliftSpeedRule:
    """叉车超速检测"""
    
    def __init__(self, speed_limit: float = 5.0, **kwargs):
        self.speed_limit = speed_limit
    
    @property
    def name(self) -> str:
        return "forklift_speed"
    
    def evaluate(self, tracks: list[Track], frame, context: dict) -> Event | None:
        for track in tracks:
            if track.class_name == "forklift":
                speed = (track.velocity[0]**2 + track.velocity[1]**2) ** 0.5
                if speed > self.speed_limit:
                    return Event(event_type="speed_violation", ...)
        return None
```

```yaml
# configs/rules/forklift_speed.yaml
name: 叉车超速
module: plugins/my_rule.py
class: ForkliftSpeedRule
params:
  speed_limit: 5.0
```

---

## LLM 集成

### 工作原理

LLM 不做检测（YOLO 做），LLM 做的是**理解检测结果并给出处理建议**。

```
规则引擎触发告警
  ↓
截图 + 事件上下文（摄像头、类型、持续时间、历史记录）
  ↓
LLM 输出：
  - 情况描述："仓库B区3号摄像头检测到1名未戴安全帽人员"
  - 风险等级：中
  - 建议措施："通知现场安全员前往提醒"
  - 历史关联："该区域近7天第3次类似违规"
```

### 配置

```yaml
llm:
  enabled: true
  provider: openai                          # OpenAI 兼容接口
  base_url: https://dashscope.aliyuncs.com/compatible-mode/v1
  api_key: ${LLM_API_KEY}
  model: qwen-plus
  max_tokens: 1000
  timeout: 30
  max_retries: 2
  monthly_budget_usd: 50
```

支持任何 OpenAI 兼容的 API（通义千问、DeepSeek、Moonshot 等）。后期可切换本地模型。

### RAG 知识检索（预留）

后期接入知识库，LLM 分析时自动检索相关历史案例、公司 SOP、处置规范：

```yaml
rag:
  enabled: false              # 第一版关闭
  vector_store: chromadb
  persist_dir: data/vector_db
  top_k: 5
  knowledge_dir: data/knowledge
```

详见 [docs/architecture.md](docs/architecture.md) 第 8.2 节。

---

## 通知

### Webhook（企微/钉钉/飞书）

```yaml
notification:
  webhook:
    enabled: true
    url: ${WEBHOOK_URL}
```

### 邮件

```yaml
notification:
  email:
    enabled: true
    smtp_host: smtp.example.com
    smtp_port: 465
    username: ${EMAIL_USER}
    password: ${EMAIL_PASS}
    to: ["admin@example.com"]
```

### 通知内容示例

```
🚨 区域闯入告警

摄像头：仓库A入口 (cam_01)
时间：2026-07-06 14:30:15
持续时间：2分钟

【LLM 分析】
情况描述：仓库A入口禁区检测到1名人员，疑似巡检人员误入。
风险等级：中
建议措施：通过对讲机确认身份并引导离开。
历史关联：该区域近7天第3次类似违规。

截图：http://localhost:8080/api/alerts/xxx/snapshot
```

---

## Web 界面

- **实时画面** — 多路摄像头九宫格，支持点击放大
- **告警列表** — 时间倒序，带截图缩略图，点击查看 LLM 分析详情
- **系统状态** — GPU 使用率、队列深度、各路 FPS、推理延迟
- **配置管理** — 摄像头配置、规则配置

---

## 安全

| 措施 | 说明 |
|------|------|
| API 认证 | Bearer Token，配置 `API_TOKEN` 环境变量 |
| WebSocket 保护 | 连接需 token 参数，缺失返回 401，无效返回 403 |
| 日志脱敏 | 自动掩码 password/api_key/token/RTSP密码 |
| 路径白名单 | 中间件级拦截，只允许 /api/*、/ws、/health、/static/* 通过 |
| 截图访问控制 | 通过认证 API 访问，不暴露文件路径 |
| 敏感配置 | `.gitignore` 排除，提供 `.example` 模板 |

---

## 部署

### 开发环境

```bash
pip install -r requirements.txt
python -m vision_agent --config configs/settings.yaml
```

零外部依赖：SQLite + 内存缓存，不需要 Redis、PostgreSQL、Docker。

### 生产环境

```bash
sudo cp deploy/vision-agent.service /etc/systemd/system/
sudo systemctl enable vision-agent
sudo systemctl start vision-agent
```

### 硬件需求

| 配置 | 摄像头路数 | GPU | 内存 | 磁盘 |
|------|-----------|-----|------|------|
| 最低 | 1-4 路 | 4GB（YOLO11n） | 8GB | 50GB |
| 推荐 | 5-10 路 | 16GB（YOLO11m） | 16GB | 200GB |
| 高配 | 10-20 路 | 24GB+（YOLO11x） | 32GB | 500GB |

---

## 项目结构

```
vision_agent/
├── .gitignore
├── README.md                          ← 你正在看的
├── requirements.txt
│
├── configs/                           ← 配置（.example 模板可推送）
│   ├── settings.yaml.example          ← 全局配置模板
│   └── cameras/
│       └── camera_01.yaml.example     ← 摄像头配置模板
│
├── docs/
│   └── architecture.md                ← 完整架构设计文档（1194行）
│
├── src/
│   └── vision_agent/                  ← 主代码包（import: from vision_agent.core import ...）
│       ├── core/                      ← 核心管线
│       │   ├── types.py               ← 统一数据模型
│       │   ├── pipeline.py            ← 主处理管线
│       │   ├── camera.py              ← 摄像头管理（FFmpeg采集）
│       │   ├── detector.py            ← 检测器（YOLO封装）
│       │   ├── tracker.py             ← 追踪器（BoT-SORT封装）
│       │   └── recorder.py            ← 录制器（环形缓冲+片段截取）
│       ├── rules/
│       │   ├── engine.py              ← 规则引擎
│       │   ├── builtin/               ← 内置规则（闯入/离岗/聚集/遗留物/计数）
│       │   └── templates/             ← YAML规则模板
│       ├── llm/
│       │   ├── analyzer.py            ← LLM分析器（截图+上下文→结构化报告）
│       │   └── provider.py            ← LLM提供者（OpenAI兼容API封装）
│       ├── actions/
│       │   ├── notifier.py            ← 通知（Webhook/邮件）
│       │   ├── recorder.py            ← 视频片段录制
│       │   └── logger.py              ← 事件日志
│       ├── web/
│       │   └── api/                   ← FastAPI REST + WebSocket
│       ├── storage/
│       │   ├── database.py            ← SQLite / PostgreSQL
│       │   ├── cache.py               ← Redis / 内存缓存
│       │   └── vector_store.py        ← ChromaDB（RAG预留）
│       └── config/
│           └── settings.py            ← 配置加载与校验
│
├── frontend/                          ← Vue 3 前端（独立目录）
│   ├── src/
│   │   ├── views/                     ← 页面组件
│   │   ├── components/                ← 通用组件
│   │   ├── stores/                    ← Pinia 状态管理
│   │   ├── composables/               ← 组合式函数
│   │   └── router/                    ← 路由配置
│   ├── package.json
│   └── vite.config.ts
│
├── plugins/                           ← 自定义插件（用户编写）
├── models/                            ← 模型文件（需手动下载，见上方说明）
│   └── yolov8n.pt                     ← YOLO 检测模型（~6MB）
├── data/                              ← 运行数据（自动创建）
├── logs/                              ← 日志（自动创建）
└── deploy/
    └── vision-agent.service           ← systemd 服务文件
```

---

## 技术栈

| 层级 | 选型 | 说明 |
|------|------|------|
| 后端框架 | FastAPI | REST API + WebSocket |
| 检测模型 | Ultralytics YOLO | YOLO11 系列，支持切换 |
| 追踪器 | BoT-SORT | 多目标追踪 |
| 视频采集 | FFmpeg 子进程 | RTSP 最稳定的方案 |
| LLM | OpenAI 兼容 API | 通义千问/DeepSeek/Moonshot 等 |
| RAG | ChromaDB（预留） | 嵌入式向量数据库 |
| 数据库 | SQLite → PostgreSQL | 先零成本，后期迁移 |
| 缓存 | Redis（可选） | 可降级为内存缓存 |
| 前端 | Vue 3 + Element Plus | Web 管理界面 |
| 部署 | systemd | 裸机部署 |

---

## 路线图

### v0.1 — 第一版（当前）

- [x] 架构设计文档
- [ ] 多路 RTSP 视频采集
- [ ] YOLO 检测 + BoT-SORT 追踪
- [ ] 规则引擎（YAML + Python，5 个内置规则）
- [ ] LLM 分析（OpenAI 兼容 API）
- [ ] 通知（Webhook + 邮件）
- [ ] Web 界面（实时画面 + 告警列表 + 系统状态）
- [ ] 安全（API 认证 + WebSocket 保护 + 日志脱敏）

### v0.2 — 增强

- [ ] RAG 知识检索（ChromaDB + 历史案例/SOP）
- [ ] HTTPS 支持（Nginx 反代）
- [ ] 审计日志
- [ ] 配置版本管理与迁移脚本
- [ ] TensorRT 推理加速
- [ ] 更多内置规则

### v0.3 — 生产化

- [ ] PostgreSQL 支持
- [ ] Docker 部署
- [ ] 多用户 + RBAC 权限
- [ ] 灰度发布（模型热更新）
- [ ] A/B 测试（规则效果对比）
- [ ] Prometheus 监控集成

---

## 文档

- [架构设计](docs/architecture.md) — 完整的系统架构设计（1194行，15个章节）
- 配置说明（待编写）
- 规则编写指南（待编写）
- API 文档（待编写）
- 部署指南（待编写）

---

## 技术来源

本项目从以下项目中提取核心设计思想和可复用资产：

| 项目 | 贡献 |
|------|------|
| [defect_detection](https://github.com/yourname/defect_detection) | 双编码器架构、LoRA 微调、FAISS 模板匹配 |
| [g-ass-source](https://github.com/yourname/g-ass-source) | Protocol 依赖注入、事件驱动、LLM Gateway |
| [sentinel_edge](https://github.com/yourname/sentinel_edge) | 规则引擎设计、影子部署、灰度发布 |
| [smart_video](https://github.com/yourname/smart_video) | YOLO 推理管线、BoT-SORT 追踪、告警管理 |

不直接整合任何项目，而是提取设计模式和算法资产，在统一架构上重新实现。

---

## 许可证

MIT License

---

| | |
|---|---|
| **作者** | Fang-Hanran |
| **版本** | v0.1 |
| **最后更新** | 2026-07-06 |
