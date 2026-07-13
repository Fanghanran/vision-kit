"""
Web API 模块 — HTTP REST API + WebSocket 实时推送

设计来源：docs/modules/web/web_api.md

职责：
- REST 端点：摄像头状态、告警列表/详情、统计、配置查看
- 告警管理：状态更新（确认/标记误报/解决）
- WebSocket 实时推送：新告警、状态变化、系统状态
- 安全：路径白名单、Bearer Token 认证、日志脱敏

设计决策：
- 路径白名单（/api/, /ws, /health）
- Token 常量时间比较（hmac.compare_digest）
- 截图/视频通过 API 间接访问（不暴露文件目录）
"""

from __future__ import annotations

from vision_agent.core.types import CameraStatus

import hmac
import logging
import re
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ─── 日志脱敏 Filter ─────────────────────────────────────────


class SanitizeFilter(logging.Filter):
    """日志脱敏过滤器（web_api.md 3.8 节）"""

    _PATTERNS = [
        (
            re.compile(
                r"(password|api_key|token|secret|credential)=\S+", re.IGNORECASE
            ),
            r"\1=***",
        ),
        (re.compile(r"rtsp://([^:]+):([^@]+)@"), r"rtsp://\1:***@"),
        (re.compile(r"Bearer\s+\S+", re.IGNORECASE), "Bearer ***"),
    ]

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            for pattern, replacement in self._PATTERNS:
                record.msg = pattern.sub(replacement, record.msg)
        return True


# ─── 配置脱敏 ────────────────────────────────────────────────

_SENSITIVE_KEYS = {"password", "api_key", "token", "secret", "credential"}
_RTSP_PATTERN = re.compile(r"(rtsp://[^:]+):([^@]+)@")


def sanitize_config(config: dict[str, Any]) -> dict[str, Any]:
    """递归脱敏配置字典（web_api.md 3.9 节）"""
    result = {}
    for key, value in config.items():
        if isinstance(value, dict):
            result[key] = sanitize_config(value)
        elif isinstance(value, list):
            result[key] = [
                sanitize_config(v) if isinstance(v, dict) else v for v in value
            ]
        elif any(kw in key.lower() for kw in _SENSITIVE_KEYS):
            result[key] = "***"
        elif isinstance(value, str):
            result[key] = _RTSP_PATTERN.sub(r"\1:***@", value)
        else:
            result[key] = value
    return result


# ─── Token 认证 ──────────────────────────────────────────────


def verify_token(token: str | None, api_token: str) -> bool:
    """常量时间 Token 比较（web_api.md 3.3 节）"""
    if not api_token:
        return True  # 未配置 Token 时跳过认证（开发模式）
    if not token:
        return False
    return hmac.compare_digest(str(token), api_token)


# ─── FastAPI 应用工厂 ────────────────────────────────────────


