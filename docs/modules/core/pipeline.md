# pipeline — 主处理管线模块 设计书

## 1. 模块职责

作为系统的核心编排器，串联采集、检测、追踪、规则、告警、LLM、通知、存储的完整处理链路。管理三层线程模型（采集层、推理层、处理层）的生命周期，实现组件组装、队列管理、优雅关闭、健康检查和热重载。

## 2. 对外接口

### 2.1 VisionAgent 主类

| 方法/属性 | 签名 | 说明 |
|-----------|------|------|
| `__init__` | `(config: AppConfig) -> None` | 从配置创建所有组件实例，组装依赖 |
| `start` | `() -> None` | 启动所有组件和线程 |
| `stop` | `() -> None` | 优雅关闭所有组件 |
| `status` | `() -> SystemStatus` | 返回系统整体运行状态 |
| `health` | `() -> HealthResponse` | 返回健康检查数据（供 /health 端点） |
| `add_camera` | `(camera_config: CameraConfig) -> None` | 动态添加一路摄像头 |
| `remove_camera` | `(camera_id: str) -> None` | 动态移除一路摄像头 |
| `reload_camera` | `(camera_id: str) -> None` | 重载指定摄像头配置 |
| `reload_rules` | `() -> None` | 重载规则引擎 |
| `get_camera_states` | `() -> dict[str, CameraState]` | 获取所有摄像头状态 |
| `get_alert_stats` | `() -> AlertStats` | 获取告警统计 |

### 2.2 FrameQueue 类

| 方法/属性 | 签名 | 说明 |
|-----------|------|------|
| `__init__` | `(maxsize: int = 200, name: str = "frame") -> None` | 创建有界队列 |
| `put` | `(frame_data: FrameData) -> None` | 推入帧，满则丢旧帧 |
| `get` | `(timeout: float = None) -> FrameData` | 取出帧，超时返回 None |
| `get_batch` | `(max_batch: int = 8, timeout_ms: int = 50) -> list[FrameData]` | 取出一批帧 |
| `qsize` | `() -> int` | 当前队列大小 |
| `clear` | `() -> None` | 清空队列 |

### 2.3 ResultQueue 类

| 方法/属性 | 签名 | 说明 |
|-----------|------|------|
| `__init__` | `(maxsize: int = 100, name: str = "result") -> None` | 创建有界队列 |
| `put` | `(result: InferenceResult) -> None` | 推入推理结果，满则丢旧结果 |
| `get` | `(timeout: float = None) -> InferenceResult` | 取出推理结果 |
| `qsize` | `() -> int` | 当前队列大小 |

### 2.4 InferenceResult 数据类

| 字段 | 类型 | 说明 |
|------|------|------|
| `camera_id` | `str` | 摄像头 ID |
| `frame` | `numpy.ndarray` | 原始帧（供后续截图使用） |
| `frame_id` | `int` | 帧序号 |
| `timestamp` | `float` | 帧采集时间戳 |
| `detections` | `list[Detection]` | 检测结果 |
| `tracks` | `list[Track]` | 追踪结果 |
| `inference_latency_ms` | `float` | 推理耗时（毫秒） |

### 2.5 HealthResponse 数据类

| 字段 | 类型 | 说明 |
|------|------|------|
| `status` | `str` | "ok" / "degraded" / "unhealthy" |
| `uptime_seconds` | `float` | 运行时长 |
| `gpu_utilization` | `float` | GPU 使用率 |
| `gpu_memory_used_mb` | `float` | GPU 已用显存 |
| `gpu_memory_total_mb` | `float` | GPU 总显存 |
| `queue_depth` | `int` | FrameQueue 积压 |
| `inference_latency_p50_ms` | `float` | 推理延迟 P50 |
| `inference_latency_p99_ms` | `float` | 推理延迟 P99 |
| `active_cameras` | `int` | 在线摄像头数 |
| `total_cameras` | `int` | 总摄像头数 |
| `today_alerts` | `int` | 今日告警数 |
| `llm_success_rate` | `float` | LLM 调用成功率 |

## 3. 内部逻辑

### 3.1 组件组装（__init__）

