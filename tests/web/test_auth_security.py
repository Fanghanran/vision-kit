"""P0 安全加固测试 — 全局认证、RBAC、WebSocket 认证、禁用用户、admin 特权

测试清单：
1. 未认证请求访问业务端点返回 401
2. 公开路径无需认证
3. viewer 可查看 alerts/cameras
4. viewer 不可管理 alerts
5. viewer 不可控制 cameras
6. operator 可管理 alerts
7. operator 可控制 cameras
8. admin 可访问所有端点
9. admin 特权绕过 PERMISSIONS 检查
10. 禁用用户无法登录或访问
11. WebSocket 无 token 拒绝（code=4001）
12. WebSocket 无效 token 拒绝（code=4001）
13. WebSocket 有效 token 接受
14. WebSocket 视频流无权限拒绝（code=4003）
15. /api/config 需要 manage:config 权限
"""

import os
import tempfile
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from starlette.testclient import TestClient

pytest.importorskip("fastapi")

from fastapi import WebSocketDisconnect  # noqa: E402

from sentinelmind.auth.manager import AuthManager  # noqa: E402
from sentinelmind.auth.models import PERMISSIONS, Role, UserStatus  # noqa: E402
from sentinelmind.web.api.app import create_app  # noqa: E402


# ─── Fixtures ──────────────────────────────────────────────


@pytest.fixture
def auth_mgr():
    """全新 AuthManager（临时文件，每测试隔离）"""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    mgr = AuthManager(db_path=path)
    yield mgr
    for p in (path, path + "-shm", path + "-wal"):
        try:
            os.unlink(p)
        except OSError:
            pass


@pytest.fixture
def database():
    """模拟数据库"""
    db = MagicMock()
    db.list_alerts.return_value = ([], 0)
    db.get_alert.return_value = None
    db.get_stats.return_value = {"total_count": 0, "by_severity": {}, "by_status": {}}
    db.update_alert.return_value = None
    return db


@pytest.fixture
def pipeline():
    """模拟 pipeline"""
    p = MagicMock()
    p.health.return_value = SimpleNamespace(
        status="ok",
        uptime_seconds=100,
        gpu_utilization=0.3,
        gpu_memory_used_mb=2000,
        gpu_memory_total_mb=8000,
        queue_depth=5,
        inference_latency_p50_ms=12.0,
        inference_latency_p99_ms=45.0,
        active_cameras=2,
        total_cameras=4,
        today_alerts=3,
        llm_success_rate=0.95,
    )
    p.get_camera_states.return_value = {}
    p.get_camera_thread.return_value = None
    p.uptime_seconds = 100.0
    return p


@pytest.fixture
def client(auth_mgr, database, pipeline):
    """带独立 AuthManager 的 TestClient"""
    import sentinelmind.auth.manager as auth_mod

    _orig = auth_mod._auth_manager
    auth_mod._auth_manager = auth_mgr
    try:
        app = create_app(database=database, pipeline=pipeline, config={})
        yield TestClient(app)
    finally:
        auth_mod._auth_manager = _orig


def _auth_header(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ─── 1. 未认证请求返回 401 ─────────────────────────────────


class TestUnauthenticatedAccess:
    def test_unauthenticated_access_returns_401(self, client):
        """未带 token 访问 /api/cameras、/api/alerts、/api/stats 返回 401"""
        endpoints = [
            ("GET", "/api/cameras"),
            ("GET", "/api/alerts"),
            ("GET", "/api/stats"),
        ]
        for method, path in endpoints:
            resp = getattr(client, method.lower())(path)
            assert resp.status_code == 401, (
                f"{method} {path} 应返回 401, 实际 {resp.status_code}"
            )


# ─── 2. 公开路径无需认证 ───────────────────────────────────


class TestPublicPaths:
    def test_public_paths_no_auth_required(self, client):
        """/health、/api/auth/login 无需认证可访问"""
        resp = client.get("/health")
        assert resp.status_code == 200
        assert "status" in resp.json()

        resp = client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "admin123"},
        )
        assert resp.status_code == 200
        assert "token" in resp.json()


# ─── 3-5. viewer 权限 ──────────────────────────────────────


class TestViewerPermissions:
    def test_viewer_can_view_alerts_and_cameras(self, client, auth_mgr):
        """viewer 访问 /api/alerts、/api/cameras 成功"""
        auth_mgr.create_user("viewer1", "pass123", Role.VIEWER)
        token = auth_mgr.login("viewer1", "pass123")
        assert token is not None

        resp = client.get("/api/alerts", headers=_auth_header(token))
        assert resp.status_code == 200

        resp = client.get("/api/cameras", headers=_auth_header(token))
        assert resp.status_code == 200

    def test_viewer_cannot_manage_alerts(self, client, auth_mgr):
        """viewer PUT /api/alerts/{id}/status 返回 403"""
        auth_mgr.create_user("viewer2", "pass123", Role.VIEWER)
        token = auth_mgr.login("viewer2", "pass123")
        assert token is not None

        resp = client.put(
            "/api/alerts/alert-01/status",
            json={"status": "acknowledged"},
            headers=_auth_header(token),
        )
        assert resp.status_code == 403

    def test_viewer_cannot_control_cameras(self, client, auth_mgr):
        """viewer POST /api/cameras/{id}/toggle 返回 403"""
        auth_mgr.create_user("viewer3", "pass123", Role.VIEWER)
        token = auth_mgr.login("viewer3", "pass123")
        assert token is not None

        resp = client.post(
            "/api/cameras/cam_01/toggle",
            headers=_auth_header(token),
        )
        assert resp.status_code == 403


