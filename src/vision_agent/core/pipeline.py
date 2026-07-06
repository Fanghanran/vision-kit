"""
主处理管线模块 — 三层线程架构编排器

设计来源：docs/modules/core/pipeline.md

职责：
- 三层线程模型：采集层（CameraThread×N）→ 推理层（InferenceThread×1）→ 处理层（ActionThread×1）
- 组装所有组件（检测器、追踪器、录制器、规则引擎、LLM、通知器、数据库）
- 有界队列管理（满则丢旧帧/结果，保障实时性）
- 优雅关闭（上游→下游，数据不丢失）
- 健康检查、线程异常恢复、热重载

设计决策（来自 pipeline.md 第 7 节）：
- 采集层 IO 密集，推理层 GPU 密集（单线程避免 GPU 竞争），处理层顺序处理简化状态
- 队列满则丢旧帧 —— 延迟比丢帧更不可接受
- 关闭顺序上游→下游 —— 先停上游确保不再产生新数据，再等队列排空
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Protocol, runtime_checkable

from queue import Empty, Full, Queue

import numpy as np

from vision_agent.core.camera import CameraConfig, CameraThread, FrameData, FrameQueue
from vision_agent.core.detector import DetectorProtocol
from vision_agent.core.recorder import ClipRecorder, RecorderConfig
from vision_agent.core.tracker import TrackerConfig, TrackerManager
from vision_agent.core.types import (
    CameraState,
    CameraStatus,
    Detection,
    Event,
    Track,
)

logger = logging.getLogger(__name__)


# ─── 异常 ────────────────────────────────────────────────────


class StartupError(Exception):
    """系统启动失败"""


class ConfigError(Exception):
    """配置校验失败"""


# ─── 未实现模块的 Protocol 桩 ────────────────────────────────
# 这些接口在对应模块实现后会被替换，pipeline 通过 Protocol 解耦


@runtime_checkable
class RuleEngineProtocol(Protocol):
    """规则引擎接口（rules/engine.py 实现）"""

    def evaluate(
        self,
        camera_id: str,
        tracks: list[Track],
        frame: np.ndarray,
        timestamp: float,
    ) -> list[Event]: ...


@runtime_checkable
class LLMAnalyzerProtocol(Protocol):
    """LLM 分析器接口（llm/analyzer.py 实现）"""

    def analyze(self, event: Event, snapshot_path: str) -> Any: ...

    @property
    def success_rate(self) -> float: ...


@runtime_checkable
class NotifierProtocol(Protocol):
    """通知器接口（actions/notifier.py 实现）"""

    def execute(self, event: Event, snapshot_path: str) -> bool: ...


@runtime_checkable
class DatabaseProtocol(Protocol):
    """数据库接口（storage/database.py 实现）"""

    def save_alert(self, event: Event, **kwargs: Any) -> str: ...

    def count_alerts_today(self) -> int: ...

    def close(self) -> None: ...


# ─── 数据类 ──────────────────────────────────────────────────


class SystemStatus(str, Enum):
    """系统运行状态"""

    STARTING = "starting"
    RUNNING = "running"
    DEGRADED = "degraded"
    SHUTTING_DOWN = "shutting_down"
    STOPPED = "stopped"


@dataclass
class InferenceResult:
    """推理层输出给处理层的结果（pipeline.md 2.4 节）"""

    camera_id: str
    frame: np.ndarray
    frame_id: int
    timestamp: float
    detections: list[Detection] = field(default_factory=list)
    tracks: list[Track] = field(default_factory=list)
    inference_latency_ms: float = 0.0


@dataclass
class HealthResponse:
    """健康检查数据（pipeline.md 2.5 节）"""

    status: str = "ok"
    uptime_seconds: float = 0.0
    gpu_utilization: float = 0.0
    gpu_memory_used_mb: float = 0.0
    gpu_memory_total_mb: float = 0.0
    queue_depth: int = 0
    inference_latency_p50_ms: float = 0.0
    inference_latency_p99_ms: float = 0.0
    active_cameras: int = 0
    total_cameras: int = 0
    today_alerts: int = 0
    llm_success_rate: float = 1.0


@dataclass
class AlertStats:
    """告警统计"""

    total: int = 0
    pending: int = 0
    acknowledged: int = 0
    rejected: int = 0
    resolved: int = 0
    today_count: int = 0


@dataclass
class PipelineConfig:
    """Pipeline 配置（pipeline.md 5.2 节）"""

    frame_queue_size: int = 200
    result_queue_size: int = 100
    shutdown_timeout: float = 30.0
    frame_drain_timeout: float = 3.0
    health_check_interval: float = 5.0
    thread_restart_max: int = 3


@dataclass
class CameraConfigItem:
    """单路摄像头配置（用于 VisionAgent 初始化）"""

    camera_config: CameraConfig
    fps: float = 5.0


# ─── ResultQueue ─────────────────────────────────────────────


class ResultQueue:
    """有界推理结果队列，满则丢弃最旧结果

    设计来源：pipeline.md 2.3 节
    与 FrameQueue 逻辑一致 —— 满则丢旧，保障实时性。
    """

    def __init__(self, maxsize: int = 100, name: str = "result"):
        self._queue: Queue[InferenceResult] = Queue(maxsize=maxsize)
        self._maxsize = maxsize
        self._name = name
        self._drop_count = 0

    def put(self, result: InferenceResult) -> None:
        if self._queue.full():
            try:
                self._queue.get_nowait()
                self._drop_count += 1
                if self._drop_count % 100 == 0:
                    logger.warning(
                        "result_dropped name=%s dropped_total=%d queue_size=%d",
                        self._name,
                        self._drop_count,
                        self._queue.qsize(),
                    )
            except Empty:
                pass
        try:
            self._queue.put_nowait(result)
        except Full:
            pass

    def get(self, timeout: float = 1.0) -> InferenceResult | None:
        try:
            return self._queue.get(timeout=timeout)
        except Empty:
            return None

    @property
    def size(self) -> int:
        return self._queue.qsize()

    def clear(self) -> None:
        while True:
            try:
                self._queue.get_nowait()
            except Empty:
                break


# ─── 延迟统计 ────────────────────────────────────────────────


class LatencyTracker:
    """滑动窗口延迟统计（P50/P99）"""

    def __init__(self, window_size: int = 1000):
        self._window: deque[float] = deque(maxlen=window_size)
        self._lock = threading.Lock()

    def record(self, latency_ms: float) -> None:
        with self._lock:
            self._window.append(latency_ms)

    def percentile(self, p: float) -> float:
        with self._lock:
            if not self._window:
                return 0.0
            sorted_vals = sorted(self._window)
            idx = int(len(sorted_vals) * p / 100)
            idx = min(idx, len(sorted_vals) - 1)
            return sorted_vals[idx]

    @property
    def p50(self) -> float:
        return self.percentile(50)

    @property
    def p99(self) -> float:
        return self.percentile(99)

    @property
    def count(self) -> int:
        return len(self._window)


# ─── 推理线程 ────────────────────────────────────────────────


class InferenceThread:
    """推理层线程（pipeline.md 3.4 节）

    从 FrameQueue 批量取帧 → YOLO 检测 → BoT-SORT 追踪 → 输出到 ResultQueue。
    同时将帧推送到录制器的环形缓冲。
    """

    def __init__(
        self,
        frame_queue: FrameQueue,
        detector: DetectorProtocol,
        tracker_manager: TrackerManager,
        result_queue: ResultQueue,
        recorder: ClipRecorder,
        batch_size: int = 8,
        batch_timeout_ms: int = 50,
    ):
        self._frame_queue = frame_queue
        self._detector = detector
        self._tracker_manager = tracker_manager
        self._result_queue = result_queue
        self._recorder = recorder
        self._batch_size = batch_size
        self._batch_timeout_s = batch_timeout_ms / 1000.0

        self._running = False
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

        # 统计
        self._total_inferences = 0
        self._total_frames = 0
        self._consecutive_failures: dict[str, int] = {}
        self._latency_tracker = LatencyTracker()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._run_loop,
            name="inference",
            daemon=True,
        )
        self._thread.start()
        logger.info("inference_thread_started")

    def stop(self, timeout: float = 5.0) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=timeout)
            self._thread = None
        logger.info("inference_thread_stopped")

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def p50_latency(self) -> float:
        return self._latency_tracker.p50

    @property
    def p99_latency(self) -> float:
        return self._latency_tracker.p99

    @property
    def total_frames(self) -> int:
        return self._total_frames

    @property
    def total_inferences(self) -> int:
        return self._total_inferences

    def _run_loop(self) -> None:
        while self._running:
            try:
                self._process_batch()
            except Exception as e:
                logger.error("inference_loop_error error=%s", str(e), exc_info=True)
                time.sleep(0.1)

    def _process_batch(self) -> None:
        # 1. 批量收集帧
        batch: list[FrameData] = []
        deadline = time.monotonic() + self._batch_timeout_s

        while len(batch) < self._batch_size and self._running:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            frame_data = self._frame_queue.get(timeout=min(remaining, 0.1))
            if frame_data is not None:
                batch.append(frame_data)
            elif batch:
                # 已有帧但取不到更多，开始处理
                break

        if not batch:
            return

        # 2. 提取纯帧列表
        frames = [bf.frame for bf in batch]
        start_time = time.monotonic()

        # 3. 检测推理
        try:
            detections_list = self._detector.detect(frames)
        except Exception as e:
            logger.error("detect_error error=%s", str(e))
            detections_list = [[] for _ in frames]
            for bf in batch:
                count = self._consecutive_failures.get(bf.camera_id, 0) + 1
                self._consecutive_failures[bf.camera_id] = count

        inference_ms = (time.monotonic() - start_time) * 1000
        self._total_inferences += 1
        self._latency_tracker.record(inference_ms)

        # 4. 追踪更新 + 构造结果 + 推入结果队列
        for bf, detections in zip(batch, detections_list):
            tracks = self._tracker_manager.update(bf.camera_id, detections, bf.frame)
            result = InferenceResult(
                camera_id=bf.camera_id,
                frame=bf.frame,
                frame_id=bf.frame_id,
                timestamp=bf.timestamp,
                detections=detections,
                tracks=tracks,
                inference_latency_ms=inference_ms,
            )
            self._result_queue.put(result)

        # 5. 帧推送到录制器
        for bf in batch:
            self._recorder.push_frame(bf.camera_id, bf.frame, bf.timestamp)

        self._total_frames += len(batch)


# ─── 处理线程 ────────────────────────────────────────────────


class ActionThread:
    """处理层线程（pipeline.md 3.5 节）

    从 ResultQueue 取出推理结果 → 规则引擎评估 → 告警 → 截图/录像 → LLM → 通知 → 存储。
    """

    def __init__(
        self,
        result_queue: ResultQueue,
        rule_engine: RuleEngineProtocol,
        recorder: ClipRecorder,
        llm_analyzer: LLMAnalyzerProtocol | None = None,
        notifiers: list[NotifierProtocol] | None = None,
        database: DatabaseProtocol | None = None,
        on_alert: Callable[[Event], None] | None = None,
    ):
        self._result_queue = result_queue
        self._rule_engine = rule_engine
        self._recorder = recorder
        self._llm_analyzer = llm_analyzer
        self._notifiers = notifiers or []
        self._database = database
        self._on_alert = on_alert

        self._running = False
        self._thread: threading.Thread | None = None

        # 统计
        self._total_processed = 0
        self._total_events = 0
        self._total_alerts = 0
        self._llm_calls = 0
        self._llm_successes = 0

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._run_loop,
            name="action",
            daemon=True,
        )
        self._thread.start()
        logger.info("action_thread_started")

    def stop(self, timeout: float = 10.0) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=timeout)
            self._thread = None
        logger.info("action_thread_stopped")

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def total_processed(self) -> int:
        return self._total_processed

    @property
    def total_events(self) -> int:
        return self._total_events

    @property
    def llm_success_rate(self) -> float:
        if self._llm_calls == 0:
            return 1.0
        return self._llm_successes / self._llm_calls

    def _run_loop(self) -> None:
        while self._running:
            try:
                result = self._result_queue.get(timeout=1.0)
                if result is None:
                    continue
                self._process_result(result)
            except Exception as e:
                logger.error("action_loop_error error=%s", str(e), exc_info=True)

    def _process_result(self, result: InferenceResult) -> None:
        self._total_processed += 1

        # 1. 规则引擎评估
        try:
            events = self._rule_engine.evaluate(
                camera_id=result.camera_id,
                tracks=result.tracks,
                frame=result.frame,
                timestamp=result.timestamp,
            )
        except Exception as e:
            logger.error("rule_eval_error camera=%s error=%s", result.camera_id, str(e))
            return

        # 2. 对每个触发的事件生成告警
        for event in events:
            self._total_events += 1
            self._handle_event(event, result)

    def _handle_event(self, event: Event, result: InferenceResult) -> None:
        # 保存截图
        snapshot_path = self._recorder.save_snapshot(
            result.camera_id, result.frame, result.timestamp
        )
        event.snapshot_path = snapshot_path

        # 截取视频片段（异步）
        self._recorder.save_clip(
            camera_id=result.camera_id,
            trigger_time=result.timestamp,
        )

        # 写入数据库
        if self._database:
            try:
                self._database.save_alert(event)
            except Exception as e:
                logger.error("db_save_error event=%s error=%s", event.event_id, str(e))

        # LLM 分析（同步调用，未来可改为异步）
        if self._llm_analyzer:
            self._llm_calls += 1
            try:
                self._llm_analyzer.analyze(event, snapshot_path)
                self._llm_successes += 1
            except Exception as e:
                logger.error(
                    "llm_analyze_error event=%s error=%s", event.event_id, str(e)
                )

        # 通知
        for notifier in self._notifiers:
            try:
                notifier.execute(event, snapshot_path)
            except Exception as e:
                logger.error("notify_error event=%s error=%s", event.event_id, str(e))

        self._total_alerts += 1
        logger.info(
            "alert_generated event=%s camera=%s type=%s severity=%s",
            event.event_id,
            event.camera_id,
            event.event_type,
            event.severity.value,
        )

        # 回调（供外部 Web 推送等）
        if self._on_alert:
            try:
                self._on_alert(event)
            except Exception as e:
                logger.warning(
                    "alert_callback_error event=%s error=%s", event.event_id, str(e)
                )


# ─── 定时任务 ────────────────────────────────────────────────


class TimerTask:
    """定时执行回调的守护线程"""

    def __init__(
        self, interval: float, callback: Callable[[], Any], name: str = "timer"
    ):
        self._interval = interval
        self._callback = callback
        self._name = name
        self._running = False
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._run_loop, name=self._name, daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None

    def _run_loop(self) -> None:
        while self._running:
            time.sleep(self._interval)
            if not self._running:
                break
            try:
                self._callback()
            except Exception as e:
                logger.error("timer_error name=%s error=%s", self._name, str(e))


# ─── VisionAgent 主类 ────────────────────────────────────────


class VisionAgent:
    """系统主入口（pipeline.md 2.1 节）

    编排三层线程：采集→推理→处理。
    管理所有组件的生命周期：组装→启动→运行→关闭。
    """

    def __init__(
        self,
        camera_configs: list[CameraConfigItem],
        detector: DetectorProtocol,
        tracker_config: TrackerConfig,
        recorder_config: RecorderConfig,
        pipeline_config: PipelineConfig | None = None,
        rule_engine: RuleEngineProtocol | None = None,
        llm_analyzer: LLMAnalyzerProtocol | None = None,
        notifiers: list[NotifierProtocol] | None = None,
        database: DatabaseProtocol | None = None,
        on_alert: Callable[[Event], None] | None = None,
    ):
        """组装所有组件（pipeline.md 3.1 节）

        Args:
            camera_configs: 摄像头配置列表
            detector: 检测器实例
            tracker_config: 追踪器配置
            recorder_config: 录制器配置
            pipeline_config: Pipeline 配置（可选，有默认值）
            rule_engine: 规则引擎（可选，None 时跳过规则评估）
            llm_analyzer: LLM 分析器（可选）
            notifiers: 通知器列表（可选）
            database: 数据库（可选）
            on_alert: 告警回调（可选，供 WebSocket 推送等）
        """
        self._pipeline_config = pipeline_config or PipelineConfig()
        self._status = SystemStatus.STOPPED
        self._start_time = 0.0
        self._lock = threading.Lock()

        # 队列
        self._frame_queue = FrameQueue(maxsize=self._pipeline_config.frame_queue_size)
        self._result_queue = ResultQueue(
            maxsize=self._pipeline_config.result_queue_size
        )

        # 检测器
        self._detector = detector

        # 追踪器
        self._tracker_manager = TrackerManager(tracker_config)

        # 录制器
        self._recorder = ClipRecorder(
            config=recorder_config,
            fps=camera_configs[0].fps if camera_configs else 5.0,
        )

        # 规则引擎
        self._rule_engine = rule_engine

        # LLM 分析器
        self._llm_analyzer = llm_analyzer

        # 通知器
        self._notifiers = notifiers or []

        # 数据库
        self._database = database

        # 摄像头线程
        self._camera_items: list[CameraConfigItem] = camera_configs
        self._camera_threads: list[CameraThread] = []
        for item in camera_configs:
            thread = CameraThread(item.camera_config, self._frame_queue)
            self._camera_threads.append(thread)

        # 推理线程
        self._inference_thread = InferenceThread(
            frame_queue=self._frame_queue,
            detector=self._detector,
            tracker_manager=self._tracker_manager,
            result_queue=self._result_queue,
            recorder=self._recorder,
            batch_size=getattr(detector.config, "batch_size", 8)
            if hasattr(detector, "config")
            else 8,
        )

        # 处理线程
        self._action_thread = ActionThread(
            result_queue=self._result_queue,
            rule_engine=self._rule_engine,
            recorder=self._recorder,
            llm_analyzer=self._llm_analyzer,
            notifiers=self._notifiers,
            database=self._database,
            on_alert=on_alert,
        )

        # 定时清理
        self._cleanup_timer = TimerTask(
            interval=3600,
            callback=self._recorder.cleanup_expired,
            name="cleanup",
        )

        # 健康监控线程
        self._monitor_thread: threading.Thread | None = None

        # 线程重启计数
        self._inference_restart_count = 0
        self._action_restart_count = 0

        # 统计
        self._total_alerts = 0
        self._status_callbacks: list[Callable[[SystemStatus], None]] = []

        logger.info(
            "vision_agent_init cameras=%d rule_engine=%s llm=%s notifiers=%d database=%s",
            len(camera_configs),
            "yes" if rule_engine else "no",
            "yes" if llm_analyzer else "no",
            len(self._notifiers),
            "yes" if database else "no",
        )

    # ─── 生命周期 ──────────────────────────────────────────────

    def start(self) -> None:
        """启动所有组件和线程（pipeline.md 3.2 节）

        启动顺序（下游先启动）：处理线程 → 推理线程 → 摄像头线程 → 定时任务
        """
        if self._status in (SystemStatus.RUNNING, SystemStatus.STARTING):
            logger.warning("already_running")
            return

        self._set_status(SystemStatus.STARTING)
        self._start_time = time.time()
        logger.info("system_starting cameras=%d", len(self._camera_threads))

        # 1. 启动处理线程
        self._action_thread.start()

        # 2. 启动推理线程
        self._inference_thread.start()

        # 3. 启动所有摄像头线程
        for thread in self._camera_threads:
            thread.start()

        # 4. 启动定时清理
        self._cleanup_timer.start()

        # 5. 启动健康监控
        self._start_monitor()

        self._set_status(SystemStatus.RUNNING)
        logger.info(
            "system_started cameras=%d uptime_start",
            len(self._camera_threads),
        )

    def stop(self) -> None:
        """优雅关闭所有组件（pipeline.md 3.6 节）

        关闭顺序上游→下游：摄像头 → 推理 → 处理 → 定时 → 检测器 → 录制器
        """
        with self._lock:
            if self._status in (SystemStatus.STOPPED, SystemStatus.SHUTTING_DOWN):
                return
            self._status = SystemStatus.SHUTTING_DOWN

        self._set_status(SystemStatus.SHUTTING_DOWN)
        logger.info("system_stopping")
        timeout = self._pipeline_config.shutdown_timeout
        deadline = time.monotonic() + timeout

        # 1. 停止摄像头线程
        for thread in self._camera_threads:
            thread.stop()

        # 等待 frame_queue 排空
        drain_deadline = time.monotonic() + self._pipeline_config.frame_drain_timeout
        while self._frame_queue.size > 0 and time.monotonic() < drain_deadline:
            time.sleep(0.1)
        self._frame_queue.clear()

        # 2. 停止推理线程
        remaining = max(0.1, deadline - time.monotonic())
        self._inference_thread.stop(timeout=remaining)
        self._result_queue.clear()

        # 3. 停止处理线程
        remaining = max(0.1, deadline - time.monotonic())
        self._action_thread.stop(timeout=remaining)

        # 4. 停止定时任务和监控
        self._cleanup_timer.stop()
        self._stop_monitor()

        # 5. 释放检测器资源
        try:
            self._detector.release()
        except Exception as e:
            logger.error("detector_release_error error=%s", str(e))

        # 6. 关闭数据库
        if self._database:
            try:
                self._database.close()
            except Exception as e:
                logger.error("db_close_error error=%s", str(e))

        # 7. 释放录制器
        self._recorder.release()

        self._set_status(SystemStatus.STOPPED)
        logger.info("system_stopped")

    @property
    def status(self) -> SystemStatus:
        return self._status

    @property
    def uptime_seconds(self) -> float:
        if self._start_time <= 0:
            return 0.0
        return time.time() - self._start_time

    # ─── 健康检查 ──────────────────────────────────────────────

    def health(self) -> HealthResponse:
        """返回健康检查数据（pipeline.md 3.7 节）"""
        active = sum(
            1 for t in self._camera_threads if t.status == CameraStatus.CONNECTED
        )
        total = len(self._camera_threads)
        queue_depth = self._frame_queue.size

        # GPU 状态
        gpu_util, gpu_mem_used, gpu_mem_total = self._get_gpu_stats()

        # 推理延迟
        p50 = self._inference_thread.p50_latency
        p99 = self._inference_thread.p99_latency

        # 今日告警
        today_alerts = 0
        if self._database:
            try:
                today_alerts = self._database.count_alerts_today()
            except Exception:
                pass

        # LLM 成功率
        llm_rate = self._action_thread.llm_success_rate

        # 健康判定（pipeline.md 3.7 节）
        if total == 0 or (active == 0 and total > 0):
            health_status = "unhealthy"
        elif queue_depth > 100 or p99 > 100:
            health_status = "degraded"
        else:
            health_status = "ok"

        return HealthResponse(
            status=health_status,
            uptime_seconds=self.uptime_seconds,
            gpu_utilization=gpu_util,
            gpu_memory_used_mb=gpu_mem_used,
            gpu_memory_total_mb=gpu_mem_total,
            queue_depth=queue_depth,
            inference_latency_p50_ms=p50,
            inference_latency_p99_ms=p99,
            active_cameras=active,
            total_cameras=total,
            today_alerts=today_alerts,
            llm_success_rate=llm_rate,
        )

    # ─── 摄像头状态 ────────────────────────────────────────────

    def get_camera_states(self) -> dict[str, CameraState]:
        """获取所有摄像头的运行状态快照"""
        states: dict[str, CameraState] = {}
        for thread in self._camera_threads:
            state = thread.camera_state
            # 更新由 pipeline 维护的计数
            state.total_alerts = self._total_alerts
            states[thread.camera_id] = state
        return states

    def get_alert_stats(self) -> AlertStats:
        """获取告警统计"""
        today_count = 0
        if self._database:
            try:
                today_count = self._database.count_alerts_today()
            except Exception:
                pass
        return AlertStats(
            total=self._action_thread.total_events,
            today_count=today_count,
        )

    # ─── 动态管理 ──────────────────────────────────────────────

    def add_camera(self, camera_config: CameraConfig, fps: float = 5.0) -> None:
        """动态添加一路摄像头（pipeline.md 2.1 节）"""
        with self._lock:
            thread = CameraThread(camera_config, self._frame_queue)
            self._camera_threads.append(thread)
            self._camera_items.append(CameraConfigItem(camera_config, fps))
            if self._status == SystemStatus.RUNNING:
                thread.start()
            logger.info("camera_added camera=%s", camera_config.camera_id)

    def remove_camera(self, camera_id: str) -> None:
        """动态移除一路摄像头"""
        thread_to_stop = None
        with self._lock:
            for i, thread in enumerate(self._camera_threads):
                if thread.camera_id == camera_id:
                    thread_to_stop = thread
                    self._camera_threads.pop(i)
                    if i < len(self._camera_items):
                        self._camera_items.pop(i)
                    break
        if thread_to_stop:
            thread_to_stop.stop()
            self._tracker_manager.remove_tracker(camera_id)
            logger.info("camera_removed camera=%s", camera_id)
        else:
            logger.warning("camera_not_found camera=%s", camera_id)

    def reload_camera(self, camera_config: CameraConfig, fps: float = 5.0) -> None:
        """重载指定摄像头配置（pipeline.md 3.8 节）

        停止旧线程 → 创建新线程 → 启动 → 重置追踪器。
        """
        camera_id = camera_config.camera_id
        old_thread = None
        new_thread = None
        with self._lock:
            for i, thread in enumerate(self._camera_threads):
                if thread.camera_id == camera_id:
                    old_thread = thread
                    new_thread = CameraThread(camera_config, self._frame_queue)
                    self._camera_threads[i] = new_thread
                    if i < len(self._camera_items):
                        self._camera_items[i] = CameraConfigItem(camera_config, fps)
                    break
        if old_thread and new_thread:
            old_thread.stop()
            if self._status == SystemStatus.RUNNING:
                new_thread.start()
            self._tracker_manager.reset(camera_id)
            logger.info("camera_reloaded camera=%s", camera_id)
        else:
            logger.warning("camera_not_found camera=%s", camera_id)

    def reload_rules(self) -> None:
        """重载规则引擎（pipeline.md 3.8 节）

        由规则引擎自身负责重新加载规则文件。
        如果加载失败，旧规则保持不变。
        """
        if self._rule_engine is None:
            logger.warning("rule_engine_not_configured")
            return
        if hasattr(self._rule_engine, "reload"):
            try:
                self._rule_engine.reload()
            except Exception as e:
                logger.error("rule_reload_failed error=%s", str(e))
                return
        logger.info("rules_reloaded")

    # ─── 内部方法 ──────────────────────────────────────────────

    def _set_status(self, status: SystemStatus) -> None:
        with self._lock:
            self._status = status
            callbacks = list(self._status_callbacks)
        for cb in callbacks:
            try:
                cb(status)
            except Exception as e:
                logger.warning(
                    "status_callback_error status=%s error=%s", status.value, str(e)
                )

    def _start_monitor(self) -> None:
        """启动健康监控线程，周期性检查子线程存活"""
        if self._monitor_thread and self._monitor_thread.is_alive():
            return
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop, name="monitor", daemon=True
        )
        self._monitor_thread.start()

    def _stop_monitor(self) -> None:
        if self._monitor_thread:
            self._monitor_thread.join(timeout=3)
            self._monitor_thread = None

    def _monitor_loop(self) -> None:
        """每 5 秒检查线程存活，异常时尝试重启（pipeline.md 6.2 节）"""
        interval = self._pipeline_config.health_check_interval
        max_restarts = self._pipeline_config.thread_restart_max

        while self._status in (SystemStatus.RUNNING, SystemStatus.DEGRADED):
            time.sleep(interval)
            if self._status not in (SystemStatus.RUNNING, SystemStatus.DEGRADED):
                break

            # 检查推理线程
            if (
                self._status != SystemStatus.SHUTTING_DOWN
                and not self._inference_thread.is_alive()
            ):
                if self._inference_restart_count < max_restarts:
                    self._inference_restart_count += 1
                    logger.warning(
                        "inference_thread_died restart=%d/%d",
                        self._inference_restart_count,
                        max_restarts,
                    )
                    time.sleep(2)
                    self._inference_thread.start()
                else:
                    logger.error(
                        "inference_thread_failed permanently restarts=%d",
                        self._inference_restart_count,
                    )
                    self._set_status(SystemStatus.DEGRADED)

            # 检查处理线程
            if (
                self._status != SystemStatus.SHUTTING_DOWN
                and not self._action_thread.is_alive()
            ):
                if self._action_restart_count < max_restarts:
                    self._action_restart_count += 1
                    logger.warning(
                        "action_thread_died restart=%d/%d",
                        self._action_restart_count,
                        max_restarts,
                    )
                    time.sleep(2)
                    self._action_thread.start()
                else:
                    logger.error(
                        "action_thread_failed permanently restarts=%d",
                        self._action_restart_count,
                    )
                    self._set_status(SystemStatus.DEGRADED)

    @staticmethod
    def _get_gpu_stats() -> tuple[float, float, float]:
        """获取 GPU 状态（utilization%, used_mb, total_mb）"""
        try:
            import torch

            if torch.cuda.is_available():
                util = torch.cuda.utilization()
                mem_used = torch.cuda.memory_allocated() / (1024 * 1024)
                mem_total = torch.cuda.get_device_properties(0).total_memory / (
                    1024 * 1024
                )
                return float(util), mem_used, mem_total
        except Exception:
            pass
        return 0.0, 0.0, 0.0
