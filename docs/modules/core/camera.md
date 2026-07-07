# camera — 摄像头管理模块 设计书

## 1. 模块职责

通过 FFmpeg 子进程读取 RTSP 视频流，每路摄像头运行在独立线程中，负责帧率控制、断线重连、帧队列管理，输出统一的 numpy BGR 数组供下游推理使用。

## 2. 对外接口

### 2.1 CameraThread 类

| 方法/属性 | 签名 | 说明 |
|-----------|------|------|
| `__init__` | `(camera_config: CameraConfig, frame_queue: FrameQueue, logger: logging.Logger) -> None` | 构造函数，接收摄像头配置、帧队列引用、日志器 |
| `start` | `() -> None` | 启动采集线程，创建 FFmpeg 子进程 |
| `stop` | `() -> None` | 停止采集线程，终止 FFmpeg 子进程，清理资源 |
| `is_alive` | `() -> bool` | 返回线程是否存活 |
| `camera_id` | `str` (属性) | 摄像头唯一标识 |
| `camera_state` | `CameraState` (属性) | 当前摄像头运行状态快照 |
| `status` | `CameraStatus` (属性) | 当前连接状态枚举 |

### 2.2 FrameData 数据类

| 字段 | 类型 | 说明 |
|------|------|------|
| `camera_id` | `str` | 摄像头 ID |
| `frame` | `numpy.ndarray` | BGR 格式图像，shape (H, W, 3) |
| `timestamp` | `float` | 帧采集时间戳（Unix 秒） |
| `frame_seq` | `int` | 帧序号，从 0 开始递增 |
| `frame_id` | `int` | 帧序号别名，等于 frame_seq |
| `width` | `int` | 帧宽度（像素） |
| `height` | `int` | 帧高度（像素） |

### 2.3 CameraConfig 数据类

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `camera_id` | `str` | 必填 | 摄像头唯一 ID |
| `camera_name` | `str` | 必填 | 摄像头显示名称 |
| `rtsp_url` | `str` | `""` | RTSP 流地址（source_type=rtsp 时必填） |
| `source_type` | `str` | `"rtsp"` | 数据源类型：`rtsp` / `video` / `test` |
| `video_path` | `str` | `""` | 本地视频文件路径（source_type=video 时使用） |
| `fps` | `float` | 5.0 | 目标帧率 |
| `width` | `int` | 640 | 输出帧宽度 |
| `height` | `int` | 640 | 输出帧高度 |
| `reconnect_delay` | `float` | 3.0 | 初始重连间隔（秒，仅 rtsp 模式） |
| `reconnect_max_delay` | `float` | 60.0 | 最大重连间隔（秒，仅 rtsp 模式） |
| `reconnect_backoff` | `float` | 2.0 | 退避倍数（仅 rtsp 模式） |
| `ffmpeg_timeout` | `float` | 10.0 | FFmpeg 连接超时（秒，仅 rtsp 模式） |
| `use_gpu_decode` | `bool` | False | 是否启用 GPU 硬解码（仅 rtsp 模式） |

## 3. 内部逻辑

### 3.1 主循环流程

```
线程启动
  |
  v
构造 FFmpeg 命令行
  |
  v
启动 FFmpeg 子进程（subprocess.Popen）
  |
  v
设置状态为 CONNECTING
  |
  v
等待子进程 stdout 可读（超时则判定连接失败）
  |
  v
设置状态为 CONNECTED
  |
  v
+--> 读取原始帧数据（stdout.read，按帧大小计算字节数）
  |     |
  |     v
  |   解码为 numpy 数组（numpy.frombuffer + reshape）
  |     |
  |     v
  |   帧率控制（计算目标间隔，sleep 补足差值）
  |     |
  |     v
  |   构造 FrameData 对象
  |     |
  |     v
  |   放入 FrameQueue（满则丢旧帧）
  |     |
  |     +-- 循环回到读帧
  |
  v（子进程退出或读取失败）
设置状态为 DISCONNECTED
  |
  v
进入重连流程
```

### 3.2 FFmpeg 命令行构造

构造命令将 RTSP 流转为 rawvideo 输出到 stdout pipe：

