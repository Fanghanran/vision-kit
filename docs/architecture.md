# SentinelMind — 系统架构设计文档

## 一、项目定位

SentinelMind 是一个多路视频智能分析框架，核心价值主张：

> **让任何视频流拥有"看懂、想明白、做决定"的能力。**

不是传统的"检测→告警"，而是"检测到异常 → LLM 分析情况 → 告诉人发生了什么、该怎么处理"。

### 目标场景

- 监控画面太多，人眼看不过来
- 非专业人员看不懂检测结果，不知道怎么处理
- 需要多路摄像头同时分析，区分不同类型的异常
- 需要 LLM 辅助理解场景，给出处理建议

### 核心链路

```
看（视频采集）→ 懂（检测+追踪）→ 判（规则引擎）→ 告（LLM分析+通知）
```

---

## 二、系统全景

### 2.1 架构总览

```
┌─────────────────────────────────────────────────────────────────┐
│                        SentinelMind                              │
│                                                                  │
│  ┌─────────── 采集层 ──────────┐                                │
│  │ CameraThread_1 ─→           │                                │
│  │ CameraThread_2 ─→ FrameQueue│                                │
│  │ CameraThread_N ─→ (有界队列) │                                │
│  └─────────────────────────────┘                                │
│                    ↓                                              │
│  ┌─────────── 推理层 ──────────┐                                │
│  │ InferenceThread              │                                │
│  │  ├─ batch合并 (max 8帧)      │                                │
│  │  ├─ YOLO 检测                │                                │
│  │  └─ BoT-SORT 追踪            │                                │
│  │              → ResultQueue   │                                │
│  └─────────────────────────────┘                                │
│                    ↓                                              │
│  ┌─────────── 处理层 ──────────┐                                │
│  │ ActionThread                 │                                │
│  │  ├─ 规则引擎评估              │                                │
│  │  ├─ 告警生成（去重+冷却）     │                                │
│  │  ├─ LLM 分析（异步）         │                                │
│  │  ├─ 通知发送（异步）          │                                │
│  │  └─ 存储写入                  │                                │
│  └─────────────────────────────┘                                │
│                                                                  │
│  ┌─────────── 展示层 ──────────┐                                │
│  │ WebServer (FastAPI)          │                                │
│  │  ├─ REST API                 │                                │
│  │  ├─ WebSocket 实时推送       │                                │
│  │  └─ Vue 3 前端               │                                │
│  └─────────────────────────────┘                                │
│                                                                  │
│  ┌─────────── 基础设施 ────────┐                                │
│  │ Storage (SQLite/PostgreSQL)  │                                │
│  │ Cache (Redis / 内存降级)      │                                │
│  │ VectorStore (ChromaDB, 预留) │                                │
│  │ Logger (结构化日志)           │                                │
│  │ Config (YAML 配置)           │                                │
│  └─────────────────────────────┘                                │
└─────────────────────────────────────────────────────────────────┘
```

### 2.2 数据流

```
摄像头 RTSP 流
  ↓ FFmpeg 子进程采集（每路独立线程）
原始帧（numpy BGR 数组）
  ↓ 帧率控制（默认 5fps）+ 有界队列（maxsize=200）
FrameQueue
  ↓ 推理线程 batch 合并（max 8帧，50ms 超时触发）
YOLO 检测结果（Detection 列表）
  ↓ BoT-SORT 多目标追踪
追踪结果（Track 列表，带 track_id 和轨迹）
  ↓ ResultQueue
规则引擎评估（YAML 配置的 Python 函数）
  ↓ 三层防线：滑动窗口去重 → 冷却时间 → 时间窗口
告警事件（Event）
  ↓ 异步
RAG 检索（从知识库检索相关历史案例、SOP、处理规范）—— 预留，第二版实现
  ↓
LLM 分析（截图 + 事件上下文 + RAG 参考资料 → 结构化报告）
  ↓
通知发送（Webhook / 邮件 / WebSocket）
  ↓
存储写入（告警记录 + 截图路径 + 视频片段路径）
```

### 2.3 模块依赖关系

```
config ─────────────────────────────────→ 所有模块

core/types ←── core/camera ←── core/pipeline
    ↑              ↑                ↑
    │              │                │
core/detector ─────┘                │
core/tracker ──────┘                │
core/recorder ─────┘                │
                                    │
rules/engine ←── rules/builtin      │
    ↑              ↑                │
    │              │                │
    └──────────────┘                │
                                    │
llm/analyzer ───────────────────────┘
    ↑
llm/provider
    ↑
storage/vector_store ─── llm/analyzer （RAG 检索，预留）

actions/notifier ──────────────────── pipeline
actions/recorder
actions/logger

storage/database ──────────────────── pipeline, web
storage/cache
storage/vector_store ─────────────── llm/analyzer（RAG，预留）

web/api ───────────────────────────── pipeline, storage
web/frontend
```

---

## 三、线程模型

### 3.1 设计原则

- **每个组件独立生命周期**，互不阻塞
- **生产者-消费者模型**，通过有界队列连接
- **有界队列丢旧帧**，宁可丢几帧也不能让延迟累积
- **任何单点故障不能拖垮整个系统**

### 3.2 线程划分

```
┌─────────────┐    ┌──────────────┐    ┌───────────────┐
│  采集层       │    │  推理层        │    │  处理层         │
│              │    │              │    │               │
│ CameraThread │    │ InferenceThread│   │ ActionThread  │
│ (每路1个)     │    │ (1个，batch)  │    │ (1个)         │
│              │    │              │    │               │
│ 输出帧到      │ →  │ 输出结果到     │ →  │ 规则→告警→     │
│ FrameQueue   │    │ ResultQueue  │    │ LLM→通知→存储  │
└─────────────┘    └──────────────┘    └───────────────┘
```

**采集层（CameraThread × N）**：
- 每路摄像头一个线程
- 只做两件事：读帧、控制帧率
- 读到帧放入 FrameQueue
- 摄像头断线不影响其他线程
- 断线后自动重连（指数退避）

