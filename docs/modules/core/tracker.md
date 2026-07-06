# tracker — 追踪器模块 设计书

## 1. 模块职责

封装 BoT-SORT 多目标追踪算法，为每路摄像头维护独立的追踪器实例，将跨帧的 Detection 关联为具有唯一 track_id 的 Track 对象，维护目标轨迹、速度等运动属性。

## 2. 对外接口

### 2.1 TrackerProtocol（抽象接口）

| 方法/属性 | 签名 | 说明 |
|-----------|------|------|
| `update` | `(detections: list[Detection], frame: numpy.ndarray) -> list[Track]` | 输入当前帧检测结果，返回活跃追踪目标列表 |
| `get_tracks` | `() -> list[Track]` | 获取当前所有活跃追踪目标（不含已丢失目标） |
| `reset` | `() -> None` | 重置追踪器内部状态，清除所有轨迹 |

### 2.2 BoTSORTTracker 实现类

| 方法/属性 | 签名 | 说明 |
|-----------|------|------|
| `__init__` | `(config: TrackerConfig) -> None` | 初始化 BoT-SORT 追踪器实例 |
| `update` | `(detections: list[Detection], frame: numpy.ndarray) -> list[Track]` | 更新追踪状态 |
| `get_tracks` | `() -> list[Track]` | 获取活跃追踪目标 |
| `reset` | `() -> None` | 重置追踪器 |
| `track_count` | `int` (属性) | 当前活跃追踪目标数量 |
| `next_track_id` | `int` (属性) | 下一个可用的 track_id |

### 2.3 TrackerConfig 数据类

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `tracker_type` | `str` | "botsort" | 追踪器类型（预留：bytetrack） |
| `track_thresh` | `float` | 0.5 | 追踪置信度阈值 |
| `track_buffer` | `int` | 30 | 目标丢失后保留帧数 |
| `match_thresh` | `float` | 0.8 | 匹配阈值（IoU 或外观特征） |
| `fuse_score` | `bool` | True | 是否融合置信度分数 |
| `new_track_thresh` | `float` | 0.6 | 新目标初始化阈值 |
| `max_age` | `int` | 30 | 目标最大丢失帧数（等于 track_buffer） |
| `min_hits` | `int` | 3 | 连续命中多少帧后才输出目标 |
| `use_appearance` | `bool` | True | 是否使用外观特征匹配 |
| `appearance_weight` | `float` | 0.5 | 外观特征在匹配中的权重 |

### 2.4 Track 数据类（已在 core/types.py 定义）

| 字段 | 类型 | 说明 |
|------|------|------|
| `track_id` | `int` | 追踪 ID（同一目标跨帧不变） |
| `class_name` | `str` | 类别名称 |
| `bbox` | `BoundingBox` | 当前帧位置 |
| `trajectory` | `list[tuple[float, float, float]]` | 历史轨迹 [(cx, cy, ts), ...] |
| `velocity` | `tuple[float, float]` | 速度向量 (vx, vy) 像素/秒 |
| `first_seen` | `float` | 首次出现时间戳 |
| `last_seen` | `float` | 最后出现时间戳 |
| `age` | `int` | 存活帧数（从首次出现计） |
| `hit_streak` | `int` | 连续命中帧数（未匹配则重置） |

### 2.5 TrackerManager 类（多路管理器）

| 方法/属性 | 签名 | 说明 |
|-----------|------|------|
| `__init__` | `(config: TrackerConfig) -> None` | 初始化管理器，按需创建追踪器实例 |
| `update` | `(camera_id: str, detections: list[Detection], frame: numpy.ndarray) -> list[Track]` | 路由到对应摄像头的追踪器 |
| `get_tracks` | `(camera_id: str) -> list[Track]` | 获取指定摄像头的追踪结果 |
| `reset` | `(camera_id: str) -> None` | 重置指定摄像头的追踪器 |
| `reset_all` | `() -> None` | 重置所有追踪器 |
| `remove_tracker` | `(camera_id: str) -> None` | 移除指定摄像头的追踪器实例 |

## 3. 内部逻辑

### 3.1 单帧追踪流程