```
ffmpeg
  -rtsp_transport tcp               # TCP 传输，比 UDP 更稳定
  -stimeout {ffmpeg_timeout_us}     # 连接超时（微秒）
  -i {rtsp_url}                     # 输入源
  -f rawvideo                       # 输出 rawvideo 格式
  -pix_fmt bgr24                    # BGR24 像素格式，与 OpenCV/numpy 兼容
  -vf scale={width}:{height}        # 缩放到目标分辨率
  -an                               # 不输出音频
  -                                 # 输出到 stdout
```

子进程通过 `subprocess.Popen` 启动，stdin 关闭，stdout 设为 pipe，stderr 捕获用于错误日志。

### 3.3 帧率控制

```
目标间隔 = 1.0 / fps（例如 fps=5 → 间隔=0.2秒）

循环内：
  读帧前记录 start_time
  读帧 + 解码
  计算 elapsed = time.monotonic() - start_time
  如果 elapsed < 目标间隔：
      time.sleep(目标间隔 - elapsed)
  如果 elapsed > 目标间隔 * 2：
      记录日志（帧处理过慢，跳帧警告）
```

使用 `time.monotonic()` 而非 `time.time()`，避免系统时间调整导致的跳变。

### 3.4 帧队列管理

FrameQueue 是对 `queue.Queue` 的封装，maxsize 默认 200。

```
放入帧时：
  如果队列已满：
      丢弃最旧帧（调用 queue.get_nowait() 丢弃一个）
      记录日志（frame_dropped，附带 camera_id 和当前队列深度）
  调用 queue.put(FrameData)
```

不使用 `queue.put(block=True)`，避免阻塞摄像头线程。丢帧比延迟更可接受。

### 3.5 断线重连（指数退避）

```
初始重连延迟 = reconnect_delay（默认 3 秒）
最大重连延迟 = reconnect_max_delay（默认 60 秒）
退避倍数 = reconnect_backoff（默认 2.0）

重连流程：
  设置状态为 CONNECTING
  计算当前延迟 = min(当前延迟 * 退避倍数, 最大重连延迟)
  日志记录：下次重连时间
  sleep(当前延迟)
  尝试重新启动 FFmpeg 子进程
  如果成功：
      重置当前延迟为初始值
      设置状态为 CONNECTED
  如果失败：
      循环回到重连流程
```

退避序列为：3s -> 6s -> 12s -> 24s -> 48s -> 60s -> 60s -> ...

如果持续断线超过 5 分钟（由 pipeline 层检测 last_frame_time），由 pipeline 发出"摄像头离线"告警。

### 3.6 帧数据解码

```
每帧原始数据大小 = width * height * 3（BGR24，每像素 3 字节）

读取过程：
  buffer = stdout.read(帧大小)
  如果读到的字节数 < 帧大小：
      判定为流断开，触发重连
  frame = numpy.frombuffer(buffer, dtype=numpy.uint8)
  frame = frame.reshape((height, width, 3))
  构造 FrameData(camera_id, frame, time.time(), frame_seq)
```

### 3.7 线程生命周期

```
__init__():
    保存配置，初始化状态变量，不启动线程

start():
    创建 threading.Thread(target=_run_loop, daemon=True)
    调用 thread.start()

stop():
    设置 _running = False
    调用 ffmpeg_process.terminate()（先优雅终止）
    等待 3 秒
    如果进程仍存活：ffmpeg_process.kill()（强制杀死）
    thread.join(timeout=5)
    日志记录线程已停止

_run_loop():
    while _running:
        try:
            _connect_and_read_frames()
        except Exception as e:
            日志记录异常
            _reconnect()
```

线程设为 daemon 模式，主线程退出时自动终止（作为安全兜底，正常流程应通过 stop() 优雅关闭）。

## 4. 依赖关系

### 4.1 依赖的模块

| 模块 | 用途 |
|------|------|
| `core/types` | 使用 CameraState、CameraStatus 数据模型 |
| `config` | 读取摄像头配置（CameraConfig） |
| `logging`（标准库） | 结构化日志 |
| `subprocess`（标准库） | 启动 FFmpeg 子进程 |
| `threading`（标准库） | 线程管理 |
| `numpy` | 帧数据解码为数组 |
| `queue`（标准库） | 有界帧队列 |

