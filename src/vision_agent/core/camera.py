"""
摄像头管理模块 — 通过 FFmpeg 子进程读取 RTSP 视频流

设计来源：docs/modules/core/camera.md

职责：
- 每路摄像头运行在独立线程中
- FFmpeg 子进程读取 RTSP 流，输出 rawvideo 到 stdout pipe
- 帧率控制（time.monotonic）
- 断线重连（指数退避 3s→6s→12s→24s→48s→60s）
- 有界帧队列（满则丢旧帧）
"""

from __future__ import annotations

import logging
import subprocess
import threading
import time
from dataclasses import dataclass
from queue import Empty, Full, Queue

import numpy as np

from vision_agent.core.exceptions import CameraStreamError
from vision_agent.core.types import CameraState, CameraStatus

logger = logging.getLogger(__name__)


# ─── 配置数据类 ──────────────────────────────────────────────


@dataclass
class CameraConfig:
    """摄像头配置（对应 cameras/cam_XX.yaml 的 camera 段）"""

    camera_id: str
    camera_name: str
    rtsp_url: str
    fps: float = 5.0
    width: int = 640
    height: int = 640
    reconnect_delay: float = 3.0
    reconnect_max_delay: float = 60.0
    reconnect_backoff: float = 2.0
    ffmpeg_timeout: float = 10.0
    use_gpu_decode: bool = False


# ─── 帧数据 ──────────────────────────────────────────────────


@dataclass
class FrameData:
    """从摄像头采集的一帧数据"""

    camera_id: str
    frame: np.ndarray  # BGR, shape (H, W, 3)
    timestamp: float
    frame_seq: int
    width: int = 0
    height: int = 0

    @property
    def frame_id(self) -> int:
        return self.frame_seq


# ─── 帧队列 ──────────────────────────────────────────────────


class FrameQueue:
    """有界帧队列，满则丢弃最旧帧

    设计决策（camera.md 3.4 节）：
    - 不使用 queue.put(block=True)，避免阻塞摄像头线程
    - 丢帧比延迟更可接受
    """

    def __init__(self, maxsize: int = 200):
        self._queue: Queue[FrameData] = Queue(maxsize=maxsize)
        self._maxsize = maxsize
        self._drop_count = 0

    def put(self, frame: FrameData) -> None:
        """放入帧，队列满时丢弃最旧帧"""
        if self._queue.full():
            try:
                self._queue.get_nowait()
                self._drop_count += 1
                if self._drop_count % 100 == 0:
                    logger.warning(
                        "frame_dropped camera=%s dropped_total=%d queue_size=%d",
                        frame.camera_id,
                        self._drop_count,
                        self._queue.qsize(),
                    )
            except Empty:
                pass
        try:
            self._queue.put_nowait(frame)
        except Full:
            pass

    def get(self, timeout: float = 1.0) -> FrameData | None:
        """获取帧，超时返回 None"""
        try:
            return self._queue.get(timeout=timeout)
        except Empty:
            return None

    def get_nowait(self) -> FrameData | None:
        try:
            return self._queue.get_nowait()
        except Empty:
            return None

    @property
    def size(self) -> int:
        return self._queue.qsize()

    @property
    def drop_count(self) -> int:
        return self._drop_count

    def clear(self) -> None:
        """清空队列"""
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except Exception:
                break


# ─── 摄像头采集线程 ──────────────────────────────────────────


