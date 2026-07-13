# 检测标注框画帧方案

> 版本: v1 | 日期: 2026-07-13

## 一、现状问题

视频推流和检测走两条独立的线程路径，导致检测结果（detections）和视频帧不同步：

```
CameraThread 采帧 → FrameQueue(容量 200) → InferenceThread(CPU 慢，追不上)
     │                                              ↓
     │                                           detections 滞后 200 帧
     │
     └──→ WebSocket 视频推流 ← get_latest_detections()
           帧号 500                          帧号 300 的检测结果
                                              框画在不对的帧上
```

## 二、修复方案

**将画框逻辑移到 InferenceThread 内部，在推理完成后的同一帧上直接画好，缓存为 JPEG。视频推流从缓存取预画好的带框帧，不再读裸帧。**

### 2.1 InferenceThread 改动

```python
# core/pipeline.py — InferenceThread

class InferenceThread:
    def __init__(self, ...):
        ...
        self._last_frame_jpeg: dict[str, bytes] = {}    # camera_id → JPEG bytes（已画框）
        self._detector_class_names: list[str] = []       # 模型类别名（画框用）

    def _loop(self):
        while self._running:
            batch = self._frame_queue.get_batch()
            ...
            detections_list = self._detector.detect(frames)

            for bf, detections in zip(batch, detections_list):
                tracks = self._tracker_manager.update(...)
                result = InferenceResult(...)
                self._result_queue.put(result)
                self._latest_detections[bf.camera_id] = detections

                # ★ 新增：在当前帧画框 + 编码 JPEG 缓存
                if detections:
                    annotated = self._draw_detections(bf.frame, detections)
                else:
                    annotated = bf.frame
                jpeg_bytes = self._encode_frame(annotated)
                self._last_frame_jpeg[bf.camera_id] = jpeg_bytes

    def _draw_detections(self, frame, detections) -> np.ndarray:
        """在帧上画检测框，返回新帧（不修改原帧）"""
        import cv2
        result = frame.copy()
        colors = {}
        for i, name in enumerate(self._detector_class_names):
            h = (i * 47) % 180
            colors[name] = cv2.cvtColor(np.uint8([[[h, 255, 255]]]),
                                         cv2.COLOR_HSV2BGR)[0][0].tolist()
        for d in detections:
            b = d.bbox
            label = f"{d.class_name} {d.confidence:.2f}"
            color = colors.get(d.class_name, (0, 255, 255))
            cv2.rectangle(result, (int(b.x1), int(b.y1)), (int(b.x2), int(b.y2)), color, 2)
            cv2.putText(result, label, (int(b.x1), int(b.y1) - 5),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)
        return result

    def _encode_frame(self, frame) -> bytes:
        """编码为 JPEG 字节"""
        import cv2
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
        return buf.tobytes() if ok else b""

    def get_last_jpeg(self, camera_id: str) -> bytes | None:
        """获取指定摄像头最新带框帧的 JPEG 字节"""
        return self._last_frame_jpeg.get(camera_id)
```

### 2.2 VisionAgent 改动

```python
# core/pipeline.py — VisionAgent

def get_last_frame_jpeg(self, camera_id: str) -> bytes | None:
    """返回指定摄像头最新带框帧的 JPEG 字节（供视频推流）"""
    inf = self._inference_thread
    return inf.get_last_jpeg(camera_id) if inf and inf.is_alive() else None
```

### 2.3 视频 WebSocket 改动

```python
# web/api/app.py — video_stream 函数

# 原来：
# 从 CameraThread.subscribe_frames() 取裸帧 → encode → 发送

# 改为：
# 轮询 pipeline.get_last_frame_jpeg(camera_id) → 直接发送（已画好框）

frame_queue = cam_thread.subscribe_frames(maxsize=30)
# ↑ 保留：用于检测 Pipeline 是否活着，但不再读帧

while True:
    jpeg_bytes = pipeline.get_last_frame_jpeg(camera_id)
    if jpeg_bytes:
        header = struct.pack(">II", ...)
        await ws.send_bytes(header + jpeg_bytes)
    else:
        # 还没出检测结果，发心跳
        await ws.send_json({"type": "ping"})
    await asyncio.sleep(0.033)  # ~30fps
```

### 2.4 影响分析

| 改动 | 影响 |
|---|---|
| InferenceThread 每个批结束后多一次 `cv2.rectangle` + `cv2.imencode` | 微增延迟（<5ms），远小于推理本身 |
| WebSocket 不再订阅 CameraThread 帧 | 推流帧率取决于推理速度，不再是摄像头采集速度 |
| 无检测结果时（启动初期/暗帧），推流暂停 | 前端显示"等待检测结果"或保持上一帧 |
| CPU 推理慢时推流也慢 | 接受——框帧必须同步，等不及就不推 |

## 三、修改文件清单

| 文件 | 操作 |
|---|---|
| core/pipeline.py — InferenceThread | 新增 `_draw_detections` + `_encode_frame` + `_last_frame_jpeg` + `get_last_jpeg` |
| core/pipeline.py — VisionAgent | 新增 `get_last_frame_jpeg` |
| web/api/app.py — video_stream | 改为轮询 `get_last_frame_jpeg`，不读裸帧 queue |
| docs/modules/core/detection_overlay_plan.md | 新增（本文档） |
