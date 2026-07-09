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

    @app.get("/api/cameras")
    async def list_cameras() -> Any:
        if not pipeline:
            return []
        states = pipeline.get_camera_states()
        return [s.to_dict() for s in states.values()]

    _ID_PATTERN = re.compile(r"^[\w\-]+$")

    @app.post("/api/cameras/{camera_id}/toggle")
    async def toggle_camera(camera_id: str) -> Any:
        """开关摄像头：在线则停止，离线则启动"""
        if not _ID_PATTERN.match(camera_id):
            raise HTTPException(status_code=400, detail="invalid camera_id")
        if not pipeline:
            raise HTTPException(status_code=404, detail="Pipeline not available")

        cam_thread = pipeline.get_camera_thread(camera_id)
        if not cam_thread:
            raise HTTPException(status_code=404, detail=f"Camera {camera_id} not found")

        if cam_thread.is_alive():
            cam_thread.stop()
            return {"camera_id": camera_id, "action": "stopped", "status": "disconnected"}
        else:
            cam_thread.start()
            return {"camera_id": camera_id, "action": "started", "status": "connecting"}

    @app.post("/api/cameras")
    async def create_camera(body: dict[str, Any] = Body(...)) -> Any:
        """添加新摄像头"""
        if not pipeline:
            raise HTTPException(status_code=404, detail="Pipeline not available")

        from vision_agent.core.camera import CameraConfig

        camera_id = body.get("id", "")
        if not camera_id:
            raise HTTPException(status_code=400, detail="Missing id field")
        if not _ID_PATTERN.match(camera_id):
            raise HTTPException(status_code=400, detail="invalid id (allowed: letters, digits, -, _)")

        # 检查是否已存在
        if pipeline.get_camera_thread(camera_id):
            raise HTTPException(status_code=409, detail=f"Camera {camera_id} already exists")

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
        return {"camera_id": camera_id, "action": "created"}

    @app.delete("/api/cameras/{camera_id}")
    async def delete_camera(camera_id: str) -> Any:
        """删除摄像头"""
        if not _ID_PATTERN.match(camera_id):
            raise HTTPException(status_code=400, detail="invalid camera_id")
        if not pipeline:
            raise HTTPException(status_code=404, detail="Pipeline not available")

        cam_thread = pipeline.get_camera_thread(camera_id)
        if not cam_thread:
            raise HTTPException(status_code=404, detail=f"Camera {camera_id} not found")

        pipeline.remove_camera(camera_id)
        return {"camera_id": camera_id, "action": "deleted"}

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

    @app.post("/api/auth/login")
    async def auth_login(body: dict[str, Any] = Body(...)) -> Any:
        username = body.get("username", "")
        password = body.get("password", "")
        if not username or not password:
            raise HTTPException(status_code=400, detail="请输入用户名和密码")
        token = auth_mgr.login(username, password)
        if not token:
            raise HTTPException(status_code=401, detail="用户名或密码错误")
        user = auth_mgr.get_user(username)
        return {"token": token, "user": user.to_dict() if user else {"username": username, "role": "unknown"}}

    @app.post("/api/auth/logout")
    async def auth_logout(user: Any = Depends(_require_auth)) -> Any:
        auth_mgr.logout(user.username)
        return {"message": "已退出"}

    @app.get("/api/auth/me")
    async def auth_me(user: Any = Depends(_require_auth)) -> Any:
        return user.to_dict()

    @app.put("/api/auth/profile")
    async def update_profile(
        body: dict[str, Any] = Body(...),
        user: Any = Depends(_require_auth),
    ) -> Any:
        """更新个人资料（头像、邮箱）"""
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

    @app.post("/api/auth/change-password")
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

    @app.get("/api/users")
    async def list_users(user: Any = Depends(_require_role(Role.ADMIN))) -> Any:
        return auth_mgr.list_users()

    @app.post("/api/users")
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

    @app.put("/api/users/{username}")
    async def update_user(
        username: str,
        body: dict[str, Any] = Body(...),
        user: Any = Depends(_require_role(Role.ADMIN)),
    ) -> Any:
        """管理员编辑用户信息"""
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

    @app.delete("/api/users/{username}")
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

    # ─── WebSocket 视频流 ──────────────────────────────────────

    @app.websocket("/ws/video/{camera_id}")
    async def video_stream(ws: WebSocket, camera_id: str) -> None:
        """WebSocket JPEG 实时帧推送

        协议（docs/frontend/MONITOR_PANEL.md 4.1 节）：
        - 前 8 字节帧头：帧序号(uint32) + 时间戳(uint32)
        - 后续字节：JPEG 数据
        """
        import asyncio
        import re
        import struct
        from io import BytesIO

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

        # 尝试导入图像编码库（模块级缓存）
        if not hasattr(video_stream, "_encode_jpeg"):
            video_stream._encode_jpeg = None  # type: ignore[attr-defined]
            try:
                import cv2

                def _encode_cv2(frame: Any) -> bytes | None:
                    ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
                    return buf.tobytes() if ok else None

                video_stream._encode_jpeg = _encode_cv2  # type: ignore[attr-defined]
            except ImportError:
                try:
                    from PIL import Image

                    def _encode_pil(frame: Any) -> bytes | None:
                        img = Image.fromarray(frame[:, :, ::-1])  # BGR → RGB
                        buf = BytesIO()
                        img.save(buf, format="JPEG", quality=80)
                        return buf.getvalue()

                    video_stream._encode_jpeg = _encode_pil  # type: ignore[attr-defined]
                except ImportError:
                    logger.error("no_jpeg_encoder action=skip_video_ws")
                    await ws.close(code=1011, reason="no JPEG encoder available")
                    return

        _encode_jpeg = video_stream._encode_jpeg  # type: ignore[attr-defined]

        await ws.accept()
        frame_queue = cam_thread.subscribe_frames(maxsize=30)
        logger.info(
            "video_ws_connected camera=%s thread_status=%s",
            camera_id,
            cam_thread.status.value,
        )

        try:
            while True:
                # 在 executor 中阻塞读取帧（不阻塞事件循环）
                try:
                    frame_data = await asyncio.get_event_loop().run_in_executor(
                        None, frame_queue.get, 5.0
                    )
                except Exception:
                    # 超时，发心跳保活
                    try:
                        await ws.send_json({"type": "ping"})
                    except Exception:
                        break
                    continue

                if frame_data is None:
                    break

                jpeg_bytes = _encode_jpeg(frame_data.frame)
                if jpeg_bytes is None:
                    logger.debug(
                        "jpeg_encode_failed camera=%s seq=%d",
                        camera_id,
                        frame_data.frame_seq,
                    )
                    continue

                # 帧头：序号(4B) + 时间戳(4B)
                header = struct.pack(
                    ">II",
                    frame_data.frame_seq & 0xFFFFFFFF,
                    int(frame_data.timestamp) & 0xFFFFFFFF,
                )
                await ws.send_bytes(header + jpeg_bytes)
        except WebSocketDisconnect:
            pass
        except Exception as e:
            logger.warning("video_ws_error camera=%s error=%s", camera_id, str(e))
        finally:
            cam_thread.unsubscribe_frames(frame_queue)
            logger.info("video_ws_disconnected camera=%s", camera_id)

    # ─── 回放 API ──────────────────────────────────────────────

    @app.get("/api/cameras/{camera_id}/replay")
    async def camera_replay(
        camera_id: str,
        start: float = Query(..., description="起始时间戳（Unix 秒）"),
        end: float = Query(..., description="结束时间戳（Unix 秒）"),
    ) -> Any:
        """返回指定时间段的录像 MP4 文件"""
        import re as _re

        if not _re.match(r"^[\w\-]+$", camera_id):
            raise HTTPException(status_code=400, detail="invalid camera_id")
        if not pipeline:
            raise HTTPException(status_code=404, detail="Pipeline not available")

        from pathlib import Path

        clip_dir = Path("data/clips") / camera_id
        if not clip_dir.exists():
            raise HTTPException(status_code=404, detail="No recordings found")

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

        raise HTTPException(status_code=404, detail="No recording in specified time range")

    @app.get("/api/cameras/{camera_id}/timeline")
    async def camera_timeline(
        camera_id: str,
        date: str = Query(..., description="日期 YYYY-MM-DD"),
    ) -> Any:
        """返回指定日期有录像的时间段列表"""
        import re as _re
        from datetime import datetime, timezone

        if not _re.match(r"^[\w\-]+$", camera_id):
            raise HTTPException(status_code=400, detail="invalid camera_id")

        from pathlib import Path

        clip_dir = Path("data/clips") / camera_id
        if not clip_dir.exists():
            return {"camera_id": camera_id, "date": date, "segments": []}

        try:
            day_start = datetime.strptime(date, "%Y-%m-%d").replace(
                tzinfo=timezone.utc
            ).timestamp()
            day_end = day_start + 86400
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date format")

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

    # ─── 广播接口（供 pipeline 调用）──────────────────────────

    app.state.ws_manager = ws_manager
    app.state.database = database
    app.state.pipeline = pipeline

    return app