class CameraThread:
    """摄像头采集线程

    设计来源：camera.md 2.1 节

    生命周期：__init__ → start → [运行中] → stop
    每路摄像头独立线程，断线自动重连（指数退避）
    """

    def __init__(
        self,
        config: CameraConfig,
        frame_queue: FrameQueue,
        cam_logger: logging.Logger | None = None,
    ):
        self._config = config
        self._frame_queue = frame_queue
        self._log = cam_logger or logger

        # 状态
        self._status = CameraStatus.CONNECTING
        self._error_message = ""
        self._frame_seq = 0
        self._total_frames = 0
        self._total_detections = 0  # 由 pipeline 更新
        self._total_alerts = 0  # 由 pipeline 更新
        self._start_time = 0.0
        self._last_frame_time = 0.0

        # 线程控制
        self._running = False
        self._thread: threading.Thread | None = None
        self._ffmpeg_process: subprocess.Popen | None = None
        self._lock = threading.Lock()

    # ─── 公开接口 ──────────────────────────────────────────────

    def start(self) -> None:
        """启动采集线程"""
        if self._thread and self._thread.is_alive():
            return
        self._running = True
        self._start_time = time.time()
        self._thread = threading.Thread(
            target=self._run_loop,
            name=f"camera-{self._config.camera_id}",
            daemon=True,
        )
        self._thread.start()
        self._log.info("camera_started camera=%s", self._config.camera_id)

    def stop(self) -> None:
        """停止采集线程，终止 FFmpeg 子进程"""
        self._running = False
        self._terminate_ffmpeg()
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None
        self._status = CameraStatus.DISCONNECTED
        self._log.info("camera_stopped camera=%s", self._config.camera_id)

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def camera_id(self) -> str:
        return self._config.camera_id

    @property
    def camera_name(self) -> str:
        return self._config.camera_name

    @property
    def status(self) -> CameraStatus:
        return self._status

    @property
    def camera_state(self) -> CameraState:
        """返回当前状态快照"""
        elapsed = time.time() - self._start_time if self._start_time else 0
        return CameraState(
            camera_id=self._config.camera_id,
            status=self._status,
            current_fps=self._calculate_fps(),
            gpu_latency_ms=0.0,
            queue_size=self._frame_queue.size,
            last_frame_time=self._last_frame_time,
            total_frames=self._total_frames,
            total_detections=self._total_detections,
            total_alerts=self._total_alerts,
            uptime_seconds=elapsed,
            error_message=self._error_message,
        )

    # ─── 内部方法 ──────────────────────────────────────────────

    def _run_loop(self) -> None:
        """主循环：连接 → 读帧 → 断线重连

        设计来源：camera.md 3.5 节
        - 指数退避：3s→6s→12s→24s→48s→60s
        - 连接成功后重置退避延迟
        - 连续 5 次失败暂停 10 分钟
        """
        current_delay = self._config.reconnect_delay
        consecutive_failures = 0
        _MAX_CONSECUTIVE_FAILURES = 5
        _PAUSE_ON_REPEATED_FAILURE = 600.0  # 10 分钟

        while self._running:
            try:
                self._status = CameraStatus.CONNECTING
                self._error_message = ""
                self._connect_and_read_frames()
                # 连接成功，重置退避延迟和失败计数
                current_delay = self._config.reconnect_delay
                consecutive_failures = 0
                # 正常退出（_running=False）时不重连
                if not self._running:
                    break
            except Exception as e:
                consecutive_failures += 1
                self._log.error(
                    "camera_error camera=%s error=%s consecutive_failures=%d",
                    self._config.camera_id,
                    str(e),
                    consecutive_failures,
                )
                self._error_message = str(e)
                self._status = CameraStatus.DISCONNECTED

            if not self._running:
                break

            # 连续失败暂停
            if consecutive_failures >= _MAX_CONSECUTIVE_FAILURES:
                self._log.error(
                    "camera_repeated_failure camera=%s failures=%d pause=%.0fs",
                    self._config.camera_id,
                    consecutive_failures,
                    _PAUSE_ON_REPEATED_FAILURE,
                )
                time.sleep(_PAUSE_ON_REPEATED_FAILURE)
                consecutive_failures = 0
                current_delay = self._config.reconnect_delay
                continue

            # 断线重连（指数退避）
            self._log.warning(
                "camera_reconnect camera=%s delay=%.1fs",
                self._config.camera_id,
                current_delay,
            )
            time.sleep(current_delay)
            current_delay = min(
                current_delay * self._config.reconnect_backoff,
                self._config.reconnect_max_delay,
            )

    def _connect_and_read_frames(self) -> None:
        """连接 FFmpeg 并读取帧"""
        cmd = self._build_ffmpeg_command()
        self._log.info(
            "ffmpeg_connect camera=%s cmd=%s", self._config.camera_id, " ".join(cmd)
        )

        self._ffmpeg_process = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,  # 丢弃 stderr 防止缓冲区满导致死锁
            close_fds=True,  # 防止 Windows 文件描述符泄漏
        )

        # 等待第一帧（超时判定连接失败）
        self._status = CameraStatus.CONNECTED
        self._error_message = ""

        frame_size = self._config.width * self._config.height * 3
        target_interval = 1.0 / self._config.fps

        while self._running:
            start_time = time.monotonic()

            # 读取一帧原始数据
            buffer = self._ffmpeg_process.stdout.read(frame_size)  # type: ignore
            if len(buffer) < frame_size:
                # 记录 FFmpeg 退出码
                returncode = self._ffmpeg_process.returncode
                raise CameraStreamError(
                    f"stream_ended camera={self._config.camera_id} "
                    f"expected={frame_size} got={len(buffer)} "
                    f"ffmpeg_exit_code={returncode}",
                    context={"camera_id": self._config.camera_id, "returncode": returncode},
                )

            # 解码为 numpy 数组（失败跳过该帧，不断开连接）
            try:
                frame = np.frombuffer(buffer, dtype=np.uint8).reshape(
                    (self._config.height, self._config.width, 3)
                )
            except ValueError as e:
                self._log.warning(
                    "frame_decode_error camera=%s seq=%d error=%s",
                    self._config.camera_id,
                    self._frame_seq,
                    str(e),
                )
                continue

            # 构造 FrameData
            self._frame_seq += 1
            self._total_frames += 1
            self._last_frame_time = time.time()

            frame_data = FrameData(
                camera_id=self._config.camera_id,
                frame=frame,
                timestamp=self._last_frame_time,
                frame_seq=self._frame_seq,
                width=self._config.width,
                height=self._config.height,
            )

            # 放入队列
            self._frame_queue.put(frame_data)

            # 帧率控制
            elapsed = time.monotonic() - start_time
            if elapsed < target_interval:
                time.sleep(target_interval - elapsed)
            elif elapsed > target_interval * 2:
                self._log.warning(
                    "frame_slow camera=%s elapsed=%.3fs target=%.3fs",
                    self._config.camera_id,
                    elapsed,
                    target_interval,
                )

    def _build_ffmpeg_command(self) -> list[str]:
        """构造 FFmpeg 命令行

        设计来源：camera.md 3.2 节
        RTSP → rawvideo (BGR24) → stdout pipe
        """
        timeout_us = int(self._config.ffmpeg_timeout * 1_000_000)
        cmd = [
            "ffmpeg",
            "-rtsp_transport",
            "tcp",
            "-stimeout",
            str(timeout_us),
            "-i",
            self._config.rtsp_url,
            "-f",
            "rawvideo",
            "-pix_fmt",
            "bgr24",
            "-vf",
            f"scale={self._config.width}:{self._config.height}",
            "-an",
            "-",
        ]
        return cmd

    def _terminate_ffmpeg(self) -> None:
        """终止 FFmpeg 子进程"""
        if self._ffmpeg_process is None:
            return
        try:
            self._ffmpeg_process.terminate()
            self._ffmpeg_process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self._ffmpeg_process.kill()
        except Exception:
            pass
        finally:
            self._ffmpeg_process = None

    def _calculate_fps(self) -> float:
        """计算实际 FPS（基于最近的帧间隔）"""
        if self._total_frames < 2 or not self._start_time:
            return 0.0
        elapsed = time.time() - self._start_time
        if elapsed <= 0:
            return 0.0
        return self._total_frames / elapsed

    def update_detection_count(self, count: int) -> None:
        """由 pipeline 调用，更新检测计数"""
        self._total_detections += count

    def update_alert_count(self, count: int = 1) -> None:
        """由 pipeline 调用，更新告警计数"""
        self._total_alerts += count
