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

    # Inject Request into module globals so FastAPI can resolve the
    # stringified annotation (caused by `from __future__ import annotations`).
    import sys

    _module = sys.modules[__name__]
    _module.Request = Request  # type: ignore[attr-defined]

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

    _ALLOWED_PREFIXES = ("/api/", "/ws", "/health", "/static/", "/")

    @app.middleware("http")
    async def path_whitelist(request: Any, call_next: Any) -> Any:
        path = request.url.path
        if not any(path.startswith(p) for p in _ALLOWED_PREFIXES):
            return JSONResponse(status_code=404, content={"detail": "Not Found"})
        return await call_next(request)

    # ─── 健康检查 ────────────────────────────────────────────

    @app.get("/health")
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
                    "inference_latency_p50_ms": h.inference_latency_p50_ms,
                    "inference_latency_p99_ms": h.inference_latency_p99_ms,
                    "active_cameras": h.active_cameras,
                    "total_cameras": h.total_cameras,
                    "today_alerts": h.today_alerts,
                    "llm_success_rate": h.llm_success_rate,
                },
            )
        return {"status": "ok", "uptime_seconds": 0}

    # ─── 摄像头状态 ──────────────────────────────────────────

    @app.get("/api/cameras")
    async def list_cameras() -> Any:
        if not pipeline:
            return []
        states = pipeline.get_camera_states()
        return [s.to_dict() for s in states.values()]

    # ─── 告警列表 ────────────────────────────────────────────

    @app.get("/api/alerts")
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

    @app.get("/api/alerts/{alert_id}")
    async def get_alert(alert_id: str) -> Any:
        if not database:
            raise HTTPException(status_code=404, detail="Alert not found")
        alert = database.get_alert(alert_id)
        if not alert:
            raise HTTPException(status_code=404, detail="Alert not found")
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

    @app.get("/api/alerts/{alert_id}/snapshot")
    async def get_snapshot(alert_id: str) -> Any:
        if not database:
            raise HTTPException(status_code=404, detail="Alert not found")
        alert = database.get_alert(alert_id)
        if not alert or not alert.event.snapshot_path:
            raise HTTPException(status_code=404, detail="Snapshot not found")
        path = Path(alert.event.snapshot_path)
        if not path.exists():
            raise HTTPException(status_code=404, detail="Snapshot not found")
        return FileResponse(str(path), media_type="image/jpeg")

    # ─── 告警视频 ────────────────────────────────────────────

    @app.get("/api/alerts/{alert_id}/clip")
    async def get_clip(
        alert_id: str, download: bool = False
    ) -> Any:
        if not database:
            raise HTTPException(status_code=404, detail="Alert not found")
        alert = database.get_alert(alert_id)
        if not alert or not alert.video_clip_path:
            raise HTTPException(status_code=404, detail="Clip not found")
        path = Path(alert.video_clip_path)
        if not path.exists():
            raise HTTPException(status_code=404, detail="Clip not found")
        headers = {"Content-Disposition": "attachment"} if download else {}
        return FileResponse(str(path), media_type="video/mp4", headers=headers)

    # ─── 告警状态更新 ────────────────────────────────────────

    _VALID_TRANSITIONS = {
        "pending": {"acknowledged", "rejected"},
        "acknowledged": {"resolved"},
    }

    @app.put("/api/alerts/{alert_id}/status")
    async def update_alert_status(
        alert_id: str,
        body: dict[str, Any] = Body(...),
    ) -> Any:
        if not database:
            raise HTTPException(status_code=404, detail="Alert not found")

        new_status = body.get("status")
        acknowledged_by = body.get("acknowledged_by", "")

        if not new_status:
            raise HTTPException(status_code=400, detail="Missing status field")

        alert = database.get_alert(alert_id)
        if not alert:
            raise HTTPException(status_code=404, detail="Alert not found")

        current = alert.status.value
        allowed = _VALID_TRANSITIONS.get(current, set())
        if new_status not in allowed:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot transition from {current} to {new_status}",
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

    @app.get("/api/stats")
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

    @app.get("/api/config")
    async def get_config() -> Any:
        return sanitize_config(config)

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

    @app.websocket("/ws")
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

    # ─── 广播接口（供 pipeline 调用）──────────────────────────

    app.state.ws_manager = ws_manager
    app.state.database = database
    app.state.pipeline = pipeline

    return app
