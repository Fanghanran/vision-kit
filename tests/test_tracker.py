"""Tests for vision_agent.core.tracker"""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import numpy as np
import pytest

from vision_agent.core.tracker import (
    BoTSORTTracker,
    TrackerConfig,
    TrackerManager,
    TrackerProtocol,
    _TrackState,
)
from vision_agent.core.types import BoundingBox, Detection, Track


# ─── Helpers ──────────────────────────────────────────────────


def _bbox(x1: float = 0, y1: float = 0, x2: float = 100, y2: float = 100) -> BoundingBox:
    return BoundingBox(x1, y1, x2, y2)


def _detection(
    bbox: BoundingBox | None = None,
    confidence: float = 0.9,
    class_name: str = "person",
    class_id: int = 0,
    frame_id: int = 0,
    timestamp: float | None = None,
) -> Detection:
    return Detection(
        frame_id=frame_id,
        class_id=class_id,
        class_name=class_name,
        confidence=confidence,
        bbox=bbox or _bbox(),
        timestamp=timestamp or time.time(),
    )


def _frame() -> np.ndarray:
    return np.zeros((480, 640, 3), dtype=np.uint8)


# ─── 1. TrackerConfig 默认值 ─────────────────────────────────


class TestTrackerConfig:
    def test_defaults(self):
        cfg = TrackerConfig()
        assert cfg.tracker_type == "botsort"
        assert cfg.track_thresh == 0.5
        assert cfg.track_buffer == 30
        assert cfg.match_thresh == 0.8
        assert cfg.fuse_score is True
        assert cfg.new_track_thresh == 0.6
        assert cfg.max_age == 30
        assert cfg.min_hits == 3
        assert cfg.use_appearance is True
        assert cfg.appearance_weight == 0.5

    def test_custom_values(self):
        cfg = TrackerConfig(track_thresh=0.7, max_age=50, min_hits=5)
        assert cfg.track_thresh == 0.7
        assert cfg.max_age == 50
        assert cfg.min_hits == 5


# ─── 2. _TrackState ──────────────────────────────────────────


class TestTrackState:
    def _make(self, ts: float = 1000.0) -> _TrackState:
        return _TrackState(
            track_id=1,
            class_name="person",
            class_id=0,
            bbox=_bbox(10, 10, 50, 50),
            timestamp=ts,
        )

    def test_creation(self):
        ts = self._make()
        assert ts.track_id == 1
        assert ts.class_name == "person"
        assert ts.age == 1
        assert ts.hit_streak == 1
        assert ts.miss_count == 0
        assert ts.velocity == (0.0, 0.0)
        assert len(ts.trajectory) == 1

    def test_update_matched(self):
        ts = self._make(1000.0)
        det = _detection(bbox=_bbox(20, 20, 60, 60), timestamp=1001.0)
        ts.update_matched(det, 1001.0)

        assert ts.age == 2
        assert ts.hit_streak == 2
        assert ts.miss_count == 0
        assert ts.bbox.x1 == 20
        assert len(ts.trajectory) == 2

    def test_update_missed(self):
        ts = self._make()
        ts.update_missed()
        assert ts.age == 2
        assert ts.hit_streak == 0
        assert ts.miss_count == 1

    def test_velocity_calculation(self):
        ts = self._make(1000.0)
        # Move 100px in x over 1 second
        det = _detection(bbox=_bbox(110, 10, 150, 50), timestamp=1001.0)
        ts.update_matched(det, 1001.0)
        vx, vy = ts.velocity
        # center moved from 30,30 to 130,30 => dx=100, dt=1 => vx=100
        assert abs(vx - 100.0) < 1e-6
        assert abs(vy) < 1e-6

    def test_velocity_zero_with_single_point(self):
        ts = self._make()
        assert ts.velocity == (0.0, 0.0)

    def test_to_track(self):
        ts = self._make()
        t = ts.to_track()
        assert isinstance(t, Track)
        assert t.track_id == 1
        assert t.class_name == "person"
        assert t.age == 1
        assert t.hit_streak == 1
        assert len(t.trajectory) == 1

    def test_trajectory_cap_at_100(self):
        ts = self._make(0.0)
        for i in range(1, 110):
            det = _detection(bbox=_bbox(10 + i, 10, 50 + i, 50), timestamp=float(i))
            ts.update_matched(det, float(i))
        assert len(ts.trajectory) == 100


# ─── 3. BoTSORTTracker 基础操作 ──────────────────────────────


class TestBoTSORTTrackerBasics:
    def _tracker(self, **overrides) -> BoTSORTTracker:
        cfg = TrackerConfig(**overrides)
        return BoTSORTTracker(cfg)

    def test_update_empty(self):
        tracker = self._tracker()
        result = tracker.update([], _frame())
        assert result == []

    def test_update_single_detection(self):
        tracker = self._tracker(min_hits=1)
        det = _detection(confidence=0.9)
        result = tracker.update([det], _frame())
        # min_hits=1 => after first hit it's confirmed
        assert len(result) == 1
        assert result[0].class_name == "person"

    def test_reset(self):
        tracker = self._tracker(min_hits=1)
        tracker.update([_detection()], _frame())
        assert tracker.track_count > 0
        tracker.reset()
        assert tracker.track_count == 0
        assert tracker.next_track_id == 0

    def test_track_count(self):
        tracker = self._tracker(min_hits=1)
        assert tracker.track_count == 0
        tracker.update([_detection()], _frame())
        assert tracker.track_count == 1

    def test_next_track_id_increments(self):
        tracker = self._tracker(min_hits=1)
        tracker.update([_detection()], _frame())
        assert tracker.next_track_id == 1