**推理层（InferenceThread × 1）**：
- 专用一个线程，避免 GPU 竞争
- 从 FrameQueue 取帧，合并成 batch 送 GPU
- 推理结果按 camera_id 分发到各自的追踪器
- 追踪结果放入 ResultQueue

**处理层（ActionThread × 1）**：
- 一个线程，顺序处理
- 从 ResultQueue 取结果，跑规则引擎
- 告警生成是同步的（很快）
- LLM 调用和通知发送是异步的（不阻塞下一条告警）

**Web 服务（独立线程）**：
- FastAPI + uvicorn
- 通过共享存储层读取状态和告警数据
- WebSocket 推送实时告警

### 3.3 队列设计

| 队列 | 最大容量 | 满了怎么办 | 阻塞谁 |
|------|---------|-----------|--------|
| FrameQueue | 200 帧 | 丢弃最旧帧，日志记录 | 不阻塞摄像头线程 |
| ResultQueue | 100 条 | 丢弃最旧结果，日志记录 | 不阻塞推理线程 |

**决策理由**：阻塞会导致延迟累积，摄像头帧不断进来，如果推理慢了，队列只会越来越长，延迟越来越大。丢几帧比延迟几秒好得多。

### 3.4 优雅关闭

关闭顺序很重要，不能随便停：

```
1. 停止采集层（摄像头线程停止读帧）
   ↓ 等待 FrameQueue 排空或超时 3 秒
2. 停止推理层（InferenceThread 停止推理）
   ↓ 等待 ResultQueue 排空或超时 3 秒
3. 停止处理层（ActionThread 完成当前告警）
4. 停止 Web 服务
5. 停止数据清理
6. 关闭存储连接
```

先停上游再停下游，确保数据不丢失。

---

## 四、GPU 推理调度

### 4.1 batch 合并策略

多路摄像头的帧混合成 batch，一次性送 GPU 推理，比逐帧推理效率高得多。

**触发条件**（满足任一即推理）：
- 积累的帧数达到 `batch_size`（默认 8）
- 等待时间达到 `batch_timeout_ms`（默认 50ms）

**性能估算（16G GPU，YOLO11m）**：

| 摄像头路数 | 每路帧率 | 总帧率 | batch数/秒 | GPU推理时间 | GPU利用率 |
|-----------|---------|--------|-----------|------------|----------|
| 4路 | 5fps | 20帧/s | ~3 | ~120ms/batch | ~35% |
| 8路 | 5fps | 40帧/s | ~5 | ~200ms/batch | ~55% |
| 10路 | 5fps | 50帧/s | ~7 | ~280ms/batch | ~70% |
| 12路 | 5fps | 60帧/s | ~8 | ~320ms/batch | ~85% |

16G GPU 跑 10 路绰绰有余，极限可到 12 路。如果用 YOLO11n（小模型），路数可以翻倍。

### 4.2 推理失败处理

```
某帧推理失败
  ↓ 捕获异常，记录日志（哪路、哪帧、什么错误）
  ↓ 该帧跳过检测，用上一帧的追踪结果兜底（追踪器有惯性）
  ↓ 连续失败 > 10 帧 → 该路降级为"仅录制不检测"
  ↓ 连续失败 > 100 帧 → 告警"检测服务异常"
  ↓ 不影响其他摄像头的推理
```

---

## 五、错误处理与容错

### 5.1 总体原则

- 每个模块独立容错，单点故障不传播
- 能降级就降级，不能降级才告警
- LLM、Redis 都是可选增强，没有它们系统照样跑

### 5.2 各模块容错策略

#### 摄像头断线

```
摄像头断线
  ↓ 标记状态为 "disconnected"
  ↓ 停止向该路发送推理请求（省 GPU）
  ↓ 启动重连：3s → 6s → 12s → 30s → 60s（指数退避，上限 60s）
  ↓ 重连成功 → 恢复推理，标记 "connected"
  ↓ 超过 5 分钟未恢复 → 告警"摄像头离线"
```

每路独立线程+独立重连状态，camera_01 断了，camera_02 正常跑。

#### GPU 推理崩溃

```
推理异常（CUDA OOM / 模型错误 / 输入异常）
  ↓ 捕获异常，记录日志
  ↓ 该帧跳过，追踪器兜底
  ↓ 连续 10 帧失败 → 该路降级为仅录制
  ↓ 连续 100 帧失败 → 告警"检测服务异常"
```

batch 推理中某一路出错，其他路的结果正常返回。

#### FFmpeg 进程崩溃

```
FFmpeg 进程退出
  ↓ 主循环检测到进程不在
  ↓ 清理残留资源（管道、缓冲区）
  ↓ 等待 3 秒后重新启动
  ↓ 连续重启 > 5 次 → 告警"采集服务异常"，暂停 10 分钟后再试
```

#### Redis 不可用

```
Redis 连接失败
  ↓ 降级为内存缓存（Python dict + TTL）
  ↓ 去重/冷却功能降级为仅内存
  ↓ WebSocket 推送降级为轮询
  ↓ 后台持续重连
```

Redis 是可选增强，不是硬依赖。

#### LLM API 不可用

```
LLM 调用失败
  ↓ 重试 2 次（间隔 2s、5s）
  ↓ 仍然失败 → 跳过 LLM 分析，直接用规则引擎原始结果通知
  ↓ 通知内容降级："3号摄像头检测到异常（LLM分析不可用）"
  ↓ 连续失败 > 20 次 → 告警"LLM服务异常"
```

LLM 是增强层，不是必要层。

### 5.3 容错策略汇总

| 故障 | 恢复策略 | 降级方案 | 告警阈值 |
|------|---------|---------|---------|
| 摄像头断线 | 指数退避重连 | 跳过该路 | 5 分钟未恢复 |
| GPU 推理崩溃 | 跳帧 + 追踪兜底 | 仅录制 | 连续 100 帧失败 |
| FFmpeg 崩溃 | 自动重启 | 暂停该路 | 连续 5 次重启 |
| Redis 不可用 | 内存缓存降级 | 无缓存 | 连续 3 次连接失败 |
| LLM API 失败 | 重试 2 次 | 裸规则告警 | 连续 20 次失败 |

