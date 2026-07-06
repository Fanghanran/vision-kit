# recorder — 录制器模块 设计书

## 1. 模块职责

维护每路摄像头的环形帧缓冲区，当告警触发时截取前后 N 秒的视频片段，通过 FFmpeg 异步转码为 H.264 MP4 文件存储到磁盘，并负责过期文件的自动清理。

## 2. 对外接口

### 2.1 ClipRecorder 类

| 方法/属性 | 签名 | 说明 |
|-----------|------|------|
| `__init__` | `(config: RecorderConfig, logger: logging.Logger) -> None` | 初始化录制器 |
| `push_frame` | `(camera_id: str, frame: numpy.ndarray, timestamp: float) -> None` | 将帧推入指定摄像头的环形缓冲 |
| `save_clip` | `(camera_id: str, trigger_time: float, before_seconds: float = 15.0, after_seconds: float = 15.0, callback: Callable[[str], None] = None) -> None` | 异步截取并保存视频片段 |
| `save_snapshot` | `(camera_id: str, frame: numpy.ndarray, timestamp: float) -> str` | 保存单帧截图，返回文件路径 |
| `cleanup_expired` | `() -> int` | 清理过期文件，返回删除文件数 |
| `get_buffer_stats` | `(camera_id: str) -> BufferStats` | 获取缓冲区统计信息 |
| `release` | `() -> None` | 释放所有缓冲区和线程资源 |

### 2.2 RecorderConfig 数据类

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `enabled` | `bool` | True | 是否启用录制 |
| `buffer_duration` | `float` | 30.0 | 环形缓冲时长（秒） |
| `default_before` | `float` | 15.0 | 告警前截取秒数 |
| `default_after` | `float` | 15.0 | 告警后截取秒数 |
| `output_dir` | `str` | "data/clips" | 视频片段输出目录 |
| `snapshot_dir` | `str` | "data/snapshots" | 截图输出目录 |
| `video_format` | `str` | "mp4" | 输出视频格式 |
| `video_codec` | `str` | "libx264" | 视频编码器 |
| `video_crf` | `int` | 23 | H.264 CRF 质量参数（0-51，越小质量越高） |
| `video_preset` | `str` | "fast" | H.264 编码预设 |
| `max_clip_workers` | `int` | 2 | 最大并发转码线程数 |
| `retention_days` | `int` | 7 | 视频片段保留天数 |
| `snapshot_retention_days` | `int` | 30 | 截图保留天数 |
| `max_disk_gb` | `float` | 50.0 | 最大磁盘占用（GB） |

### 2.3 BufferStats 数据类

| 字段 | 类型 | 说明 |
|------|------|------|
| `camera_id` | `str` | 摄像头 ID |
| `buffer_size` | `int` | 当前缓冲帧数 |
| `buffer_duration` | `float` | 缓冲覆盖的实际时长（秒） |
| `memory_mb` | `float` | 缓冲区占用内存（MB） |
| `oldest_timestamp` | `float` | 最早帧的时间戳 |
| `newest_timestamp` | `float` | 最新帧的时间戳 |

## 3. 内部逻辑

### 3.1 环形缓冲管理

```
每个摄像头维护一个 deque（collections.deque）作为环形缓冲：

缓冲区结构：
  _buffers: dict[str, deque[BufferedFrame]]
  BufferedFrame = (frame: numpy.ndarray, timestamp: float, frame_id: int)

push_frame(camera_id, frame, timestamp)：
  如果 camera_id 不在 _buffers 中：
    创建新 deque，maxsize = buffer_duration * fps + 安全余量
    例如 30 秒 @ 5fps → maxsize = 170（留 20 帧余量）
  
  deque.append(BufferedFrame(frame, timestamp, frame_id))
  
  deque 自动丢弃最旧帧（maxsize 限制）

内存估算（单路）：
  帧大小 = 640 * 640 * 3 ≈ 1.2 MB
  30 秒 @ 5fps = 150 帧 ≈ 180 MB
  8 路总计 ≈ 1.4 GB（可接受）

优化：
  帧数据存储前可压缩（numpy 压缩或降低色彩精度）
  当前版本不压缩，优先保证速度（压缩/解压会增加 CPU 开销）
```

### 3.2 告警触发截取

