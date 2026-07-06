"""tests for core.recorder — 环形缓冲录制器"""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from vision_agent.core.recorder import (
    BufferedFrame,
    BufferStats,
    ClipRecorder,
    RecorderConfig,
)


# ─── helpers ──────────────────────────────────────────────────


def _make_frame(h: int = 64, w: int = 64, fill: int = 0) -> np.ndarray:
    """生成一个小尺寸纯色帧，节省内存。"""
    return np.full((h, w, 3), fill, dtype=np.uint8)


# ─── fixtures ─────────────────────────────────────────────────


@pytest.fixture
def cfg(tmp_path: Path) -> RecorderConfig:
    """返回一个指向临时目录的 RecorderConfig，fps=10 加速测试。"""
    out = tmp_path / "clips"
    snap = tmp_path / "snapshots"
    return RecorderConfig(
        output_dir=str(out),
        snapshot_dir=str(snap),
        fps=10.0,
        buffer_duration=1.0,  # 1 秒 -> maxsize = 1*10 + 20 = 30
        retention_days=1,
        snapshot_retention_days=1,
    )


@pytest.fixture
def recorder(cfg: RecorderConfig, tmp_path: Path) -> ClipRecorder:
    return ClipRecorder(config=cfg, fps=10.0)


# ─── 1. RecorderConfig 默认值 ─────────────────────────────────


class TestRecorderConfigDefaults:
    def test_default_values(self):
        c = RecorderConfig()
        assert c.enabled is True
        assert c.buffer_duration == 30.0
        assert c.default_before == 15.0
        assert c.default_after == 15.0
        assert c.output_dir == "data/clips"
        assert c.snapshot_dir == "data/snapshots"
        assert c.video_format == "mp4"
        assert c.video_codec == "libx264"
        assert c.video_crf == 23
        assert c.video_preset == "fast"
        assert c.max_clip_workers == 2
        assert c.retention_days == 7
        assert c.snapshot_retention_days == 30
        assert c.max_disk_gb == 50.0
        assert c.fps == 5.0

    def test_custom_values(self):
        c = RecorderConfig(enabled=False, fps=25.0, retention_days=30)
        assert c.enabled is False
        assert c.fps == 25.0
        assert c.retention_days == 30
        # 其余保持默认
        assert c.video_format == "mp4"


# ─── 2. BufferedFrame 创建 ────────────────────────────────────


class TestBufferedFrame:
    def test_creation(self):
        frame = _make_frame(fill=42)
        bf = BufferedFrame(frame=frame, timestamp=123.456, frame_id=7)
        assert bf.timestamp == 123.456
        assert bf.frame_id == 7
        assert bf.frame.shape == (64, 64, 3)
        assert bf.frame[0, 0, 0] == 42

    def test_frame_is_independent(self):
        """修改原始数组不应影响 BufferedFrame 中的副本。"""
        frame = _make_frame(fill=0)
        bf = BufferedFrame(frame=frame.copy(), timestamp=1.0, frame_id=1)
        frame[:] = 255
        assert bf.frame[0, 0, 0] == 0


# ─── 3. push_frame 后 buffer_size 增加 ───────────────────────


class TestPushFrame:
    def test_buffer_size_increases(self, recorder: ClipRecorder):
        cam = "cam-0"
        stats = recorder.get_buffer_stats(cam)
        assert stats is None  # 不存在

        recorder.push_frame(cam, _make_frame(), timestamp=1.0)
        stats = recorder.get_buffer_stats(cam)
        assert stats is not None
        assert stats.buffer_size == 1

        recorder.push_frame(cam, _make_frame(), timestamp=2.0)
        stats = recorder.get_buffer_stats(cam)
        assert stats.buffer_size == 2

    def test_disabled_recorder_noop(self, cfg: RecorderConfig):
        cfg.enabled = False
        rec = ClipRecorder(config=cfg)
        rec.push_frame("cam-x", _make_frame(), 1.0)
        assert rec.get_buffer_stats("cam-x") is None

    def test_frame_id_increments(self, recorder: ClipRecorder):
        cam = "cam-0"
        recorder.push_frame(cam, _make_frame(), 1.0)
        recorder.push_frame(cam, _make_frame(), 2.0)
        recorder.push_frame(cam, _make_frame(), 3.0)

        with recorder._lock:
            ids = [bf.frame_id for bf in recorder._buffers[cam]]
        assert ids == [1, 2, 3]

    def test_frame_is_copied(self, recorder: ClipRecorder):
        """push_frame 内部做 copy，外部修改不影响缓冲。"""
        cam = "cam-0"
        frame = _make_frame(fill=10)
        recorder.push_frame(cam, frame, 1.0)
        frame[:] = 255  # 修改外部

        with recorder._lock:
            stored = recorder._buffers[cam][0].frame
        assert stored[0, 0, 0] == 10


