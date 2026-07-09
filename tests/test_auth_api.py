"""用户管理 REST API 测试 — 统计、会话管理、登录历史（FastAPI TestClient）"""

import pytest
from starlette.testclient import TestClient

from vision_agent.auth.manager import AuthManager
from vision_agent.auth.models import Role


# ─── Fixtures ──────────────────────────────────────────────


@pytest.fixture
def auth_mgr(tmp_path):
    """独立 AuthManager，写入临时数据库"""
    db_path = tmp_path / "test_auth_api.db"
    return AuthManager(db_path=str(db_path))


@pytest.fixture
def client(auth_mgr):
    """创建使用独立 AuthManager 的 TestClient"""
    import vision_agent.auth.manager as auth_mod

    _orig = auth_mod._auth_manager
    auth_mod._auth_manager = auth_mgr
    try:
        from vision_agent.web.api.app import create_app

        app = create_app(database=None, pipeline=None, config={})
        return TestClient(app)
    finally:
        auth_mod._auth_manager = _orig


def _login(client, username="admin", password="admin123"):
    """快捷登录，返回 token"""
    resp = client.post("/api/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200
    return resp.json()["token"]


def _auth_header(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ─── 用户统计 API ─────────────────────────────────────────


class TestUserStatsAPI:
    def test_user_stats_admin(self, client):
        """GET /api/users/stats 管理员返回统计信息"""
        token = _login(client)
        resp = client.get("/api/users/stats", headers=_auth_header(token))
        assert resp.status_code == 200
        data = resp.json()
        assert "total_users" in data
        assert "by_role" in data
        assert "active_count" in data
        assert "disabled_count" in data
        assert "online_count" in data
        assert data["total_users"] >= 1
        assert "admin" in data["by_role"]

    def test_user_stats_denied(self, client):
        """GET /api/users/stats 非管理员访问被拒"""
        # 创建 viewer 用户并登录
        client.post(
            "/api/users",
            json={"username": "viewer1", "password": "pass123", "role": "viewer"},
            headers=_auth_header(_login(client)),
        )
        viewer_token = _login(client, "viewer1", "pass123")
        resp = client.get("/api/users/stats", headers=_auth_header(viewer_token))
        assert resp.status_code == 403


# ─── 会话管理 API ─────────────────────────────────────────


class TestUserSessionsAPI:
    def test_user_sessions_admin(self, client):
        """GET /api/users/{username}/sessions 管理员可查他人会话"""
        # 让 admin 登录产生会话
        admin_token = _login(client)
        # 管理员查询自己的会话
        resp = client.get(
            "/api/users/admin/sessions", headers=_auth_header(admin_token)
        )
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) >= 1
        assert data[0]["username"] == "admin"

    def test_user_sessions_self(self, client):
        """GET /api/users/{username}/sessions 本人可查自己会话"""
        # 创建用户
        admin_token = _login(client)
        client.post(
            "/api/users",
            json={"username": "sess_user", "password": "pass123", "role": "viewer"},
            headers=_auth_header(admin_token),
        )
        # 登录自己的会话
        user_token = _login(client, "sess_user", "pass123")
        resp = client.get(
            "/api/users/sess_user/sessions", headers=_auth_header(user_token)
        )
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) >= 1
        assert data[0]["username"] == "sess_user"

    def test_user_sessions_denied(self, client):
        """GET /api/users/{username}/sessions 他人查被拒"""
        # 创建两个普通用户
        admin_token = _login(client)
        client.post(
            "/api/users",
            json={"username": "alice", "password": "pass", "role": "viewer"},
            headers=_auth_header(admin_token),
        )
        client.post(
            "/api/users",
            json={"username": "bob", "password": "pass", "role": "viewer"},
            headers=_auth_header(admin_token),
        )
        # alice 试图查 bob 的会话
        alice_token = _login(client, "alice", "pass")
        resp = client.get(
            "/api/users/bob/sessions", headers=_auth_header(alice_token)
        )
        assert resp.status_code == 403


# ─── 强制下线 API ─────────────────────────────────────────


class TestRevokeSessionsAPI:
    def test_revoke_sessions_admin(self, client):
        """DELETE /api/users/{username}/sessions 管理员强制下线"""
        admin_token = _login(client)
        # 创建用户并登录
        client.post(
            "/api/users",
            json={"username": "revoke_me", "password": "pass", "role": "viewer"},
            headers=_auth_header(admin_token),
        )
        user_token = _login(client, "revoke_me", "pass")

        # 验证用户有活跃会话
        resp = client.get(
            "/api/users/revoke_me/sessions", headers=_auth_header(admin_token)
        )
        assert len(resp.json()) >= 1

        # 管理员强制下线
        resp = client.delete(
            "/api/users/revoke_me/sessions", headers=_auth_header(admin_token)
        )
        assert resp.status_code == 200
        assert "下线" in resp.json()["message"]

        # 验证会话已消失
        resp = client.get(
            "/api/users/revoke_me/sessions", headers=_auth_header(admin_token)
        )
        assert resp.json() == []

        # 被强制下线的 token 失效
        me_resp = client.get("/api/auth/me", headers=_auth_header(user_token))
        assert me_resp.status_code == 401

    def test_revoke_sessions_denied(self, client):
        """DELETE /api/users/{username}/sessions 非管理员被拒"""
        admin_token = _login(client)
        client.post(
            "/api/users",
            json={"username": "revoke_victim", "password": "pass", "role": "viewer"},
            headers=_auth_header(admin_token),
        )
        client.post(
            "/api/users",
            json={"username": "revoke_attacker", "password": "pass", "role": "viewer"},
            headers=_auth_header(admin_token),
        )
        attacker_token = _login(client, "revoke_attacker", "pass")
        resp = client.delete(
            "/api/users/revoke_victim/sessions", headers=_auth_header(attacker_token)
        )
        assert resp.status_code == 403