### 4.2 被依赖的模块

| 模块 | 依赖方式 |
|------|---------|
| `core/pipeline` | 创建 CameraThread 实例，管理生命周期，读取 camera_state |
| `core/recorder` | 从 FrameQueue 获取帧用于环形缓冲 |

## 5. 配置项

### 5.1 全局配置（settings.yaml → camera 段）

| 字段名 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `camera.frame_queue_size` | `int` | 200 | 帧队列最大容量 |
| `camera.default_fps` | `float` | 5.0 | 默认帧率（可被单路覆盖） |
| `camera.default_width` | `int` | 640 | 默认输出宽度 |
| `camera.default_height` | `int` | 640 | 默认输出高度 |
| `camera.ffmpeg_timeout` | `float` | 10.0 | FFmpeg 连接超时秒数 |

### 5.2 单路摄像头配置（cameras/cam_XX.yaml）

| 字段名 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `camera.camera_id` | `str` | 必填 | 摄像头唯一 ID |
| `camera.camera_name` | `str` | 必填 | 摄像头显示名称 |
| `camera.source_type` | `str` | `"rtsp"` | 数据源类型：rtsp / video / test |
| `camera.rtsp_url` | `str` | `""` | RTSP 流地址（source_type=rtsp 时必填） |
| `camera.video_path` | `str` | `""` | 视频文件路径（source_type=video 时必填） |
| `camera.fps` | `float` | 继承全局 | 该路目标帧率 |
| `camera.width` | `int` | 继承全局 | 该路输出宽度 |
| `camera.height` | `int` | 继承全局 | 该路输出高度 |
| `camera.reconnect_delay` | `float` | 3.0 | 初始重连间隔（仅 rtsp 模式） |
| `camera.reconnect_max_delay` | `float` | 60.0 | 最大重连间隔（仅 rtsp 模式） |
| `camera.use_gpu_decode` | `bool` | False | 是否 GPU 硬解码（仅 rtsp 模式） |

## 6. 错误处理

### 6.1 异常场景与降级策略

| 异常场景 | 处理方式 | 是否影响其他路 |
|---------|---------|--------------|
| RTSP 连接超时 | 标记 CONNECTING，指数退避重连 | 不影响 |
| FFmpeg 进程异常退出 | 自动重启进程，连续 5 次失败暂停 10 分钟再试 | 不影响 |
| FFmpeg stdout 读取字节数不足 | 判定流断开，触发重连 | 不影响 |
| 帧解码失败（numpy reshape 报错） | 跳过该帧，记录日志，继续读下一帧 | 不影响 |
| 帧队列满 | 丢弃最旧帧，记录 frame_dropped 日志 | 不影响 |
| 摄像头配置格式错误 | 启动时校验失败，该路不启动，日志报错 | 不影响 |
| FFmpeg 命令不存在 | 启动时检测 ffmpeg 路径，不存在则启动失败并报错 | 所有路均受影响 |

### 6.2 FFmpeg 子进程管理

- 使用 `subprocess.Popen` 的 `close_fds=True` 防止文件描述符泄漏
- stderr 输出通过独立线程异步读取，防止 stderr 缓冲区满导致子进程阻塞
- 每次重连前确保旧进程已完全终止（terminate + wait + kill 三步）
- 子进程退出码记录到日志，便于排查 FFmpeg 错误原因

### 6.3 资源清理

- stop() 调用时确保：FFmpeg 子进程终止、stdout pipe 关闭、线程 join 完成
- 异常退出时在 finally 块中清理残留资源
- 日志记录每次资源清理的详情

## 7. 设计决策

### 7.1 为什么用 FFmpeg 子进程而不是 OpenCV VideoCapture

FFmpeg 子进程方案比 OpenCV 更稳定：
- OpenCV 的 RTSP 底层仍依赖 FFmpeg，但封装层增加了不稳定性
- FFmpeg 子进程崩溃不会影响主进程，可独立重启
- FFmpeg 支持更多协议选项和网络参数调优（如 TCP 传输、超时设置）
- rawvideo pipe 输出零拷贝到 numpy，性能不比 OpenCV 差