---

## 六、数据模型

### 6.1 检测结果

**BoundingBox** — 边界框

| 字段 | 类型 | 说明 |
|------|------|------|
| x1 | float | 左上角 x |
| y1 | float | 左上角 y |
| x2 | float | 右下角 x |
| y2 | float | 右下角 y |

**Detection** — 单帧检测结果

| 字段 | 类型 | 说明 |
|------|------|------|
| frame_id | int | 帧序号 |
| class_id | int | 类别 ID（0=person, 2=car...） |
| class_name | str | 类别名称 |
| confidence | float | 置信度 0-1 |
| bbox | BoundingBox | 边界框 |
| timestamp | float | 时间戳（Unix 秒） |

**Track** — 追踪目标

| 字段 | 类型 | 说明 |
|------|------|------|
| track_id | int | 追踪 ID（同一目标跨帧不变） |
| class_name | str | 类别名称 |
| bbox | BoundingBox | 当前帧位置 |
| trajectory | list | 历史轨迹 [(x, y, timestamp), ...] |
| velocity | tuple | 速度向量 (vx, vy) |
| first_seen | float | 首次出现时间 |
| last_seen | float | 最后出现时间 |
| age | int | 存活帧数 |
| hit_streak | int | 连续命中帧数 |

### 6.2 事件与告警

**Event** — 规则引擎产出的事件

| 字段 | 类型 | 说明 |
|------|------|------|
| event_id | str | UUID |
| event_type | str | 事件类型（intrusion / crowd / absence / ...） |
| camera_id | str | 摄像头 ID |
| camera_name | str | 摄像头名称 |
| rule_name | str | 触发的规则名称 |
| detections | list | 相关的 Detection 列表 |
| tracks | list | 相关的 Track 列表 |
| snapshot_path | str | 截图保存路径 |
| timestamp | float | 事件时间戳 |
| severity | str | 严重级别（info / warning / critical） |
| metadata | dict | 规则附带的额外信息 |

**LLMAnalysis** — LLM 分析结果

| 字段 | 类型 | 说明 |
|------|------|------|
| description | str | 情况描述 |
| risk_level | str | 风险等级（低 / 中 / 高 / 紧急） |
| suggestion | str | 建议措施 |
| context | str | 历史关联信息 |
| raw_response | str | LLM 原始返回 |

**Alert** — 最终告警

| 字段 | 类型 | 说明 |
|------|------|------|
| alert_id | str | UUID |
| event | Event | 触发事件 |
| llm_analysis | LLMAnalysis | LLM 分析结果（可为 None） |
| video_clip_path | str | 前后 N 秒视频片段路径（可为 None） |
| status | str | 状态（pending / acknowledged / rejected / resolved） |
| notified_channels | list | 已通知的渠道 |
| created_at | float | 创建时间 |
| acknowledged_at | float | 确认时间 |
| acknowledged_by | str | 确认人 |

**Alert 状态流转**：

```
pending → acknowledged → resolved     （正常处理）
pending → rejected                    （误报标记）
```

| 状态 | 含义 | 操作人 |
|------|------|--------|
| pending | 新告警，待处理 | — |
| acknowledged | 已确认，正在处理 | 值班人员 |
| rejected | 误报，标记为不成立 | 值班人员 |
| resolved | 已解决 | 处理人员 |

被标记为 `rejected` 的告警会被记录，用于后期优化规则阈值（误报率高的规则需要调整参数）。

### 6.3 系统状态

**CameraState** — 摄像头运行状态

| 字段 | 类型 | 说明 |
|------|------|------|
| camera_id | str | 摄像头 ID |
| status | str | 状态（connecting / connected / disconnected / error） |
| current_fps | float | 实际检测 FPS |
| gpu_latency_ms | float | 推理延迟 |
| queue_size | int | 帧队列积压 |
| last_frame_time | float | 最后一帧时间 |
| total_frames | int | 总处理帧数 |
| total_detections | int | 总检测数 |
| total_alerts | int | 总告警数 |
| uptime_seconds | float | 运行时长 |
| error_message | str | 最近一次错误信息 |

### 6.4 数据模型设计决策

- 所有 ID 用 UUID，不用自增整数（分布式友好，合并不冲突）
- 时间戳统一用 Unix 秒（float），序列化简单，比较快
- 帧图像（numpy 数组）只在内存中传递，不序列化到数据库，持久化只存文件路径
- 所有数据模型支持 `to_dict()` / `from_dict()`，方便 JSON 序列化
- Alert 和 Event 分开：一个 Event 可能产生多个 Alert（不同通知渠道），也可能不产生 Alert（被冷却过滤）

---

## 七、规则引擎

### 7.1 规则编写方式

**YAML 声明式（80% 场景）**：

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

**Python 函数扩展（20% 复杂场景）**：

```yaml
# configs/rules/custom_rule.yaml
name: 叉车超速
description: 叉车行驶速度超过限速
module: plugins/forklift_speed.py
class: ForkliftSpeedRule
params:
  speed_limit: 5.0
  zone: [[0,0], [1920,0], [1920,1080], [0,1080]]
```

用户写一个 Python 类，实现 RuleProtocol 接口即可。

### 7.2 三层防线

所有规则共享三层防线机制，防止误报泛滥：

**第一层：滑动窗口去重**
- 连续 N 帧（默认 5 帧）检测到同一事件才算触发
- 防止单帧误检导致告警
- 窗口大小可在规则 YAML 中配置

**第二层：冷却时间**
- 同一摄像头同一规则，冷却期内（默认 5 分钟）不重复告警
- 防止同一事件反复通知
- 冷却时间可在规则 YAML 中配置

**第三层：时间窗口**
- 可配置规则只在特定时间段生效
- 例如：只在工作时间（8:00-18:00）检测离岗
- 例如：只在夜间（22:00-06:00）检测闯入

### 7.3 内置规则（第一版 5 个）