```
VisionAgent.__init__(config: AppConfig)

从配置创建所有组件：

  1. 创建队列：
     frame_queue = FrameQueue(maxsize=200)
     result_queue = ResultQueue(maxsize=100)

  2. 创建检测器：
     detector = YOLODetector(config.detector, device=config.gpu.device)
     detector.warmup()

  3. 创建追踪器管理器：
     tracker_manager = TrackerManager(config.tracker)

  4. 创建录制器：
     recorder = ClipRecorder(config.recording)

  5. 创建规则引擎：
     rule_engine = RuleEngine(config.rules)

  6. 创建 LLM 分析器（可选）：
     if config.llm.enabled:
         llm_analyzer = LLMAnalyzer(config.llm)
     else:
         llm_analyzer = None

  7. 创建通知器列表：
     notifiers = []
     for channel in config.notification.channels:
         notifier = create_notifier(channel)
         notifiers.append(notifier)

  8. 创建数据库连接：
     database = Database(config.storage)

  9. 创建摄像头线程列表：
     camera_threads = []
     for cam_config in config.cameras:
         thread = CameraThread(cam_config, frame_queue)
         camera_threads.append(thread)

  10. 创建推理线程：
      inference_thread = InferenceThread(
          frame_queue, detector, tracker_manager, result_queue)

  11. 创建处理线程：
      action_thread = ActionThread(
          result_queue, rule_engine, llm_analyzer,
          notifiers, recorder, database)

  12. 创建定时任务：
      cleanup_timer = TimerTask(
          interval=3600, callback=recorder.cleanup_expired)

  13. 保存所有组件引用
  14. 初始化 _running = False
```

### 3.2 启动流程（start）

```
VisionAgent.start()

  验证前置条件：
    - GPU 是否可用（torch.cuda.is_available()）
    - 模型文件是否存在
    - 数据目录是否可写
    任一失败：抛出 StartupError，日志记录具体原因

  设置 _running = True
  记录启动时间 _start_time

  按顺序启动组件（下游先启动，确保上游产出有人接收）：

  1. 启动数据库连接
  2. 启动处理线程（ActionThread.start）
  3. 启动推理线程（InferenceThread.start）
  4. 启动所有摄像头线程（CameraThread.start × N）
  5. 启动定时清理任务
  6. 启动 Web 服务（FastAPI，独立线程）

  日志记录：系统启动完成，N 路摄像头已启动
```

### 3.3 采集层线程（CameraThread × N）

每个 CameraThread 的主循环已在 camera.md 中详述。pipeline 层关注的是：

```
帧进入 pipeline 的入口：

CameraThread 输出：
  frame_data = FrameData(camera_id, frame, timestamp, frame_seq)
  frame_queue.put(frame_data)

pipeline 不直接调用 CameraThread，而是通过 frame_queue 间接连接。
```

### 3.4 推理层线程（InferenceThread × 1）

```
InferenceThread 主循环：

  while _running:
    1. batch 收集：
       batch_frames = frame_queue.get_batch(
           max_batch=config.detector.batch_size,
           timeout_ms=config.detector.batch_timeout_ms)
       如果 batch 为空：continue

    2. 记录 batch 收集时间

    3. 提取纯帧列表：
       frames = [bf.frame for bf in batch_frames]

    4. 执行检测推理：
       try:
           detections_list = detector.detect(frames)
       except Exception as e:
           日志记录推理失败
           detections_list = [[] for _ in frames]
           更新连续失败计数器

    5. 执行追踪更新：
       for bf, detections in zip(batch_frames, detections_list):
           tracks = tracker_manager.update(bf.camera_id, detections, bf.frame)

           构造 InferenceResult：
             result = InferenceResult(
                 camera_id=bf.camera_id,
                 frame=bf.frame,
                 frame_id=bf.frame_id,
                 timestamp=bf.timestamp,
                 detections=detections,
                 tracks=tracks,
                 inference_latency_ms=latency
             )

           推入 result_queue：
             result_queue.put(result)

    6. 更新推理统计：
       - 总推理次数、总帧数
       - 延迟统计（滑动窗口 P50、P99）
       - 每路连续失败计数

    7. 帧同步推送到录制器：
       for bf in batch_frames:
           recorder.push_frame(bf.camera_id, bf.frame, bf.timestamp)

    8. 连续失败降级检查：
       for camera_id, failure_count in consecutive_failures.items():
           if failure_count > 10:
               标记该路为降级模式（仅录制，跳过推理）
           if failure_count > 100:
               触发告警"检测服务异常"
```

### 3.5 处理层线程（ActionThread × 1）

