"""单元测试 — vision_agent.core.camera

覆盖范围：
  1. CameraConfig 创建和默认值
  2. FrameData 创建和 frame_id 属性
  3. FrameQueue: put/get、队列满时丢旧帧、drop_count
  4. CameraThread: start/stop 生命周期、camera_id/camera_name 属性
  5. CameraThread: camera_state 返回正确的 CameraState
  6. CameraThread: _build_ffmpeg_command 生成正确的命令行
  7. CameraThread: 状态从 CONNECTING → CONNECTED 的变化

所有 FFmpeg 子进程和线程均通过 unittest.mock 模拟。
"""

from __future__ import annotations

import logging
import threading
import time
from unittest.mock import MagicMock, patch, PropertyMock

import numpy as np
import pytest

from vision_agent.core.camera import (
    CameraConfig,
    CameraThread,
    FrameData,
    FrameQueue,
)
from vision_agent.core.types import CameraState, CameraStatus


# ─── 辅助函数 ─────────────────────────────────────────────────


def _make_config(**overrides) -> CameraConfig:
    """创建测试用 CameraConfig，允许覆盖任意字段。"""
    defaults = dict(
        camera_id="cam_01",
        camera_name="正门摄像头",
        rtsp_url="rtsp://192.168.1.100:554/stream1",
    )
    defaults.update(overrides)
    return CameraConfig(**defaults)


def _make_frame(
    camera_id: str = "cam_01",
    frame_seq: int = 1,
    width: int = 640,
    height: int = 640,
) -> FrameData:
    """创建测试用 FrameData。"""
    return FrameData(
        camera_id=camera_id,
        frame=np.zeros((height, width, 3), dtype=np.uint8),
        timestamp=time.time(),
        frame_seq=frame_seq,
        width=width,
        height=height,
    )


# ─── 1. CameraConfig 创建和默认值 ────────────────────────────


class TestCameraConfig:
    def test_create_with_required_fields(self):
        cfg = _make_config()
        assert cfg.camera_id == "cam_01"
        assert cfg.camera_name == "正门摄像头"
        assert cfg.rtsp_url == "rtsp://192.168.1.100:554/stream1"

    def test_default_values(self):
        cfg = _make_config()
        assert cfg.fps == 0.0  # 0 表示自动检测
        assert cfg.width == 640
        assert cfg.height == 640
        assert cfg.reconnect_delay == 3.0
        assert cfg.reconnect_max_delay == 60.0
        assert cfg.reconnect_backoff == 2.0
        assert cfg.ffmpeg_timeout == 10.0
        assert cfg.use_gpu_decode is False

    def test_custom_values(self):
        cfg = _make_config(
            fps=10.0,
            width=1280,
            height=720,
            use_gpu_decode=True,
        )
        assert cfg.fps == 10.0
        assert cfg.width == 1280
        assert cfg.height == 720
        assert cfg.use_gpu_decode is True


# ─── 2. FrameData 创建和 frame_id 属性 ───────────────────────


class TestFrameData:
    def test_create(self):
        frame = _make_frame(frame_seq=42)
        assert frame.camera_id == "cam_01"
        assert frame.frame_seq == 42
        assert frame.width == 640
        assert frame.height == 640
        assert frame.frame.shape == (640, 640, 3)

    def test_frame_id_equals_frame_seq(self):
        frame = _make_frame(frame_seq=7)
        assert frame.frame_id == 7
        assert frame.frame_id == frame.frame_seq

    def test_frame_data_is_bgr(self):
        frame = _make_frame()
        # shape 的最后一个维度应为 3（BGR 通道）
        assert frame.frame.shape[2] == 3


# ─── 3. FrameQueue: put/get、队列满时丢旧帧、drop_count ───────