| 规则 | 条件 | 典型场景 |
|------|------|---------|
| 区域闯入 | 目标出现在禁止区域 + 持续 N 秒 | 仓库禁区有人进入 |
| 离岗检测 | 指定区域在工作时间无人 | 前台/门卫岗位无人 |
| 人员聚集 | 一个区域人数超过阈值 | 工厂门口人群聚集 |
| 遗留物 | 物体停留超过 N 分钟 | 行李/包裹无人认领 |
| 人数统计 | 经过某条计数线的人数 | 出入口客流统计 |

### 7.4 规则热重载

- 规则 YAML 文件变化时自动重新加载（通过文件监控或定期扫描）
- 规则重载不影响正在处理的帧，新规则在下一帧生效
- 重载失败保留旧规则，日志记录错误

---

## 八、插件接口

### 8.1 三个扩展点

系统通过 Protocol（接口）定义三个扩展点，不依赖具体实现。

#### DetectorProtocol — 检测器接口

| 方法 | 输入 | 输出 | 说明 |
|------|------|------|------|
| detect(frames) | 帧列表 | 每帧的 Detection 列表 | batch 推理 |
| warmup() | — | — | 预热，加载模型到 GPU |
| release() | — | — | 释放资源 |
| model_name | — | str | 模型名称（属性） |
| classes | — | list | 支持的类别（属性） |

**默认实现**：YOLODetector（Ultralytics 封装）

**后期扩展**：工业质检可换成双编码器，人脸识别可换成 InsightFace，分割可换成 SAM

#### RuleProtocol — 规则接口

| 方法 | 输入 | 输出 | 说明 |
|------|------|------|------|
| name | — | str | 规则名称（属性） |
| camera_ids | — | list 或 None | 适用摄像头，None=全部（属性） |
| evaluate(tracks, frame, context) | 追踪列表、帧、上下文 | Event 或 None | 评估规则 |
| reset() | — | — | 重置内部状态 |

**默认实现**：5 个内置规则

**扩展方式**：YAML 声明式（自动生成）或 Python 类（手动编写）

#### ActionProtocol — 行动接口

| 方法 | 输入 | 输出 | 说明 |
|------|------|------|------|
| execute(alert) | Alert 对象 | bool（是否成功） | 执行行动 |
| name | — | str | 行动名称（属性） |

**默认实现**：WebhookNotifier、EmailNotifier、LLMAnalyzer、ClipRecorder、EventLogger

**后期扩展**：HTTP 调用外部系统、数据库写入、短信通知

### 8.2 RAG 知识检索（预留）

LLM 分析当前只看截图和事件上下文。通过 RAG（检索增强生成），可以让 LLM 参考历史案例、公司规章制度、标准处置流程来给出更精准的建议。

**接入位置**：在 LLM 分析之前，增加一步知识库检索。LLMAnalyzer 的 `analyze()` 方法接受可选的 `rag_context` 参数，RAG 检索结果通过此参数注入，不影响现有逻辑。

**增强后的分析流程**：

```
告警事件
  ↓
将事件描述 + 告警类型 + 摄像头信息 组成检索 query
  ↓
从向量知识库检索 top-5 相关文档
  ↓
拼接 prompt：事件上下文 + 截图 + 检索到的参考资料
  ↓
LLM 分析 → 输出（参考了历史案例和 SOP）
```

**知识库内容来源**：

| 内容类型 | 来源 | 价值 |
|---------|------|------|
| 历史告警及处理记录 | 系统自动积累 | "上次同类事件是这么处理的" |
| 安全管理规章制度 | 手动导入 | "根据规定第X条，应采取XX措施" |
| 标准处置流程 SOP | 手动导入 | "可疑物品处置流程：先疏散，再通知安保" |
| 区域特殊说明 | 手动导入 | "该区域为化学品仓库，需穿戴防护装备" |

**技术选型（预留）**：

| 方案 | 说明 | 适用场景 |
|------|------|---------|
| ChromaDB（推荐） | 嵌入式向量数据库，零部署，Python 原生 | 第一版 RAG 实现 |
| FAISS | 性能最好，需自行管理元数据 | 大规模检索 |

**配置预留**：

```yaml
# configs/settings.yaml 中预留 RAG 配置段
rag:
  enabled: false               # 第一版关闭，第二版开启
  vector_store: chromadb       # 向量数据库类型
  persist_dir: data/vector_db  # 向量数据存储路径
  embedding_model: text-embedding-v3  # 嵌入模型
  top_k: 5                     # 检索返回条数
  knowledge_dir: data/knowledge  # 知识文档目录（Markdown/文本文件）
```

**对现有代码的影响**：零影响。RAG 是纯增量功能，`rag.enabled: false` 时系统行为与没有 RAG 完全一致。

### 8.3 插件加载机制

不需要复杂的插件管理系统。简单做法：

1. 内置模块直接 import
2. 自定义模块放在 `plugins/` 目录
3. 配置文件指定类路径：`module: plugins/my_rule.py` + `class: MyCustomRule`
4. 系统启动时动态 import 加载

---

## 九、配置体系

### 9.1 配置结构

**全局配置**（`configs/settings.yaml`）：

```
system        — 系统名称、日志、数据目录、清理策略
gpu           — GPU 编号、batch 大小、FP16、TensorRT
detector      — 模型文件、置信度、IoU、类别、输入分辨率
tracker       — 追踪器类型和参数
rules         — 规则目录、是否热重载
llm           — 开关、Provider、API 地址、Key、模型、超时、预算
rag           — 开关、向量数据库类型、嵌入模型、检索条数、知识文档目录（预留）
notification  — Webhook、邮件配置
storage       — SQLite / PostgreSQL 选择和参数
redis         — 开关、地址
web           — 地址、端口、CORS
```

**摄像头配置**（`configs/cameras/cam_01.yaml`）：

```
camera        — ID、名称、RTSP 地址、帧率、分辨率、重连参数
detection     — 覆盖全局检测配置（置信度、类别过滤）
rules         — 适用的规则列表
recording     — 是否录制、缓冲时长、格式
```

### 9.2 敏感信息处理

