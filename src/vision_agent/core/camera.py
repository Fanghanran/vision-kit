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

from vision_agent.core.exceptions import CameraConnectionError, CameraStreamError
from vision_agent.core.types import CameraState, CameraStatus

logger = logging.getLogger(__name__)


# ─── 配置数据类 ──────────────────────────────────────────────


@dataclass
class CameraConfig:
    """摄像头配置（对应 cameras/cam_XX.yaml 的 camera 段）"""

    camera_id: str
    camera_name: str
    rtsp_url: str = ""
    source_type: str = "rtsp"  # rtsp / video / test
    video_path: str = ""  # source_type=video 时使用
    fps: float = 0.0  # 0 表示自动检测
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

        # 实时帧率（滑动窗口）
        self._fps_window_start = 0.0
        self._fps_window_count = 0
        self._realtime_fps = 0.0

        # 帧订阅者（用于 WebSocket 视频流推送）
        self._frame_subscribers: list[Queue[FrameData | None]] = []
        self._subscribers_lock = threading.Lock()

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
        # 通知所有订阅者结束
        with self._subscribers_lock:
            for q in self._frame_subscribers:
                try:
                    q.put_nowait(None)
                except Full:
                    pass
        self._terminate_ffmpeg()
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None
        self._status = CameraStatus.DISCONNECTED
        self._log.info("camera_stopped camera=%s", self._config.camera_id)

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def subscribe_frames(self, maxsize: int = 30) -> Queue[FrameData | None]:
        """订阅帧流，返回一个 Queue。None 表示结束信号。"""
        q: Queue[FrameData | None] = Queue(maxsize=maxsize)
        with self._subscribers_lock:
            self._frame_subscribers.append(q)
        return q

    def unsubscribe_frames(self, q: Queue[FrameData | None]) -> None:
        """取消帧订阅"""
        with self._subscribers_lock:
            if q in self._frame_subscribers:
                self._frame_subscribers.remove(q)

    def _push_to_subscribers(self, frame: FrameData) -> None:
        """将帧推送给所有订阅者，队列满则丢弃"""
        with self._subscribers_lock:
            for q in self._frame_subscribers:
                try:
                    q.put_nowait(frame)
                except Full:
                    pass

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
            total_detections=self._total_detections,
            total_alerts=self._total_alerts,
            uptime_seconds=elapsed,
            error_message=self._error_message,
        )

    @property
    def source_type(self) -> str:
        return self._config.source_type

    @property
    def rtsp_url(self) -> str:
        return self._config.rtsp_url

    @property
    def width(self) -> int:
        return self._config.width

    @property
    def height(self) -> int:
        return self._config.height

    # ─── 内部方法 ──────────────────────────────────────────────

    def _resolve_fps(self) -> float:
        """根据来源类型自动检测帧率"""
        fps = self._config.fps
        if fps > 0:
            return fps

        source_type = self._config.source_type
        if source_type == "test":
            return 25.0
        if source_type == "video":
            try:
                import cv2

                cap = cv2.VideoCapture(self._config.video_path)
                if cap.isOpened():
                    native_fps = cap.get(cv2.CAP_PROP_FPS)
                    cap.release()
                    if native_fps > 0:
                        return native_fps
            except Exception:
                pass
            return 25.0
        # rtsp: 无法提前检测，默认 15
        return 15.0

    def _run_loop(self) -> None:
        """主循环：连接 → 读帧 → 断线重连

        设计来源：camera.md 3.5 节
        - 指数退避：3s→6s→12s→24s→48s→60s
        - 连接成功后重置退避延迟
        - 连续 5 次失败暂停 10 分钟

        支持三种数据源（开发环境可用视频文件或测试图案替代摄像头）：
        - rtsp：FFmpeg 读取 RTSP 流
        - video：OpenCV 读取本地视频文件
        - test：生成带时间戳的测试图案
        """
        source_type = self._config.source_type

        # video/test 模式不需要重连逻辑
        if source_type == "video":
            self._run_video_loop()
            return
        if source_type == "test":
            self._run_test_loop()
            return

        # rtsp 模式：标准重连逻辑
        current_delay = self._config.reconnect_delay
        consecutive_failures = 0
        _MAX_CONSECUTIVE_FAILURES = 5
        _PAUSE_ON_REPEATED_FAILURE = 600.0  # 10 分钟

        while self._running:
            try:
                self._status = CameraStatus.CONNECTING
                self._error_message = ""
                self._frame_seq = 0
                self._total_frames = 0
                self._total_detections = 0
                self._total_alerts = 0
                self._start_time = time.time()
                self._fps_window_start = 0.0
                self._fps_window_count = 0
                self._realtime_fps = 0.0
                self._connect_and_read_frames()
                current_delay = self._config.reconnect_delay
                consecutive_failures = 0
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

    def _run_video_loop(self) -> None:
        """从本地视频文件读取帧（开发模式）"""
        video_path = self._config.video_path
        if not video_path:
            raise CameraConnectionError("video_path 未配置")

        try:
            import cv2
        except ImportError:
            raise CameraConnectionError("opencv-python 未安装，无法使用视频模式")

        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            raise CameraConnectionError(f"无法打开视频文件: {video_path}")

        self._status = CameraStatus.CONNECTED
        self._error_message = ""
        self._log.info("video_source camera=%s path=%s", self._config.camera_id, video_path)

        fps = self._resolve_fps()
        target_interval = 1.0 / fps

        try:
            while self._running:
                # 无订阅者时休眠，不读帧
                if not self._has_subscribers():
                    time.sleep(target_interval)
                    continue

                start_time = time.monotonic()
                ret, frame = cap.read()
                if not ret:
                    # 视频结束，循环播放
                    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    continue

                # 缩放到配置尺寸
                h, w = frame.shape[:2]
                if w != self._config.width or h != self._config.height:
                    frame = cv2.resize(frame, (self._config.width, self._config.height))

                self._frame_seq += 1
                self._total_frames += 1
                self._last_frame_time = time.time()
                self._update_fps_window()

                frame_data = FrameData(
                    camera_id=self._config.camera_id,
                    frame=frame,
                    timestamp=self._last_frame_time,
                    frame_seq=self._frame_seq,
                    width=self._config.width,
                    height=self._config.height,
                )
                self._frame_queue.put(frame_data)
                self._push_to_subscribers(frame_data)

                # 帧率控制
                elapsed = time.monotonic() - start_time
                if elapsed < target_interval:
                    time.sleep(target_interval - elapsed)
        finally:
            cap.release()

    def _has_subscribers(self) -> bool:
        """是否有活跃的帧订阅者（WebSocket 视频流）"""
        with self._subscribers_lock:
            return len(self._frame_subscribers) > 0

    def _run_test_loop(self) -> None:
        """生成测试图案帧（开发模式，没有订阅者时休眠省资源）"""
        self._status = CameraStatus.CONNECTED
        self._error_message = ""

        fps = self._resolve_fps()
        target_interval = 1.0 / fps
        self._log.info("test_source camera=%s fps=%.1f", self._config.camera_id, fps)

        while self._running:
            # 无订阅者时休眠，不生成帧
            if not self._has_subscribers():
                time.sleep(target_interval)
                continue

            start_time = time.monotonic()

            # 生成带时间戳的测试图案
            frame = self._generate_test_frame()

            self._frame_seq += 1
            self._total_frames += 1
            self._last_frame_time = time.time()
            self._update_fps_window()

            frame_data = FrameData(
                camera_id=self._config.camera_id,
                frame=frame,
                timestamp=self._last_frame_time,
                frame_seq=self._frame_seq,
                width=self._config.width,
                height=self._config.height,
            )
            self._frame_queue.put(frame_data)
            self._push_to_subscribers(frame_data)

            elapsed = time.monotonic() - start_time
            if elapsed < target_interval:
                time.sleep(target_interval - elapsed)

    def _generate_test_frame(self) -> np.ndarray:
        """生成测试图案帧（带颜色渐变和时间戳文字）"""
        w, h = self._config.width, self._config.height
        frame = np.zeros((h, w, 3), dtype=np.uint8)

        # 渐变背景
        t = time.time()
        r = int((np.sin(t * 0.5) + 1) * 127)
        g = int((np.sin(t * 0.3 + 2) + 1) * 127)
        b = int((np.sin(t * 0.7 + 4) + 1) * 127)
        frame[:, :] = [b, g, r]  # BGR

        # 网格线
        for x in range(0, w, 80):
            frame[:, x] = [200, 200, 200]
        for y in range(0, h, 80):
            frame[y, :] = [200, 200, 200]

        # 模拟目标（移动的矩形）
        box_x = int((np.sin(t * 0.8) + 1) * (w - 100) / 2)
        box_y = int((np.cos(t * 0.6) + 1) * (h - 100) / 2)
        frame[box_y : box_y + 100, box_x : box_x + 100] = [0, 0, 255]  # 红色方块

        return frame

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
        fps = self._resolve_fps()
        target_interval = 1.0 / fps

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
            self._update_fps_window()

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
            self._push_to_subscribers(frame_data)

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

    def _update_fps_window(self) -> None:
        """每收到一帧调用，滑动窗口计算实时帧率"""
        now = time.time()
        if self._fps_window_start == 0.0:
            self._fps_window_start = now
            self._fps_window_count = 1
            return
        self._fps_window_count += 1
        elapsed = now - self._fps_window_start
        if elapsed >= 1.0:
            self._realtime_fps = round(self._fps_window_count / elapsed, 1)
            self._fps_window_start = now
            self._fps_window_count = 0

    def _calculate_fps(self) -> float:
        """返回实时 FPS（基于最近 1 秒窗口）"""
        return self._realtime_fps
    def update_detection_count(self, count: int) -> None:
        """由 pipeline 调用，更新检测计数"""
        self._total_detections += count

    def update_alert_count(self, count: int = 1) -> None:
        """由 pipeline 调用，更新告警计数"""
        self._total_alerts += count