```
ActionThread 主循环：

  while _running:
    1. 从 result_queue 取出结果：
       result = result_queue.get(timeout=1.0)
       如果 result 为 None：continue

    2. 推送帧到录制器（如果推理线程未推送）：
       recorder.push_frame(result.camera_id, result.frame, result.timestamp)

    3. 规则引擎评估：
       events = rule_engine.evaluate(
           camera_id=result.camera_id,
           tracks=result.tracks,
           frame=result.frame,
           timestamp=result.timestamp)

    4. 对每个触发的事件生成告警：
       for event in events:
           构造 Alert(event=event)

           保存截图：
             snapshot_path = recorder.save_snapshot(
                 result.camera_id, result.frame, result.timestamp)
             event.snapshot_path = snapshot_path

           截取视频片段（异步）：
             recorder.save_clip(
                 camera_id=result.camera_id,
                 trigger_time=result.timestamp,
                 callback=lambda path: alert.video_clip_path = path)

           写入数据库：
             database.save_alert(alert)

           异步 LLM 分析（不阻塞）：
             if llm_analyzer:
                 submit_async(llm_analyzer.analyze, alert)

           异步通知发送（不阻塞）：
             for notifier in notifiers:
                 submit_async(notifier.execute, alert)

           更新摄像头告警计数
           通过 WebSocket 推送实时告警到前端

    5. 更新处理统计
```

### 3.6 优雅关闭（stop）

```
VisionAgent.stop()

  关闭顺序严格按上游→下游，确保数据不丢失：

  设置 _running = False

  日志记录：系统开始关闭

  1. 停止所有摄像头线程：
     for thread in camera_threads:
         thread.stop()     # 停止读帧，终止 FFmpeg
     日志等待：等待 frame_queue 排空（最多 3 秒）
     frame_queue.clear()

  2. 停止推理线程：
     inference_thread.stop()
     日志等待：等待 result_queue 排空（最多 3 秒）
     result_queue.clear()

  3. 停止处理线程：
     action_thread.stop()  # 等待当前告警处理完成（最多 10 秒）

  4. 停止 Web 服务：
     web_server.stop()

  5. 停止定时任务：
     cleanup_timer.stop()

  6. 释放检测器资源：
     detector.release()    # 释放 GPU 显存

  7. 关闭数据库连接：
     database.close()

  8. 释放录制器：
     recorder.release()

  日志记录：系统已完全关闭
```

关闭超时保护：
- 整个关闭流程最多 30 秒
- 超时后强制终止所有线程（thread.join(timeout=0) + daemon 兜底）
- 超时情况记录到日志

### 3.7 健康检查（health）

```
VisionAgent.health() -> HealthResponse

  收集所有组件的运行指标（不依赖 LLM、Redis）：

  摄像头状态：
    active_cameras = sum(1 for t in camera_threads if t.status == CameraStatus.CONNECTED)
    total_cameras = len(camera_threads)

  队列深度：
    queue_depth = frame_queue.qsize()

  GPU 状态（通过 GPUtil 或 torch.cuda）：
    gpu_utilization = torch.cuda.utilization()
    gpu_memory_used = torch.cuda.memory_allocated() / 1024 / 1024
    gpu_memory_total = torch.cuda.get_device_properties(0).total_memory / 1024 / 1024

  推理延迟（从 InferenceThread 统计）：
    inference_latency_p50 = inference_thread.p50_latency
    inference_latency_p99 = inference_thread.p99_latency

  运行时长：
    uptime_seconds = time.time() - _start_time

  今日告警数（从数据库查询）：
    today_alerts = database.count_alerts_today()

  LLM 成功率（从 LLMAnalyzer 统计）：
    llm_success_rate = llm_analyzer.success_rate if llm_analyzer else 1.0

  健康判定：
    if active_cameras == 0 or GPU 不可用:
        status = "unhealthy"
    elif queue_depth > 100 or p99_latency > 100ms 持续 30 秒:
        status = "degraded"
    else:
        status = "ok"
```

### 3.8 热重载

#### 摄像头配置热重载

```
reload_camera(camera_id: str)

  流程：
    1. 读取新的摄像头配置文件
    2. 校验配置格式
    3. 找到对应的 CameraThread
    4. 停止该线程（thread.stop()）
    5. 创建新的 CameraThread（新配置）
    6. 启动新线程
    7. 更新组件引用
    8. 重置该路的追踪器（tracker_manager.reset(camera_id)）
    9. 日志记录：摄像头已重载

  注意：
    - 重载期间（几秒）该路没有新帧，不影响其他路
    - 追踪器必须重置，因为画面可能完全变化
```

#### 规则配置热重载

