# YOLO 模型训练指南

> 版本: v1 | 日期: 2026-07-09 | 关联模块: core/detector.py

## 一、训练目标

针对 Vision Agent 项目的三种内置规则，训练一个安全帽 + 人员检测模型：

| 类别 ID | 类别名称 | 用途 |
|---|---|---|
| 0 | person | 区域闯入 / 计数线穿越 / 区域清空的检测目标 |
| 1 | hard-hat | 安全帽佩戴检测 |
| 2 | no-hard-hat | 未佩戴安全帽检测 |

模型输入分辨率：640×640，格式：YOLOv8n（轻量，适合 CPU 推理和边缘部署）。

## 二、环境搭建

### 2.1 Python 环境

```bash
# 创建虚拟环境
python -m venv venv_yolo
source venv_yolo/bin/activate  # Linux/Mac
# venv_yolo\Scripts\activate   # Windows

# 核心依赖
pip install ultralytics>=8.1
pip install torch>=2.0 torchvision

# 标注工具依赖
pip install opencv-python>=4.8 pillow>=10.0
```

### 2.2 标注工具

| 工具 | 安装方式 | 适用场景 | 推荐度 |
|---|---|---|---|
| **LabelImg** | `pip install labelimg` | 快速标注 PascalVOC/YOLO，单文件轻量 | ⭐⭐⭐ |
| **X-AnyLabeling** | `pip install x-anylabeling` | 支持 YOLOv5/v8/v11/OBB/SAM 辅助，自动检测辅助标注，多格式互转 | ⭐⭐⭐⭐⭐ |
| **Label Studio** | `pip install label-studio` | 团队协作、多格式导出、Web 界面 | ⭐⭐⭐ |
| **CVAT** | Docker 部署 | 视频标注、专业级 | ⭐⭐ |
| **Roboflow** | 在线平台 | 免安装、自动预处理 | ⭐⭐⭐ |

#### LabelImg（推荐单人快速标注）

```bash
pip install labelimg

# 启动
labelImg

# 操作：
# 1. Open Dir → 选择图片文件夹
# 2. Change Save Dir → 选择标注保存目录
# 3. 左侧切换为 YOLO 格式（View → Auto Save mode → PascalVOC 改为 YOLO）
# 4. 快捷键：W 创建标注框，Ctrl+S 保存，D 下一张，A 上一张
```

输出格式：每张图片对应一个同名 `.txt` 文件：
```
class_id x_center y_center width height
0 0.523 0.418 0.152 0.286   # person
1 0.487 0.352 0.098 0.184   # hard-hat
```

#### X-AnyLabeling（推荐，功能最强）

```bash
pip install x-anylabeling

# 启动
x-anylabeling
```

优势：
- 内置 YOLOv5/v8/v11 等多种格式，无需手动切换
- 支持 **SAM / SAM2 / EdgeSAM** 等大模型辅助标注：点一下目标自动生成分割框，再导出为检测框
- 支持多边形（Polygon）、矩形（BBox）、关键点（Keypoint）等多种标注类型
- 内建 GPU 加速推理标注管道
- 中文界面，上手更快

操作流程：
```
1. 打开图片文件夹 →
2. 加载 SAM 辅助模型（AI Model → Segment Anything → vit_b） →
3. 点一下目标中心 → 自动识别边框 →
4. 选择标签类别 → 按空格确认 →
5. 导出 → YOLO 格式
```

> **SAM 辅助标注效率**：传统人工标 1000 张图约需 4-6 小时，SAM 辅助后约需 1-2 小时，节省 60%+ 时间。

> **注意**：首次加载 SAM 模型需下载权重文件（~375MB），需要良好网络环境。

#### Label Studio（推荐多人/多格式导出）

```bash
pip install label-studio

# 启动
label-studio start

# 1. 浏览器打开 http://localhost:8080
# 2. 创建项目 → 导入图片
# 3. 设置标签：person(0), hard-hat(1), no-hard-hat(2)
# 4. 标注完成后 → Export → YOLO 格式导出
# 5. 导出产物：labels/ 目录 + classes.txt
```

标注类型选择 **"目标检测（Bounding Box）"**，导出格式选择 **YOLO**。

#### Roboflow（在线，免安装）

```
1. 注册 https://roboflow.com
2. 上传图片 → 在线标注 → Export Dataset
3. 选择 YOLOv8 格式下载
4. 自动生成 dataset.yaml + train/val/test 拆分
```

### 2.3 标注目录结构

无论用哪个工具，最终统一为：

```
data/datasets/helmet/
├── raw/                    # 原始图片（标注前）
│   ├── img_0001.jpg
│   ├── img_0002.jpg
│   └── ...
├── images/
│   ├── train/              # 训练集 80%
│   └── val/                # 验证集 20%
├── labels/
│   ├── train/              # 与 images/train/ 一一对应
│   │   ├── img_0001.txt
│   │   └── ...
│   └── val/
└── dataset.yaml
```

### 2.4 标注规范

| 规则 | 说明 |
|---|---|
| 框要紧贴目标边缘 | 不要留白太多，也不要切掉目标 |
| 遮挡目标也要标 | 被部分遮挡的人/帽子，框选出可见部分即可 |
| 极小目标可跳过 | 小于 10×10 像素的目标标注意义不大 |
| 密集目标逐个标 | 不要一个框框一群人 |
| 每类至少 200 个框 | person 容易达标，hard-hat 和 no-hard-hat 需注意平衡 |
| 模糊/过曝图片要剔除 | 扔进 val 集测鲁棒性，不要放进训练集 |