| 信息 | 处理方式 |
|------|---------|
| RTSP 密码 | 写在摄像头配置文件里（内网环境）或环境变量 |
| LLM API Key | 必须用 `${LLM_API_KEY}` 环境变量 |
| 数据库密码 | 必须用 `${DB_PASS}` 环境变量 |
| Webhook URL | `${WEBHOOK_URL}` 环境变量 |
| 邮件密码 | `${EMAIL_PASS}` 环境变量 |

### 9.3 热重载策略

| 配置类型 | 热重载 | 说明 |
|---------|--------|------|
| 摄像头配置 | ✅ | 变化后自动重载该路，不重启进程 |
| 规则配置 | ✅ | 变化后自动重载规则引擎 |
| 全局配置 | ❌ | 需要重启进程 |
| 模型切换 | ❌ | 需要重启进程 |

### 9.4 配置校验

启动时校验所有配置，格式错误直接报错退出，不带着错误运行。校验项包括：

- RTSP 地址格式
- 模型文件是否存在
- 规则 YAML 语法
- 端口是否被占用
- GPU 是否可用
- 数据目录是否可写

### 9.5 配置版本与数据迁移

系统会持续演进，配置格式和存储方案都会变化。需要在设计阶段就考虑迁移路径。

**配置版本管理**：

`settings.yaml` 顶部增加 `version` 字段，系统启动时校验版本，不匹配则报错并提示升级脚本路径：

```yaml
version: 1                    # 配置格式版本号
system:
  name: "SentinelMind"
  ...
```

校验逻辑：读取配置 → 检查 version 字段 → 与当前代码期望的版本对比 → 不匹配则报错退出，输出类似：

```
ERROR: 配置版本不匹配，当前配置为 v1，代码期望 v2。
请运行 python scripts/migrate_config_v1_to_v2.py 升级配置。
```

**数据迁移路径**（预留，不需要第一版实现）：

| 迁移场景 | 脚本路径 | 说明 |
|---------|---------|------|
| SQLite → PostgreSQL | `scripts/migrate_sqlite_to_postgres.py` | 导出 SQLite 数据，导入 PostgreSQL |
| 配置格式升级 | `scripts/migrate_config_v1_to_v2.py` | 自动转换旧格式 YAML 到新格式 |
| 规则格式升级 | `scripts/migrate_rules_v1_to_v2.py` | 自动转换旧规则 YAML 到新格式 |
| 数据目录结构变更 | `scripts/migrate_data_dir.py` | 迁移截图/视频目录结构 |

**迁移策略**：
- 迁移脚本必须支持 `--dry-run` 模式（只检查不执行）
- 迁移前自动备份原数据（SQLite 复制一份，YAML 复制一份 `.bak`）
- 迁移失败必须能回滚（恢复备份）
- 迁移脚本记录执行日志，方便排查问题

---

## 十、日志与可观测性

### 10.1 日志格式

结构化日志，每行一个事件：

```
2026-07-06 10:30:15 INFO  [cam_01] frame_processed fps=4.8 detections=3 latency_ms=18
2026-07-06 10:30:16 WARN  [cam_02] reconnect attempt=3 next_retry_in=12s
2026-07-06 10:30:17 INFO  [engine] rule_triggered rule=intrusion camera=cam_01 tracks=[1,3]
2026-07-06 10:30:18 INFO  [llm] analysis_complete camera=cam_01 risk=medium latency_ms=2340
2026-07-06 10:30:18 INFO  [notify] webhook_sent camera=cam_01 status=ok
2026-07-06 10:30:20 ERROR [cam_03] inference_failed frame=1247 error="CUDA out of memory"
```

格式：`时间 级别 [模块] 事件 key=value ...`

- `[模块]` 标识来源：cam_01/cam_02/engine/llm/notify/storage/web
- 关键指标用 key=value 附加，方便后续搜索和统计

### 10.2 日志管理

- 使用 Python `logging` 模块 + `RotatingFileHandler`
- 日志文件最大 50MB，保留 5 个历史文件
- 控制台也输出简化格式（开发时方便看）
- 日志级别可通过配置动态调整（不用重启）

### 10.3 运行时指标

Web 界面的状态面板显示以下指标（内存中维护，不依赖外部监控工具）：

| 指标 | 来源 | 刷新频率 |
|------|------|---------|
| 每路摄像头状态 | CameraThread | 实时 |
| 每路实际 FPS | CameraThread | 每秒 |
| GPU 使用率 / 显存 | GPUtil | 每 2 秒 |
| 推理延迟（P50 / P99） | InferenceThread | 每秒 |
| 帧队列积压 | FrameQueue | 实时 |
| 今日告警数 | Storage | 每 10 秒 |
| LLM 调用成功率 | LLMAnalyzer | 每分钟 |
| 错误计数（最近 1 小时） | Logger | 每分钟 |

### 10.4 系统健康检查与自监控

系统不只是"给别人告警"，系统自己也要"被监控"。

**健康检查端点**：`GET /health`

返回 JSON 格式的系统健康状态：

```json
{
  "status": "ok",
  "uptime_seconds": 12345,
  "gpu_utilization": 0.45,
  "gpu_memory_used_mb": 6200,
  "gpu_memory_total_mb": 16384,
  "queue_depth": 12,
  "inference_latency_p50_ms": 15,
  "inference_latency_p99_ms": 28,
  "active_cameras": 8,
  "total_cameras": 10,
  "today_alerts": 23,
  "llm_success_rate": 0.98
}
```

**健康判定规则**：

| 条件 | 状态 | HTTP 码 |
|------|------|---------|
| 所有指标正常 | ok | 200 |
| GPU 不可用 或 队列积压 > 100 帧 或 推理延迟 P99 > 100ms 持续 30 秒 | degraded | 200（带 warning 字段） |
| 所有摄像头离线 或 GPU 完全不可用 | unhealthy | 503 |

**设计约束**：`/health` 端点本身不依赖 LLM 或 Redis，确保在依赖服务故障时仍能返回真实状态。如果健康检查本身因为依赖不可用而超时，外部监控就失去了意义。

**外部监控接入**：

`/health` 端点设计为标准的外部监控接口，可以对接：

