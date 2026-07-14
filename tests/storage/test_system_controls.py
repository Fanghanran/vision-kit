"""P4 系统控制面板测试 — CRUD + 白名单 + 类型校验 + API 权限"""

import tempfile
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from vision_agent.storage.database import DatabaseManager, SYSTEM_CONTROLS_DEFAULTS


@pytest.fixture
def db(tmp_path):
    """创建临时数据库实例"""
    db_path = tmp_path / "test.db"
    mgr = DatabaseManager({"type": "sqlite", "sqlite": {"path": str(db_path)}})
    mgr.connect()
    yield mgr
    mgr.close()


@pytest.fixture
def client(tmp_path):
    """创建带 admin token 的 TestClient"""
    from vision_agent.auth.manager import get_auth_manager

    get_auth_manager.__globals__["_auth_manager"] = None
    auth_db = tmp_path / "auth.db"
    auth_mgr = get_auth_manager(db_path=str(auth_db))
    token = auth_mgr.login("admin", "admin123")

    db_path = tmp_path / "test.db"
    database = DatabaseManager({"type": "sqlite", "sqlite": {"path": str(db_path)}})
    database.connect()

    from vision_agent.web.api.app import create_app

    app = create_app(database=database, pipeline=None, config={})
    tc = TestClient(app)
    tc.headers["Authorization"] = f"Bearer {token}"
    yield tc

    database.close()
    get_auth_manager.__globals__["_auth_manager"] = None


# ─── 后端逻辑测试 ─────────────────────────────────────────


class TestSystemControlsInit:
    def test_init_default_controls(self, db):
        """初始化后返回所有默认控制项"""
        controls = db.get_controls()
        for key, default_val in SYSTEM_CONTROLS_DEFAULTS.items():
            assert key in controls, f"缺少控制项: {key}"
            assert controls[key]["value"] == default_val, f"{key} 默认值不正确"

    def test_init_idempotent(self, db):
        """重复初始化不覆盖已修改的值"""
        db.update_control("llm.enabled", False, "admin")
        # 模拟重新初始化
        db._init_system_controls()
        assert db.get_control_value("llm.enabled") is False


class TestGetControl:
    def test_get_control_value_exists(self, db):
        """查询存在的 key 返回正确值"""
        val = db.get_control_value("llm.enabled")
        assert val is True

    def test_get_control_value_default(self, db):
        """查询不存在的 key 返回 None"""
        val = db.get_control_value("nonexistent.key")
        assert val is None

    def test_get_control_detail(self, db):
        """获取控制项详情含 updated_by 和 updated_at"""
        db.update_control("llm.enabled", False, "admin")
        detail = db.get_control("llm.enabled")
        assert detail is not None
        assert detail["value"] is False
        assert detail["updated_by"] == "admin"
        assert detail["updated_at"] > 0


class TestUpdateControl:
    def test_update_success(self, db):
        """更新成功后值正确"""
        ok = db.update_control("llm.enabled", False, "admin")
        assert ok is True
        assert db.get_control_value("llm.enabled") is False

    def test_update_invalid_key(self, db):
        """非法 key 返回 False"""
        ok = db.update_control("nonexistent.key", True, "admin")
        assert ok is False

    def test_update_bool_type_check(self, db):
        """布尔项传入 string 返回 False"""
        ok = db.update_control("llm.enabled", "true", "admin")
        assert ok is False

    def test_update_bool_accepts_true(self, db):
        """布尔项接受 True"""
        ok = db.update_control("llm.enabled", True, "admin")
        assert ok is True
        assert db.get_control_value("llm.enabled") is True


class TestUpdateControlsBatch:
    def test_batch_update_success(self, db):
        """批量更新成功"""
        controls = {"llm.enabled": False, "audit.enabled": False}
        count = db.update_controls(controls, "admin")
        assert count == 2
        assert db.get_control_value("llm.enabled") is False
        assert db.get_control_value("audit.enabled") is False

    def test_batch_partial_invalid(self, db):
        """批量中含非法 key，合法 key 仍更新"""
        controls = {"llm.enabled": False, "nonexistent.key": True}
        count = db.update_controls(controls, "admin")
        assert count == 1
        assert db.get_control_value("llm.enabled") is False

    def test_batch_type_error_skipped(self, db):
        """批量中含类型错误的项被跳过"""
        controls = {"llm.enabled": False, "audit.enabled": "invalid"}
        count = db.update_controls(controls, "admin")
        assert count == 1
        assert db.get_control_value("llm.enabled") is False


# ─── API 端点测试 ─────────────────────────────────────────


class TestControlsAPI:
    def test_get_controls_admin(self, client):
        """管理员获取控制项成功"""
        resp = client.get("/api/system/controls")
        assert resp.status_code == 200
        data = resp.json()
        assert "controls" in data
        assert "llm.enabled" in data["controls"]

    def test_update_control_admin(self, client):
        """管理员更新控制项成功"""
        resp = client.put("/api/system/controls/llm.enabled", json={"value": False})
        assert resp.status_code == 200
        data = resp.json()
        assert data["key"] == "llm.enabled"
        assert data["value"] is False

    def test_update_control_invalid_key(self, client):
        """非法 key 返回 400"""
        resp = client.put("/api/system/controls/nonexistent.key", json={"value": True})
        assert resp.status_code == 400

    def test_batch_update_admin(self, client):
        """管理员批量更新成功"""
        resp = client.put("/api/system/controls", json={
            "controls": {"llm.enabled": False, "audit.enabled": False}
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["updated"] == 2

    def test_batch_update_all_invalid(self, client):
        """批量更新全部失败返回 400"""
        resp = client.put("/api/system/controls", json={
            "controls": {"nonexistent.key": True}
        })
        assert resp.status_code == 400