# ─── 4. 环形缓冲自动丢弃旧帧 ─────────────────────────────────


class TestRingBufferEviction:
    def test_old_frames_evicted(self, recorder: ClipRecorder):
        """push 超过 maxsize 的帧后，最早帧被丢弃。"""
        cam = "cam-ring"
        maxsize = int(recorder._config.buffer_duration * recorder._fps) + 20  # 30

        for i in range(maxsize + 10):
            recorder.push_frame(cam, _make_frame(fill=i % 256), timestamp=i * 0.1)

        stats = recorder.get_buffer_stats(cam)
        assert stats is not None
        assert stats.buffer_size == maxsize  # 不会超过 maxlen

    def test_evicted_frame_is_oldest(self, recorder: ClipRecorder):
        cam = "cam-ring2"
        maxsize = int(recorder._config.buffer_duration * recorder._fps) + 20

        for i in range(maxsize + 5):
            recorder.push_frame(cam, _make_frame(), timestamp=i * 0.1)

        with recorder._lock:
            first_ts = recorder._buffers[cam][0].timestamp
        # 最早的 5 帧（timestamp 0.0 ~ 0.4）应被丢弃
        assert first_ts == pytest.approx(5 * 0.1, abs=0.01)


# ─── 5. get_buffer_stats 返回正确统计 ─────────────────────────


class TestGetBufferStats:
    def test_nonexistent_camera_returns_none(self, recorder: ClipRecorder):
        assert recorder.get_buffer_stats("ghost") is None

    def test_empty_buffer_returns_zero_stats(self, recorder: ClipRecorder):
        """只初始化了 buffer 但没有 push，也应返回零值 stats。"""
        cam = "cam-empty"
        # 触发 buffer 创建但不 append
        recorder.push_frame(cam, _make_frame(), 1.0)
        # 手动清空以模拟 "刚初始化"
        with recorder._lock:
            recorder._buffers[cam].clear()

        stats = recorder.get_buffer_stats(cam)
        assert stats is not None
        assert stats.buffer_size == 0
        assert stats.buffer_duration == 0.0

    def test_correct_fields(self, recorder: ClipRecorder):
        cam = "cam-stats"
        t0 = 1000.0
        for i in range(5):
            recorder.push_frame(cam, _make_frame(), timestamp=t0 + i * 0.2)

        stats = recorder.get_buffer_stats(cam)
        assert stats is not None
        assert stats.camera_id == cam
        assert stats.buffer_size == 5
        assert stats.oldest_timestamp == pytest.approx(t0)
        assert stats.newest_timestamp == pytest.approx(t0 + 4 * 0.2)
        assert stats.buffer_duration == pytest.approx(0.8)
        assert stats.memory_mb > 0


# ─── 6. save_snapshot（mock cv2.imwrite）──────────────────────


class TestSaveSnapshot:
    @patch("vision_agent.core.recorder.cv2", create=True)
    def test_returns_path_on_success(self, mock_cv2, recorder: ClipRecorder):
        mock_cv2.imwrite.return_value = True

        # save_snapshot 内部会 import cv2，需要 patch 那个 import
        with patch.dict("sys.modules", {"cv2": mock_cv2}):
            result = recorder.save_snapshot(
                "cam-0", _make_frame(), timestamp=1700000000.0
            )

        assert result != ""
        assert "cam-0_1700000000.jpg" in result
        mock_cv2.imwrite.assert_called_once()

    @patch.dict("sys.modules", {"cv2": MagicMock(imwrite=MagicMock(return_value=True))})
    def test_creates_date_subdirectory(self, recorder: ClipRecorder):
        ts = 1700000000.0  # 2023-11-14 UTC
        result = recorder.save_snapshot("cam-1", _make_frame(), timestamp=ts)
        assert "2023" in result
        assert "cam-1" in result

    @patch.dict("sys.modules", {"cv2": MagicMock(imwrite=MagicMock(side_effect=Exception("fail")))})
    def test_returns_empty_on_failure(self, recorder: ClipRecorder):
        result = recorder.save_snapshot("cam-0", _make_frame(), timestamp=1700000000.0)
        assert result == ""


# ─── 7. cleanup_expired（mock 文件系统）────────────────────────


