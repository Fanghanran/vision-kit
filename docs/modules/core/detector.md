# detector — 检测器模块 设计书

## 1. 模块职责

封装 YOLO 目标检测模型，提供统一的批量推理接口，将原始帧转换为结构化的 Detection 列表。支持多帧 batch 推理以提高 GPU 利用率，处理推理失败的降级逻辑。

## 2. 对外接口

### 2.1 DetectorProtocol（抽象接口）

| 方法/属性 | 签名 | 说明 |
|-----------|------|------|
| `detect` | `(frames: list[numpy.ndarray]) -> list[list[Detection]]` | batch 推理，返回每帧的检测结果列表 |
| `warmup` | `() -> None` | 模型预热，首次推理前调用 |
| `release` | `() -> None` | 释放模型资源（GPU 显存） |
| `model_name` | `str` (属性) | 当前模型名称 |
| `classes` | `list[str]` (属性) | 支持检测的类别名称列表 |

### 2.2 YOLODetector 实现类

| 方法/属性 | 签名 | 说明 |
|-----------|------|------|
| `__init__` | `(config: DetectorConfig, device: str = "cuda:0") -> None` | 加载 YOLO 模型，设置推理参数 |
| `detect` | `(frames: list[numpy.ndarray]) -> list[list[Detection]]` | batch 推理 |
| `warmup` | `() -> None` | 用空白帧跑一次推理，预热 GPU |
| `release` | `() -> None` | 删除模型、清空 CUDA 缓存 |
| `model_name` | `str` (属性) | 返回模型文件名 |
| `classes` | `list[str]` (属性) | 返回模型类别列表 |
| `detect_single` | `(frame: numpy.ndarray) -> list[Detection]` | 单帧推理便捷方法 |
| `set_confidence` | `(threshold: float) -> None` | 运行时调整置信度阈值 |

### 2.3 DetectorConfig 数据类

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `model_path` | `str` | 必填 | 模型文件路径（.pt 或 .engine） |
| `confidence` | `float` | 0.5 | 置信度阈值 |
| `iou_threshold` | `float` | 0.45 | NMS IoU 阈值 |
| `batch_size` | `int` | 8 | 最大 batch 大小 |
| `batch_timeout_ms` | `int` | 50 | batch 超时触发时间（毫秒） |
| `input_size` | `int` | 640 | 模型输入分辨率 |
| `fp16` | `bool` | True | 是否使用 FP16 推理 |
| `classes_filter` | `list[str]` | None | 只检测指定类别（None=全部） |
| `max_det` | `int` | 300 | 单帧最大检测数 |

### 2.4 Detection 数据类（已在 core/types.py 定义）

| 字段 | 类型 | 说明 |
|------|------|------|
| `frame_id` | `int` | 帧序号 |
| `class_id` | `int` | 类别 ID |
| `class_name` | `str` | 类别名称 |
| `confidence` | `float` | 置信度 0-1 |
| `bbox` | `BoundingBox` | 边界框 (x1, y1, x2, y2) |
| `timestamp` | `float` | 检测时间戳 |

## 3. 内部逻辑

### 3.1 初始化流程

```
YOLODetector.__init__(config, device)
  |
  v
验证模型文件存在（不存在则抛出 FileNotFoundError）
  |
  v
加载 YOLO 模型（Ultralytics YOLO(model_path)）
  |
  v
判断模型类型：
  - .pt 文件 → PyTorch 模型，原生加载
  - .engine 文件 → TensorRT 引擎，使用 trt 模式加载
  |
  v
设置推理参数（conf, iou, imgsz, max_det）
  |
  v
如果 fp16=True 且 device 包含 cuda：
    将模型转为 half 精度（model.half()）
  |
  v
获取类别列表（model.names）
  |
  v
如果 classes_filter 不为空：
    计算需要过滤的 class_id 集合
  |
  v
初始化完成，模型在 GPU 显存中
```

### 3.2 模型预热（warmup）

```
warmup()
  |
  v
创建空白输入帧（全零 numpy 数组，shape 对应 input_size）
  |
  v
执行 3 次推理（填充 CUDA kernel 缓存、分配显存）
  |
  v
同步 CUDA（torch.cuda.synchronize()）
  |
  v
日志记录预热耗时
```

预热的目的是消除首次推理的额外开销（CUDA kernel 编译、显存分配），避免第一帧推理异常慢。

### 3.3 batch 推理流程

