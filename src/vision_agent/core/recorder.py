"""
录制器模块 — 环形缓冲录制 + 告警片段截取

设计来源：docs/modules/core/recorder.md

职责：
- 每路摄像头维护 deque 环形帧缓冲
- 告警触发时异步截取前后 N 秒视频片段
- FFmpeg 转码为 H.264 MP4
- 截图保存
- 过期文件自动清理
"""

from __future__ import annotations

import logging
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

import numpy as np

logger = logging.getLogger(__name__)


# ─── 配置数据类 ──────────────────────────────────────────────


@dataclass
class RecorderConfig:
    """录制器配置（对应 settings.yaml 的 recording 段）"""

    enabled: bool = True
    buffer_duration: float = 30.0
    default_before: float = 15.0
    default_after: float = 15.0
    output_dir: str = "data/clips"
    snapshot_dir: str = "data/snapshots"
    video_format: str = "mp4"
    video_codec: str = "libx264"
    video_crf: int = 23
    video_preset: str = "fast"
    max_clip_workers: int = 2
    retention_days: int = 7
    snapshot_retention_days: int = 30
    max_disk_gb: float = 50.0
    fps: float = 5.0


@dataclass
class BufferedFrame:
    """环形缓冲中的一帧"""

    frame: np.ndarray
    timestamp: float
    frame_id: int


@dataclass
class BufferStats:
    """缓冲区统计信息"""

    camera_id: str
    buffer_size: int
    buffer_duration: float
    memory_mb: float
    oldest_timestamp: float
    newest_timestamp: float


# ─── 录制器 ──────────────────────────────────────────────────


def _sanitize_id(camera_id: str) -> str:
    """清理 camera_id，防止路径穿越"""
    return "".join(c for c in camera_id if c.isalnum() or c in "-_")