# ─── 6-7. operator 权限 ────────────────────────────────────


class TestOperatorPermissions:
    def test_operator_can_manage_alerts(self, client, auth_mgr, database):
        """operator PUT /api/alerts/{id}/status 成功"""
        auth_mgr.create_user("operator1", "pass123", Role.OPERATOR)
        token = auth_mgr.login("operator1", "pass123")
        assert token is not None

        # 模拟告警数据
        alert = MagicMock()
        alert.alert_id = "alert-01"
        alert.event = MagicMock()
        alert.event.event_type = "motion"
        alert.event.camera_id = "cam_01"
        alert.event.camera_name = "测试摄像头"
        alert.event.severity.value = "warning"
        alert.status.value = "pending"
        alert.llm_analysis = None
        alert.created_at = 1234567890.0

        updated_alert = MagicMock()
        updated_alert.alert_id = "alert-01"
        updated_alert.event = alert.event
        updated_alert.status.value = "acknowledged"
        updated_alert.llm_analysis = None
        updated_alert.created_at = 1234567890.0

        database.get_alert.side_effect = [alert, updated_alert]

        resp = client.put(
            "/api/alerts/alert-01/status",
            json={"status": "acknowledged"},
            headers=_auth_header(token),
        )
        assert resp.status_code == 200

    def test_operator_can_control_cameras(self, client, auth_mgr, pipeline):
        """operator POST /api/cameras/{id}/toggle 成功"""
        auth_mgr.create_user("operator2", "pass123", Role.OPERATOR)
        token = auth_mgr.login("operator2", "pass123")
        assert token is not None

        cam_thread = MagicMock()
        cam_thread.is_alive.return_value = False
        pipeline.get_camera_thread.return_value = cam_thread

        resp = client.post(
            "/api/cameras/cam_op/toggle",
            headers=_auth_header(token),
        )
        assert resp.status_code == 200
        cam_thread.start.assert_called_once()


# ─── 8-9. admin 特权 ───────────────────────────────────────


class TestAdminPermissions:
    def test_admin_can_access_all_endpoints(self, client, auth_mgr, database, pipeline):
        """admin 访问所有端点成功"""
        # 模拟告警
        alert = MagicMock()
        alert.alert_id = "alert-admin"
        alert.event = MagicMock()
        alert.event.event_type = "motion"
        alert.event.camera_id = "cam_01"
        alert.event.camera_name = "测试"
        alert.event.severity.value = "warning"
        alert.status.value = "pending"
        alert.llm_analysis = None
        alert.created_at = 1234567890.0

        updated_alert = MagicMock()
        updated_alert.alert_id = "alert-admin"
        updated_alert.event = alert.event
        updated_alert.status.value = "acknowledged"
        updated_alert.llm_analysis = None
        updated_alert.created_at = 1234567890.0

        database.get_alert.side_effect = [alert, updated_alert]

        # 模拟摄像头
        cam_thread = MagicMock()
        cam_thread.is_alive.return_value = False
        pipeline.get_camera_thread.return_value = cam_thread

        token = auth_mgr.login("admin", "admin123")
        assert token is not None

        # view:alerts
        resp = client.get("/api/alerts", headers=_auth_header(token))
        assert resp.status_code == 200

        # view:cameras
        resp = client.get("/api/cameras", headers=_auth_header(token))
        assert resp.status_code == 200

        # stats (依赖 view:alerts)
        resp = client.get("/api/stats", headers=_auth_header(token))
        assert resp.status_code == 200

        # manage:config
        resp = client.get("/api/config", headers=_auth_header(token))
        assert resp.status_code == 200

        # manage:alerts
        resp = client.put(
            "/api/alerts/alert-admin/status",
            json={"status": "acknowledged"},
            headers=_auth_header(token),
        )
        assert resp.status_code == 200

        # control:cameras
        resp = client.post(
            "/api/cameras/cam_admin/toggle",
            headers=_auth_header(token),
        )
        assert resp.status_code == 200

    def test_admin_privilege_bypasses_permission_check(self, client, auth_mgr, database):
        """admin 访问任意权限端点，不走 PERMISSIONS 匹配"""
        # 临时从 PERMISSIONS 中移除 admin 的所有权限
        orig_perms = PERMISSIONS.get(Role.ADMIN, set()).copy()
        PERMISSIONS[Role.ADMIN] = set()

        try:
            alert = MagicMock()
            alert.alert_id = "alert-bypass"
            alert.event = MagicMock()
            alert.event.event_type = "motion"
            alert.event.camera_id = "cam_01"
            alert.event.camera_name = "测试"
            alert.event.severity.value = "warning"
            alert.status.value = "pending"
            alert.llm_analysis = None
            alert.created_at = 1234567890.0

            updated_alert = MagicMock()
            updated_alert.alert_id = "alert-bypass"
            updated_alert.event = alert.event
            updated_alert.status.value = "acknowledged"
            updated_alert.llm_analysis = None
            updated_alert.created_at = 1234567890.0

            database.get_alert.side_effect = [alert, updated_alert]

            token = auth_mgr.login("admin", "admin123")
            assert token is not None

            # 即使 PERMISSIONS 中没有 admin，也应能访问（通过 role == ADMIN 绕过）
            resp = client.get("/api/config", headers=_auth_header(token))
            assert resp.status_code == 200

            resp = client.put(
                "/api/alerts/alert-bypass/status",
                json={"status": "acknowledged"},
                headers=_auth_header(token),
            )
            assert resp.status_code == 200
        finally:
            PERMISSIONS[Role.ADMIN] = orig_perms


