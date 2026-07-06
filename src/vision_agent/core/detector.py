"""
检测器模块 — YOLO 目标检测模型封装

设计来源：docs/modules/core/detector.md

职责：
- 封装 Ultralytics YOLO 模型
- 提供 batch 推理接口
- 结果解析（Ultralytics Results → Detection 列表）
- 推理失败降级（跳帧、OOM 重试、连续失败降级）
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

import numpy as np

from vision_agent.core.types import BoundingBox, Detection

logger = logging.getLogger(__name__)


# ─── 配置数据类 ──────────────────────────────────────────────


@dataclass
class DetectorConfig:
    """检测器配置（对应 settings.yaml 的 detector 段）"""

    model_path: str
    confidence: float = 0.5
    iou_threshold: float = 0.45
    batch_size: int = 8
    batch_timeout_ms: int = 50
    input_size: int = 640
    fp16: bool = True
    classes_filter: list[str] | None = None
    max_det: int = 300


# ─── 协议接口 ────────────────────────────────────────────────


@runtime_checkable
class DetectorProtocol(Protocol):
    """检测器抽象接口（detector.md 2.1 节）"""

    def detect(self, frames: list[np.ndarray]) -> list[list[Detection]]:
        """batch 推理，返回每帧的检测结果列表"""
        ...

    def warmup(self) -> None:
        """模型预热"""
        ...

    def release(self) -> None:
        """释放模型资源"""
        ...

    @property
    def model_name(self) -> str:
        """模型名称"""
        ...

    @property
    def classes(self) -> list[str]:
        """支持的检测类别列表"""
        ...


# ─── YOLO 检测器实现 ─────────────────────────────────────────


class YOLODetector:
    """YOLO 检测器（detector.md 2.2 节）

    封装 Ultralytics YOLO，支持：
    - .pt PyTorch 模型
    - .engine TensorRT 引擎
    - FP16 半精度推理
    - 类别过滤
    - OOM 自动降级
    """

    def __init__(self, config: DetectorConfig, device: str = "cuda:0"):
        self._config = config
        self._device = self._resolve_device(device)
        self._model = None
        self._class_names: dict[int, str] = {}
        self._filter_ids: set[int] | None = None
        self._current_batch_size = config.batch_size
        self._original_batch_size = config.batch_size

        # 连续失败计数器（detector.md 3.5 节）
        self._consecutive_failures: dict[str, int] = {}  # camera_id → count
        self._total_inferences = 0
        self._total_failures = 0
        self._success_since_oom = 0  # OOM 后成功次数，用于恢复 batch_size
        self._fp16_enabled = False

        self._load_model()

    # ─── 公开接口 ──────────────────────────────────────────────

    def detect(
        self,
        frames: list[np.ndarray],
        camera_id: str = "",
    ) -> list[list[Detection]]:
        """batch 推理（detector.md 3.3 节）

        返回每帧的检测结果列表。
        推理失败的帧返回空列表（追踪器用上一帧结果兜底）。
        """
        if not frames:
            return []

        self._total_inferences += 1
        start_time = time.monotonic()

        try:
            results = self._infer_batch(frames)
            # 成功：重置失败计数，尝试恢复 batch_size
            if camera_id:
                self._consecutive_failures[camera_id] = 0
            self._success_since_oom += 1
            self._maybe_restore_batch_size()
            return results
        except RuntimeError as e:
            if "out of memory" in str(e).lower():
                return self._handle_oom(frames)
            return self._handle_detect_failure(camera_id, frames, e)
        except Exception as e:
            return self._handle_detect_failure(camera_id, frames, e)
        finally:
            # 推理超时检测（>500ms 告警）
            elapsed_ms = (time.monotonic() - start_time) * 1000
            if elapsed_ms > 500:
                logger.warning(
                    "detect_slow camera=%s elapsed=%.0fms frames=%d",
                    camera_id,
                    elapsed_ms,
                    len(frames),
                )

    def detect_single(self, frame: np.ndarray) -> list[Detection]:
        """单帧推理便捷方法"""
        results = self.detect([frame])
        return results[0] if results else []

    def warmup(self) -> None:
        """模型预热（detector.md 3.2 节）

        用空白帧跑 3 次推理，填充 CUDA kernel 缓存。
        """
        if self._model is None:
            return
        logger.info("detector_warmup start device=%s", self._device)
        dummy = np.zeros(
            (self._config.input_size, self._config.input_size, 3), dtype=np.uint8
        )
        start = time.monotonic()
        for _ in range(3):
            self._model(dummy, verbose=False)
        self._sync_cuda()
        elapsed = time.monotonic() - start
        logger.info("detector_warmup done elapsed=%.2fs", elapsed)

    def release(self) -> None:
        """释放模型资源"""
        if self._model is not None:
            del self._model
            self._model = None
        self._clear_cuda_cache()
        logger.info("detector_released")

    @property
    def model_name(self) -> str:
        return Path(self._config.model_path).name

    @property
    def classes(self) -> list[str]:
        return list(self._class_names.values())

    def set_confidence(self, threshold: float) -> None:
        """运行时调整置信度阈值"""
        self._config.confidence = threshold

    @property
    def config(self) -> DetectorConfig:
        return self._config

    # ─── 内部方法 ──────────────────────────────────────────────

    @staticmethod
    def _resolve_device(device: str) -> str:
        """检查 CUDA 可用性，不可用则回退 CPU"""
        if "cuda" in device:
            try:
                import torch

                if not torch.cuda.is_available():
                    logger.warning("cuda_unavailable fallback=cpu requested=%s", device)
                    return "cpu"
            except ImportError:
                logger.warning("torch_not_available fallback=cpu")
                return "cpu"
        return device

    def _load_model(self) -> None:
        """加载 YOLO 模型（detector.md 3.1 节）"""
        model_path = Path(self._config.model_path)
        if not model_path.exists():
            raise FileNotFoundError(f"模型文件不存在: {model_path}")

        from ultralytics import YOLO

        self._model = YOLO(str(model_path))

        # 获取类别名
        names = self._model.names
        self._class_names = {int(k): str(v) for k, v in names.items()}

        # 设置推理参数
        self._model.overrides["conf"] = self._config.confidence
        self._model.overrides["iou"] = self._config.iou_threshold
        self._model.overrides["imgsz"] = self._config.input_size
        self._model.overrides["max_det"] = self._config.max_det

        # FP16
        if self._config.fp16 and "cuda" in self._device:
            try:
                self._model.half()
                self._fp16_enabled = True
                logger.info("detector_fp16 enabled")
            except Exception as e:
                logger.warning("detector_fp16_failed error=%s", str(e))

        # 类别过滤
        if self._config.classes_filter:
            self._filter_ids = {
                cid
                for cid, cname in self._class_names.items()
                if cname in self._config.classes_filter
            }
            logger.info(
                "detector_class_filter classes=%s ids=%s",
                self._config.classes_filter,
                self._filter_ids,
            )

        logger.info(
            "detector_loaded model=%s classes=%d device=%s",
            self.model_name,
            len(self._class_names),
            self._device,
        )

    def _infer_batch(self, frames: list[np.ndarray]) -> list[list[Detection]]:
        """执行 batch 推理"""
        if self._model is None:
            return [[] for _ in frames]

        # Ultralytics YOLO 直接接受 list[np.ndarray]
        results_list = self._model(
            frames,
            conf=self._config.confidence,
            iou=self._config.iou_threshold,
            imgsz=self._config.input_size,
            max_det=self._config.max_det,
            verbose=False,
        )

        self._sync_cuda()

        # 解析结果
        batch_results = []
        for i, results in enumerate(results_list):
            detections = self._parse_results(results, frame_id=i)
            batch_results.append(detections)

        return batch_results

    def _parse_results(self, results, frame_id: int) -> list[Detection]:
        """解析 Ultralytics Results → Detection 列表

        设计来源：detector.md 3.4 节
        """
        detections = []
        timestamp = time.time()

        if results.boxes is None:
            return detections

        boxes = results.boxes
        for j in range(len(boxes)):
            class_id = int(boxes.cls[j])
            confidence = float(boxes.conf[j])

            # 类别过滤
            if self._filter_ids is not None and class_id not in self._filter_ids:
                continue

            # 坐标
            xyxy = boxes.xyxy[j].cpu().numpy()
            bbox = BoundingBox(
                x1=float(xyxy[0]),
                y1=float(xyxy[1]),
                x2=float(xyxy[2]),
                y2=float(xyxy[3]),
            )

            class_name = self._class_names.get(class_id, f"class_{class_id}")

            detections.append(
                Detection(
                    frame_id=frame_id,
                    class_id=class_id,
                    class_name=class_name,
                    confidence=confidence,
                    bbox=bbox,
                    timestamp=timestamp,
                )
            )

        return detections

    def _handle_oom(self, frames: list[np.ndarray]) -> list[list[Detection]]:
        """OOM 处理（detector.md 3.5 节）

        清空 CUDA 缓存，batch_size 减半重试。
        FP16 溢出时自动降级为 FP32。
        """
        # FP16 自动降级 FP32
        if self._fp16_enabled:
            logger.warning("detect_oom fp16_downgrade action=switch_to_fp32")
            try:
                if self._model is not None:
                    self._model.float()
                self._fp16_enabled = False
            except Exception:
                pass

        logger.warning(
            "detect_oom batch_size=%d halving to %d",
            self._current_batch_size,
            self._current_batch_size // 2,
        )
        self._clear_cuda_cache()
        self._current_batch_size = max(1, self._current_batch_size // 2)
        self._success_since_oom = 0

        # 分成小 batch 重试
        results = []
        for i in range(0, len(frames), self._current_batch_size):
            chunk = frames[i : i + self._current_batch_size]
            try:
                chunk_results = self._infer_batch(chunk)
                results.extend(chunk_results)
            except Exception as e:
                logger.error("detect_oom_retry_failed error=%s", str(e))
                results.extend([[] for _ in chunk])

        return results

    def _handle_detect_failure(
        self, camera_id: str, frames: list[np.ndarray], error: Exception
    ) -> list[list[Detection]]:
        """推理失败处理（detector.md 3.5 节）

        - 记录连续失败计数
        - >10 次：该路降级为仅录制不检测
        - >100 次：触发系统告警
        """
        self._total_failures += 1
        if camera_id:
            count = self._consecutive_failures.get(camera_id, 0) + 1
            self._consecutive_failures[camera_id] = count

            if count > 100:
                logger.error(
                    "detect_critical camera=%s failures=%d error=%s",
                    camera_id,
                    count,
                    str(error),
                )
            elif count > 10:
                logger.warning(
                    "detect_degraded camera=%s failures=%d action=skip_detection",
                    camera_id,
                    count,
                )
            else:
                logger.warning(
                    "detect_failure camera=%s failures=%d error=%s",
                    camera_id,
                    count,
                    str(error),
                )
        else:
            logger.error("detect_error error=%s", str(error))

        return [[] for _ in frames]

    def _maybe_restore_batch_size(self) -> None:
        """OOM 后连续成功 N 次，尝试恢复原始 batch_size"""
        if (
            self._current_batch_size < self._original_batch_size
            and self._success_since_oom >= 50
        ):
            restored = min(self._current_batch_size * 2, self._original_batch_size)
            logger.info(
                "detect_batch_restore from=%d to=%d",
                self._current_batch_size,
                restored,
            )
            self._current_batch_size = restored
            self._success_since_oom = 0

    def get_failure_count(self, camera_id: str) -> int:
        """获取指定摄像头的连续失败次数"""
        return self._consecutive_failures.get(camera_id, 0)

    def _sync_cuda(self) -> None:
        """同步 CUDA"""
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.synchronize()
        except ImportError:
            pass

    def _clear_cuda_cache(self) -> None:
        """清空 CUDA 缓存"""
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:
            pass