class TestFrameQueue:
    def test_put_and_get(self):
        q = FrameQueue(maxsize=10)
        f1 = _make_frame(frame_seq=1)
        f2 = _make_frame(frame_seq=2)
        q.put(f1)
        q.put(f2)
        assert q.size == 2
        got = q.get(timeout=0.1)
        assert got is not None
        assert got.frame_seq == 1
        got2 = q.get(timeout=0.1)
        assert got2 is not None
        assert got2.frame_seq == 2

    def test_get_timeout_returns_none(self):
        q = FrameQueue(maxsize=10)
        result = q.get(timeout=0.01)
        assert result is None

    def test_get_nowait_empty_returns_none(self):
        q = FrameQueue(maxsize=10)
        assert q.get_nowait() is None

    def test_queue_full_drops_oldest(self):
        q = FrameQueue(maxsize=3)
        for i in range(3):
            q.put(_make_frame(frame_seq=i))
        assert q.size == 3

        # 队列已满，放入第 4 帧应丢弃 frame_seq=0 的帧
        q.put(_make_frame(frame_seq=99))
        assert q.size == 3
        assert q.drop_count == 1

        # 取出的最旧帧应该是 frame_seq=1（0 已被丢弃）
        oldest = q.get(timeout=0.1)
        assert oldest is not None
        assert oldest.frame_seq == 1

    def test_drop_count_accumulates(self):
        q = FrameQueue(maxsize=2)
        for i in range(5):
            q.put(_make_frame(frame_seq=i))
        # 容量为 2，放了 5 个，丢弃了 3 个
        assert q.drop_count == 3
        assert q.size == 2

    def test_size_property(self):
        q = FrameQueue(maxsize=100)
        assert q.size == 0
        q.put(_make_frame(frame_seq=1))
        assert q.size == 1
        q.get(timeout=0.1)
        assert q.size == 0

    def test_default_maxsize(self):
        q = FrameQueue()
        assert q._maxsize == 200


# ─── 4. CameraThread: start/stop 生命周期、属性 ──────────────


class TestCameraThreadLifecycle:
    def _make_thread(self, **config_overrides) -> CameraThread:
        cfg = _make_config(**config_overrides)
        queue = FrameQueue(maxsize=50)
        return CameraThread(config=cfg, frame_queue=queue)

    @patch("vision_agent.core.camera.subprocess.Popen")
    def test_start_creates_thread(self, mock_popen):
        """start() 应创建并启动线程。"""
        mock_proc = MagicMock()
        mock_proc.stdout.read.return_value = b""
        mock_proc.stdout.__bool__ = MagicMock(return_value=True)
        mock_popen.return_value = mock_proc

        ct = self._make_thread()
        ct.start()
        assert ct.is_alive()
        ct.stop()

    @patch("vision_agent.core.camera.subprocess.Popen")
    def test_stop_joins_thread(self, mock_popen):
        """stop() 应将 _running 设为 False 并 join 线程。"""
        mock_proc = MagicMock()
        mock_proc.stdout.read.return_value = b""
        mock_popen.return_value = mock_proc

        ct = self._make_thread()
        ct.start()
        ct.stop()
        assert not ct.is_alive()
        assert ct.status == CameraStatus.DISCONNECTED

    @patch("vision_agent.core.camera.subprocess.Popen")
    def test_camera_id_property(self, mock_popen):
        ct = self._make_thread(camera_id="cam_99")
        assert ct.camera_id == "cam_99"

    @patch("vision_agent.core.camera.subprocess.Popen")
    def test_camera_name_property(self, mock_popen):
        ct = self._make_thread(camera_name="后门摄像头")
        assert ct.camera_name == "后门摄像头"

    @patch("vision_agent.core.camera.subprocess.Popen")
    def test_start_twice_does_not_create_extra_thread(self, mock_popen):
        """连续调用 start() 两次不应创建新线程。"""
        mock_proc = MagicMock()
        mock_proc.stdout.read.return_value = b""
        mock_popen.return_value = mock_proc

        ct = self._make_thread()
        ct.start()
        thread_id_1 = ct._thread.ident
        ct.start()  # 第二次调用应被忽略
        thread_id_2 = ct._thread.ident
        assert thread_id_1 == thread_id_2
        ct.stop()

    @patch("vision_agent.core.camera.subprocess.Popen")
    def test_stop_when_not_started(self, mock_popen):
        """未启动时调用 stop() 不应报错。"""
        ct = self._make_thread()
        ct.stop()
        assert ct.status == CameraStatus.DISCONNECTED