class ClipRecorder:
    """视频片段录制器（recorder.md 2.1 节）

    每路摄像头维护一个 deque 环形缓冲。
    告警触发时异步截取前后 N 秒视频片段。
    """

    def __init__(
        self,
        config: RecorderConfig,
        fps: float = 5.0,
        rec_logger: logging.Logger | None = None,
    ):
        self._config = config
        self._fps = fps if fps > 0 else config.fps
        self._log = rec_logger or logger
        self._buffers: dict[str, deque[BufferedFrame]] = {}
        self._frame_counters: dict[str, int] = {}
        self._lock = threading.Lock()
        self._executor = threading.Semaphore(config.max_clip_workers)

        # 创建输出目录
        Path(config.output_dir).mkdir(parents=True, exist_ok=True)
        Path(config.snapshot_dir).mkdir(parents=True, exist_ok=True)

    # ─── 公开接口 ──────────────────────────────────────────────

    def hot_update_config(self, updates: dict[str, Any]) -> None:
        """运行时更新配置（热加载用，强制类型转换）"""
        for key, value in updates.items():
            if hasattr(self._config, key):
                old = getattr(self._config, key)
                try:
                    setattr(self._config, key, type(old)(value))
                except (ValueError, TypeError):
                    logger.warning(
                        "hot_update_config_invalid_type key=%s value=%s expected=%s",
                        key, value, type(old).__name__,
                    )

    def push_frame(self, camera_id: str, frame: np.ndarray, timestamp: float) -> None:
        """将帧推入指定摄像头的环形缓冲"""
        if not self._config.enabled:
            return

        with self._lock:
            if camera_id not in self._buffers:
                maxsize = int(self._config.buffer_duration * self._fps) + 20
                self._buffers[camera_id] = deque(maxlen=maxsize)
                self._frame_counters[camera_id] = 0

            self._frame_counters[camera_id] += 1
            self._buffers[camera_id].append(
                BufferedFrame(
                    frame=frame.copy(),  # 防止外部修改
                    timestamp=timestamp,
                    frame_id=self._frame_counters[camera_id],
                )
            )

    def save_clip(
        self,
        camera_id: str,
        trigger_time: float,
        before_seconds: float | None = None,
        after_seconds: float | None = None,
        callback: Callable[[str], None] | None = None,
    ) -> None:
        """异步截取并保存视频片段（recorder.md 3.2 节）

        截取 trigger_time 前后 N 秒的帧，异步转码为 MP4。
        """
        if not self._config.enabled:
            return

        before = (
            before_seconds
            if before_seconds is not None
            else self._config.default_before
        )
        after = (
            after_seconds if after_seconds is not None else self._config.default_after
        )

        # 在后台线程中执行
        thread = threading.Thread(
            target=self._save_clip_worker,
            args=(camera_id, trigger_time, before, after, callback),
            daemon=True,
            name=f"clip-{camera_id}",
        )
        thread.start()

    def save_snapshot(self, camera_id: str, frame: np.ndarray, timestamp: float) -> str:
        """保存单帧截图，返回文件路径"""
        date_str = datetime.fromtimestamp(timestamp).strftime("%Y/%m/%d")
        dir_path = Path(self._config.snapshot_dir) / date_str
        dir_path.mkdir(parents=True, exist_ok=True)

        filename = f"{camera_id}_{int(timestamp)}.jpg"
        file_path = dir_path / filename

        try:
            import cv2

            cv2.imwrite(str(file_path), frame)
            logger.info("snapshot_saved camera=%s path=%s", camera_id, file_path)
        except Exception as e:
            logger.error("snapshot_failed camera=%s error=%s", camera_id, str(e))
            return ""

        return str(file_path)

    def cleanup_expired(self) -> int:
        """清理过期文件（recorder.md 3.5 节）"""
        deleted = 0
        now = time.time()

        for directory, retention_days in [
            (self._config.output_dir, self._config.retention_days),
            (self._config.snapshot_dir, self._config.snapshot_retention_days),
        ]:
            cutoff = now - retention_days * 86400
            dir_path = Path(directory)
            if not dir_path.exists():
                continue

            for file_path in dir_path.rglob("*"):
                if file_path.is_file() and file_path.stat().st_mtime < cutoff:
                    try:
                        file_path.unlink()
                        deleted += 1
                    except Exception as e:
                        logger.warning("cleanup_failed path=%s error=%s", file_path, e)

        if deleted > 0:
            logger.info("cleanup_done deleted=%d", deleted)
        return deleted

    def get_buffer_stats(self, camera_id: str) -> BufferStats | None:
        """获取缓冲区统计信息"""
        with self._lock:
            if camera_id not in self._buffers:
                return None

            buf = self._buffers[camera_id]
            if not buf:
                return BufferStats(
                    camera_id=camera_id,
                    buffer_size=0,
                    buffer_duration=0.0,
                    memory_mb=0.0,
                    oldest_timestamp=0.0,
                    newest_timestamp=0.0,
                )

            oldest = buf[0].timestamp
            newest = buf[-1].timestamp
            # 估算内存：每帧 640*640*3 bytes
            frame_bytes = buf[0].frame.nbytes if len(buf) > 0 else 0
            memory_mb = len(buf) * frame_bytes / (1024 * 1024)

            return BufferStats(
                camera_id=camera_id,
                buffer_size=len(buf),
                buffer_duration=newest - oldest,
                memory_mb=memory_mb,
                oldest_timestamp=oldest,
                newest_timestamp=newest,
            )

    def release(self) -> None:
        """释放所有缓冲区"""
        with self._lock:
            self._buffers.clear()
            self._frame_counters.clear()
        logger.info("recorder_released")

    # ─── 内部方法 ──────────────────────────────────────────────

    def _save_clip_worker(
        self,
        camera_id: str,
        trigger_time: float,
        before_seconds: float,
        after_seconds: float,
        callback: Callable[[str], None] | None,
    ) -> None:
        """后台线程：截取帧并转码为 MP4"""
        self._executor.acquire()
        try:
            # 提取 before 帧
            before_frames = self._extract_frames(
                camera_id, trigger_time - before_seconds, trigger_time
            )

            # 等待 after 秒，收集 after 帧
            if after_seconds > 0:
                time.sleep(after_seconds)
            after_frames = self._extract_frames(
                camera_id, trigger_time, trigger_time + after_seconds
            )

            frames = before_frames + after_frames
            if not frames:
                logger.warning("clip_no_frames camera=%s", camera_id)
                return

            # 生成输出路径
            date_str = datetime.fromtimestamp(trigger_time).strftime("%Y/%m/%d")
            dir_path = Path(self._config.output_dir) / date_str
            dir_path.mkdir(parents=True, exist_ok=True)
            filename = f"{camera_id}_{int(trigger_time)}.{self._config.video_format}"
            output_path = dir_path / filename

            # 写入临时 rawvideo 文件
            self._write_rawvideo(frames, output_path)

            logger.info(
                "clip_saved camera=%s frames=%d path=%s",
                camera_id,
                len(frames),
                output_path,
            )

            if callback:
                callback(str(output_path))

        except Exception as e:
            logger.error("clip_failed camera=%s error=%s", camera_id, str(e))
        finally:
            self._executor.release()

    def _extract_frames(
        self,
        camera_id: str,
        start_time: float,
        end_time: float,
    ) -> list[np.ndarray]:
        """从缓冲区提取指定时间范围的帧"""
        with self._lock:
            if camera_id not in self._buffers:
                return []

            frames = []
            for bf in self._buffers[camera_id]:
                if start_time <= bf.timestamp <= end_time:
                    frames.append(bf.frame)

        return frames

    def _write_rawvideo(self, frames: list[np.ndarray], output_path: Path) -> None:
        """用 FFmpeg 将帧列表转码为 MP4"""
        if not frames:
            return

        h, w = frames[0].shape[:2]
        cmd = [
            "ffmpeg",
            "-y",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "bgr24",
            "-s",
            f"{w}x{h}",
            "-r",
            str(self._fps),
            "-i",
            "pipe:0",
            "-c:v",
            self._config.video_codec,
            "-crf",
            str(self._config.video_crf),
            "-preset",
            self._config.video_preset,
            "-pix_fmt",
            "yuv420p",
            str(output_path),
        ]

        proc = None
        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                close_fds=True,
            )
            for frame in frames:
                if proc.stdin:
                    proc.stdin.write(frame.tobytes())
            if proc.stdin:
                proc.stdin.close()
            proc.wait(timeout=60)
        except subprocess.TimeoutExpired:
            logger.error("ffmpeg_timeout path=%s timeout=60s", output_path)
            if proc:
                proc.kill()
        except Exception as e:
            logger.error("ffmpeg_encode_failed path=%s error=%s", output_path, e)
            if proc:
                if proc.stdin:
                    proc.stdin.close()
                proc.kill()
