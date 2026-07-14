"""认证体系集成测试 — 用户登录、权限、Token、限流、CRUD"""

import os
import tempfile
import time

import pytest
from starlette.testclient import TestClient

from sentinelmind.auth.manager import AuthManager
from sentinelmind.auth.models import Role
from sentinelmind.web.api.app import create_app


# ─── Fixtures ──────────────────────────────────────────────

@pytest.fixture
def auth_mgr():
    """全新 AuthManager（临时文件，避免跨线程内存 DB 问题）"""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    mgr = AuthManager(db_path=path)
    yield mgr
    try:
        os.unlink(path)
    except OSError:
        pass
    try:
        os.unlink(path + "-shm")
    except OSError:
        pass
    try:
        os.unlink(path + "-wal")
    except OSError:
        pass


@pytest.fixture(scope="module")
def auth_mgr_for_client():
    """供 API 测试用的独立 AuthManager"""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    mgr = AuthManager(db_path=path)
    yield mgr
    try:
        os.unlink(path)
    except OSError:
        pass
    try:
        os.unlink(path + "-shm")
    except OSError:
        pass
    try:
        os.unlink(path + "-wal")
    except OSError:
        pass


@pytest.fixture
def client(auth_mgr_for_client):
    """带 auth 的 TestClient"""
    import sentinelmind.auth.manager as auth_mod

    _orig = auth_mod._auth_manager
    auth_mod._auth_manager = auth_mgr_for_client
    try:
        app = create_app(database=None, pipeline=None, config={})
        return TestClient(app)
    finally:
        auth_mod._auth_manager = _orig


# ─── AuthManager 用户管理 ─────────────────────────────────

class TestAuthManagerUsers:
    def test_create_user_success(self, auth_mgr):
        user = auth_mgr.create_user("test1", "pass123", Role.VIEWER)
        assert user["username"] == "test1"
        assert user["role"] == "viewer"
        assert user["email"] == ""

    def test_create_user_duplicate(self, auth_mgr):
        auth_mgr.create_user("dup", "pass")
        with pytest.raises(ValueError, match="已存在"):
            auth_mgr.create_user("dup", "pass2")

    def test_default_admin_exists(self, auth_mgr):
        admin = auth_mgr.get_user("admin")
        assert admin is not None
        assert admin.is_admin

    def test_delete_user(self, auth_mgr):
        auth_mgr.create_user("todel", "pass")
        auth_mgr.delete_user("todel")
        assert auth_mgr.get_user("todel") is None

    def test_delete_default_admin_raises(self, auth_mgr):
        with pytest.raises(ValueError, match="默认管理员"):
            auth_mgr.delete_user("admin")

    def test_list_users(self, auth_mgr):
        auth_mgr.create_user("u1", "p", email="u1@test.com")
        users = auth_mgr.list_users()
        assert len(users) >= 2  # admin + u1
        assert any(u["email"] == "u1@test.com" for u in users)

    def test_update_user(self, auth_mgr):
        auth_mgr.create_user("u2", "p")
        updated = auth_mgr.update_user("u2", email="new@test.com", role="admin")
        assert updated["email"] == "new@test.com"
        assert updated["role"] == "admin"

    def test_update_user_not_found(self, auth_mgr):
        with pytest.raises(ValueError, match="不存在"):
            auth_mgr.update_user("nobody", email="x")


# ─── 密码 ─────────────────────────────────────────────────

class TestPassword:
    def test_hash_and_verify(self, auth_mgr):
        h = auth_mgr._hash_password("secret")
        assert auth_mgr.verify_password("secret", h)
        assert not auth_mgr.verify_password("wrong", h)

    def test_hash_is_unique(self, auth_mgr):
        h1 = auth_mgr._hash_password("pass")
        h2 = auth_mgr._hash_password("pass")
        assert h1 != h2  # 不同 salt
        assert auth_mgr.verify_password("pass", h1)
        assert auth_mgr.verify_password("pass", h2)


# ─── Token ─────────────────────────────────────────────────