```
TrackerManager.update(camera_id, detections, frame)
  |
  v
查找该 camera_id 对应的 BoTSORTTracker 实例
  如果不存在：创建新实例，注册到管理器
  |
  v
调用 BoTSORTTracker.update(detections, frame)
  |
  v
内部流程：
  1. 将 Detection 列表转换为追踪器内部格式：
     - 构造检测矩阵 [N, 5]：每行 [x1, y1, x2, y2, confidence]
     - 提取类别信息 [class_id, class_name]
  |
  2. 预测已有轨迹的新位置（卡尔曼滤波预测）：
     - 对每个已追踪目标，根据运动模型预测当前帧位置
     - 生成预测边界框
  |
  3. 计算匹配代价矩阵：
     - IoU 匹配：计算预测框与检测框的 IoU 矩阵
     - 如果 use_appearance=True：
         提取外观特征（ReID 特征向量）
         计算外观相似度矩阵
         融合代价 = appearance_weight * 外观 + (1 - appearance_weight) * IoU
     - 否则：代价 = 1 - IoU
  |
  4. 匈牙利算法匹配（Hungarian Algorithm）：
     - 输入代价矩阵，输出最优匹配对
     - 匹配成功的 (track, detection) 对更新轨迹
     - 未匹配的 track 标记为丢失（hit_streak 重置为 0）
     - 未匹配的 detection 尝试初始化新轨迹
  |
  5. 轨迹生命周期管理：
     - 匹配成功：更新 bbox、轨迹、速度、age+1、hit_streak+1
     - 匹配失败：age+1、hit_streak=0、max_age 计数器递增
     - 连续丢失 > max_age：删除该轨迹
     - 新检测初始化：如果 confidence > new_track_thresh，创建新轨迹
     - 新轨迹需要连续命中 >= min_hits 才被输出
  |
  6. 构造 Track 对象列表（仅输出活跃且已确认的轨迹）
  |
  v
返回 list[Track]
```

### 3.2 轨迹更新细节

对每个匹配成功的轨迹：

```
更新 bbox = 当前帧检测的 BoundingBox
更新 last_seen = 当前时间戳
更新 age += 1
更新 hit_streak += 1

更新轨迹：
  当前中心点 = bbox.center
  追加 (cx, cy, timestamp) 到 trajectory
  如果 trajectory 长度 > 100：截断保留最近 100 个点

更新速度：
  如果 trajectory 至少有 2 个点：
    取最近 2 个点 (p1, p2)
    dt = p2.timestamp - p1.timestamp
    如果 dt > 0：
      vx = (p2.x - p1.x) / dt  （像素/秒）
      vy = (p2.y - p1.y) / dt
      velocity = (vx, vy)
  否则：
    velocity = (0.0, 0.0)
```

### 3.3 轨迹丢失处理

```
对每个未匹配的已有轨迹：
  hit_streak = 0
  连续丢失计数 += 1

  如果连续丢失 > max_age：
    从追踪器中删除该轨迹
    日志记录：track_id, 最后位置, 存活帧数

特殊逻辑：
  - 即使目标暂时丢失（遮挡、出画面），追踪器会保持预测位置
  - 在 max_age 帧内重新出现可自动关联（基于运动预测 + IoU 匹配）
  - 如果使用外观特征，遮挡后的重新识别更准确
```

### 3.4 速度计算方式

```
简化方案（默认）：
  最近 2 帧中心点位移 / 时间间隔
  单位：像素/秒
  优点：计算快，足够规则引擎使用

平滑方案（可选）：
  最近 N 帧（N=5）的加权平均速度
  最近的帧权重更高（指数衰减）
  优点：更平滑，减少抖动
```

### 3.5 TrackerManager 路由逻辑

```
追踪器按 camera_id 隔离：
  _trackers: dict[str, BoTSORTTracker]
  
  每路摄像头有独立的追踪器实例：
  - 独立的 track_id 空间（每路从 0 开始）
  - 独立的轨迹历史
  - 独立的丢失计数

  这样设计的原因：
  - 不同摄像头看到的是不同场景，目标不应跨路关联
  - 追踪器状态完全隔离，故障互不影响
  - 内存占用可控（每路约 1-5MB）
```

### 3.6 追踪器内部帧格式转换

```
Detection 列表 → BoT-SORT 输入格式：

  输入：list[Detection]
  处理：
    构造 numpy 数组 det_matrix，shape (N, 5)
    每行：[x1, y1, x2, y2, confidence]
    
    类别信息单独存储：
    class_ids: list[int] = [d.class_id for d in detections]
    class_names: list[str] = [d.class_name for d in detections]
    
  传入 BoT-SORT 追踪器
```

## 4. 依赖关系

### 4.1 依赖的模块

| 模块 | 用途 |
|------|------|
| `core/types` | 使用 Detection、BoundingBox、Track 数据模型 |
| `config` | 读取 TrackerConfig 配置 |
| `ultralytics` | BoT-SORT 追踪器实现（Ultralytics 内置） |
| `numpy` | 追踪矩阵计算 |
| `scipy.optimize` | 匈牙利算法（linear_sum_assignment） |
| `logging` | 结构化日志 |

### 4.2 被依赖的模块

| 模块 | 依赖方式 |
|------|---------|
| `core/pipeline` | InferenceThread 调用 update()，ActionThread 调用 get_tracks() |
| `rules/engine` | 规则引擎评估使用 Track 列表作为输入 |