# ─── 10. 禁用用户 ──────────────────────────────────────────


class TestDisabledUser:
    def test_disabled_user_cannot_login_or_access(self, client, auth_mgr):
        """禁用用户 login 返回 None，现有 token 访问返回 403"""
        auth_mgr.create_user("disabled1", "pass123", Role.VIEWER)

        # 先登录获取有效 token
        token = auth_mgr.login("disabled1", "pass123")
        assert token is not None

        # 验证 token  initially 可用
        resp = client.get("/api/alerts", headers=_auth_header(token))
        assert resp.status_code == 200

        # 禁用用户
        auth_mgr.update_user("disabled1", status=UserStatus.DISABLED.value)

        # 禁用后无法登录
        assert auth_mgr.login("disabled1", "pass123") is None

        # 禁用后现有 token 访问返回 401（verify_token 对禁用用户返回 None）
        resp = client.get("/api/alerts", headers=_auth_header(token))
        assert resp.status_code == 401


# ─── 11-13. WebSocket 认证 ─────────────────────────────────


class TestWebSocketAuth:
    def test_ws_rejects_missing_token(self, client):
        """/ws 无 token 连接关闭 code=4001"""
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect("/ws"):
                pass
        assert exc_info.value.code == 4001

    def test_ws_rejects_invalid_token(self, client):
        """/ws 无效 token 连接关闭 code=4001"""
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect("/ws?token=invalid_token"):
                pass
        assert exc_info.value.code == 4001

    def test_ws_accepts_valid_token(self, client, auth_mgr):
        """/ws 有效 token 连接成功"""
        token = auth_mgr.login("admin", "admin123")
        assert token is not None

        with client.websocket_connect(f"/ws?token={token}") as ws:
            ws.send_text("ping")


# ─── 14. WebSocket 视频流权限 ──────────────────────────────


class TestWebSocketVideoAuth:
    def test_ws_video_rejects_unauthorized(self, client, auth_mgr):
        """/ws/video/cam_01 无 view:cameras 权限用户连接关闭 code=4003"""
        # 所有标准角色都有 view:cameras，需临时移除以测试拒绝场景
        auth_mgr.create_user("noview", "pass123", Role.VIEWER)
        token = auth_mgr.login("noview", "pass123")
        assert token is not None

        orig_perms = PERMISSIONS.get(Role.VIEWER, set()).copy()
        PERMISSIONS[Role.VIEWER] = set()

        try:
            with pytest.raises(WebSocketDisconnect) as exc_info:
                with client.websocket_connect(f"/ws/video/cam_01?token={token}"):
                    pass
            assert exc_info.value.code == 4003
        finally:
            PERMISSIONS[Role.VIEWER] = orig_perms


# ─── 15. /api/config 权限 ──────────────────────────────────


class TestConfigPermission:
    def test_config_requires_manage_config(self, client, auth_mgr):
        """/api/config 需要 manage:config 权限"""
        # viewer 没有 manage:config
        auth_mgr.create_user("cfg_viewer", "pass123", Role.VIEWER)
        viewer_token = auth_mgr.login("cfg_viewer", "pass123")
        assert viewer_token is not None

        resp = client.get("/api/config", headers=_auth_header(viewer_token))
        assert resp.status_code == 403

        # operator 也没有 manage:config
        auth_mgr.create_user("cfg_operator", "pass123", Role.OPERATOR)
        op_token = auth_mgr.login("cfg_operator", "pass123")
        assert op_token is not None

        resp = client.get("/api/config", headers=_auth_header(op_token))
        assert resp.status_code == 403

        # admin 有 manage:config（通过角色特权）
        admin_token = auth_mgr.login("admin", "admin123")
        assert admin_token is not None

        resp = client.get("/api/config", headers=_auth_header(admin_token))
        assert resp.status_code == 200