# ─── 登录历史 API ─────────────────────────────────────────


class TestLoginHistoryAPI:
    def test_login_history_self(self, client):
        """GET /api/users/{username}/login-history 本人可查"""
        # 登录几次产生历史
        _login(client, "admin", "admin123")
        token = _login(client, "admin", "admin123")

        resp = client.get(
            "/api/users/admin/login-history", headers=_auth_header(token)
        )
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) >= 2
        for entry in data:
            assert entry["username"] == "admin"

    def test_login_history_denied(self, client):
        """GET /api/users/{username}/login-history 他人查被拒"""
        admin_token = _login(client)
        client.post(
            "/api/users",
            json={"username": "hist_user", "password": "pass", "role": "viewer"},
            headers=_auth_header(admin_token),
        )
        client.post(
            "/api/users",
            json={"username": "snooper", "password": "pass", "role": "viewer"},
            headers=_auth_header(admin_token),
        )
        # hist_user 登录产生历史
        _login(client, "hist_user", "pass")
        # snooper 试图查看 hist_user 的历史
        snooper_token = _login(client, "snooper", "pass")
        resp = client.get(
            "/api/users/hist_user/login-history", headers=_auth_header(snooper_token)
        )
        assert resp.status_code == 403

    def test_login_history_admin_can_view_others(self, client):
        """GET /api/users/{username}/login-history 管理员可查他人"""
        admin_token = _login(client)
        client.post(
            "/api/users",
            json={"username": "target_user", "password": "pass", "role": "viewer"},
            headers=_auth_header(admin_token),
        )
        # target_user 登录产生历史
        _login(client, "target_user", "pass")

        # 管理员查看 target_user 的历史
        resp = client.get(
            "/api/users/target_user/login-history", headers=_auth_header(admin_token)
        )
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) >= 1
        assert data[0]["username"] == "target_user"


# ─── 个人信息详情 API ──────────────────────────────────────


class TestMeDetailAPI:
    def test_me_detail(self, client):
        """GET /api/auth/me/detail 返回完整个人信息"""
        token = _login(client)
        resp = client.get("/api/auth/me/detail", headers=_auth_header(token))
        assert resp.status_code == 200
        data = resp.json()
        assert data["username"] == "admin"
        assert data["role"] == "admin"
        assert "last_login" in data
        assert "active_sessions" in data
        assert "preferences" in data
        # 登录后 last_login 应存在
        assert data["last_login"] is not None
        assert data["last_login"]["success"] is True

    def test_me_detail_unauthorized(self, client):
        """GET /api/auth/me/detail 未登录被拒 401"""
        resp = client.get("/api/auth/me/detail")
        assert resp.status_code == 401


# ─── 通知偏好 API ──────────────────────────────────────────


class TestPreferencesAPI:
    def test_get_preferences(self, client):
        """GET /api/auth/me/preferences 返回偏好"""
        token = _login(client)
        resp = client.get("/api/auth/me/preferences", headers=_auth_header(token))
        assert resp.status_code == 200
        data = resp.json()
        assert data["notify_alert"]["enabled"] is True
        assert data["notify_system"]["enabled"] is True
        assert data["notify_daily"]["enabled"] is False
        assert data["notify_alert"]["channels"] == ["webhook"]

    def test_update_preferences(self, client):
        """PUT /api/auth/preferences 更新通知偏好"""
        token = _login(client)
        resp = client.put(
            "/api/auth/preferences",
            json={
                "notify_alert": {"enabled": False, "channels": ["email", "webhook"]},
                "notify_daily": {"enabled": True},
            },
            headers=_auth_header(token),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["notify_alert"]["enabled"] is False
        assert data["notify_alert"]["channels"] == ["email", "webhook"]
        assert data["notify_daily"]["enabled"] is True
        # 验证持久化
        resp2 = client.get("/api/auth/me/preferences", headers=_auth_header(token))
        assert resp2.status_code == 200
        assert resp2.json()["notify_alert"]["enabled"] is False

    def test_update_preferences_unauthorized(self, client):
        """PUT /api/auth/preferences 未登录被拒 401"""
        resp = client.put("/api/auth/preferences", json={})
        assert resp.status_code == 401
