# 配置文件拆分重构

> 版本: v2 | 日期: 2026-07-13

## 一、现状问题

`configs/settings.yaml` 是一个大杂烩：detector + tracker + llm + web + notification + gpu + recording + system + storage + redis + rag + rules 全塞在一个文件里，200+ 行。改个模型路径要翻到 detector 段，改个端口要翻到 web 段。

## 二、参考设计（smart_video_analytics）

| 做法 | 效果 |
|---|---|
| 按职责分文件 | `model_config.yaml` 只管模型，`camera_config.yaml` 只管摄像头 |
| 同类型信息聚合 | 每个摄像头一个 YAML 块，内含 rtsp + model + rules + params |
| dataclass 强类型 | ConfigManager 加载后转为 `ModelConfig` / `CameraConfig` 等 dataclass |

## 三、拆分方案

```
configs/
├── system.yaml           # 保持
├── detector.yaml          # 从 settings 拆出
├── tracker.yaml           # 从 settings 拆出
├── gpu.yaml               # 从 settings 拆出
├── llm.yaml               # 从 settings 拆出
├── notification.yaml      # 从 settings 拆出
├── server.yaml            # 从 settings 拆出
├── storage.yaml           # 从 settings 拆出
├── recording.yaml         # 从 settings 拆出
│
├── settings.yaml          # 保留旧格式作为兼容入口（内部 merge 各文件）
│
├── cameras/               # 不变
│   └── *.yaml
├── rules/                 # 不变
│   └── *.yaml
└── botsort.yaml           # 不变
```

**settings.yaml 不删——改为"入口文件"，内部从各子文件合并。** 这样旧的 `--config configs/settings.yaml` 启动方式不破坏，新项目也不用改 ConfigManager 调用方。

## 四、各文件内容

### system.yaml
```yaml
version: 1
system:
  name: "Vision Agent"
  log_level: INFO
  log_file: logs/vision_agent.log
  log_max_size: 50MB
  log_backup_count: 5
  data_dir: data
  cleanup_interval: 3600
```

### detector.yaml
```yaml
detector:
  model_path: models/helmet.pt
  confidence: 0.5
  iou_threshold: 0.45
  classes: null
  input_size: 640
```

### gpu.yaml
```yaml
gpu:
  device: 0
  batch_size: 8
  fp16: true
  tensorrt: false
```

### tracker.yaml
```yaml
tracker:
  type: botsort
  config: configs/botsort.yaml
```

### llm.yaml
```yaml
llm:
  enabled: true
  provider: openai
  base_url: https://dashscope.aliyuncs.com/compatible-mode/v1
  api_key: ${LLM_API_KEY}
  model: qwen-plus
  max_tokens: 1000
  timeout: 30
  max_retries: 2
  monthly_budget_usd: 50
```

### notification.yaml
```yaml
notification:
  webhook:
    enabled: true
    url: ${WEBHOOK_URL}
    timeout: 10
  email:
    enabled: false
    smtp_host: smtp.example.com
    smtp_port: 465
    username: ${EMAIL_USER}
    password: ${EMAIL_PASS}
    to: ["admin@example.com"]
```

### server.yaml
```yaml
web:
  enabled: true
  host: 0.0.0.0
  port: 8080
  api_token: ""
  cors_origins:
    - "http://localhost:3000"
```

### storage.yaml
```yaml
storage:
  type: sqlite
  path: data/vision_agent.db

redis:
  enabled: false
  host: localhost
  port: 6379
  db: 0
```

### recording.yaml
```yaml
recording:
  buffer_duration: 30
  output_dir: data/clips
  snapshot_dir: data/snapshots
  retention_days: 7
  max_disk_gb: 100
```

### settings.yaml（精简后）
```yaml
# Vision Agent 配置入口
# 各模块配置从以下文件加载：
#   system.yaml / detector.yaml / gpu.yaml / tracker.yaml
#   llm.yaml / notification.yaml / server.yaml / storage.yaml / recording.yaml
version: 1
```

## 五、ConfigManager 改动

`settings.py` 的 `load()` 方法新增自动合并逻辑：

```python
def load(self, config_path: str):
    base_dir = Path(config_path).parent

    # 尝试从拆分的文件加载
    merged = {}
    for name in ("system", "detector", "gpu", "tracker", "llm",
                 "notification", "server", "storage", "recording"):
        file = base_dir / f"{name}.yaml"
        if file.exists():
            with open(file) as f:
                merged.update(yaml.safe_load(f) or {})

    # 也加载主文件（向后兼容：如果主文件里有值，覆盖子文件）
    with open(config_path) as f:
        master = yaml.safe_load(f) or {}
    merged.update(master)

    # 去掉 version 字段
    merged.pop("version", None)
    ...
```

保持向下兼容——`--config configs/settings.yaml` 依然可以用，内部自动从子文件合并。

## 六、改动文件清单

| 文件 | 操作 |
|---|---|
| configs/system.yaml | 新增 |
| configs/detector.yaml | 新增（内容从 settings 拆出） |
| configs/gpu.yaml | 新增 |
| configs/llm.yaml | 新增 |
| configs/notification.yaml | 新增 |
| configs/server.yaml | 新增 |
| configs/storage.yaml | 新增 |
| configs/recording.yaml | 新增 |
| configs/settings.yaml | 精简为入口 |
| configs/settings.yaml.example | 同步精简 |
| src/vision_agent/config/settings.py | ConfigManager.load() 支持拆分文件合并 |
| docs/modules/config/config.md | 更新文档 |