# ─── 5. CameraThread: camera_state 返回正确的 CameraState ────


class TestCameraThreadCameraState:
    def _make_thread(self) -> tuple[CameraThread, FrameQueue]:
        cfg = _make_config()
        queue = FrameQueue(maxsize=50)
        return CameraThread(config=cfg, frame_queue=queue), queue

    def test_initial_camera_state(self):
        """未启动时 camera_state 应反映初始状态。"""
        ct, _ = self._make_thread()
        state = ct.camera_state
        assert isinstance(state, CameraState)
        assert state.camera_id == "cam_01"
        assert state.status == CameraStatus.CONNECTING
        assert state.total_detections == 0
        assert state.total_alerts == 0
        assert state.error_message == ""

    @patch("vision_agent.core.camera.subprocess.Popen")
    def test_camera_state_after_update_detection(self, mock_popen):
        """update_detection_count 应反映在 camera_state 中。"""
        ct, _ = self._make_thread()
        ct.update_detection_count(5)
        ct.update_detection_count(3)
        state = ct.camera_state
        assert state.total_detections == 8

    @patch("vision_agent.core.camera.subprocess.Popen")
    def test_camera_state_after_update_alert(self, mock_popen):
        """update_alert_count 应反映在 camera_state 中。"""
        ct, _ = self._make_thread()
        ct.update_alert_count(2)
        ct.update_alert_count()
        state = ct.camera_state
        assert state.total_alerts == 3

    def test_camera_state_queue_size(self):
        """camera_state.queue_size 应与 FrameQueue.size 一致。"""
        ct, queue = self._make_thread()
        for i in range(5):
            queue.put(_make_frame(frame_seq=i))
        state = ct.camera_state
        assert state.queue_size == 5


# ─── 6. CameraThread: _build_ffmpeg_command ───────────────────


class TestBuildFFmpegCommand:
    def test_default_command(self):
        """默认配置应生成正确的 FFmpeg 命令行。"""
        cfg = _make_config()
        queue = FrameQueue()
        ct = CameraThread(config=cfg, frame_queue=queue)
        cmd = ct._build_ffmpeg_command()

        assert cmd[0] == "ffmpeg"
        assert "-rtsp_transport" in cmd
        assert cmd[cmd.index("-rtsp_transport") + 1] == "tcp"
        assert "-i" in cmd
        assert cmd[cmd.index("-i") + 1] == cfg.rtsp_url
        assert "-f" in cmd
        assert cmd[cmd.index("-f") + 1] == "rawvideo"
        assert "-pix_fmt" in cmd
        assert cmd[cmd.index("-pix_fmt") + 1] == "bgr24"
        assert "-vf" in cmd
        assert cmd[cmd.index("-vf") + 1] == "scale=640:640"
        assert "-an" in cmd
        assert cmd[-1] == "-"

    def test_custom_resolution(self):
        """自定义分辨率应体现在 -vf 参数中。"""
        cfg = _make_config(width=1280, height=720)
        queue = FrameQueue()
        ct = CameraThread(config=cfg, frame_queue=queue)
        cmd = ct._build_ffmpeg_command()

        vf_idx = cmd.index("-vf")
        assert cmd[vf_idx + 1] == "scale=1280:720"

    def test_stimeout_conversion_to_microseconds(self):
        """ffmpeg_timeout（秒）应转换为微秒并传入 -stimeout。"""
        cfg = _make_config(ffmpeg_timeout=15.0)
        queue = FrameQueue()
        ct = CameraThread(config=cfg, frame_queue=queue)
        cmd = ct._build_ffmpeg_command()

        assert "-stimeout" in cmd
        stimeout_idx = cmd.index("-stimeout")
        assert cmd[stimeout_idx + 1] == "15000000"  # 15 * 1_000_000

    def test_rtsp_url_passed_correctly(self):
        """RTSP URL 应正确传入 -i 参数。"""
        url = "rtsp://admin:pass@10.0.0.1:554/cam/realmonitor"
        cfg = _make_config(rtsp_url=url)
        queue = FrameQueue()
        ct = CameraThread(config=cfg, frame_queue=queue)
        cmd = ct._build_ffmpeg_command()

        i_idx = cmd.index("-i")
        assert cmd[i_idx + 1] == url