```
detect(frames: list[numpy.ndarray]) -> list[list[Detection]]
  |
  v
输入校验：
  - frames 为空 → 返回空列表
  - 单帧走 detect_single 优化路径
  |
  v
预处理（preprocess）：
  对每帧执行 resize 到 input_size（如果不是正方形则 pad）
  如果 fp16：转为 half tensor
  堆叠为 batch tensor（shape: [N, 3, H, W]）
  移动到 GPU（tensor.to(device)）
  |
  v
推理（infer）：
  使用 torch.no_grad() 上下文
  调用 model(batch_tensor)
  同步 CUDA
  |
  v
后处理（postprocess）：
  对 batch 中每个结果：
    1. 提取 boxes, scores, class_ids
    2. 过滤置信度 < threshold 的检测
    3. 过滤不在 classes_filter 中的类别
    4. 应用 NMS（Ultralytics 内置）
    5. 坐标转换：模型输出坐标 → 原始帧坐标（逆 resize/pad）
    6. 构造 Detection 对象列表
  |
  v
返回 list[list[Detection]]
```

### 3.4 结果解析（Ultralytics Results → Detection 列表）

```
对 Ultralytics Results 对象中的每个 Results：
  获取 results.boxes：
    - xyxy 坐标 → BoundingBox(x1, y1, x2, y2)
    - conf 置信度 → confidence
    - cls 类别 ID → class_id
    - 通过 model.names[class_id] 获取 class_name
  对每个检测框：
    坐标缩放回原始帧尺寸（如果模型做了 resize）
    构造 Detection(frame_id, class_id, class_name, confidence, bbox, timestamp)
  返回该帧的 Detection 列表
```

### 3.5 推理失败处理

```
单帧推理失败：
  捕获异常（CUDA OOM、输入形状不匹配、模型内部错误）
  日志记录：camera_id, frame_id, 异常类型, 异常信息
  该帧返回空列表（跳过检测，追踪器用上一帧结果兜底）
  更新连续失败计数器（per camera_id）

连续失败计数器：
  - 每次失败 +1
  - 每次成功重置为 0
  - > 10：该路降级为"仅录制不检测"（通知 pipeline 跳过该路推理）
  - > 100：触发系统告警"检测服务异常"

OOM 处理特殊逻辑：
  如果异常是 CUDA OutOfMemoryError：
    尝试清空 CUDA 缓存（torch.cuda.empty_cache()）
    将 batch_size 减半重试
    如果仍然 OOM：该帧返回空列表
```

### 3.6 batch 合并策略（由推理线程调用）

batch 合并逻辑在 pipeline 的 InferenceThread 中实现，detector 模块只负责接收 frames 列表并返回结果。但 detector 提供 batch 配置参数供 pipeline 使用：

```
触发条件（满足任一即推理）：
  1. 积累帧数 >= batch_size（默认 8）
  2. 等待时间 >= batch_timeout_ms（默认 50ms）

batch 合并时记录每帧的 camera_id 映射：
  batch_frames = [(cam_id_1, frame_1), (cam_id_2, frame_2), ...]
  推理后按 camera_id 分发结果到各自的追踪器
```

## 4. 依赖关系

### 4.1 依赖的模块

| 模块 | 用途 |
|------|------|
| `core/types` | 使用 Detection、BoundingBox 数据模型 |
| `config` | 读取 DetectorConfig 配置 |
| `ultralytics` | YOLO 模型加载与推理 |
| `torch` | PyTorch 推理、CUDA 管理 |
| `numpy` | 帧数据处理 |
| `logging` | 结构化日志 |

### 4.2 被依赖的模块

| 模块 | 依赖方式 |
|------|---------|
| `core/pipeline` | InferenceThread 调用 detect() 方法，管理生命周期 |
| `core/tracker` | 间接依赖：检测结果作为追踪器的输入 |

## 5. 配置项

### 5.1 全局配置（settings.yaml → detector 段）

| 字段名 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `detector.model_path` | `str` | 必填 | 模型文件路径 |
| `detector.confidence` | `float` | 0.5 | 全局置信度阈值 |
| `detector.iou_threshold` | `float` | 0.45 | NMS IoU 阈值 |
| `detector.batch_size` | `int` | 8 | 最大 batch 大小 |
| `detector.batch_timeout_ms` | `int` | 50 | batch 超时毫秒数 |
| `detector.input_size` | `int` | 640 | 模型输入分辨率 |
| `detector.fp16` | `bool` | True | 是否 FP16 推理 |
| `detector.classes_filter` | `list[str]` | null | 类别过滤（null=全部） |
| `detector.max_det` | `int` | 300 | 单帧最大检测数 |

### 5.2 摄像头级覆盖（cameras/cam_XX.yaml → detection 段）

| 字段名 | 类型 | 说明 |
|--------|------|------|
| `detection.confidence` | `float` | 覆盖该路的置信度阈值 |
| `detection.classes_filter` | `list[str]` | 覆盖该路的类别过滤 |

摄像头级配置优先于全局配置，用于特定场景优化（如某路只检测人，不检测车）。

### 5.3 GPU 配置（settings.yaml → gpu 段）

| 字段名 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `gpu.device_id` | `int` | 0 | GPU 编号 |
| `gpu.batch_size` | `int` | 8 | batch 大小（与 detector 配置合并） |
| `gpu.fp16` | `bool` | True | FP16 开关 |
| `gpu.tensorrt` | `bool` | False | 是否启用 TensorRT 加速 |