# ─── 4. BoTSORTTracker IoU 计算 ──────────────────────────────


class TestIoU:
    def test_complete_overlap(self):
        box = _bbox(0, 0, 100, 100)
        iou = BoTSORTTracker._compute_iou(box, box)
        assert abs(iou - 1.0) < 1e-9

    def test_no_overlap(self):
        a = _bbox(0, 0, 50, 50)
        b = _bbox(60, 60, 100, 100)
        iou = BoTSORTTracker._compute_iou(a, b)
        assert iou == 0.0

    def test_partial_overlap(self):
        a = _bbox(0, 0, 100, 100)
        b = _bbox(50, 50, 150, 150)
        # intersection: 50*50=2500; union: 10000+10000-2500=17500
        expected = 2500 / 17500
        iou = BoTSORTTracker._compute_iou(a, b)
        assert abs(iou - expected) < 1e-9


# ─── 5. 轨迹生命周期 ────────────────────────────────────────


class TestTrackLifecycle:
    def test_new_then_unconfirmed_then_confirmed_then_lost_then_removed(self):
        """新检测 -> 确认 -> 丢失 -> 删除

        Uses min_hits=1 so a single match confirms a track.
        Tests the full lifecycle: creation, confirmed visibility, lost (no match),
        and eventual removal after exceeding max_age.
        """
        cfg = TrackerConfig(
            min_hits=1,
            max_age=2,
            match_thresh=0.8,
            new_track_thresh=0.5,
        )
        tracker = BoTSORTTracker(cfg)

        # Frame 1: new detection -> track created, hit_streak >= min_hits=1 => confirmed
        det = _detection(bbox=_bbox(10, 10, 50, 50), confidence=0.9)
        tracks = tracker.update([det], _frame())
        assert len(tracks) == 1  # confirmed immediately (min_hits=1)
        assert tracks[0].class_name == "person"
        assert tracker.track_count == 1

        # Frame 2: same position -> matched, still confirmed
        det2 = _detection(bbox=_bbox(10, 10, 50, 50), confidence=0.9)
        tracks = tracker.update([det2], _frame())
        assert len(tracks) == 1  # still confirmed

        # Frame 3: no detection -> track is now lost (unconfirmed)
        tracks = tracker.update([], _frame())
        assert len(tracks) == 0  # hit_streak=0 < min_hits=1
        assert tracker.track_count == 1  # track still alive internally

        # Frame 4: no detection -> still alive
        tracker.update([], _frame())
        assert tracker.track_count == 1

        # Frame 5: no detection -> miss_count exceeds max_age=2 => removed
        tracker.update([], _frame())
        assert tracker.track_count == 0  # cleaned up


# ─── 6. TrackerManager ──────────────────────────────────────


class TestTrackerManager:
    def test_multi_camera_isolation(self):
        mgr = TrackerManager(TrackerConfig(min_hits=1))
        det_a = _detection(bbox=_bbox(10, 10, 50, 50))
        det_b = _detection(bbox=_bbox(200, 200, 300, 300))

        mgr.update("cam_a", [det_a], _frame())
        mgr.update("cam_b", [det_b], _frame())

        tracks_a = mgr.get_tracks("cam_a")
        tracks_b = mgr.get_tracks("cam_b")
        assert len(tracks_a) == 1
        assert len(tracks_b) == 1
        # Track IDs start at 0 in each independent tracker
        assert tracks_a[0].track_id == 0
        assert tracks_b[0].track_id == 0

    def test_get_tracks_unknown_camera(self):
        mgr = TrackerManager(TrackerConfig())
        assert mgr.get_tracks("nonexistent") == []

    def test_reset_single_camera(self):
        mgr = TrackerManager(TrackerConfig(min_hits=1))
        mgr.update("cam_a", [_detection()], _frame())
        assert len(mgr.get_tracks("cam_a")) == 1
        mgr.reset("cam_a")
        # After reset, the tracker exists but is empty
        assert len(mgr.get_tracks("cam_a")) == 0

    def test_reset_all(self):
        mgr = TrackerManager(TrackerConfig(min_hits=1))
        mgr.update("cam_a", [_detection()], _frame())
        mgr.update("cam_b", [_detection()], _frame())
        mgr.reset_all()
        assert len(mgr.get_tracks("cam_a")) == 0
        assert len(mgr.get_tracks("cam_b")) == 0

    def test_remove_tracker(self):
        mgr = TrackerManager(TrackerConfig(min_hits=1))
        mgr.update("cam_a", [_detection()], _frame())
        mgr.remove_tracker("cam_a")
        # After removal, get_tracks returns empty (tracker no longer exists)
        assert mgr.get_tracks("cam_a") == []

    def test_remove_nonexistent_is_noop(self):
        mgr = TrackerManager(TrackerConfig())
        mgr.remove_tracker("no_such_cam")  # should not raise


# ─── 7. TrackerProtocol isinstance 检查 ──────────────────────


class TestTrackerProtocol:
    def test_bostracker_isinstance(self):
        tracker = BoTSORTTracker(TrackerConfig())
        assert isinstance(tracker, TrackerProtocol)

    def test_tracker_protocol_methods(self):
        """Verify the protocol exposes the expected method names."""
        assert hasattr(TrackerProtocol, "update")
        assert hasattr(TrackerProtocol, "get_tracks")
        assert hasattr(TrackerProtocol, "reset")