- **简单方案**：cron + curl，每分钟检查一次，非 200 则发邮件告警
- **标准方案**：Prometheus 抓取 + AlertManager 告警规则
- **容器方案**：Docker/K8s 的 livenessProbe 和 readinessProbe

```bash
# 最简单的外部监控脚本
*/1 * * * * curl -sf http://localhost:8080/health || echo "SentinelMind unhealthy" | mail -s "Alert" admin@example.com
```

---

---

## 十一、存储方案

### 11.1 存储分层

| 数据 | 存储介质 | 保留时间 | 说明 |
|------|---------|---------|------|
| 告警记录 | SQLite → PostgreSQL | 长期 | 核心业务数据 |
| 告警截图 | 本地文件 | 30 天 | 按日期目录组织 |
| 告警视频片段 | 本地文件 | 7 天 | 环形缓冲 + 告警截取 |
| 实时状态 | Redis / 内存 | 不持久化 | 摄像头状态、队列深度 |
| 系统配置 | YAML 文件 | 长期 | 版本管理 |
| 日志文件 | 本地文件 | 轮转保留 | 50MB × 5 个 |
| 向量知识库 | ChromaDB（预留） | 长期 | RAG 检索用，第二版实现 |

### 11.2 数据清理策略

后台定时任务（默认每小时执行一次）：

- 删除超过保留天数的截图和视频片段
- 标记超过 90 天的告警为 "archived"（不删除，但不再展示）
- 清理空的日期目录

### 11.3 存储演进路径

```
第一版：SQLite（零部署成本，单文件数据库）
   ↓ 数据量增长或需要多用户并发
第二版：PostgreSQL（改一个配置项即可切换）
   ↓ 需要分布式部署
第三版：PostgreSQL + Redis + OSS
```

---

## 十二、安全设计

### 12.1 安全威胁分析

| 威胁 | 风险等级 | 影响 |
|------|---------|------|
| 日志泄露敏感信息 | 高 | API Key、摄像头密码被日志记录后泄露 |
| WebSocket 无认证 | 高 | 同网段任何人可连接，查看实时告警和截图 |
| API 无认证 | 中 | 任何人可调用 API，修改配置、查看告警 |
| 截图/视频直接访问 | 中 | 监控画面泄露，成为"监控泄露"入口 |
| 配置文件泄露 | 中 | 含摄像头密码、API Key |
| 数据库未加密 | 低 | SQLite 文件在本地，需物理访问才能获取 |

### 12.2 第一版必须实现的安全措施

#### 日志脱敏

实现一个 Python logging Filter，自动识别并替换敏感字段：

- 匹配字段名：`password`、`api_key`、`token`、`secret`、`credential`、`authorization`
- 匹配后处理：替换为 `***`（固定掩码），无论原始值是什么长度
- RTSP URL 特殊处理：正则匹配 `rtsp://([^:]+):([^@]+)@` 部分，将密码替换为 `***`
- Authorization Header：匹配 `Bearer\s+\S+`，替换为 `Bearer ***`

脱敏前后对比：

| 原始日志 | 脱敏后 |
|---------|--------|
| `password=abc123secret` | `password=***` |
| `api_key=sk-xxxxxxxxxxxx` | `api_key=***` |
| `rtsp://admin:mypass@192.168.1.100/stream1` | `rtsp://admin:***@192.168.1.100/stream1` |
| `Authorization: Bearer eyJhbGciOi...` | `Authorization: Bearer ***` |

实现成本：约 30 行代码，一个 logging.Filter 子类。

#### API 认证

通过 FastAPI 的 Depends 机制实现 Bearer Token 认证：

- 配置文件中设置 `web.api_token`（支持 `${API_TOKEN}` 环境变量引用）
- 所有 `/api/*` 路由要求 `Authorization: Bearer <token>` 头
- 未设置 api_token 时跳过认证（开发模式）
- 实现成本：约 20 行代码

```yaml
web:
  api_token: ${API_TOKEN}  # 不设则不启用认证（仅限开发环境）
```

#### WebSocket 保护

WebSocket 连接时要求传 token 参数：

- 连接地址：`ws://localhost:8080/ws?token=<api_token>`
- token 缺失：拒绝握手，返回 HTTP 401，连接不建立
- token 无效：拒绝握手，返回 HTTP 403，连接不建立
- 连接建立后 token 失效（如配置变更）：服务端主动断开，返回 4001 状态码
- 实现成本：约 10 行代码

#### 截图/视频访问控制

不通过静态文件直接服务，而是通过需要认证的 API 接口访问：

- `GET /api/alerts/{alert_id}/snapshot` — 获取告警截图（需认证）
- `GET /api/alerts/{alert_id}/clip` — 获取告警视频片段（需认证）
- 文件路径不暴露在 URL 中，通过 alert_id 间接访问
- 实现成本：约 15 行代码

### 12.3 安全配置项

```yaml
# configs/settings.yaml 中的安全相关配置

web:
  api_token: ${API_TOKEN}      # API 和 WebSocket 认证 Token
  cors_origins:                 # CORS 允许的来源（生产环境应限制为具体域名）
    - "http://localhost:3000"

# 日志脱敏默认开启，无需额外配置
# 截图/视频通过 API 访问，无需额外配置
```

### 12.4 第一版标记风险，后期再做

| 问题 | 第一版处理 | 后期方案 | 优先级 |
|------|-----------|---------|--------|
| 无 HTTPS | HTTP 明文传输 | Nginx 反代 + Let's Encrypt | 第二版优先实现 |
| 无审计日志 | 仅业务日志 | 独立审计日志模块，记录所有敏感操作 | 第二版优先实现 |
| 配置文件含明文密码 | `.gitignore` 排除 + README 安全提醒 | 支持 SOPS 加密或 Vault 集成 | 视客户需求决定 |
| 无多用户 | 单 Token，无角色区分 | 用户管理 + RBAC 权限控制 | 视客户需求决定 |
| 数据库存储未加密 | SQLite 在本地磁盘，物理访问才需要担心 | PostgreSQL + 列级加密 | 视客户需求决定 |
| RTSP 密码明文 | 配置文件中明文存储（内网环境可接受） | 环境变量引用，不写死在文件中 | 第二版顺便做 |