class TestToken:
    def test_login_returns_token(self, auth_mgr):
        token = auth_mgr.login("admin", "admin123")
        assert token is not None
        assert len(token) > 20

    def test_login_wrong_password(self, auth_mgr):
        assert auth_mgr.login("admin", "wrong") is None

    def test_verify_token_returns_user(self, auth_mgr):
        token = auth_mgr.login("admin", "admin123")
        user = auth_mgr.verify_token(token)
        assert user is not None
        assert user.username == "admin"

    def test_verify_invalid_token(self, auth_mgr):
        assert auth_mgr.verify_token("invalid-token") is None

    def test_logout_invalidates_token(self, auth_mgr):
        token = auth_mgr.login("admin", "admin123")
        auth_mgr.logout("admin")
        assert auth_mgr.verify_token(token) is None

    def test_disabled_user_login_fails(self, auth_mgr):
        auth_mgr.create_user("dis", "pass")
        auth_mgr.update_user("dis", status=1)  # 禁用
        assert auth_mgr.login("dis", "pass") is None

    def test_login_throttle(self, auth_mgr):
        auth_mgr.create_user("lockme", "right")
        for _ in range(6):
            auth_mgr.login("lockme", "wrong")
        # 锁定时正确密码也无法登录
        assert auth_mgr.login("lockme", "right") is None


# ─── 认证 API ─────────────────────────────────────────────

class TestAuthAPI:
    def test_login_success(self, client):
        resp = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
        assert resp.status_code == 200
        assert "token" in resp.json()
        assert resp.json()["user"]["username"] == "admin"

    def test_login_wrong_password(self, client):
        resp = client.post("/api/auth/login", json={"username": "admin", "password": "wrong"})
        assert resp.status_code == 401

    def test_me_with_token(self, client):
        login = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
        token = login.json()["token"]
        resp = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        assert resp.json()["username"] == "admin"

    def test_me_without_token(self, client):
        resp = client.get("/api/auth/me")
        assert resp.status_code == 401

    def test_logout(self, client):
        login = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
        token = login.json()["token"]
        resp = client.post("/api/auth/logout", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200

    def test_change_password(self, auth_mgr_for_client, client):
        # 创建独立用户避免影响其他测试
        auth_mgr_for_client.create_user("pwtest", "oldpass")
        login = client.post("/api/auth/login", json={"username": "pwtest", "password": "oldpass"})
        token = login.json()["token"]
        resp = client.post("/api/auth/change-password",
                           json={"old_password": "oldpass", "new_password": "newpass"},
                           headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        # 新密码可用
        login2 = client.post("/api/auth/login", json={"username": "pwtest", "password": "newpass"})
        assert login2.status_code == 200

    def test_update_profile(self, client):
        login = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
        token = login.json()["token"]
        resp = client.put("/api/auth/profile", json={"email": "admin@test.com"},
                          headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        assert resp.json()["email"] == "admin@test.com"


# ─── 用户管理 API（仅 admin）────────────────────────────

class TestUserManagementAPI:
    def test_admin_can_list_users(self, client):
        login = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
        token = login.json()["token"]
        resp = client.get("/api/users", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_admin_can_create_user(self, client):
        login = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
        token = login.json()["token"]
        resp = client.post("/api/users", json={"username": "new", "password": "pass", "email": "new@test.com"},
                           headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        assert resp.json()["username"] == "new"

    def test_admin_can_delete_user(self, client):
        login = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
        token = login.json()["token"]
        client.post("/api/users", json={"username": "tod", "password": "p"},
                    headers={"Authorization": f"Bearer {token}"})
        resp = client.delete("/api/users/tod", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200

    def test_admin_cannot_delete_self(self, client):
        login = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
        token = login.json()["token"]
        resp = client.delete("/api/users/admin", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 400

    def test_admin_can_update_user(self, client):
        login = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
        token = login.json()["token"]
        client.post("/api/users", json={"username": "upd", "password": "p"},
                    headers={"Authorization": f"Bearer {token}"})
        resp = client.put("/api/users/upd", json={"email": "x@y.com", "role": "operator", "status": 0},
                          headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        assert resp.json()["role"] == "operator"
