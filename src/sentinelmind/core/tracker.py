"""
追踪器模块 — BoT-SORT 多目标追踪

设计来源：docs/modules/core/tracker.md

职责：
- 封装多目标追踪算法
- 为每路摄像头维护独立的追踪器实例
- 将 Detection 关联为具有 track_id 的 Track 对象
- 维护轨迹、速度、生命周期
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import numpy as np

from sentinelmind.core.types import BoundingBox, Detection, Track

logger = logging.getLogger(__name__)


# ─── 配置数据类 ──────────────────────────────────────────────


@dataclass
class TrackerConfig:
    """追踪器配置（对应 settings.yaml 的 tracker 段）"""

    tracker_type: str = "botsort"
    track_thresh: float = 0.5
    track_buffer: int = 30
    match_thresh: float = 0.8
    fuse_score: bool = True
    new_track_thresh: float = 0.6
    max_age: int = 30
    min_hits: int = 3
    use_appearance: bool = True
    appearance_weight: float = 0.5
    trajectory_max_length: int = 100
    velocity_window: int = 2


# ─── 协议接口 ────────────────────────────────────────────────


@runtime_checkable
class TrackerProtocol(Protocol):
    """追踪器抽象接口（tracker.md 2.1 节）"""

    def update(self, detections: list[Detection], frame: np.ndarray) -> list[Track]:
        """输入当前帧检测结果，返回活跃追踪目标列表"""
        ...

    def get_tracks(self) -> list[Track]:
        """获取当前所有活跃追踪目标"""
        ...

    def reset(self) -> None:
        """重置追踪器内部状态"""
        ...


# ─── 内部轨迹数据 ────────────────────────────────────────────


class _TrackState:
    """追踪器内部的轨迹状态（tracker.md 3.1-3.3 节）"""

    __slots__ = (
        "track_id",
        "class_name",
        "class_id",
        "bbox",
        "trajectory",
        "velocity",
        "first_seen",
        "last_seen",
        "age",
        "hit_streak",
        "miss_count",
    )

    def __init__(
        self,
        track_id: int,
        class_name: str,
        class_id: int,
        bbox: BoundingBox,
        timestamp: float,
    ):
        self.track_id = track_id
        self.class_name = class_name
        self.class_id = class_id
        self.bbox = bbox
        cx, cy = bbox.center
        self.trajectory: list[tuple[float, float, float]] = [(cx, cy, timestamp)]
        self.velocity: tuple[float, float] = (0.0, 0.0)
        self.first_seen = timestamp
        self.last_seen = timestamp
        self.age = 1
        self.hit_streak = 1
        self.miss_count = 0

    def update_matched(self, detection: Detection, timestamp: float) -> None:
        """匹配成功时更新轨迹"""
        self.bbox = detection.bbox
        self.last_seen = timestamp
        self.age += 1
        self.hit_streak += 1
        self.miss_count = 0

        # 更新轨迹
        cx, cy = detection.bbox.center
        self.trajectory.append((cx, cy, timestamp))
        if len(self.trajectory) > 100:
            self.trajectory = self.trajectory[-100:]

        # 更新速度（最近 2 帧）
        self._update_velocity()

    def update_missed(self) -> None:
        """匹配失败时更新"""
        self.age += 1
        self.hit_streak = 0
        self.miss_count += 1

    def _update_velocity(self) -> None:
        """计算速度（像素/秒）"""
        if len(self.trajectory) < 2:
            self.velocity = (0.0, 0.0)
            return
        p1 = self.trajectory[-2]
        p2 = self.trajectory[-1]
        dt = p2[2] - p1[2]
        if dt > 0:
            vx = (p2[0] - p1[0]) / dt
            vy = (p2[1] - p1[1]) / dt
            self.velocity = (vx, vy)
        else:
            self.velocity = (0.0, 0.0)

    def to_track(self) -> Track:
        """转换为公开的 Track 数据模型"""
        return Track(
            track_id=self.track_id,
            class_name=self.class_name,
            bbox=self.bbox,
            trajectory=list(self.trajectory),
            velocity=self.velocity,
            first_seen=self.first_seen,
            last_seen=self.last_seen,
            age=self.age,
            hit_streak=self.hit_streak,
        )


# ─── BoT-SORT 追踪器 ────────────────────────────────────────


class BoTSORTTracker:
    """BoT-SORT 追踪器实现（tracker.md 2.2 节）

    使用 Ultralytics 内置的 BoT-SORT 追踪器。
    如果 Ultralytics 不可用，降级为简化的 IoU 匹配追踪。
    """

    def __init__(self, config: TrackerConfig):
        self._config = config
        self._tracks: dict[int, _TrackState] = {}
        self._next_id = 0
        self._frame_count = 0
        self._ultralytics_tracker = None
        self._min_iou_for_match = 0.1

        self._init_tracker()

    def update(self, detections: list[Detection], frame: np.ndarray) -> list[Track]:
        """更新追踪状态（tracker.md 3.1 节）"""
        self._frame_count += 1
        timestamp = time.time()

        if self._ultralytics_tracker is not None:
            return self._update_ultralytics(detections, frame, timestamp)
        else:
            return self._update_simple(detections, timestamp)

    def get_tracks(self) -> list[Track]:
        """获取活跃且已确认的轨迹"""
        return [
            state.to_track()
            for state in self._tracks.values()
            if state.hit_streak >= self._config.min_hits
        ]

    def reset(self) -> None:
        """重置追踪器"""
        self._tracks.clear()
        self._next_id = 0
        self._frame_count = 0

    @property
    def track_count(self) -> int:
        return len(self._tracks)

    @property
    def next_track_id(self) -> int:
        return self._next_id

    # ─── 内部方法 ──────────────────────────────────────────────

    def _init_tracker(self) -> None:
        """尝试初始化 Ultralytics 内置追踪器"""
        try:
            from ultralytics.trackers import BOTSORT

            self._ultralytics_tracker = BOTSORT(
                args=type(
                    "Args",
                    (),
                    {
                        "tracker_type": self._config.tracker_type,
                        "track_high_thresh": self._config.track_thresh,
                        "track_buffer": self._config.track_buffer,
                        "match_thresh": self._config.match_thresh,
                        "fuse_score": self._config.fuse_score,
                        "new_track_thresh": self._config.new_track_thresh,
                        "gmc_method": "sparseOptFlow",
                        "proximity_thresh": 0.5,
                        "appearance_thresh": 0.25,
                        "with_reid": self._config.use_appearance,
                    },
                )()
            )
            logger.info("tracker_init type=botsort(ultralytics)")
        except (ImportError, AttributeError, TypeError):
            self._ultralytics_tracker = None
            logger.info("tracker_init type=simple_iou fallback=ultralytics_unavailable")

    def _update_ultralytics(
        self, detections: list[Detection], frame: np.ndarray, timestamp: float
    ) -> list[Track]:
        """使用 Ultralytics BoT-SORT 追踪"""
        # 构造 Ultralytics 输入格式 [N, 6]: x1, y1, x2, y2, conf, cls
        if not detections:
            # 更新已有轨迹的 miss 计数
            for state in self._tracks.values():
                state.update_missed()
            self._cleanup_tracks()
            return self.get_tracks()

        det_array = np.array(
            [
                [d.bbox.x1, d.bbox.y1, d.bbox.x2, d.bbox.y2, d.confidence, d.class_id]
                for d in detections
            ]
        )

        try:
            results = self._ultralytics_tracker.update(det_array, frame)
        except Exception as e:
            logger.warning("tracker_update_error error=%s", str(e), exc_info=True)
            return self._update_simple(detections, timestamp)

        # 解析追踪结果
        self._process_tracker_results(results, detections, timestamp)
        return self.get_tracks()

    def _process_tracker_results(
        self, results, detections: list[Detection], timestamp: float
    ) -> None:
        """处理 Ultralytics 追踪器结果"""
        # 标记所有轨迹为未匹配
        for state in self._tracks.values():
            state.update_missed()

        if results is not None and len(results) > 0:
            matched_det_ids = set()
            for row in results:
                if len(row) < 6:
                    continue
                x1, y1, x2, y2, track_id, cls_id = row[:6]
                track_id = int(track_id)

                if track_id in self._tracks:
                    # 已有轨迹匹配
                    bbox = BoundingBox(float(x1), float(y1), float(x2), float(y2))
                    det = self._find_closest_detection(detections, bbox)
                    if det:
                        self._tracks[track_id].update_matched(det, timestamp)
                        matched_det_ids.add(id(det))
                else:
                    # 新轨迹
                    det = self._find_closest_detection(
                        detections,
                        BoundingBox(float(x1), float(y1), float(x2), float(y2)),
                    )
                    if det:
                        self._tracks[track_id] = _TrackState(
                            track_id=track_id,
                            class_name=det.class_name,
                            class_id=det.class_id,
                            bbox=BoundingBox(
                                float(x1), float(y1), float(x2), float(y2)
                            ),
                            timestamp=timestamp,
                        )
                        matched_det_ids.add(id(det))
                        self._next_id = max(self._next_id, track_id + 1)

        self._cleanup_tracks()

    def _update_simple(
        self, detections: list[Detection], timestamp: float
    ) -> list[Track]:
        """简化的 IoU 匹配追踪（Ultralytics 不可用时的降级方案）"""
        # 计算 IoU 矩阵
        matched_det_indices: set[int] = set()

        # 标记所有轨迹为未匹配
        for state in self._tracks.values():
            state.update_missed()

        if detections:
            # 对每个检测，找 IoU 最大的已有轨迹
            for i, det in enumerate(detections):
                if i in matched_det_indices:
                    continue

                best_iou = 0.0
                best_track_id = -1

                for tid, state in self._tracks.items():
                    if state.miss_count > 0 and state.miss_count > self._config.max_age:
                        continue
                    iou = self._compute_iou(state.bbox, det.bbox)
                    if iou > best_iou:
                        best_iou = iou
                        best_track_id = tid

                if best_iou >= self._config.match_thresh and best_track_id >= 0:
                    # 匹配成功
                    self._tracks[best_track_id].update_matched(det, timestamp)
                    matched_det_indices.add(i)
                elif det.confidence >= self._config.new_track_thresh:
                    # 新轨迹
                    new_state = _TrackState(
                        track_id=self._next_id,
                        class_name=det.class_name,
                        class_id=det.class_id,
                        bbox=det.bbox,
                        timestamp=timestamp,
                    )
                    self._tracks[self._next_id] = new_state
                    self._next_id += 1
                    matched_det_indices.add(i)

        self._cleanup_tracks()
        return self.get_tracks()

    def _cleanup_tracks(self) -> None:
        """清理超过 max_age 的轨迹"""
        to_remove = [
            tid
            for tid, state in self._tracks.items()
            if state.miss_count > self._config.max_age
        ]
        for tid in to_remove:
            state = self._tracks.pop(tid)
            logger.debug(
                "track_removed track_id=%d class=%s age=%d last_pos=(%.0f,%.0f)",
                tid,
                state.class_name,
                state.age,
                state.bbox.center[0],
                state.bbox.center[1],
            )

    @staticmethod
    def _compute_iou(box_a: BoundingBox, box_b: BoundingBox) -> float:
        """计算两个边界框的 IoU"""
        x1 = max(box_a.x1, box_b.x1)
        y1 = max(box_a.y1, box_b.y1)
        x2 = min(box_a.x2, box_b.x2)
        y2 = min(box_a.y2, box_b.y2)

        inter = max(0, x2 - x1) * max(0, y2 - y1)
        if inter <= 0:
            return 0.0

        area_a = box_a.area
        area_b = box_b.area
        union = area_a + area_b - inter

        return inter / union if union > 0 else 0.0

    @staticmethod
    def _find_closest_detection(
        detections: list[Detection],
        target_bbox: BoundingBox,
        min_iou: float = 0.1,
    ) -> Detection | None:
        """找到与目标边界框最接近的检测（IoU 低于阈值返回 None）"""
        best_iou = 0.0
        best_det = None
        for det in detections:
            iou = BoTSORTTracker._compute_iou(det.bbox, target_bbox)
            if iou > best_iou:
                best_iou = iou
                best_det = det
        return best_det if best_iou >= min_iou else None


# ─── 多路追踪管理器 ──────────────────────────────────────────


class TrackerManager:
    """多路摄像头追踪管理器（tracker.md 2.5 节）

    每路摄像头有独立的追踪器实例，状态完全隔离。
    """

    def __init__(self, config: TrackerConfig):
        self._config = config
        self._trackers: dict[str, BoTSORTTracker] = {}

    def update(
        self,
        camera_id: str,
        detections: list[Detection],
        frame: np.ndarray,
    ) -> list[Track]:
        """路由到对应摄像头的追踪器"""
        if camera_id not in self._trackers:
            self._trackers[camera_id] = BoTSORTTracker(self._config)
            logger.info("tracker_created camera=%s", camera_id)
        return self._trackers[camera_id].update(detections, frame)

    def get_tracks(self, camera_id: str) -> list[Track]:
        """获取指定摄像头的追踪结果"""
        if camera_id in self._trackers:
            return self._trackers[camera_id].get_tracks()
        return []

    def reset(self, camera_id: str) -> None:
        """重置指定摄像头的追踪器"""
        if camera_id in self._trackers:
            self._trackers[camera_id].reset()

    def reset_all(self) -> None:
        """重置所有追踪器"""
        for tracker in self._trackers.values():
            tracker.reset()

    def remove_tracker(self, camera_id: str) -> None:
        """移除指定摄像头的追踪器实例"""
        self._trackers.pop(camera_id, None)
        logger.info("tracker_removed camera=%s", camera_id)