**优先级说明**：第二版优先实现 HTTPS 和审计日志，Vault 和 RBAC 视客户需求决定。

### 12.5 .gitignore 安全清单

以下文件/目录必须在 `.gitignore` 中排除：

```
# 配置文件（含密码和 API Key）
configs/settings.yaml
configs/cameras/

# 运行数据（含截图、视频、数据库）
data/

# 日志
logs/

# 环境变量
.env
```

提供 `configs/settings.yaml.example` 作为模板，不含真实密码。

### 12.6 运行时安全基线（第一版实现）

不只是认证和脱敏，还要在运行时主动防御"随手扫目录"这类攻击。

**系统启动时检查**：

- `configs/cameras/` 目录权限是否为 `600`（仅 owner 可读写），否则启动失败并提示修复命令
- `.env` 文件是否存在且不为空，不存在则警告（非阻断）
- `data/` 和 `logs/` 目录是否可写，不可写则启动失败

**Web 层路径保护**：

采用白名单策略：所有不在白名单内的路径在 app.middleware 层统一返回 404，不暴露文件存在性。不逐个列举黑名单（容易遗漏），而是只允许已知安全的路径通过。

白名单路径（其余全部 404）：

| 路径 | 说明 |
|------|------|
| `/api/*` | REST API 端点 |
| `/ws` | WebSocket 连接 |
| `/health` | 健康检查 |
| `/static/*` | 前端静态资源（由构建工具生成，不含敏感文件） |
| `/` | 前端入口页面 |

被拦截的示例路径（返回 404，不泄露存在性）：

- `/.git`、`/.env`、`/configs/`、`/data/`、`/logs/`、`/models/`
- `/*.py`、`/*.yaml`、`/*.db`
- 任何不在白名单中的路径

**实现方式**：一个 FastAPI 中间件，在请求到达路由之前检查路径前缀，不在白名单内直接返回 404。

**API 返回数据脱敏**：

- `/api/config` 端点返回配置时，自动掩码所有包含 `password`、`api_key`、`token`、`secret` 的字段
- 截图和视频只通过 `/api/alerts/{id}/snapshot` 和 `/api/alerts/{id}/clip` 访问，不暴露文件系统路径
- 错误响应中不包含堆栈跟踪、文件路径、数据库连接字符串

**实现成本**：约 20 行代码（一个中间件 + 白名单前缀匹配 + 字段掩码函数）。

---

## 十三、部署方式

### 13.1 开发环境

```bash
git clone <repo>
cd sentinelmind
pip install -r requirements.txt
cp configs/example.yaml configs/settings.yaml
# 编辑 settings.yaml 填入摄像头地址和 API Key
python -m sentinelmind --config configs/settings.yaml
```

零外部依赖：SQLite + 内存缓存，不需要 Redis、PostgreSQL、Docker。

### 13.2 生产环境

```bash
# systemd 服务方式部署
sudo cp deploy/sentinelmind.service /etc/systemd/system/
sudo systemctl enable sentinelmind
sudo systemctl start sentinelmind
```

### 13.3 运行时目录结构

```
sentinelmind/
├── configs/                 # 配置（用户编辑）
│   ├── settings.yaml        # 全局配置
│   ├── cameras/             # 每路摄像头配置
│   └── rules/               # 规则配置
├── data/                    # 运行数据（自动生成，gitignore）
│   ├── sentinelmind.db      # SQLite 数据库
│   ├── snapshots/           # 告警截图（按日期）
│   ├── clips/               # 告警视频片段（按日期）
│   ├── vector_db/           # 向量知识库（RAG，预留）
│   └── knowledge/           # 知识文档目录（RAG，预留）
├── logs/                    # 日志（自动生成，gitignore）
├── models/                  # 模型文件
└── plugins/                 # 自定义插件（用户编写）
```

### 13.4 硬件需求

| 配置 | 摄像头路数 | GPU | 内存 | 磁盘 |
|------|-----------|-----|------|------|
| 最低 | 1-4 路 | 4GB（YOLO11n） | 8GB | 50GB |
| 推荐 | 5-10 路 | 16GB（YOLO11m） | 16GB | 200GB |
| 高配 | 10-20 路 | 24GB+（YOLO11x） | 32GB | 500GB |

### 13.5 性能测试建议

在系统上线前，需要验证以下性能指标，确保系统在预期负载下稳定运行。

**基础性能测试**：

| 测试项 | 方法 | 验收标准 |
|--------|------|---------|
| API 响应延迟 | `wrk` 或 `locust` 对 `/api/alerts` 和 `/health` 做负载测试 | P99 < 100ms |
| 单路推理延迟 | 单路摄像头 5fps 跑 8 小时，记录 GPU 显存增长曲线 | 显存无持续增长（排除泄漏） |
| 推理吞吐量 | 逐步增加 batch_size，找到最大吞吐点 | batch=8 时吞吐 ≥ 50 帧/秒 |
| 告警延迟 | 从检测到异常到告警发出的端到端延迟 | < 3 秒（含 LLM 分析） |

**稳定性测试**：

| 测试项 | 方法 | 验收标准 |
|--------|------|---------|
| 多路长时间运行 | 12 路同时接入，持续运行 72 小时 | 队列深度稳定，丢帧率 < 1% |
| 摄像头断线恢复 | 人工断开/恢复摄像头，观察系统行为 | 自动重连，无进程崩溃 |
| GPU 显存压力 | 逐步增加路数直到 OOM，观察降级行为 | 优雅降级，不崩溃 |
| LLM API 超时 | 模拟 LLM API 超时/失败 | 裸规则告警正常发出 |

**测试工具建议**：

| 工具 | 用途 |
|------|------|
| `wrk` 或 `locust` | HTTP API 负载测试 |
| `nvidia-smi` 或 `GPUtil` | GPU 显存/使用率监控 |
| `htop` / `iotop` | CPU/内存/磁盘 IO 监控 |
| 自定义脚本 | 摄像头断线/恢复模拟 |