```
save_clip(camera_id, trigger_time, before_seconds, after_seconds, callback)

前置条件：
  - 检查 _buffers 中存在该 camera_id
  - 检查缓冲区中有足够帧

截取逻辑：
  计算时间窗口：
    start_time = trigger_time - before_seconds
    end_time = trigger_time + after_seconds

  从环形缓冲中筛选时间窗口内的帧：
    buffered_frames = [f for f in buffer if start_time <= f.timestamp <= end_time]

  如果时间窗口起始部分的帧已被丢弃（缓冲区不够大）：
    日志警告：缓冲区不足，实际截取时长 < before_seconds + after_seconds
    使用缓冲区中最早的帧作为起始

  如果 after_seconds 对应的帧尚未到达：
    启动延迟收集：
      等待后续帧到达，直到 end_time 的帧被推送
      最大等待时间 = after_seconds + 5 秒（容忍 5 秒延迟）
      超时后使用已收集到的帧（截取片段可能比预期短）

异步转码：
  将截取到的帧列表提交到转码线程池
  使用 ThreadPoolExecutor（max_workers = max_clip_workers）
  callback 在转码完成后被调用（传入文件路径）
```

### 3.3 FFmpeg 转码流程

```
转码线程（异步执行）：

生成输出文件路径：
  {output_dir}/YYYY/MM/DD/{camera_id}_{timestamp}.mp4
  例如：data/clips/2026/07/06/cam_01_1719897615.mp4

确保目录存在（os.makedirs(parents=True, exist_ok=True)）

方案一：pipe 写入 FFmpeg（默认）
  启动 FFmpeg 子进程：
    ffmpeg -y
      -f rawvideo
      -pix_fmt bgr24
      -s {width}x{height}
      -r {fps}
      -i pipe:0
      -c:v {video_codec}
      -crf {video_crf}
      -preset {video_preset}
      -pix_fmt yuv420p
      {output_path}

  通过 stdin 逐帧写入 numpy 数组
  等待 FFmpeg 进程结束
  检查退出码（0=成功）

方案二：临时 raw 文件 + FFmpeg 转码（备选）
  先写入临时 raw 文件，再调用 FFmpeg 转码
  适用于内存紧张场景

转码完成后：
  验证输出文件存在且大小 > 0
  记录日志：文件路径、时长、文件大小
  调用 callback(output_path)（如果有）
```

### 3.4 截图保存

```
save_snapshot(camera_id, frame, timestamp) -> str

生成文件路径：
  {snapshot_dir}/YYYY/MM/DD/{camera_id}_{timestamp}.jpg
  例如：data/snapshots/2026/07/06/cam_01_1719897615.jpg

确保目录存在

使用 cv2.imwrite 保存（JPEG quality=85）
如果 cv2 不可用：使用 PIL.Image 保存

返回文件路径（供 Event.snapshot_path 使用）
```

### 3.5 存储清理

```
cleanup_expired() -> int

清理视频片段：
  遍历 {output_dir}/ 下所有 .mp4 文件
  解析文件修改时间或文件名中的时间戳
  如果文件年龄 > retention_days（默认 7 天）：
    删除文件
    计数器 += 1

清理截图：
  遍历 {snapshot_dir}/ 下所有 .jpg 文件
  如果文件年龄 > snapshot_retention_days（默认 30 天）：
    删除文件
    计数器 += 1

清理空目录：
  自底向上遍历日期目录
  删除空目录

磁盘空间检查：
  计算 {output_dir} 和 {snapshot_dir} 总大小
  如果 > max_disk_gb：
    按修改时间排序，从最旧开始删除
    直到总大小 < max_disk_gb * 0.8（留 20% 余量）

返回删除文件总数
```

### 3.6 定时清理调度

```
cleanup_expired 不由 recorder 自身调度，而是由 pipeline 的定时任务调用。

默认每小时调用一次：
  pipeline 启动定时清理线程
  每 3600 秒调用一次 cleanup_expired
  清理失败不影响主流程（捕获异常，记录日志）
```

## 4. 依赖关系

### 4.1 依赖的模块

| 模块 | 用途 |
|------|------|
| `config` | 读取 RecorderConfig 配置 |
| `numpy` | 帧数据处理 |
| `subprocess`（标准库） | 启动 FFmpeg 转码子进程 |
| `threading`（标准库） | 异步转码线程池 |
| `collections`（标准库） | deque 环形缓冲 |
| `cv2` 或 `PIL` | 截图保存 |
| `pathlib`（标准库） | 文件路径管理 |
| `logging` | 结构化日志 |

### 4.2 被依赖的模块

| 模块 | 依赖方式 |
|------|---------|
| `core/pipeline` | 推送帧、触发截取、调度清理、管理生命周期 |
| `actions/recorder`（如果存在独立 Action） | 作为 ActionProtocol 实现，由 ActionThread 调用 |

## 5. 配置项

### 5.1 全局配置（settings.yaml → recording 段）

