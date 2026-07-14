# SentinelMind v2 关键问题与经验

> 记录 v2 开发期间遇到的重要问题、根因和解决方案，避免重复踩坑。

---

## 一、架构问题

### 1.1 帧框不同步 — 检测框画在错误的帧上

**现象**：监控画面上的标注框跟人体位置明显偏移。

**根因**：视频推流和检测走两条独立的线程路径。

```
旧架构：
  CameraThread → subscribe_frames → WebSocket（拿到最新帧 #500）
  CameraThread → FrameQueue → InferenceThread（2秒后才出结果，帧 #300 的检测）
  WebSocket 拿 #300 的检测结果画在 #500 的帧上 → 不匹配
```

CPU 推理慢时队列积压 200 帧，检测结果比当前帧晚十几秒。

**解决方案**：将画框逻辑从 WebSocket 阶段移到 InferenceThread 内部。推理完成后的同一帧上直接 `cv2.rectangle` + `cv2.putText` + `cv2.imencode` → 缓存为 JPEG。WebSocket 从缓存取预画好的帧。

```
新架构：
  InferenceThread 推理完成 → 同一帧上画框 → 编码 JPEG → _last_frame_jpeg
  WebSocket 轮询 get_last_frame_jpeg() → frame_id 去重 → 发送
```

**相关文档**：[detection_overlay_plan.md](..docs/modules/core/detection_overlay_plan.md)

---

### 1.2 视频推流改架构后摄像头停止产帧

**现象**：重构视频推流后，三路摄像头全部 `fps=0.0`，监控黑屏。

**根因**：CameraThread 有 `_has_subscribers()` 检查——无 WebSocket 订阅者时休眠节资源。旧 WS 代码调用了 `cam_thread.subscribe_frames()` 保持订阅。新 WS 只轮询 `get_last_frame_jpeg()`，未订阅 → `_has_subscribers()` 永远 false → 摄像头进入休眠 → FrameQueue 空 → 推理无事可做 → 无画面。

**解决方案**：WS 中保留 `subscribe_frames(maxsize=5)`，不读队列但维持订阅，让摄像头不休眠。

---

### 1.3 配置拆分后摄像头未加载

**现象**：重构为 `cameras.yaml` + `rules.yaml` 后，前端摄像头列表为空。

**根因**：`assemble_components()` 仍从已删除的 `configs/cameras/` 目录加载摄像头。ConfigManager 已正确加载 `cameras.yaml` 到全局配置字典，但 pipeline 初始化时未读取。

**解决方案**：改为从 ConfigManager 合并后的 `config["cameras"]` 字典读取，支持新旧两种格式。

---

### 1.4 get_last_jpeg 返回值类型不匹配

**现象**：WebSocket 循环崩溃，日志 `too many values to unpack (expected 2)`。

**根因**：`get_last_jpeg` 返回 `entry[0]`（纯 bytes），但 WS 代码期望 `(jpeg_bytes, frame_id)` 元组。一个 bytes 对象有几千个元素，解包 `a, b = entry` 失败。

**解决方案**：统一 `_last_frame_jpeg` 存储为 `(bytes, frame_id)` 元组，`get_last_jpeg` 返回完整元组，WS 正确解包并基于 `frame_id` 去重。

---

### 1.5 hot_update 热加载线程从未启动

**现象**：热加载配置编写完成后，修改 YAML 文件无效。

**根因**：`config_mgr.start_hot_reload()` 在 `main()` 中从未被调用。watcher 回调已注册，但监控线程未启动，整个文件扫描逻辑是不可达代码。

**解决方案**：在 `pipeline.start()` 之后添加 `config_mgr.start_hot_reload()`。

---

### 1.6 摄像头配置热加载只支持 enabled 字段

**现象**：修改 `cameras.yaml` 的 `video_path`、`rtsp_url` 等字段后，热加载不生效。

**根因**：`_reload_cameras_yaml` 更新了内存配置但未通知 pipeline 重建 CameraThread。`__main__.py` 的 watcher 只处理 `global_updated`，未处理 `camera_added`/`camera_updated`/`camera_removed`。配置变更用了 `remove_camera + add_camera` 而非 `reload_camera`。

**解决方案**：watcher 回调新增 `camera_added`/`camera_updated`/`camera_removed` 分支，抽取 `_build_cam_cfg` 复用，用 `reload_camera` 替换线程。

---

## 二、配置问题

### 2.1 system.yaml 键名与代码不一致

**现象**：system.yaml 写的 `log_file`、`log_max_size`、`cleanup_interval` 从未生效。