def create_app(
    database: Any = None,
    camera_manager: Any = None,
    pipeline: Any = None,
    config: dict[str, Any] | None = None,
) -> Any:
    """创建 FastAPI 应用实例

    Args:
        database: DatabaseManager 实例
        camera_manager: 摄像头管理器（pipeline 或 CameraThread 列表）
        pipeline: VisionAgent 实例
        config: web 配置段

    Returns:
        FastAPI 应用实例
    """
    try:
        from fastapi import (
            Body,
            FastAPI,
            HTTPException,
            Query,
            Request,
            WebSocket,
            WebSocketDisconnect,
            Depends,
        )
        from fastapi.middleware.cors import CORSMiddleware
        from fastapi.responses import FileResponse, JSONResponse
    except ImportError:
        logger.error("fastapi_not_installed action=skip_web")
        return None

    # Inject types into module globals so FastAPI can resolve the
    # stringified annotations (caused by `from __future__ import annotations`).
    import sys

    _module = sys.modules[__name__]
    _module.Request = Request  # type: ignore[attr-defined]
    _module.WebSocket = WebSocket  # type: ignore[attr-defined]

    config = config or {}
    cors_origins = config.get("cors_origins", ["http://localhost:3000"])

    app = FastAPI(
        title="Vision Agent", version="1.0.0", description="多路视频智能分析框架"
    )

    # CORS 中间件
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ─── 路径白名单中间件 ────────────────────────────────────

    _ALLOWED_PREFIXES = ("/api/", "/ws", "/health", "/static/")

    from starlette.types import ASGIApp, Receive, Scope, Send

    class PathWhitelistMiddleware:
        """ASGI 中间件：路径白名单（兼容 WebSocket）"""

        def __init__(self, app: ASGIApp) -> None:
            self.app = app

        async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
            # WebSocket 和 lifespan 直接放行
            if scope["type"] != "http":
                await self.app(scope, receive, send)
                return
            path = scope.get("path", "")
            if not any(path.startswith(p) for p in _ALLOWED_PREFIXES):
                response = JSONResponse(status_code=404, content={"detail": "Not Found"})
                await response(scope, receive, send)
                return
            await self.app(scope, receive, send)

    app.add_middleware(PathWhitelistMiddleware)

    # ─── 健康检查 ────────────────────────────────────────────

    @app.get("/health",
        tags=["系统"],
        summary="系统健康检查",
        description="返回系统运行状态：GPU 使用率、推理延迟 P50/P99、在线摄像头数量、今日告警数等。",
        responses={200: {"description": "正常运行"}, 503: {"description": "系统不健康"}},
    )
    async def health() -> Any:
        if pipeline:
            h = pipeline.health()
            status_code = 200 if h.status != "unhealthy" else 503
            return JSONResponse(
                status_code=status_code,
                content={
                    "status": h.status,
                    "uptime_seconds": h.uptime_seconds,
                    "gpu_utilization": h.gpu_utilization,
                    "gpu_memory_used_mb": h.gpu_memory_used_mb,
                    "gpu_memory_total_mb": h.gpu_memory_total_mb,
                    "queue_depth": h.queue_depth,
                    "inference_latency_p50_ms": round(h.inference_latency_p50_ms, 2),
                    "inference_latency_p99_ms": round(h.inference_latency_p99_ms, 2),
                    "active_cameras": h.active_cameras,
                    "total_cameras": h.total_cameras,
                    "today_alerts": h.today_alerts,
                    "llm_success_rate": h.llm_success_rate,
                },
            )
        return {"status": "ok", "uptime_seconds": 0}

    # ─── 摄像头状态 ──────────────────────────────────────────

    @app.get("/api/cameras",
        tags=["摄像头"],
        summary="摄像头状态列表",
        description="返回所有摄像头的实时状态：连接状态、FPS、告警数、队列深度等。",
    )
    async def list_cameras() -> Any:
        if not pipeline:
            return []
        states = pipeline.get_camera_states()
        return [s.to_dict() for s in states.values()]

    _ID_PATTERN = re.compile(r"^[\w\-]+$")

    def _save_camera_yaml(camera_id: str, body: dict[str, Any]) -> None:
        """将摄像头配置写入 configs/cameras.yaml（新格式：camera + detector 分节）"""
        from pathlib import Path

        try:
            import yaml
        except ImportError:
            return

        cam_file = Path("configs/cameras.yaml")
        import os as _os

        # 读取现有配置
        existing: dict[str, Any] = {"cameras": {}}
        if cam_file.exists():
            try:
                loaded = yaml.safe_load(cam_file.read_text(encoding="utf-8")) or {}
                existing = loaded if isinstance(loaded, dict) else {"cameras": {}}
            except Exception:
                pass

        # 保持已有的 detector 段（如果有）
        cameras = existing.get("cameras", {})
        prev = cameras.get(camera_id, {})
        prev_detector = prev.get("detector", {}) if isinstance(prev, dict) else {}

        # camera 段
        resolution = body.get("resolution", [640, 640])
        cam_data: dict[str, Any] = {
            "id": camera_id,
            "name": body.get("name", camera_id),
            "source_type": body.get("source_type", "rtsp"),
            "rtsp_url": body.get("rtsp_url", ""),
            "video_path": body.get("video_path", ""),
            "fps": body.get("fps", 0),
            "resolution": resolution,
        }

        # detector 段（可选）
        detector_data = body.get("detector")
        if isinstance(detector_data, dict):
            merged_detector = {**prev_detector, **detector_data}
        else:
            merged_detector = prev_detector

        entry: dict[str, Any] = {"camera": cam_data}
        if merged_detector:
            entry["detector"] = merged_detector

        existing.setdefault("cameras", {})[camera_id] = entry

        output = yaml.dump(existing, allow_unicode=True, default_flow_style=False, sort_keys=False)
        tmp = cam_file.with_suffix(".tmp")
        tmp.write_text(output, encoding="utf-8")
        _os.replace(str(tmp), str(cam_file))
        logger.info("camera_yaml_saved camera=%s file=%s", camera_id, cam_file)

    def _delete_camera_yaml(camera_id: str) -> None:
        """从 configs/cameras.yaml 中删除摄像头（原子写入）"""
        from pathlib import Path

        import os as _os

        try:
            import yaml
        except ImportError:
            return

        cam_file = Path("configs/cameras.yaml")
        if not cam_file.exists():
            return

        try:
            existing = yaml.safe_load(cam_file.read_text(encoding="utf-8")) or {}
        except Exception:
            return

        if isinstance(existing, dict) and "cameras" in existing:
            existing["cameras"].pop(camera_id, None)
            output = yaml.dump(existing, allow_unicode=True, default_flow_style=False, sort_keys=False)
            tmp = cam_file.with_suffix(".tmp")
            tmp.write_text(output, encoding="utf-8")
            _os.replace(str(tmp), str(cam_file))
            logger.info("camera_yaml_deleted camera=%s file=%s", camera_id, cam_file)

    @app.post("/api/cameras/{camera_id}/toggle",
        tags=["摄像头"],
        summary="开关摄像头",
        description="在线则停止采集，离线则启动采集。",
        responses={200: {"description": "操作成功"}, 404: {"description": "摄像头不存在"}},
    )
    async def toggle_camera(camera_id: str) -> Any:
        if not _ID_PATTERN.match(camera_id):
            raise HTTPException(status_code=400, detail="摄像头ID不合法")
        if not pipeline:
            raise HTTPException(status_code=404, detail="系统未启动")

        cam_thread = pipeline.get_camera_thread(camera_id)
        if not cam_thread:
            raise HTTPException(status_code=404, detail=f"摄像头 {camera_id} 不存在")

        if cam_thread.is_alive():
            cam_thread.stop()
            return {"camera_id": camera_id, "action": "stopped", "status": "disconnected"}
        else:
            cam_thread.start()
            return {"camera_id": camera_id, "action": "started", "status": "connecting"}

    @app.post("/api/cameras",
        tags=["摄像头"],
        summary="添加摄像头",
        description="添加一路新摄像头。source_type 支持 rtsp/video/test，fps=0 自动检测。配置自动持久化到 configs/cameras/。",
        responses={200: {"description": "创建成功"}, 400: {"description": "参数错误"}, 409: {"description": "摄像头已存在"}},
    )
    async def create_camera(body: dict[str, Any] = Body(...)) -> Any:
        if not pipeline:
            raise HTTPException(status_code=404, detail="系统未启动")

        from vision_agent.core.camera import CameraConfig

        camera_id = body.get("id", "")
        if not camera_id:
            raise HTTPException(status_code=400, detail="缺少摄像头ID")
        if not _ID_PATTERN.match(camera_id):
            raise HTTPException(status_code=400, detail="ID 格式不合法（仅允许字母、数字、-、_）")

        # 检查是否已存在
        if pipeline.get_camera_thread(camera_id):
            raise HTTPException(status_code=409, detail=f"摄像头 {camera_id} 已存在")

        resolution = body.get("resolution", [640, 640])
        width = max(320, min(resolution[0] if resolution else 640, 4096))
        height = max(240, min(resolution[1] if len(resolution) > 1 else 640, 4096))
        cam_config = CameraConfig(
            camera_id=camera_id,
            camera_name=body.get("name", camera_id),
            rtsp_url=body.get("rtsp_url", ""),
            source_type=body.get("source_type", "rtsp"),
            video_path=body.get("video_path", ""),
            fps=body.get("fps", 0),
            width=width,
            height=height,
        )
        pipeline.add_camera(cam_config, fps=body.get("fps", 0))

        # 持久化到 YAML 文件
        _save_camera_yaml(camera_id, body)

        return {"camera_id": camera_id, "action": "created"}

    @app.put("/api/cameras/{camera_id}",
        tags=["摄像头"],
        summary="更新摄像头配置",
        description="修改摄像头参数（来源/帧率/分辨率等），停旧线程→改配置→启新线程→写YAML持久化。",
        responses={200: {"description": "更新成功"}, 404: {"description": "摄像头不存在"}},
    )
    async def update_camera(camera_id: str, body: dict[str, Any] = Body(...)) -> Any:
        if not _ID_PATTERN.match(camera_id):
            raise HTTPException(status_code=400, detail="摄像头ID不合法")
        if not pipeline:
            raise HTTPException(status_code=404, detail="系统未启动")

        cam_thread = pipeline.get_camera_thread(camera_id)
        if not cam_thread:
            raise HTTPException(status_code=404, detail=f"摄像头 {camera_id} 不存在")

        from vision_agent.core.camera import CameraConfig

        # 先停旧线程
        was_alive = cam_thread.is_alive()
        if was_alive:
            cam_thread.stop()

        # 用新参数重建 CameraConfig
        resolution = body.get("resolution", [640, 640])
        width = max(320, min(resolution[0] if resolution else 640, 4096))
        height = max(240, min(resolution[1] if len(resolution) > 1 else 640, 4096))
        new_config = CameraConfig(
            camera_id=camera_id,
            camera_name=body.get("name", cam_thread.camera_name),
            rtsp_url=body.get("rtsp_url", ""),
            source_type=body.get("source_type", "rtsp"),
            video_path=body.get("video_path", ""),
            fps=body.get("fps", 0),
            width=width,
            height=height,
        )

        # 重载摄像头
        fps = body.get("fps", 0)
        pipeline.reload_camera(new_config, fps)

        # 持久化
        _save_camera_yaml(camera_id, body)

        action = "reloaded_and_started" if was_alive else "reloaded"
        return {"camera_id": camera_id, "action": action}

    @app.delete("/api/cameras/{camera_id}",
        tags=["摄像头"],
        summary="删除摄像头",
        description="停止摄像头并删除内存线程和 YAML 配置文件。",
        responses={200: {"description": "删除成功"}, 404: {"description": "摄像头不存在"}},
    )
    async def delete_camera(camera_id: str) -> Any:
        if not _ID_PATTERN.match(camera_id):
            raise HTTPException(status_code=400, detail="摄像头ID不合法")
        if not pipeline:
            raise HTTPException(status_code=404, detail="系统未启动")

        cam_thread = pipeline.get_camera_thread(camera_id)
        if not cam_thread:
            raise HTTPException(status_code=404, detail=f"摄像头 {camera_id} 不存在")

        pipeline.remove_camera(camera_id)

        # 删除对应的 YAML 文件
        _delete_camera_yaml(camera_id)

        return {"camera_id": camera_id, "action": "deleted"}

    @app.get("/api/cameras/stats",
        tags=["摄像头"],
        summary="摄像头统计",
        description="返回在线/离线/告警中/总计数量。",
    )
    async def camera_stats() -> Any:
        if not pipeline:
            return {"total": 0, "online": 0, "offline": 0, "alerting": 0}
        states = pipeline.get_camera_states()
        total = len(states)
        online = sum(1 for s in states.values() if s.status == CameraStatus.CONNECTED)
        offline = sum(1 for s in states.values() if s.status in (CameraStatus.DISCONNECTED, CameraStatus.ERROR))
        alerting = sum(1 for s in states.values() if s.total_alerts > 0)
        return {"total": total, "online": online, "offline": offline, "alerting": alerting}

    @app.get("/api/cameras/{camera_id}",
        tags=["摄像头"],
        summary="摄像头详情",
        description="返回单个摄像头的完整信息：配置 + 运行指标。",
        responses={200: {"description": "成功"}, 404: {"description": "摄像头不存在"}},
    )
    async def get_camera_detail(camera_id: str) -> Any:
        if not _ID_PATTERN.match(camera_id):
            raise HTTPException(status_code=400, detail="摄像头ID不合法")
        if not pipeline:
            raise HTTPException(status_code=404, detail="系统未启动")
        cam_thread = pipeline.get_camera_thread(camera_id)
        if not cam_thread:
            raise HTTPException(status_code=404, detail=f"摄像头 {camera_id} 不存在")
        state = cam_thread.camera_state
        return {
            "camera_id": state.camera_id,
            "camera_name": cam_thread.camera_name,
            "status": state.status.value,
            "source_type": cam_thread.source_type,
            "fps": state.current_fps,
            "queue_size": state.queue_size,
            "total_detections": state.total_detections,
            "total_alerts": state.total_alerts,
            "uptime_seconds": state.uptime_seconds,
            "error_message": state.error_message,
            "rtsp_url": _RTSP_PATTERN.sub(r"\1:***@", cam_thread.rtsp_url),
            "resolution": [cam_thread.width, cam_thread.height],
        }

    # ─── 告警列表 ────────────────────────────────────────────

    @app.get("/api/alerts",
        tags=["告警"],
        summary="告警列表（分页+筛选）",
        description="分页查询告警，支持按状态/严重级别/摄像头/事件类型/时间范围筛选。",
    )
    async def list_alerts(
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
        status: str | None = None,
        camera_id: str | None = None,
        event_type: str | None = None,
        severity: str | None = None,
        start_time: float | None = None,
        end_time: float | None = None,
    ) -> Any:
        if not database:
            return {"items": [], "total": 0, "page": page, "page_size": page_size}

        filters: dict[str, Any] = {}
        if status:
            filters["status"] = status
        if camera_id:
            filters["camera_id"] = camera_id
        if event_type:
            filters["event_type"] = event_type
        if severity:
            filters["severity"] = severity
        if start_time:
            filters["start_time"] = start_time
        if end_time:
            filters["end_time"] = end_time

        alerts, total = database.list_alerts(filters, page, page_size)
        items = []
        for a in alerts:
            items.append(
                {
                    "alert_id": a.alert_id,
                    "event_type": a.event.event_type,
                    "camera_id": a.event.camera_id,
                    "camera_name": a.event.camera_name,
                    "severity": a.event.severity.value,
                    "status": a.status.value,
                    "risk_level": a.llm_analysis.risk_level if a.llm_analysis else None,
                    "created_at": a.created_at,
                }
            )

        return {"items": items, "total": total, "page": page, "page_size": page_size}

    # ─── 告警详情 ────────────────────────────────────────────

    @app.get("/api/alerts/{alert_id}",
        tags=["告警"],
        summary="告警详情",
        description="获取单个告警的完整信息，包括 LLM 分析结果、截图和视频片段路径。",
        responses={200: {"description": "成功"}, 404: {"description": "告警不存在"}},
    )
    async def get_alert(alert_id: str) -> Any:
        if not database:
            raise HTTPException(status_code=404, detail="告警不存在")
        alert = database.get_alert(alert_id)
        if not alert:
            raise HTTPException(status_code=404, detail="告警不存在")
        # 扁平化响应，与列表端点一致
        event = alert.event
        return {
            "alert_id": alert.alert_id,
            "event_type": event.event_type,
            "camera_id": event.camera_id,
            "camera_name": event.camera_name,
            "severity": event.severity.value,
            "status": alert.status.value,
            "risk_level": alert.llm_analysis.risk_level if alert.llm_analysis else None,
            "created_at": alert.created_at,
            "llm_analysis": alert.llm_analysis.to_dict() if alert.llm_analysis else None,
            "snapshot_path": event.snapshot_path,
            "video_clip_path": alert.video_clip_path,
            "notified_channels": alert.notified_channels,
            "acknowledged_at": alert.acknowledged_at,
            "acknowledged_by": alert.acknowledged_by,
        }

    # ─── 告警截图 ────────────────────────────────────────────

    @app.get("/api/alerts/{alert_id}/snapshot",
        tags=["告警"],
        summary="告警截图",
        description="返回告警关联的 JPEG 截图文件。",
        responses={200: {"description": "JPEG 图片"}, 404: {"description": "截图不存在"}},
    )
    async def get_snapshot(alert_id: str) -> Any:
        if not database:
            raise HTTPException(status_code=404, detail="告警不存在")
        alert = database.get_alert(alert_id)
        if not alert or not alert.event.snapshot_path:
            raise HTTPException(status_code=404, detail="截图不存在")
        path = Path(alert.event.snapshot_path)
        if not path.exists():
            raise HTTPException(status_code=404, detail="截图不存在")
        return FileResponse(str(path), media_type="image/jpeg")

    # ─── 告警视频 ────────────────────────────────────────────

    @app.get("/api/alerts/{alert_id}/clip",
        tags=["告警"],
        summary="告警视频片段",
        description="返回告警关联的 MP4 视频片段，可通过 download 参数控制浏览器下载或内联播放。",
        responses={200: {"description": "MP4 视频"}, 404: {"description": "视频片段不存在"}},
    )
    async def get_clip(
        alert_id: str, download: bool = False
    ) -> Any:
        if not database:
            raise HTTPException(status_code=404, detail="告警不存在")
        alert = database.get_alert(alert_id)
        if not alert or not alert.video_clip_path:
            raise HTTPException(status_code=404, detail="视频片段不存在")
        path = Path(alert.video_clip_path)
        if not path.exists():
            raise HTTPException(status_code=404, detail="视频片段不存在")
        headers = {"Content-Disposition": "attachment"} if download else {}
        return FileResponse(str(path), media_type="video/mp4", headers=headers)

    # ─── 告警状态更新 ────────────────────────────────────────

    _VALID_TRANSITIONS = {
        "pending": {"acknowledged", "rejected"},
        "acknowledged": {"resolved"},
    }

    @app.put("/api/alerts/{alert_id}/status",
        tags=["告警"],
        summary="更新告警状态",
        description="更新告警状态（pending→acknowledged→resolved，或 pending→rejected）。操作后通过 WebSocket 广播。",
        responses={200: {"description": "更新成功"}, 400: {"description": "状态流转不合法"}, 404: {"description": "告警不存在"}},
    )
    async def update_alert_status(
        alert_id: str,
        body: dict[str, Any] = Body(...),
    ) -> Any:
        if not database:
            raise HTTPException(status_code=404, detail="告警不存在")

        new_status = body.get("status")
        acknowledged_by = body.get("acknowledged_by", "")

        if not new_status:
            raise HTTPException(status_code=400, detail="缺少状态字段")

        alert = database.get_alert(alert_id)
        if not alert:
            raise HTTPException(status_code=404, detail="告警不存在")

        current = alert.status.value
        allowed = _VALID_TRANSITIONS.get(current, set())
        if new_status not in allowed:
            raise HTTPException(
                status_code=400,
                detail=f"不能从 {current} 转为 {new_status}",
            )

        updates: dict[str, Any] = {"status": new_status}
        if new_status == "acknowledged":
            updates["acknowledged_at"] = time.time()
            updates["acknowledged_by"] = acknowledged_by
        elif new_status == "rejected":
            updates["rejected_at"] = time.time()
            updates["acknowledged_by"] = acknowledged_by

        database.update_alert(alert_id, updates)

        # WebSocket 广播
        await ws_manager.broadcast(
            {
                "type": "alert_status",
                "alert_id": alert_id,
                "new_status": new_status,
                "updated_by": acknowledged_by,
            }
        )

        updated = database.get_alert(alert_id)
        if not updated:
            return {"alert_id": alert_id, "status": new_status}
        ev = updated.event
        return {
            "alert_id": updated.alert_id,
            "event_type": ev.event_type,
            "camera_id": ev.camera_id,
            "camera_name": ev.camera_name,
            "severity": ev.severity.value,
            "status": updated.status.value,
            "risk_level": updated.llm_analysis.risk_level if updated.llm_analysis else None,
            "created_at": updated.created_at,
        }

    # ─── 统计 ────────────────────────────────────────────────

    @app.get("/api/stats",
        tags=["系统"],
        summary="统计概览",
        description="返回指定周期的告警统计：总数、按严重级别/状态/摄像头分布、系统运行时长。period 支持 today/7d/30d。",
    )
    async def get_stats(
        period: str = "today",
    ) -> Any:
        if not database:
            return {"period": period, "total_alerts": 0}

        now = time.time()
        if period == "today":
            start = now - (now % 86400)
        elif period == "7d":
            start = now - 7 * 86400
        elif period == "30d":
            start = now - 30 * 86400
        else:
            start = now - 86400

        stats = database.get_stats({"start_time": start, "end_time": now})
        return {
            "period": period,
            "total_alerts": stats.get("total_count", 0),
            "alerts_by_type": {},
            "alerts_by_severity": stats.get("by_severity", {}),
            "alerts_by_camera": {},
            "alerts_by_status": stats.get("by_status", {}),
            "active_cameras": pipeline.health().active_cameras if pipeline else 0,
            "system_uptime_hours": pipeline.uptime_seconds / 3600 if pipeline else 0,
        }

    # ─── 配置查看 ────────────────────────────────────────────

    @app.get("/api/config",
        tags=["系统"],
        summary="系统配置（脱敏）",
        description="返回当前全局配置的快照，敏感字段（密码/API Key/Token）已脱敏处理。",
    )
    async def get_config() -> Any:
        return sanitize_config(config)

    # ─── 认证 API ──────────────────────────────────────────────

    from vision_agent.auth.manager import get_auth_manager
    from vision_agent.auth.models import Role

    auth_mgr = get_auth_manager()

    def _get_current_user(request: Request) -> Any:
        """从请求头提取 Token 并验证用户"""
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
            user = auth_mgr.verify_token(token)
            if user:
                return user
        return None

    def _require_auth(request: Request) -> Any:
        """要求认证，未登录返回 401"""
        user = _get_current_user(request)
        if not user:
            raise HTTPException(status_code=401, detail="请先登录")
        return user

    def _require_role(*roles: Role):
        """要求特定角色"""
        def checker(request: Request) -> Any:
            user = _require_auth(request)
            if not any(auth_mgr.require_role(user, r) for r in roles):
                raise HTTPException(status_code=403, detail="权限不足")
            return user
        return checker

    @app.post("/api/auth/login",
        tags=["认证"],
        summary="用户登录",
        description="验证用户名和密码，返回 Bearer Token（24h 有效）。5 次失败后锁定 5 分钟。",
        responses={200: {"description": "登录成功，返回 token 和用户信息"}, 401: {"description": "用户名或密码错误"}},
    )
    async def auth_login(request: Request, body: dict[str, Any] = Body(...)) -> Any:
        username = body.get("username", "")
        password = body.get("password", "")
        if not username or not password:
            raise HTTPException(status_code=400, detail="请输入用户名和密码")
        # 获取客户端 IP
        ip = request.client.host if request.client else ""
        token = auth_mgr.login(username, password, ip=ip)
        if not token:
            raise HTTPException(status_code=401, detail="用户名或密码错误")
        user = auth_mgr.get_user(username)
        return {"token": token, "user": user.to_dict() if user else {"username": username, "role": "unknown"}}

    @app.post("/api/auth/logout",
        tags=["认证"],
        summary="退出登录",
        description="使当前 Token 失效。",
    )
    async def auth_logout(user: Any = Depends(_require_auth)) -> Any:
        auth_mgr.logout(user.username)
        return {"message": "已退出"}

    @app.get("/api/auth/me",
        tags=["认证"],
        summary="当前用户信息",
        description="返回当前登录用户的完整信息（用户名、角色、邮箱、头像色等）。",
    )
    async def auth_me(user: Any = Depends(_require_auth)) -> Any:
        return user.to_dict()

    @app.put("/api/auth/profile",
        tags=["认证"],
        summary="更新个人资料",
        description="修改当前用户的邮箱和头像背景色。",
    )
    async def update_profile(
        body: dict[str, Any] = Body(...),
        user: Any = Depends(_require_auth),
    ) -> Any:
        email = body.get("email")
        avatar_bg = body.get("avatar_bg")
        try:
            updated = auth_mgr.update_user(
                user.username,
                email=email,
                avatar_bg=avatar_bg,
            )
            return updated
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.post("/api/auth/change-password",
        tags=["认证"],
        summary="修改密码",
        description="验证旧密码后更新为新密码。",
        responses={200: {"description": "密码已修改"}, 401: {"description": "旧密码错误"}},
    )
    async def change_password(
        body: dict[str, Any] = Body(...),
        user: Any = Depends(_require_auth),
    ) -> Any:
        old_pw = body.get("old_password", "")
        new_pw = body.get("new_password", "")
        if not old_pw or not new_pw:
            raise HTTPException(status_code=400, detail="请输入旧密码和新密码")
        if not auth_mgr.verify_password(old_pw, user.password_hash):
            raise HTTPException(status_code=401, detail="旧密码错误")
        auth_mgr.update_user(user.username, password=new_pw)
        return {"message": "密码已修改"}

    @app.get("/api/auth/me/detail",
        tags=["认证"],
        summary="个人信息详情",
        description="返回当前用户的完整信息：基本资料 + 最后登录 + 活跃会话数 + 通知偏好。",
    )
    async def auth_me_detail(user: Any = Depends(_require_auth)) -> Any:
        detail = auth_mgr.get_user_detail(user.username)
        if not detail:
            raise HTTPException(status_code=404, detail="用户不存在")
        return detail

    @app.get("/api/auth/me/preferences",
        tags=["认证"],
        summary="通知偏好",
        description="返回当前用户的通知偏好设置。",
    )
    async def auth_preferences(user: Any = Depends(_require_auth)) -> Any:
        return auth_mgr.get_preferences(user.username)

    @app.put("/api/auth/preferences",
        tags=["认证"],
        summary="更新通知偏好",
        description="修改通知偏好设置（告警推送/系统通知/日报推送的启停和渠道）。",
    )
    async def update_preferences(
        body: dict[str, Any] = Body(...),
        user: Any = Depends(_require_auth),
    ) -> Any:
        # 输入校验：只允许合法的 channel 值
        ALLOWED_CHANNELS = {"webhook", "email"}
        for key in body:
            if isinstance(body[key], dict):
                channels = body[key].get("channels")
                if channels is not None:
                    if not isinstance(channels, list):
                        raise HTTPException(status_code=422, detail=f"{key}.channels 必须是字符串数组")
                    for ch in channels:
                        if not isinstance(ch, str) or ch not in ALLOWED_CHANNELS:
                            raise HTTPException(status_code=422, detail=f"非法渠道: {ch}")
                enabled = body[key].get("enabled")
                if enabled is not None and not isinstance(enabled, bool):
                    raise HTTPException(status_code=422, detail=f"{key}.enabled 必须是布尔值")
        return auth_mgr.update_preferences(user.username, body)

    @app.get("/api/users",
        tags=["用户管理"],
        summary="用户列表（仅管理员）",
        description="返回所有用户的列表，需要管理员权限。",
        responses={200: {"description": "用户列表"}, 403: {"description": "权限不足"}},
    )
    async def list_users(user: Any = Depends(_require_role(Role.ADMIN))) -> Any:
        return auth_mgr.list_users()

    @app.post("/api/users",
        tags=["用户管理"],
        summary="创建用户（仅管理员）",
        description="创建新用户，指定用户名、密码、角色（admin/operator/viewer）和可选邮箱。",
        responses={200: {"description": "创建成功"}, 400: {"description": "参数错误"}, 409: {"description": "用户名已存在"}},
    )
    async def create_user(
        body: dict[str, Any] = Body(...),
        user: Any = Depends(_require_role(Role.ADMIN)),
    ) -> Any:
        username = body.get("username", "")
        password = body.get("password", "")
        role = body.get("role", "viewer")
        email = body.get("email", "")
        if not username or not password:
            raise HTTPException(status_code=400, detail="请输入用户名和密码")
        try:
            return auth_mgr.create_user(username, password, role, email=email)
        except ValueError as e:
            raise HTTPException(status_code=409, detail=str(e))

    @app.put("/api/users/{username}",
        tags=["用户管理"],
        summary="编辑用户（仅管理员）",
        description="修改用户信息：邮箱、密码、角色、状态（0=正常,1=禁用）、头像色。未传入的字段不修改。",
        responses={200: {"description": "更新成功"}, 400: {"description": "用户不存在"}},
    )
    async def update_user(
        username: str,
        body: dict[str, Any] = Body(...),
        user: Any = Depends(_require_role(Role.ADMIN)),
    ) -> Any:
        try:
            return auth_mgr.update_user(
                username,
                email=body.get("email"),
                password=body.get("password") if body.get("password") else None,
                role=body.get("role"),
                status=body.get("status"),
                avatar_bg=body.get("avatar_bg"),
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.delete("/api/users/{username}",
        tags=["用户管理"],
        summary="删除用户（仅管理员）",
        description="删除指定用户。默认 admin 不可删除。",
        responses={200: {"description": "删除成功"}, 400: {"description": "不能删除默认管理员"}, 404: {"description": "用户不存在"}},
    )
    async def delete_user(
        username: str,
        user: Any = Depends(_require_role(Role.ADMIN)),
    ) -> Any:
        try:
            auth_mgr.delete_user(username)
            return {"message": f"用户 {username} 已删除"}
        except ValueError as e:
            detail = str(e)
            status = 404 if "不存在" in detail else 400
            raise HTTPException(status_code=status, detail=detail)

    # ─── 用户管理：统计 / 会话 / 登录历史 ──────────────────────

    @app.get("/api/users/stats",
        tags=["用户管理"],
        summary="用户统计",
        description="返回用户总数、角色分布、启用/禁用数、在线数。需要管理员权限。",
    )
    async def user_stats(user: Any = Depends(_require_role(Role.ADMIN))) -> Any:
        if not auth_mgr:
            raise HTTPException(status_code=404, detail="认证系统未加载")
        return auth_mgr.get_user_stats()

    @app.get("/api/users/{username}/sessions",
        tags=["用户管理"],
        summary="活跃会话",
        description="列出指定用户的所有活跃 Token 会话。本人或管理员可查。",
    )
    async def user_sessions(
        username: str,
        user: Any = Depends(_require_auth),
    ) -> Any:
        if not auth_mgr:
            raise HTTPException(status_code=404, detail="认证系统未加载")
        # 本人或管理员
        if user.username != username and not auth_mgr.require_role(user, Role.ADMIN):
            raise HTTPException(status_code=403, detail="权限不足")
        sessions = auth_mgr.list_active_sessions()
        return [s for s in sessions if s["username"] == username]

    @app.delete("/api/users/{username}/sessions",
        tags=["用户管理"],
        summary="强制下线",
        description="撤销指定用户的所有 Token 会话。需要管理员权限。",
    )
    async def revoke_sessions(
        username: str,
        user: Any = Depends(_require_role(Role.ADMIN)),
    ) -> Any:
        if not auth_mgr:
            raise HTTPException(status_code=404, detail="认证系统未加载")
        ok = auth_mgr.revoke_sessions(username)
        return {"message": f"用户 {username} 已强制下线" if ok else f"用户 {username} 无活跃会话"}

    @app.get("/api/users/{username}/login-history",
        tags=["用户管理"],
        summary="登录历史",
        description="返回指定用户最近的登录历史记录。本人或管理员可查。",
    )
    async def login_history(
        username: str,
        user: Any = Depends(_require_auth),
        limit: int = Query(20, ge=1, le=100),
    ) -> Any:
        if not auth_mgr:
            raise HTTPException(status_code=404, detail="认证系统未加载")
        if user.username != username and not auth_mgr.require_role(user, Role.ADMIN):
            raise HTTPException(status_code=403, detail="权限不足")
        return auth_mgr.get_login_history(username, limit)

    # ─── WebSocket ────────────────────────────────────────────

    class WSManager:
        """WebSocket 连接管理器"""

        def __init__(self) -> None:
            self._connections: list[WebSocket] = []

        async def connect(self, ws: WebSocket) -> None:
            await ws.accept()
            self._connections.append(ws)
            logger.info("ws_connected total=%d", len(self._connections))

        def disconnect(self, ws: WebSocket) -> None:
            if ws in self._connections:
                self._connections.remove(ws)
            logger.debug("ws_disconnected total=%d", len(self._connections))

        async def broadcast(self, message: dict[str, Any]) -> None:
            dead: list[WebSocket] = []
            for ws in self._connections:
                try:
                    await ws.send_json(message)
                except Exception:
                    dead.append(ws)
            for ws in dead:
                self.disconnect(ws)

    ws_manager = WSManager()

    @app.websocket("/ws",
        name="事件推送 WebSocket",
    )
    async def websocket_endpoint(ws: WebSocket) -> None:
        await ws_manager.connect(ws)
        try:
            while True:
                # 心跳：等待客户端消息（ping/pong 或数据）
                await ws.receive_text()
        except WebSocketDisconnect:
            ws_manager.disconnect(ws)
        except Exception:
            ws_manager.disconnect(ws)

    # ─── WebSocket 视频流 ──────────────────────────────────────

    @app.websocket("/ws/video/{camera_id}")
    async def video_stream(ws: WebSocket, camera_id: str) -> None:
        """WebSocket 实时帧推送（帧已在 InferenceThread 中画好检测框，帧框同步）

        协议：前 8 字节帧头(seq+timestamp) + JPEG 数据
        """
        import asyncio
        import re
        import struct
        import time as _time

        # 路径遍历防护
        if not re.match(r"^[\w\-]+$", camera_id):
            await ws.close(code=1008, reason="invalid camera_id")
            return

        if not pipeline:
            await ws.close(code=1008, reason="pipeline not available")
            return

        cam_thread = pipeline.get_camera_thread(camera_id)
        if not cam_thread:
            await ws.close(code=1008, reason=f"camera {camera_id} not found")
            return

        await ws.accept()
        # 保持订阅让摄像头线程不休眠（实际帧从 InferenceThread 缓存读取）
        frame_queue = cam_thread.subscribe_frames(maxsize=5)
        logger.info(
            "video_ws_connected camera=%s thread_status=%s",
            camera_id,
            cam_thread.status.value,
        )

        # 帧序号（用于帧头）
        _last_seq = -1
        # 帧 ID 去重，避免重复发送同一帧
        _last_frame_id = -1

        try:
            while True:
                entry = pipeline.get_last_frame_jpeg(camera_id)

                if entry and entry[1] != _last_frame_id:
                    jpeg_bytes, frame_id = entry
                    if len(jpeg_bytes) > 100:
                        _last_frame_id = frame_id
                        ts = int(_time.time() * 1000) & 0xFFFFFFFF
                        header = struct.pack(">II", _last_seq & 0xFFFFFFFF, ts)
                        _last_seq += 1
                        await ws.send_bytes(header + jpeg_bytes)
                else:
                    # 还没出新帧，发心跳保活
                    try:
                        await ws.send_json({"type": "ping"})
                    except Exception:
                        break

                await asyncio.sleep(0.033)  # ~30 fps

        except WebSocketDisconnect:
            pass
        except Exception as e:
            logger.warning("video_ws_error camera=%s error=%s", camera_id, str(e))
        finally:
            cam_thread.unsubscribe_frames(frame_queue)
            logger.info("video_ws_disconnected camera=%s", camera_id)

    # ─── 回放 API ──────────────────────────────────────────────

    @app.get("/api/cameras/{camera_id}/replay",
        tags=["监控"],
        summary="历史录像回放",
        description="返回指定时间范围内的录像 MP4 文件。start/end 为 Unix 时间戳（秒）。",
        responses={200: {"description": "MP4 视频文件"}, 404: {"description": "未找到录像"}},
    )
    async def camera_replay(
        camera_id: str,
        start: float = Query(..., description="起始时间戳（Unix 秒）"),
        end: float = Query(..., description="结束时间戳（Unix 秒）"),
    ) -> Any:
        """返回指定时间段的录像 MP4 文件"""
        import re as _re

        if not _re.match(r"^[\w\-]+$", camera_id):
            raise HTTPException(status_code=400, detail="摄像头ID不合法")
        if not pipeline:
            raise HTTPException(status_code=404, detail="系统未启动")

        from pathlib import Path

        clip_dir = Path("data/clips") / camera_id
        if not clip_dir.exists():
            raise HTTPException(status_code=404, detail="没有找到录像文件")

        clips = sorted(clip_dir.glob("*.mp4"))
        for clip_path in clips:
            try:
                ts_str = clip_path.stem.split("_")[0]
                clip_ts = float(ts_str)
                if start <= clip_ts <= end:
                    return FileResponse(
                        str(clip_path),
                        media_type="video/mp4",
                        headers={
                            "Content-Disposition": f'inline; filename="{camera_id}_{clip_ts:.0f}.mp4"'
                        },
                    )
            except (ValueError, IndexError):
                continue

        raise HTTPException(status_code=404, detail="指定时间范围内没有录像")

    @app.get("/api/cameras/{camera_id}/timeline",
        tags=["监控"],
        summary="录像时间轴",
        description="返回指定日期有录像的时间段列表，用于时间轴高亮。date 格式为 YYYY-MM-DD。",
        responses={200: {"description": "时间段列表"}, 400: {"description": "日期格式错误"}},
    )
    async def camera_timeline(
        camera_id: str,
        date: str = Query(..., description="日期 YYYY-MM-DD"),
    ) -> Any:
        """返回指定日期有录像的时间段列表"""
        import re as _re
        from datetime import datetime, timezone

        if not _re.match(r"^[\w\-]+$", camera_id):
            raise HTTPException(status_code=400, detail="摄像头ID不合法")

        # 先校验日期格式
        try:
            day_start = datetime.strptime(date, "%Y-%m-%d").replace(
                tzinfo=timezone.utc
            ).timestamp()
            day_end = day_start + 86400
        except ValueError:
            raise HTTPException(status_code=400, detail="日期格式不正确")

        from pathlib import Path

        clip_dir = Path("data/clips") / camera_id
        if not clip_dir.exists():
            return {"camera_id": camera_id, "date": date, "segments": []}

        segments: list[dict[str, Any]] = []
        clips = sorted(clip_dir.glob("*.mp4"))
        for clip_path in clips:
            try:
                ts_str = clip_path.stem.split("_")[0]
                clip_ts = float(ts_str)
                if day_start <= clip_ts < day_end:
                    file_size = clip_path.stat().st_size
                    duration_est = max(1.0, file_size / (5 * 50_000))
                    segments.append(
                        {
                            "start": clip_ts,
                            "end": clip_ts + duration_est,
                            "size_bytes": file_size,
                        }
                    )
            except (ValueError, IndexError):
                continue

        return {"camera_id": camera_id, "date": date, "segments": segments}

    # ─── 规则管理 API ──────────────────────────────────────────

    try:
        from vision_agent.web.api.rules import create_router as create_rules_router

        rules_router = create_rules_router(auth_dependency=_require_auth)
        if rules_router is not None:
            app.include_router(rules_router)
            logger.info("rules_api_mounted")
    except Exception as e:
        logger.warning("rules_api_failed error=%s", e)

    # ─── 广播接口（供 pipeline 调用）──────────────────────────

    app.state.ws_manager = ws_manager
    app.state.database = database
    app.state.pipeline = pipeline

    return app