| 字段名 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `recording.enabled` | `bool` | True | 是否启用录制 |
| `recording.buffer_duration` | `float` | 30.0 | 环形缓冲时长（秒） |
| `recording.default_before` | `float` | 15.0 | 告警前截取秒数 |
| `recording.default_after` | `float` | 15.0 | 告警后截取秒数 |
| `recording.output_dir` | `str` | "data/clips" | 视频输出目录 |
| `recording.snapshot_dir` | `str` | "data/snapshots" | 截图输出目录 |
| `recording.video_codec` | `str` | "libx264" | 编码器 |
| `recording.video_crf` | `int` | 23 | CRF 质量 |
| `recording.video_preset` | `str` | "fast" | 编码预设 |
| `recording.max_clip_workers` | `int` | 2 | 并发转码线程数 |
| `recording.retention_days` | `int` | 7 | 视频保留天数 |
| `recording.snapshot_retention_days` | `int` | 30 | 截图保留天数 |
| `recording.max_disk_gb` | `float` | 50.0 | 最大磁盘占用 |

### 5.2 摄像头级配置（cameras/cam_XX.yaml → recording 段）

| 字段名 | 类型 | 说明 |
|--------|------|------|
| `recording.enabled` | `bool` | 该路是否启用录制（覆盖全局） |
| `recording.buffer_duration` | `float` | 该路缓冲时长 |

### 5.3 规则级配置（rules/xxx.yaml → actions 段）

| 字段名 | 类型 | 说明 |
|--------|------|------|
| `actions[].before` | `float` | 该规则触发时的前截取秒数 |
| `actions[].after` | `float` | 该规则触发时的后截取秒数 |

规则级配置优先于全局默认值，不同告警可配置不同的截取时长。

## 6. 错误处理

### 6.1 异常场景与降级策略

| 异常场景 | 处理方式 | 降级方案 |
|---------|---------|---------|
| FFmpeg 不存在 | 启动时检测，不存在则禁用录制 | 日志警告，save_clip 返回空路径 |
| FFmpeg 转码失败（退出码非 0） | 记录 FFmpeg stderr 输出到日志 | 删除不完整文件，save_clip 返回空路径 |
| 磁盘空间不足（写入时） | 捕获 OSError，记录日志 | 跳过保存，触发紧急清理 |
| 缓冲区帧数不足（刚启动时） | 使用可用帧，截取片段比预期短 | 日志警告实际截取时长 |
| 内存不足（缓冲区过大） | 检测内存使用，超过阈值时缩小缓冲 | 减少 buffer_duration 或帧分辨率 |
| cv2.imwrite 失败 | 捕获异常，尝试 PIL 备选 | 两种都失败则返回空路径 |
| 异步转码线程池满 | save_clip 调用排队等待 | 日志记录等待时间 |

### 6.2 文件完整性保护

```
转码过程中的文件保护：
  1. 先写入临时文件：{output_path}.tmp
  2. 转码完成后：os.rename(临时文件, 正式文件)
  3. 如果转码中途失败：临时文件不被 rename，下次 cleanup 时删除

这确保：
  - 磁盘上只有完整的视频文件
  - 不会出现半写入的损坏文件
  - crash 恢复后临时文件可被清理
```

## 7. 设计决策

### 7.1 为什么使用环形缓冲而不是实时录制

- 实时录制每个摄像头每秒写 5 帧到磁盘，10 路就是 50 帧/秒，IO 压力大
- 绝大部分时间没有告警，录制的视频没有价值
- 环形缓冲只在内存中保留最近 30 秒，告警时才转码写磁盘
- 内存换磁盘 IO，10 路约 1.4GB 内存（可接受），省去 95% 的无效磁盘写入

### 7.2 为什么前后各 15 秒

- 前 15 秒：提供事件发生的上下文（怎么开始的）
- 后 15 秒：提供事件发展的后续（怎么结束的）
- 30 秒总时长足够人类判断发生了什么
- 文件大小适中（H.264 压缩后约 2-5MB）

### 7.3 为什么异步转码

- FFmpeg 转码是 CPU 密集操作，同步执行会阻塞主处理管线
- 主管线需要保持实时性，不能因为转码延迟几秒
- 异步线程池（max_workers=2）控制并发数，不占用过多 CPU
- 转码完成通过 callback 通知 pipeline，pipeline 更新 Alert.video_clip_path

### 7.4 为什么使用 H.264 编码

- H.264 兼容性最好，所有浏览器和播放器都支持
- CRF=23 是质量与大小的平衡点（接近视觉无损）
- preset=fast 优先编码速度，适合实时场景
- 如果后期需要更高压缩率，可切换到 H.265（需要浏览器支持）

### 7.5 为什么保留 7 天视频 / 30 天截图

- 视频文件较大（2-5MB/片段），保留太久磁盘占用大
- 7 天足够事后复盘和取证
- 截图文件小（50-200KB/张），可以保留更久用于趋势分析
- 保留天数可通过配置调整，满足不同合规要求

### 7.6 磁盘空间保护

- max_disk_gb 设置硬上限，防止磁盘被写满
- 写满磁盘会导致系统崩溃（日志无法写入、数据库损坏）
- 达到上限后自动从最旧文件开始删除
- 留 20% 余量避免频繁触发清理