# ─── 7. CameraThread: 状态从 CONNECTING → CONNECTED ─────────


class TestStateTransition:
    @patch("vision_agent.core.camera.subprocess.Popen")
    def test_connecting_to_connected_transition(self, mock_popen):
        """start() 后状态应经过 CONNECTING 最终变为 CONNECTED。"""
        # 模拟 FFmpeg 进程：stdout.read 返回足够数据后触发流结束
        frame_size = 640 * 640 * 3
        fake_frame = b"\x00" * frame_size

        mock_proc = MagicMock()
        # 第一次 read 返回完整帧，第二次返回空数据（模拟流结束）
        mock_proc.stdout.read.side_effect = [fake_frame, b""]
        mock_popen.return_value = mock_proc

        cfg = _make_config()
        queue = FrameQueue(maxsize=50)
        ct = CameraThread(config=cfg, frame_queue=queue)

        # 初始状态应为 CONNECTING
        assert ct.status == CameraStatus.CONNECTING

        ct.start()

        # 等待线程进入 CONNECTED 状态并完成读帧
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if ct.status in (CameraStatus.CONNECTED, CameraStatus.DISCONNECTED):
                break
            time.sleep(0.05)

        # 至少应经历过 CONNECTED 状态（可能已因流结束而变为 DISCONNECTED）
        # 检查队列中是否有帧（证明曾进入 CONNECTED 状态并读取了帧）
        frame = queue.get(timeout=0.1)
        assert frame is not None, "应至少读取到一帧，证明曾进入 CONNECTED 状态"
        assert frame.frame_seq == 1

        ct.stop()
        assert ct.status == CameraStatus.DISCONNECTED

    @patch("vision_agent.core.camera.subprocess.Popen")
    def test_initial_status_is_connecting(self, mock_popen):
        """CameraThread 构造后 status 应为 CONNECTING。"""
        cfg = _make_config()
        queue = FrameQueue()
        ct = CameraThread(config=cfg, frame_queue=queue)
        assert ct.status == CameraStatus.CONNECTING

    @patch("vision_agent.core.camera.subprocess.Popen")
    def test_stop_sets_disconnected(self, mock_popen):
        """stop() 后 status 应为 DISCONNECTED。"""
        mock_proc = MagicMock()
        mock_proc.stdout.read.return_value = b"\x00" * (640 * 640 * 3)
        mock_popen.return_value = mock_proc

        cfg = _make_config()
        queue = FrameQueue()
        ct = CameraThread(config=cfg, frame_queue=queue)
        ct.start()
        time.sleep(0.3)
        ct.stop()
        assert ct.status == CameraStatus.DISCONNECTED

    @patch("vision_agent.core.camera.subprocess.Popen")
    def test_ffmpeg_error_sets_error_message(self, mock_popen):
        """FFmpeg 进程异常时，error_message 应被设置。"""
        mock_popen.side_effect = FileNotFoundError("ffmpeg not found")

        cfg = _make_config()
        queue = FrameQueue()
        ct = CameraThread(config=cfg, frame_queue=queue)
        ct.start()

        # 等待线程处理异常
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if ct._error_message:
                break
            time.sleep(0.05)

        assert ct._error_message != ""
        ct.stop()