## 6. 错误处理

### 6.1 异常场景与降级策略

| 异常场景 | 处理方式 | 降级方案 |
|---------|---------|---------|
| 模型文件不存在 | 启动时校验失败，进程报错退出 | 无法降级，必须有模型 |
| CUDA 设备不可用 | 检测是否有 CPU fallback 配置 | 降级为 CPU 推理（速度降 5-10 倍） |
| CUDA OOM | 清空缓存，batch_size 减半重试 | 减小 batch 降低显存 |
| 推理超时（单帧 > 500ms） | 跳过该帧，返回空列表 | 追踪器兜底 |
| 模型内部异常 | 捕获异常，跳帧，记录日志 | 追踪器兜底 |
| 输入帧格式异常（非 BGR24） | 捕获 reshape 错误，跳帧 | 记录日志 |
| FP16 模型精度溢出 | 自动降级为 FP32 | 速度降低约 30% |

### 6.2 推理统计

detector 内部维护以下计数器（用于健康检查和性能监控）：

| 统计项 | 类型 | 说明 |
|--------|------|------|
| `total_inferences` | `int` | 总推理次数 |
| `total_frames` | `int` | 总处理帧数 |
| `total_failures` | `int` | 总失败次数 |
| `avg_latency_ms` | `float` | 平均推理延迟（滑动窗口） |
| `p99_latency_ms` | `float` | P99 推理延迟 |
| `consecutive_failures` | `dict[str, int]` | 每路摄像头连续失败次数 |

## 7. 设计决策

### 7.1 为什么使用 DetectorProtocol 而不是直接用 YOLODetector

- Protocol 定义接口，不依赖具体实现，便于后期扩展
- 工业质检可换成双编码器模型，人脸识别可换成 InsightFace，分割可换成 SAM
- 测试时可注入 MockDetector，不依赖 GPU
- 符合依赖倒置原则（DIP）

### 7.2 为什么 batch_size 默认为 8

- 性能平衡点：batch_size=8 在 16GB GPU 上推理效率最高
  - batch_size=1：GPU 利用率低，推理吞吐最低
  - batch_size=4：GPU 利用率约 40%
  - batch_size=8：GPU 利用率约 70%，性价比最优
  - batch_size=16：GPU 利用率约 90%，但延迟增加，显存压力大
- 8 帧正好覆盖 4 路 5fps 的 0.4 秒积攒量，延迟可接受

### 7.3 为什么 batch_timeout 为 50ms

- 如果帧率低（只有 1-2 路），batch 可能凑不满 8 帧
- 50ms 超时确保低负载时推理延迟不超过 50ms + 推理时间
- 50ms 的 CPU 开销很小（一次 select/poll 操作）
- 超时触发意味着 batch 可能只有 1-2 帧，但保证了实时性

### 7.4 为什么默认开启 FP16

- FP16 推理速度比 FP32 快 1.5-2 倍（GPU Tensor Core 加速）
- FP16 显存占用约为 FP32 的一半
- 目标检测精度损失可忽略（YOLO 对 FP16 鲁棒性好）
- 只在支持 FP16 的 GPU 上开启（compute capability >= 7.0），否则自动降级

### 7.5 为什么推理失败不重试而是跳帧

- 推理失败通常意味着 GPU 状态异常或输入数据问题
- 重试同一帧大概率还是失败
- 跳帧让追踪器用上一帧的追踪结果兜底，保证连续性
- 如果连续失败则触发更高级别的降级处理

### 7.6 为什么坐标转换在 detector 内部完成

- detector 知道模型做了哪些预处理（resize、pad），最适合做逆转换
- 下游（tracker、rules）拿到的坐标直接对应原始帧，无需再转换
- 避免坐标系混乱导致的 bug

## 8. 端-云扩展预留

### 8.1 RemoteDetector 实现

当前设计仅实现 YOLODetector（本地推理）。后期扩展边缘设备时，新增 RemoteDetector 实现：

| 实现 | 推理位置 | 适用场景 |
|------|---------|---------|
| YOLODetector（默认） | 本地 GPU | 服务器有 GPU，摄像头在同一局域网 |
| RemoteDetector | 边缘设备 | 边缘设备有算力，只上报结果 |

RemoteDetector 实现 DetectorProtocol 接口，detect() 方法通过 HTTP 调用边缘设备的推理 API，返回格式与本地推理一致。

### 8.2 混合模式

支持同一系统中部分摄像头用本地推理、部分用边缘推理。配置方式：

```yaml
# 全局默认
detector:
  type: local

# 单个摄像头覆盖
camera:
  detector_type: remote    # 这路走边缘
  edge_endpoint: http://192.168.1.50:8080/api/detect
```

### 8.3 设计约束

- DetectorProtocol 接口不变，RemoteDetector 只是新增一个实现
- 边缘设备不可用时，降级处理方式与本地推理失败一致（跳帧+追踪兜底）
- 不在第一版实现