### 7.2 为什么每路独立线程

- 每路线程独立生命周期，camera_01 断线不影响 camera_02
- FFmpeg 子进程的 stdout read 是阻塞操作，放在独立线程中不会阻塞其他路
- 实现简单，Python GIL 不影响此场景（IO 密集，非 CPU 密集）

### 7.3 为什么有界队列满时丢旧帧

- 阻塞会导致延迟累积：下游推理慢时，摄像头帧不断进来，队列只会越来越长
- 监控场景下丢几帧不影响告警准确性，但延迟几秒可能导致告警无意义
- 丢旧帧保证系统延迟稳定在可控范围内

### 7.4 为什么指数退避而不是固定间隔重连

- RTSP 服务器重启或网络中断通常需要几秒到几分钟恢复
- 短间隔反复重连会给服务器造成压力
- 指数退避在前期快速尝试、后期减少无意义重试，平衡恢复速度和资源消耗

### 7.5 为什么默认输出 640x640

- YOLO 模型输入通常是正方形，640 是常用尺寸
- 在 FFmpeg 层预缩放，避免在推理层再做 resize（减少 GPU 前处理时间）
- 640x640 BGR24 每帧约 1.2MB，10 路 5fps 约 60MB/s，内存可接受
- 实际分辨率可根据摄像头和模型调整（如 1280x1280 用于高精度场景）

### 7.6 GPU 硬解码的取舍

- 默认关闭，因为 GPU 解码增加 CUDA 显存占用，与推理争抢 GPU 资源
- 路数少（4 路以下）时 CPU 解码足够，GPU 解码收益不明显
- 路数多（10 路以上）时可开启，需验证显存是否足够
- 通过配置项控制，不改变接口

## 8. 数据源模式

### 8.1 source_type 配置

摄像头支持三种数据源模式，通过 `source_type` 字段配置：

| source_type | 说明 | 数据来源 | 使用场景 |
|-------------|------|---------|---------|
| `rtsp`（默认） | RTSP 视频流 | FFmpeg 读取 RTSP，输出原始帧 | 生产环境，真实摄像头 |
| `video` | 本地视频文件 | OpenCV 读取视频文件，循环播放 | 开发测试，用录好的视频 |
| `test` | 测试图案 | 自动生成渐变背景 + 移动方块 | 开发调试，不需要任何外部文件 |

**配置示例**：

```yaml
# RTSP 模式（生产）
camera:
  id: cam_01
  name: 仓库入口
  source_type: rtsp
  rtsp_url: "rtsp://admin:pass@192.168.1.100/stream"

# 视频文件模式（开发测试）
camera:
  id: cam_01
  name: 测试摄像头
  source_type: video
  video_path: "data/test_05.mp4"

# 测试图案模式（开发调试）
camera:
  id: cam_01
  name: 测试摄像头
  source_type: test
```

### 8.2 各模式行为

| 模式 | 重连 | 帧率控制 | 循环播放 | 外部依赖 |
|------|------|---------|---------|---------|
| rtsp | ✅ 指数退避重连 | ✅ | ❌（断线重连） | FFmpeg |
| video | ❌（播放完循环） | ✅ | ✅ | OpenCV |
| test | ❌（持续生成） | ✅ | N/A | 无 |

### 8.3 边缘设备扩展（预留）

后期扩展边缘设备时，增加 `source_type=edge`：

| source_type | 说明 | 数据来源 |
|-------------|------|---------|
| edge | 边缘设备上报 | HTTP/MQTT 接收边缘设备的检测结果 |

当 source_type=edge 时，CameraThread 不再启动 FFmpeg 进程，而是启动一个 HTTP/MQTT 监听服务，接收边缘设备推送的检测结果。

### 8.4 设计约束

- CameraThread 的输出接口不变（帧 + 元数据），source_type 只影响数据获取方式
- video/test 模式不需要重连逻辑
- 校验逻辑按 source_type 区分：rtsp 必填 rtsp_url，video 必填 video_path
- 边缘设备断线时，降级为 disconnected 状态，与本地摄像头断线行为一致
- 不在第一版实现，但接口设计时预留 source_type 字段