## 5. 配置项

### 5.1 全局配置（settings.yaml → tracker 段）

| 字段名 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `tracker.type` | `str` | "botsort" | 追踪器类型 |
| `tracker.track_thresh` | `float` | 0.5 | 追踪置信度阈值 |
| `tracker.track_buffer` | `int` | 30 | 丢失保留帧数 |
| `tracker.match_thresh` | `float` | 0.8 | 匹配阈值 |
| `tracker.fuse_score` | `bool` | True | 融合置信度 |
| `tracker.new_track_thresh` | `float` | 0.6 | 新目标阈值 |
| `tracker.max_age` | `int` | 30 | 最大丢失帧数 |
| `tracker.min_hits` | `int` | 3 | 最小确认命中数 |
| `tracker.use_appearance` | `bool` | True | 使用外观特征 |
| `tracker.appearance_weight` | `float` | 0.5 | 外观权重 |

### 5.2 性能相关配置

| 字段名 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `tracker.trajectory_max_length` | `int` | 100 | 轨迹最大存储点数 |
| `tracker.velocity_window` | `int` | 2 | 速度计算窗口大小 |

## 6. 错误处理

### 6.1 异常场景与降级策略

| 异常场景 | 处理方式 | 降级方案 |
|---------|---------|---------|
| Detection 列表为空 | 正常处理，所有轨迹标记为未匹配 | 追踪器保持预测位置 |
| 追踪器内部异常（数值溢出、矩阵奇异） | 捕获异常，reset 该路追踪器，返回空 Track 列表 | 丢失当前帧追踪，下帧重新开始 |
| 外观特征提取失败 | 自动降级为纯 IoU 匹配 | 匹配精度略降，遮挡后重识别能力变弱 |
| 帧图像格式异常 | 捕获异常，跳过外观特征提取 | 降级为纯 IoU 匹配 |
| 内存占用过高（轨迹数过多） | 强制清理 age > max_age * 2 的轨迹 | 可能丢失一些长时遮挡的目标 |
| track_id 溢出 | Python int 无上限，理论上不会溢出 | — |

### 6.2 追踪器重置条件

以下情况触发追踪器重置：

- pipeline 检测到该路摄像头断线重连（画面变化大，旧轨迹无意义）
- 连续推理失败超过阈值后恢复（追踪器状态可能已过期）
- 手动调用 reset（API 触发）
- 配置热重载（追踪器参数变化）

## 7. 设计决策

### 7.1 为什么选择 BoT-SORT 而不是其他追踪器

- BoT-SORT 在 MOT（多目标追踪）benchmark 上表现优秀
- Ultralytics 原生支持，集成成本最低
- 支持外观特征匹配（ReID），遮挡后重识别能力强
- 计算开销可控（单路约 1-3ms/帧），不影响推理管线
- smart_video 项目已验证过 BoT-SORT 在监控场景的效果
- 后期可切换到 ByteTrack（同一接口，配置项改 type 即可）

### 7.2 为什么每路独立追踪器实例

- 不同摄像头看到的是不同物理场景，目标不应跨摄像头关联
- 跨摄像头关联（ReID）是更高级的功能，第一版不实现
- 每路独立实例故障隔离，camera_01 的追踪器异常不影响 camera_02
- 内存占用可控，每路 1-5MB（取决于目标数量和轨迹长度）

### 7.3 为什么 min_hits 默认为 3

- 防止单帧误检被追踪器立即输出为目标
- 3 帧连续命中（约 0.6 秒 @5fps）说明目标确实存在
- 权衡：min_hits 太高会导致新目标输出延迟，太低会有误检
- 3 是 MOT 社区常用的默认值

### 7.4 为什么 max_age 默认为 30

- 30 帧 @5fps = 6 秒，目标出画面 6 秒后才彻底丢弃
- 这段时间内目标重新出现可自动关联
- 太短会导致遮挡后立即丢失，太长会导致 ID switch（误匹配）
- 30 是 BoT-SORT 论文推荐的默认值

### 7.5 为什么轨迹最大长度限制为 100 个点

- 100 个点 @5fps = 20 秒的历史
- 规则引擎通常只看最近几秒的轨迹，20 秒足够
- 不限制会导致内存持续增长（长时间运行的目标）
- 100 个点的 (x, y, timestamp) 约 2.4KB，即使 100 个目标也只有 240KB

### 7.6 TrackerManager 而不是直接暴露 BoTSORTTracker

- 封装多路路由逻辑，pipeline 只需传 camera_id
- 按需创建追踪器实例，未接入的摄像头不占资源
- 统一管理所有追踪器的生命周期（reset_all、remove_tracker）
- 后期可扩展为共享追踪器（跨摄像头关联）的管理入口