```
reload_rules()

  流程：
    1. 扫描 rules 目录，读取所有 YAML 文件
    2. 解析规则配置
    3. 校验规则语法
    4. 构建新的规则列表
    5. 替换 rule_engine 的规则集合（原子操作）
    6. 新规则在下一帧立即生效
    7. 日志记录：规则已重载，共 N 条规则

  失败处理：
    如果新规则解析失败：
      保留旧规则不变
      日志记录错误详情
      不中断服务
```

#### 文件监控方式

```
规则和摄像头配置的热重载由文件监控触发：

  使用 watchdog 库（或简单轮询）监控：
    - configs/cameras/ 目录（*.yaml 文件变化）
    - configs/rules/ 目录（*.yaml 文件变化）

  防抖动：
    文件变化后等待 1 秒（防止编辑器保存时的多次事件）
    合并 1 秒内的多次变化为一次重载

  可通过配置关闭热重载（开发时可能不需要）：
    rules.hot_reload: true/false
    cameras.hot_reload: true/false
```

## 4. 依赖关系

### 4.1 依赖的模块

| 模块 | 用途 |
|------|------|
| `core/types` | 所有数据模型 |
| `core/camera` | CameraThread，帧采集 |
| `core/detector` | YOLODetector，目标检测 |
| `core/tracker` | TrackerManager，多目标追踪 |
| `core/recorder` | ClipRecorder，视频录制与截图 |
| `rules/engine` | RuleEngine，规则引擎评估 |
| `llm/analyzer` | LLMAnalyzer，LLM 分析（可选） |
| `actions/notifier` | 各种通知器实现 |
| `storage/database` | Database，持久化存储 |
| `web/server` | WebServer，HTTP/WebSocket 服务 |
| `config` | AppConfig，全局配置加载与校验 |
| `logging` | 结构化日志 |

### 4.2 被依赖的模块

| 模块 | 依赖方式 |
|------|---------|
| `web/api` | 通过 VisionAgent 实例访问系统状态和告警数据 |
| `__main__` | 入口点创建 VisionAgent 并启动 |

## 5. 配置项

### 5.1 系统配置（settings.yaml → system 段）

| 字段名 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `system.name` | `str` | "SentinelMind" | 系统名称 |
| `system.log_level` | `str` | "INFO" | 日志级别 |
| `system.log_dir` | `str` | "logs" | 日志目录 |
| `system.data_dir` | `str` | "data" | 数据目录 |
| `system.log_max_size_mb` | `int` | 50 | 单个日志文件最大 MB |
| `system.log_backup_count` | `int` | 5 | 日志备份文件数 |

### 5.2 队列配置（settings.yaml → pipeline 段，或分散在各组件配置中）

| 字段名 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `pipeline.frame_queue_size` | `int` | 200 | FrameQueue 最大容量 |
| `pipeline.result_queue_size` | `int` | 100 | ResultQueue 最大容量 |
| `pipeline.shutdown_timeout` | `int` | 30 | 优雅关闭超时（秒） |
| `pipeline.frame_drain_timeout` | `int` | 3 | 关闭时队列排空超时（秒） |

### 5.3 热重载配置

| 字段名 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `rules.hot_reload` | `bool` | True | 规则文件热重载 |
| `cameras.hot_reload` | `bool` | True | 摄像头配置热重载 |

## 6. 错误处理

### 6.1 异常场景与降级策略

| 异常场景 | 处理方式 | 降级方案 |
|---------|---------|---------|
| 启动时 GPU 不可用 | 检查 cuda.is_available()，不可用则报错 | 可选：降级为 CPU 推理（配置允许时） |
| 启动时模型文件缺失 | 抛出 StartupError，进程退出 | 无法降级 |
| 启动时配置校验失败 | 抛出 ConfigError，进程退出 | 无法降级 |
| 某路摄像头断线 | 该路 CameraThread 自动重连，其他路正常 | 跳过该路推理 |
| 推理线程异常退出 | 捕获异常，尝试重启推理线程（最多 3 次） | 3 次重启失败后告警 |
| 处理线程异常退出 | 捕获异常，尝试重启处理线程（最多 3 次） | 3 次重启失败后告警 |
| FrameQueue 持续满（> 30 秒） | 日志警告，可能是推理速度不足 | 减少路数或降低帧率 |
| 规则引擎评估异常 | 捕获异常，跳过该帧的规则评估 | 该帧不产生告警 |
| Web 服务启动失败 | 日志记录，不影响主处理管线 | 无 Web 界面，仅日志告警 |
| 通知发送失败 | 重试 1 次，仍失败记录日志 | 不影响其他通知渠道 |