**根因**：代码读 `log_dir` 和 `log_max_size_mb`，配置写的 `log_file` 和 `log_max_size`。键名不对 → `dict.get()` 用默认值 → 配置白写。

**修复**：统一为 `log_dir`/`log_max_size_mb`，删掉代码未读的 `cleanup_interval`。

### 2.2 cameras.yaml 旧格式兼容

重构后新增 `{camera: {...}, detector: {...}}` 分节格式，同时兼容旧格式（整节当 camera）。`_load_camera_configs` 和 `_reload_cameras_yaml` 需保持一致的解析逻辑，否则热重载时会损坏内部结构。

### 2.3 `iou` vs `iou_threshold` 键名不一致

全局默认值 `_DEFAULTS["detector"]` 使用 `iou`，YAML 使用 `iou_threshold`。合并后两个键共存，读取 `iou` 的代码取到默认值，per-camera 覆盖失效。统一为 `iou_threshold`。

---

## 三、性能问题

### 3.1 CPU 推理瓶颈暴露

**现象**：帧框同步后 FPS 降到 1-2。

**根因**：这不是 Bug。旧架构原始帧 25fps 推流 + 延时画框掩盖了推理慢的问题。新架构帧框同步了，FPS = 推理速度。CPU 跑 helmet.pt（39MB）→ 1-2.5秒/帧。

**影响**：正常。换 GPU 后单路可达 25 FPS（推理 ~10ms/帧）。

---

## 四、CI/CD 问题

### 4.1 test_detection_overlay.py 在 CI 全红

**现象**：本地 231 passed，CI 7 failed。

**根因**：CI Linux 镜像未装 `opencv-python-headless`，`import cv2` 失败 → 4 个画框测试返回空 bytes + 3 个 HSV 测试 ModuleNotFoundError。

**修复**：文件顶部加 `pytest.importorskip("cv2")`——CI 无 cv2 时这 7 个测试 skip，本地有 cv2 照常跑。

### 4.2 ruff lint 错误本地未发现

**现象**：本地 pytest 全过但 CI ruff check 报 F821（未导入 Any）和 F841（未用变量）。

**根因**：三 Agent 流程中 CodeWriter 只跑 `pytest`，未跑 `ruff check`。`from __future__ import annotations` 让注解变成字符串，运行时不会炸，lint 才发现。

**改进**：三 Agent 的 TestAgent 应增加一步 `ruff check src/ tests/`。

---

## 五、三 Agent 工作流实践

### 5.1 工作流效果

v2 期间共执行 6 轮完整三 Agent 流程：

| 模块 | CodeWriter | CodeReviewer 问题数 | TestWriter 测试数 |
|------|-----------|-------------------|------------------|
| 规则管理 | 8 文件 | 17 条（5H+6M+6L） | 53 |
| 用户管理 v2 | 8 文件 | 11+2 遗漏 | 25 |
| 个人设置 v2 | 4 文件 | 3H+3M+2L | 10 |
| 摄像头内嵌 detector | 5 文件 | 3H+5M+3L | 7 |
| 热加载增强 | 3 文件 | 2H+4M+1L | 6 |
| 检测框画帧 | 2 文件 | 3H+5M+3L | 7 |

**平均每轮**：代理发现 ~9 个问题，编写 ~18 个测试。
**总产出**：231 个测试，人均 3-4 轮迭代修复到全部通过。

### 5.2 暴露的不足

1. **CodeReviewer 不查 lint 级别问题**（F821/F841），需人工补 ruff
2. **CodeWriter 不了解 CI 环境差异**（cv2 缺失），需 TestAgent 加强环境兼容测试
3. **CodeWriter 容易漏掉全局影响**（配置拆分后 `assemble_components` 未更新、视频 WS 改架构未处理订阅）

---

## 六、经验总结

| 原则 | 说明 |
|------|------|
| 帧框不同步是架构问题不是优化问题 | 不能通过调参解决，必须改数据流 |
| 配置键名不一致 = 配置白写 | YAML 键名和代码 `dict.get()` 必须对上，建议写校验 |
| 热加载代码路径需端到端验证 | 写完 handler + watcher + 线程启动，缺一环就全废 |
| 本地测试 ≠ CI 测试 | 依赖 cv2 的测试需 importorskip，ruff 需在本地和 CI 各跑一次 |
| 向后兼容是配置重构最危险的部分 | 新旧格式解析逻辑必须在所有路径（初始加载+热重载）保持一致 |
| 三 Agent 流程有效但需补 lint 步 | CodeWriter→CodeReviewer→TestAgent 后加 RuffCheck |