### 13.6 上线检查清单（第一版）

部署前逐项检查，全部通过后才能正式上线。

**环境检查**：

- [ ] Python 版本 ≥ 3.10
- [ ] CUDA 已安装，`nvidia-smi` 正常输出
- [ ] FFmpeg 已安装，`ffmpeg -version` 正常输出
- [ ] GPU 显存 ≥ 预期需求（参见 13.4 硬件需求表）

**配置检查**：

- [ ] `configs/settings.yaml` 已从 `.example` 复制并填写完成
- [ ] 密码通过 `${ENV}` 环境变量引用，未写死在文件中
- [ ] `configs/cameras/*.yaml` 已配置，RTSP 地址可通（用 `ffplay` 测试）
- [ ] `API_TOKEN` 环境变量已设置
- [ ] `LLM_API_KEY` 环境变量已设置（如启用 LLM）
- [ ] `WEBHOOK_URL` 环境变量已设置（如启用通知）

**文件检查**：

- [ ] 模型文件已下载至 `models/` 目录
- [ ] `data/` 目录已创建，权限正确（owner 可读写）
- [ ] `logs/` 目录已创建，权限正确
- [ ] `configs/cameras/` 目录权限为 600
- [ ] `.gitignore` 已排除敏感文件

**启动检查**：

- [ ] `python -m sentinelmind --config configs/settings.yaml` 启动无报错
- [ ] `/health` 端点返回 200
- [ ] 各路摄像头状态为 "connected"
- [ ] Web 界面可正常访问
- [ ] 系统日志无异常，无敏感信息泄露
- [ ] 手动触发一次测试告警，通知正常到达
- [ ] 回滚方案已准备：保留上一版可执行文件，数据库备份已确认可恢复

---

## 十四、技术栈

| 层级 | 选型 | 说明 |
|------|------|------|
| 后端框架 | FastAPI | 统一一个框架，REST + WebSocket |
| 检测模型 | Ultralytics YOLO | YOLO11 系列，支持切换模型大小 |
| 追踪器 | BoT-SORT | 多目标追踪，从 smart_video 验证过 |
| 视频采集 | FFmpeg 子进程 | RTSP 最稳定的方案 |
| LLM | OpenAI 兼容 API | 先用云 API，后期可切本地 |
| RAG | ChromaDB（预留） | 嵌入式向量数据库，零部署，第二版实现 |
| 数据库 | SQLite → PostgreSQL | 先零成本，后期迁移 |
| 缓存 | Redis（可选） | 可降级为内存缓存 |
| 前端 | Vue 3 + Element Plus | 统一技术栈 |
| 日志 | Python logging | 标准库，RotatingFileHandler |
| 部署 | systemd | 裸机部署，后期可加 Docker |

---

## 十六、端-云架构演进

### 16.1 演进路径

当前 SentinelMind 采用纯服务器架构（第一版）。后期根据规模扩展需求，可演进为端-云协同架构：

```
第一版（当前）：
  摄像头 → RTSP → 服务器(GPU推理+规则+LLM) → 通知

第二版（端-云）：
  摄像头 → 边缘设备(推理) → 结果上报 → 服务器(规则+LLM) → 通知
```

### 16.2 边缘设备职责

| 职责 | 边缘设备 | 服务器 |
|------|---------|--------|
| 视频采集 | ✅ 本地采集 | ❌ |
| 目标检测 | ✅ 本地推理 | ❌ |
| 多目标追踪 | ✅ 本地追踪 | ❌ |
| 规则引擎 | ❌ | ✅ |
| LLM 分析 | ❌ | ✅ |
| 告警通知 | ❌ | ✅ |
| 配置管理 | 接收下发 | ✅ 下发 |

边缘设备只做"看"和"懂"，服务器做"判"和"告"。

### 16.3 扩展涉及的模块

| 模块 | 扩展内容 |
|------|---------|
| camera.py | 新增 source_type 配置（stream/edge） |
| detector.py | 新增 RemoteDetector 实现 |
| pipeline.py | 支持 edge 数据流模式（跳过推理层） |
| config | 新增边缘设备配置段 |

### 16.4 设计约束

- 第一版的所有接口（DetectorProtocol、RuleProtocol、ActionProtocol）保持不变
- 边缘设备上报数据格式与本地检测结果格式一致
- 边缘设备断线降级行为与本地摄像头断线一致
- 不在第一版实现，但接口设计已预留扩展点

## 十七、技术来源

### 17.1 项目资产提取

本项目从以下四个项目中提取核心设计思想和可复用资产：

| 来源项目 | 提取内容 |
|---------|---------|
| **defect_detection** | 双编码器架构思路、LoRA 微调策略、FAISS 模板匹配、三策略阈值、工厂隔离设计 |
| **g-ass-source** | Protocol 依赖注入模式、事件驱动设计、LLM Gateway（断路器+重试+预算）、插件架构思想 |
| **sentinel_edge** | Lua 规则引擎设计（滑动窗口+冷却+静默时段）、影子部署模式、灰度发布策略 |
| **smart_video** | YOLO+TensorRT 推理管线、BoT-SORT 追踪集成、模板化规则系统、告警管理（去重+通知）、FFmpeg 采集 |

不直接整合任何项目，而是提取设计模式和算法资产，在统一架构上重新实现。

### 17.2 安全设计参考

安全设计参考了以下行业标准和最佳实践：

| 参考来源 | 应用点 |
|---------|--------|
| **OWASP Top 10 for LLM Applications 2025** | LLM 调用安全（输入校验、输出过滤、预算控制）、RAG 注入防护 |
| **FastAPI 官方安全最佳实践** | API 认证（Bearer Token Depends）、CORS 配置、依赖注入安全模式 |
| **WebSocket 安全实践** | 连接认证（token 参数验证）、状态码规范（4001 认证失败） |
| **日志安全规范** | 敏感字段脱敏（密码/Token/Key 自动掩码）、结构化日志不含明文凭证 |

---

| | |
|---|---|
| **文档版本** | v1.2 |
| **作者** | 方瀚然 |
| **最后更新** | 2026-07-06 |