### 6.2 线程异常恢复

```
InferenceThread / ActionThread 异常退出时的恢复机制：

  在 VisionAgent 层面监控线程存活状态：
    每 5 秒检查一次所有线程的 is_alive()
    
  如果 InferenceThread 退出：
    如果 _running == True（非主动停止）：
      日志记录：推理线程异常退出
      等待 2 秒
      重启推理线程（max 3 次）
      3 次后仍失败：告警"推理服务异常"

  如果 ActionThread 退出：
    同上逻辑，独立计数器

  如果某路 CameraThread 退出：
    摄像头线程内部已有重连逻辑
    如果线程本身退出（而非 FFmpeg 子进程退出）：
      日志记录异常
      重启该路 CameraThread
```

## 7. 设计决策

### 7.1 为什么采用三层线程模型

- 采集层：IO 密集，需要独立线程避免阻塞
- 推理层：GPU 密集，单线程避免 GPU 竞争（多线程争抢 GPU 会导致 batch 效率下降）
- 处理层：业务逻辑，顺序处理简化状态管理
- 三层解耦：各层速度独立，通过队列缓冲，某层慢了不会拖垮上游

### 7.2 为什么 FrameQueue 和 ResultQueue 都是满则丢旧帧

- 监控场景的核心需求是实时性，延迟比丢帧更不可接受
- 摄像头帧不断进来，如果下游处理慢了，队列只会无限增长
- 丢几帧不影响告警准确性（同一路的下一帧通常包含相同的目标）
- 如果阻塞上游，整个系统延迟会持续累积，最终变得不可用

### 7.3 为什么关闭顺序是上游→下游

- 先停上游（摄像头），确保不再有新数据进入
- 等待队列排空，让已采集的帧得到处理
- 再停推理，让已有结果得到处理
- 最后停处理，让已生成的告警完成通知和存储
- 如果反过来（先停下游），上游还在产生数据，这些数据会丢失

### 7.4 为什么 Web 服务最后关闭

- Web 服务在关闭过程中提供状态查询（用户可以看到"系统正在关闭"）
- 健康检查端点在关闭过程中仍然可用（返回 shutting_down 状态）
- WebSocket 推送最后的告警通知

### 7.5 为什么热重载选择文件监控而非 API 触发

- 运维人员直接编辑 YAML 文件是最自然的工作流
- 不需要额外的管理 API 或 CLI 工具
- 文件监控（watchdog）轻量可靠
- API 触发方式可在后期通过 reload_rules() 等方法补充

### 7.6 组装在 __init__ 而不是 start 的原因

- __init__ 时创建所有组件实例，此时可以校验配置
- start() 时才启动线程和网络，__init__ 失败可以快速报错
- 组件引用在 __init__ 时固定，方便测试时注入 mock
- 分离构造与启动，符合单一职责原则

## 10. 端-云扩展预留

### 10.1 数据流模式

当前设计仅支持"拉流→推理"模式。后期扩展支持两种数据流模式：

| 模式 | 数据流 | 适用场景 |
|------|--------|---------|
| stream（默认） | 摄像头→FFmpeg→帧→推理→结果 | 服务器有 GPU |
| edge | 边缘设备→检测结果→规则引擎 | 边缘设备有算力 |

在 edge 模式下，pipeline 跳过推理层，直接将边缘设备上报的检测结果送入处理层（规则引擎→告警→LLM→通知）。

### 10.2 线程模型变化

| 模式 | 采集层 | 推理层 | 处理层 |
|------|--------|--------|--------|
| stream | CameraThread（FFmpeg） | InferenceThread（GPU） | ActionThread |
| edge | EdgeReceiverThread（HTTP/MQTT） | 跳过 | ActionThread |

EdgeReceiverThread 替代 CameraThread + InferenceThread，直接输出 Track 列表到 ResultQueue。

### 10.3 混合部署

支持同一系统中部分摄像头走 stream 模式、部分走 edge 模式。pipeline 根据每个摄像头的 source_type 配置选择对应的数据流。

### 10.4 设计约束

- ActionThread 和下游组件（规则引擎、LLM、通知）完全不感知数据来源
- edge 模式下 FrameQueue 不使用，InferenceThread 空转
- 不在第一版实现，但 pipeline 的组件组装逻辑需支持 source_type 路由