## 三、数据集

使用公开数据集 **Safety Helmet Wearing Dataset (SHWD)**，无需公司资源。

| 项目 | 说明 |
|---|---|
| 来源 | [Kaggle SHWD](https://www.kaggle.com/datasets/andrewmvd/hard-hat-detection) 或 [GitHub SHWD](https://github.com/njvisionpower/Safety-Helmet-Wearing-Dataset) |
| 图片数 | ~7,500 张（含标注） |
| 标注格式 | Pascal VOC XML → 转为 YOLO txt 格式 |
| 类别映射 | person → 0, head → 1, helmet → 2 |

### 数据准备步骤

```bash
# 1. 下载数据集
kaggle datasets download andrewmvd/hard-hat-detection -p data/raw/

# 2. 解压
unzip data/raw/hard-hat-detection.zip -d data/raw/

# 3. 转为 YOLO 格式（脚本）
python scripts/convert_shwd_to_yolo.py \
  --input data/raw/annotations \
  --images data/raw/images \
  --output data/datasets/helmet/
```

### 输出目录结构

```
data/datasets/helmet/
├── images/
│   ├── train/
│   └── val/
├── labels/
│   ├── train/
│   └── val/
└── dataset.yaml
```

### dataset.yaml

```yaml
path: ../data/datasets/helmet
train: images/train
val: images/val

names:
  0: person
  1: hard-hat
  2: no-hard-hat

nc: 3
```

## 三、训练

### 环境

```bash
pip install ultralytics>=8.1
```

### 训练命令

```bash
yolo detect train \
  data=data/datasets/helmet/dataset.yaml \
  model=yolov8n.pt \
  epochs=100 \
  imgsz=640 \
  batch=8 \
  device=0 \
  patience=20 \
  save_period=10 \
  project=models/ \
  name=helmet_v1
```

| 参数 | 值 | 说明 |
|---|---|---|
| model | yolov8n.pt | 预训练权重，n 版本最小（6MB） |
| epochs | 100 | 总训练轮数 |
| patience | 20 | 20 轮不提升则早停 |
| batch | 8 | GPU 显存不够时降到 4 |
| device | 0 | GPU 编号，CPU 训练用 `device=cpu` |

### 训练产物

```
models/helmet_v1/
├── weights/
│   ├── best.pt          # 最优权重（mAP 最高）→ 复制到 models/helmet.pt
│   └── last.pt          # 最后一轮权重
├── results.png          # 训练曲线（loss/mAP/precision/recall）
├── confusion_matrix.png # 混淆矩阵
└── val_batch0_pred.jpg  # 验证集预测样本
```

## 四、评估

### 关键指标

| 指标 | 目标 | 说明 |
|---|---|---|
| mAP@0.5 | > 0.85 | 所有类别的平均精度 |
| mAP@0.5:0.95 | > 0.60 | 更严格的 IoU 阈值范围 |
| person AP | > 0.80 | 人体检测精度（最核心） |
| hard-hat AP | > 0.80 | 安全帽检测精度 |
| GPU 延迟 | < 30ms | RTX 3060 级别 |

### 评估命令

```bash
yolo detect val \
  data=data/datasets/helmet/dataset.yaml \
  model=models/helmet_v1/weights/best.pt \
  imgsz=640
```

## 五、接入 Vision Agent

### 5.1 模型放置

```bash
cp models/helmet_v1/weights/best.pt models/helmet.pt
```

### 5.2 修改配置

```yaml
# configs/settings.yaml
detector:
  model: models/helmet.pt       # 替换原来的 yolov8n.pt
  confidence: 0.5
  iou_threshold: 0.45
  classes: [0, 1, 2]            # person, hard-hat, no-hard-hat
  input_size: 640
```

### 5.3 规则配置示例

```yaml
# configs/rules/helmet_check.yaml
name: "安全帽检测"
conditions:
  - type: object_in_zone
    params:
      zone: [[0, 0], [1920, 0], [1920, 1080], [0, 1080]]
      target_classes: ["no-hard-hat"]
camera_ids: ["cam_01"]
severity: warning
cooldown: 60
actions:
  - type: notify
  - type: record_clip
```

### 5.4 验证 Pipeline

```bash
# 1. 确认模型加载
python -m vision_agent --check

# 2. 用测试视频验证
python scripts/test_detection.py \
  --model models/helmet.pt \
  --source data/test_video.mp4 \
  --output data/output_demo.mp4

# 3. 启动系统
python -m vision_agent --config configs/settings.yaml

# 4. 打开前端 http://localhost:8080 查看实时效果
```

## 六、训练时间估算

| 硬件 | 100 轮耗时 |
|---|---|
| RTX 3060 (12GB) | ~2 小时 |
| RTX 4060 Laptop (8GB) | ~3 小时 |
| MacBook M1/M2 | ~5 小时（mps 加速） |
| CPU only | ~24 小时 |

## 七、常见问题

### GPU 显存不足

```bash
# 减小 batch + 图片尺寸
yolo detect train ... batch=4 imgsz=416
```

### 检测精度不达标

1. 增加 epoch 到 200
2. 关闭早停：`patience=0`
3. 加数据增强：`augment=True`
4. 换更大的模型：`model=yolov8s.pt`

### 误报/漏报调整

- 频繁漏报 → 降低 `confidence` 到 0.3
- 频繁误报 → 提高 `confidence` 到 0.7，或提高滑动窗口 `window_size`
