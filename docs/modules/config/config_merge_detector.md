# Config 合并方案：摄像头内嵌 detector

> 版本: v1 | 日期: 2026-07-14

## 一、目标

每路摄像头可内嵌自己的 `detector` 参数，不写则走全局默认。

```yaml
cameras:
  cam_01:
    camera:
      id: cam_01
      name: 正门
      source_type: rtsp
      rtsp_url: rtsp://...
      resolution: [1920, 1080]
      fps: 25
    detector:                          # ← 可选，覆盖全局默认
      model_path: models/helmet.pt
      confidence: 0.5

  cam_02:
    camera:
      id: cam_02
      name: 仓库
      source_type: rtsp
      rtsp_url: rtsp://...
      resolution: [1280, 720]
      fps: 15
      # 不写 detector → 用全局默认

detector:                              # ← 全局默认
  model_path: models/yolov8n.pt
  confidence: 0.5
  iou_threshold: 0.45
  classes: null
  input_size: 640
```

## 二、改动范围

### 2.1 Config 加载层

| 文件 | 改动 |
|---|---|
| `config/settings.py` | `_load_camera_configs()` 解析摄像头下 `camera` + `detector` 分节 |
| | `get_camera()` 返回合并后的完整配置（全局 detector + 摄像头 override） |

### 2.2 API 层

| 文件 | 改动 |
|---|---|
| `web/api/app.py` | `_save_camera_yaml()` 写 camera + detector 分节；create/update 请求体支持 detector 字段 |

### 2.3 Pipeline 层（暂不改，只读配置）

| 文件 | 改动 |
|---|---|
| `core/pipeline.py` | `add_camera()` 读取当前摄像头的 detector 配置 |
| `core/camera.py` | `CameraConfig` 新增 `detector_overrides: dict` |
| `core/detector.py` | 预留 `get_model_for_camera(camera_id)` 接口（空实现） |

### 2.4 前端

| 文件 | 改动 |
|---|---|
| `api/cameras.ts` | `CreateCameraPayload` 新增 `detector` 可选字段 |
| `views/Cameras.vue` | 添加/编辑弹窗可选填 detector 参数 |
| `stores/cameras.ts` | 透传 |

### 2.5 YAML 文件

`cameras.yaml` 格式改为新结构。兼容旧格式：如果摄像头下没有 `camera` 分节，整节当 `camera` 处理。

## 三、ConfigManager 合并逻辑

```python
def get_camera(self, camera_id: str) -> dict[str, Any]:
    """返回完整摄像头配置（全局 detector + 摄像头 detector 覆盖）"""
    cam_config = self._camera_configs.get(camera_id, {})
    raw = cam_config.get("camera", cam_config)  # 兼容旧格式

    # detector: 全局默认 + 摄像头覆盖
    global_detector = self.get("detector")
    cam_detector = cam_config.get("detector", {})
    merged_detector = deep_merge(global_detector, cam_detector) if cam_detector else global_detector

    result = dict(raw)
    result["detector"] = merged_detector
    return result
```

## 四、兼容策略

- 已有 `cameras.yaml` 如没有 `camera` / `detector` 分节 → 整节当 `camera`，走全局 detector
- API 新增 `detector` 字段 → 可选，不传不影响
- Pipeline 暂不改 → 全局 detector 不变，摄像头级 detector 存着备用

## 五、改动文件清单

| 文件 | 操作 |
|---|---|
| configs/cameras.yaml | 改为 `camera` + `detector` 分节 |
| config/settings.py | `get_camera()` 合并 detector |
| web/api/app.py | `_save_camera_yaml()` 适配新格式 |
| frontend/src/api/cameras.ts | `CreateCameraPayload` 加 detector |
| docs/modules/config/config_merge_detector.md | 新增（本文档） |