class TestCleanupExpired:
    def test_deletes_old_files(self, recorder: ClipRecorder, tmp_path: Path):
        """在 output_dir 下放一个过期文件，验证被删除。"""
        old_file = Path(recorder._config.output_dir) / "old_clip.mp4"
        old_file.parent.mkdir(parents=True, exist_ok=True)
        old_file.write_text("dummy")
        # 设置 mtime 为很久以前
        old_time = time.time() - 86400 * 10  # 10 天前
        import os
        os.utime(old_file, (old_time, old_time))

        # 新文件不应被删除
        new_file = Path(recorder._config.output_dir) / "new_clip.mp4"
        new_file.write_text("dummy")

        deleted = recorder.cleanup_expired()
        assert deleted >= 1
        assert not old_file.exists()
        assert new_file.exists()

    def test_skips_nonexistent_directory(self, cfg: RecorderConfig):
        cfg.output_dir = str(Path(cfg.output_dir) / "nonexistent")
        cfg.snapshot_dir = str(Path(cfg.snapshot_dir) / "nonexistent")
        rec = ClipRecorder(config=cfg)
        assert rec.cleanup_expired() == 0

    def test_preserves_recent_files(self, recorder: ClipRecorder):
        """刚创建的文件不应被清理。"""
        recent = Path(recorder._config.snapshot_dir) / "recent.jpg"
        recent.parent.mkdir(parents=True, exist_ok=True)
        recent.write_text("dummy")

        deleted = recorder.cleanup_expired()
        assert recent.exists()


# ─── 8. release 清空缓冲区 ───────────────────────────────────


class TestRelease:
    def test_clears_all_buffers(self, recorder: ClipRecorder):
        for cam in ("cam-A", "cam-B"):
            for i in range(5):
                recorder.push_frame(cam, _make_frame(), timestamp=i * 0.1)

        assert recorder.get_buffer_stats("cam-A") is not None
        assert recorder.get_buffer_stats("cam-B") is not None

        recorder.release()

        assert recorder.get_buffer_stats("cam-A") is None
        assert recorder.get_buffer_stats("cam-B") is None

    def test_counters_cleared(self, recorder: ClipRecorder):
        recorder.push_frame("cam-X", _make_frame(), 1.0)
        recorder.release()
        # push 新帧后 frame_id 应从 1 重新开始
        recorder.push_frame("cam-X", _make_frame(), 2.0)
        with recorder._lock:
            assert recorder._buffers["cam-X"][0].frame_id == 1


# ─── 9. 多路摄像头缓冲隔离 ────────────────────────────────────


class TestMultiCameraIsolation:
    def test_independent_buffers(self, recorder: ClipRecorder):
        cam_a, cam_b = "cam-A", "cam-B"

        for i in range(3):
            recorder.push_frame(cam_a, _make_frame(fill=10), timestamp=i * 0.1)
        for i in range(7):
            recorder.push_frame(cam_b, _make_frame(fill=20), timestamp=i * 0.1)

        stats_a = recorder.get_buffer_stats(cam_a)
        stats_b = recorder.get_buffer_stats(cam_b)

        assert stats_a is not None and stats_b is not None
        assert stats_a.buffer_size == 3
        assert stats_b.buffer_size == 7

    def test_frame_counters_independent(self, recorder: ClipRecorder):
        recorder.push_frame("cam-1", _make_frame(), 1.0)
        recorder.push_frame("cam-1", _make_frame(), 2.0)
        recorder.push_frame("cam-2", _make_frame(), 1.0)

        with recorder._lock:
            assert recorder._frame_counters["cam-1"] == 2
            assert recorder._frame_counters["cam-2"] == 1

    def test_releasing_one_does_not_affect_other(self, recorder: ClipRecorder):
        """release 是全量清空；如果只想清一路，需重新初始化。"""
        recorder.push_frame("cam-1", _make_frame(), 1.0)
        recorder.push_frame("cam-2", _make_frame(), 1.0)

        recorder.release()

        # 两路都应被清空（release 是全局操作）
        assert recorder.get_buffer_stats("cam-1") is None
        assert recorder.get_buffer_stats("cam-2") is None


# ─── save_clip + FFmpeg（mock subprocess）────────────────────


class TestSaveClipWithMockFFmpeg:
    def test_save_clip_extracts_and_calls_ffmpeg(self, recorder: ClipRecorder):
        """验证 save_clip 能正确提取帧并通过 FFmpeg 转码。"""
        cam = "cam-clip"
        t0 = 100.0
        # 填充缓冲
        for i in range(20):
            recorder.push_frame(cam, _make_frame(), timestamp=t0 + i * 0.2)

        mock_proc = MagicMock()
        mock_proc.stdin = MagicMock()
        mock_proc.wait.return_value = None

        with patch("vision_agent.core.recorder.subprocess.Popen", return_value=mock_proc):
            callback = MagicMock()
            recorder.save_clip(
                camera_id=cam,
                trigger_time=t0 + 3.0,
                before_seconds=2.0,
                after_seconds=0,
                callback=callback,
            )
            # 等待后台线程完成
            import time as _t
            _t.sleep(0.5)

        mock_proc.wait.assert_called_once()
        callback.assert_called_once()

    def test_save_clip_disabled_noop(self, cfg: RecorderConfig):
        cfg.enabled = False
        rec = ClipRecorder(config=cfg)
        # 不应抛出异常
        rec.save_clip("cam-0", trigger_time=1.0)
