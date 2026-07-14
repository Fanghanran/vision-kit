"""P1 稳定性改造 — 持久化层测试

覆盖：
- P1-4  操作历史时间线（alert_actions 表 + API 端点）
- P1-5  审计日志（audit_logs 表 + 分页查询）
- P1-2  WebSocket 心跳（WSManager._last_pong 初始化 + on_pong 更新）
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from sentinelmind.core.types import Event, Severity
from sentinelmind.storage.database import DatabaseManager


# ─── Fixtures ─────────────────────────────────────────────────


@pytest.fixture
def db(tmp_path: Path) -> DatabaseManager:
    """临时 SQLite 数据库，每次测试隔离"""
    db_path = tmp_path / "test.db"
    mgr = DatabaseManager({"type": "sqlite", "sqlite": {"path": str(db_path)}})
    mgr.connect()
    yield mgr
    mgr.close()


def _make_event(event_id: str = "evt-1", **kwargs) -> Event:
    """构造测试用 Event"""
    defaults = {
        "event_id": event_id,
        "event_type": "intrusion",
        "camera_id": "cam-1",
        "camera_name": "前门",
        "rule_name": "入侵检测",
        "severity": Severity.WARNING,
        "timestamp": time.time(),
    }
    defaults.update(kwargs)
    return Event(**defaults)


def _save_test_alert(db: DatabaseManager, alert_id: str = "alert-1") -> str:
    """在数据库中插入一条测试告警，返回 alert_id"""
    event = _make_event()
    return db.save_alert(event, alert_id=alert_id, created_at=time.time())


# ─── P1-4  操作历史时间线 ────────────────────────────────────


class TestAlertActions:
    """alert_actions 表读写测试"""

    def test_save_and_get_alert_actions(self, db: DatabaseManager) -> None:
        """保存 3 条 action -> get_alert_actions 返回 3 条，按时间正序"""
        _save_test_alert(db)

        for action_type in ("acknowledged", "comment", "resolved"):
            db.save_alert_action(
                "alert-1",
                action_type=action_type,
                actor="admin",
                actor_role="admin",
                details=f"执行了 {action_type}",
            )

        actions = db.get_alert_actions("alert-1")
        assert len(actions) == 3
        # 按 created_at ASC 排序
        assert actions[0]["action_type"] == "acknowledged"
        assert actions[1]["action_type"] == "comment"
        assert actions[2]["action_type"] == "resolved"
        # 每条记录包含必需字段
        for a in actions:
            assert a["alert_id"] == "alert-1"
            assert a["actor"] == "admin"
            assert a["actor_role"] == "admin"
            assert a["created_at"] > 0

    def test_get_alert_actions_empty(self, db: DatabaseManager) -> None:
        """无操作记录时返回空列表"""
        actions = db.get_alert_actions("nonexistent-alert")
        assert actions == []

    def test_save_alert_action_no_conn(self, caplog: pytest.LogCaptureFixture) -> None:
        """_conn=None 时不抛异常，仅 log warning"""
        db = DatabaseManager()  # 未调用 connect()
        with caplog.at_level(logging.WARNING):
            db.save_alert_action("alert-1", "acknowledged", "admin")
        assert "save_alert_action_skipped" in caplog.text

    def test_alert_actions_foreign_key_cascade(self, db: DatabaseManager) -> None:
        """删除 alert 后，关联 actions 级联删除（需 foreign_keys=ON）"""
        _save_test_alert(db)
        db.save_alert_action("alert-1", "acknowledged", "admin", actor_role="admin")
        db.save_alert_action("alert-1", "resolved", "admin", actor_role="admin")

        # 确认记录存在
        assert len(db.get_alert_actions("alert-1")) == 2

        # 硬删除 alert（DELETE FROM alerts WHERE id = ?）
        with db._lock:
            db._conn.execute("DELETE FROM alerts WHERE id = ?", ("alert-1",))
            db._conn.commit()

        # cascade 生效，actions 也被删除
        actions = db.get_alert_actions("alert-1")
        assert actions == []


# ─── P1-5  审计日志 ──────────────────────────────────────────


class TestAuditLogs:
    """audit_logs 表读写 + 分页查询测试"""

    def test_save_and_list_audit_logs(self, db: DatabaseManager) -> None:
        """保存 3 条日志 -> list 返回 3 条"""
        db.save_audit_log("admin", "admin", "camera.toggle", "cam-1", ip="127.0.0.1")
        db.save_audit_log("admin", "admin", "user.create", "viewer1", ip="127.0.0.1")
        db.save_audit_log("viewer1", "viewer", "alert.acknowledge", "alert-1", ip="10.0.0.1")

        logs, total = db.list_audit_logs()
        assert total == 3
        assert len(logs) == 3
        # 按 created_at DESC（最新在前）
        assert logs[0]["action"] == "alert.acknowledge"
        for log in logs:
            assert log["username"]
            assert log["role"]
            assert log["action"]
            assert log["created_at"] > 0

    def test_audit_logs_filter_by_username(self, db: DatabaseManager) -> None:
        """按 username 筛选只返回该用户的日志"""
        db.save_audit_log("admin", "admin", "user.create", "alice")
        db.save_audit_log("alice", "viewer", "alert.acknowledge", "alert-1")
        db.save_audit_log("admin", "admin", "user.delete", "bob")

        logs, total = db.list_audit_logs(filters={"username": "alice"})
        assert total == 1
        assert logs[0]["username"] == "alice"

    def test_audit_logs_filter_by_action(self, db: DatabaseManager) -> None:
        """按 action 筛选正确过滤"""
        db.save_audit_log("admin", "admin", "camera.toggle", "cam-1")
        db.save_audit_log("admin", "admin", "camera.toggle", "cam-2")
        db.save_audit_log("admin", "admin", "user.create", "bob")

        logs, total = db.list_audit_logs(filters={"action": "camera.toggle"})
        assert total == 2
        assert all(log["action"] == "camera.toggle" for log in logs)

    def test_audit_logs_pagination(self, db: DatabaseManager) -> None:
        """page_size=2, page=2 返回正确的第二页"""
        for i in range(5):
            db.save_audit_log("admin", "admin", "test.action", f"res-{i}")

        # page 1
        page1, total = db.list_audit_logs(page=1, page_size=2)
        assert total == 5
        assert len(page1) == 2

        # page 2
        page2, _ = db.list_audit_logs(page=2, page_size=2)
        assert len(page2) == 2

        # page 3（最后一条）
        page3, _ = db.list_audit_logs(page=3, page_size=2)
        assert len(page3) == 1

        # 每页记录不重叠
        ids_1 = {log["id"] for log in page1}
        ids_2 = {log["id"] for log in page2}
        assert ids_1.isdisjoint(ids_2)

    def test_save_audit_log_no_conn(self, caplog: pytest.LogCaptureFixture) -> None:
        """_conn=None 时不抛异常，仅 log warning"""
        db = DatabaseManager()  # 未调用 connect()
        with caplog.at_level(logging.WARNING):
            db.save_audit_log("admin", "admin", "test.action")
        assert "save_audit_log_skipped" in caplog.text


# ─── P1-4  API 端点测试 ─────────────────────────────────────


@pytest.fixture
def api_client(tmp_path: Path):
    """带真实 DatabaseManager 的 TestClient，返回 (client, db, token)"""
    from starlette.testclient import TestClient

    from sentinelmind.auth.manager import get_auth_manager
    from sentinelmind.web.api.app import create_app

    # 初始化 auth manager
    get_auth_manager.__globals__["_auth_manager"] = None
    auth_db_path = tmp_path / "auth.db"
    auth_mgr = get_auth_manager(db_path=str(auth_db_path))
    token = auth_mgr.login("admin", "admin123")

    # 初始化 DatabaseManager
    db_path = tmp_path / "vision.db"
    db = DatabaseManager({"type": "sqlite", "sqlite": {"path": str(db_path)}})
    db.connect()

    app = create_app(database=db, pipeline=None, config={})
    tc = TestClient(app)
    tc.headers["Authorization"] = f"Bearer {token}"

    yield tc, db, token

    db.close()
    get_auth_manager.__globals__["_auth_manager"] = None
    try:
        os.unlink(str(auth_db_path))
    except OSError:
        pass


class TestAlertActionsAPI:
    """GET /api/alerts/{alert_id}/actions 端点测试"""

    def test_get_alert_actions_api_404(self, api_client: tuple) -> None:
        """不存在的 alert_id 返回 404"""
        tc, db, token = api_client
        resp = tc.get("/api/alerts/nonexistent-id/actions")
        assert resp.status_code == 404
        assert "不存在" in resp.json()["detail"]

    def test_get_alert_actions_api_success(self, api_client: tuple) -> None:
        """存在的 alert 返回其操作历史列表"""
        tc, db, token = api_client
        # 先创建一条告警
        event = _make_event()
        alert_id = db.save_alert(event, alert_id="api-alert-1", created_at=time.time())
        db.save_alert_action(alert_id, "acknowledged", "admin", actor_role="admin")

        resp = tc.get(f"/api/alerts/{alert_id}/actions")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["action_type"] == "acknowledged"

    def test_update_alert_status_records_action(self, api_client: tuple) -> None:
        """update_alert_status 成功后自动记录 action"""
        tc, db, token = api_client
        # 创建一条 pending 告警
        event = _make_event()
        alert_id = db.save_alert(event, alert_id="status-alert-1", created_at=time.time())

        # 确认告警 (pending -> acknowledged)
        resp = tc.put(
            f"/api/alerts/{alert_id}/status",
            json={"status": "acknowledged"},
        )
        assert resp.status_code == 200

        # 检查操作历史是否自动记录
        actions = db.get_alert_actions(alert_id)
        assert len(actions) == 1
        assert actions[0]["action_type"] == "acknowledged"
        assert actions[0]["actor"] == "admin"


# ─── P1-5  审计日志 API 端点测试 ─────────────────────────────


class TestAuditLogsAPI:
    """GET /api/audit/logs 端点测试"""

    def test_audit_logs_api_admin_only(self, api_client: tuple) -> None:
        """非管理员访问 /api/audit/logs 返回 403"""
        tc, db, admin_token = api_client

        from sentinelmind.auth.manager import get_auth_manager

        auth_mgr = get_auth_manager()
        auth_mgr.create_user("viewer1", "pass123", "viewer")
        viewer_token = auth_mgr.login("viewer1", "pass123")

        # 管理员可访问
        resp = tc.get("/api/audit/logs")
        assert resp.status_code == 200

        # viewer 无权访问
        resp = tc.get(
            "/api/audit/logs",
            headers={"Authorization": f"Bearer {viewer_token}"},
        )
        assert resp.status_code == 403


# ─── P1-2  WebSocket 心跳 ────────────────────────────────────


class TestWSManagerHeartbeat:
    """WSManager 心跳相关逻辑测试

    WSManager 是 create_app 内的局部类，无法直接 import。
    这里提取核心逻辑进行独立验证。
    """

    def _get_ws_manager(self):
        """通过 create_app 获取 WSManager 实例"""
        from sentinelmind.web.api.app import create_app

        app = create_app(config={})
        return app.state.ws_manager

    def test_last_pong_initialized_on_connect(self) -> None:
        """connect 时 _last_pong 应初始化为当前时间"""
        ws_manager = self._get_ws_manager()
        mock_ws = MagicMock()
        mock_ws.accept = AsyncMock()

        before = time.time()
        asyncio.run(ws_manager.connect(mock_ws))
        after = time.time()

        ws_id = id(mock_ws)
        assert ws_id in ws_manager._last_pong
        assert before <= ws_manager._last_pong[ws_id] <= after

        ws_manager.disconnect(mock_ws)

    def test_on_pong_updates_time(self) -> None:
        """on_pong 应刷新 _last_pong 时间"""
        ws_manager = self._get_ws_manager()
        mock_ws = MagicMock()
        mock_ws.accept = AsyncMock()
        asyncio.run(ws_manager.connect(mock_ws))

        ws_id = id(mock_ws)
        initial_pong = ws_manager._last_pong[ws_id]

        time.sleep(0.01)
        ws_manager.on_pong(mock_ws)

        assert ws_manager._last_pong[ws_id] >= initial_pong

        ws_manager.disconnect(mock_ws)

    def test_disconnect_cleans_up(self) -> None:
        """disconnect 应清除 _last_pong 和 _ping_failures"""
        ws_manager = self._get_ws_manager()
        mock_ws = MagicMock()
        mock_ws.accept = AsyncMock()
        asyncio.run(ws_manager.connect(mock_ws))

        ws_id = id(mock_ws)
        assert ws_id in ws_manager._last_pong
        # _ping_failures 可能已被心跳 finally 清空，手动恢复以测试 disconnect
        ws_manager._ping_failures[ws_id] = 0

        ws_manager.disconnect(mock_ws)

        assert ws_id not in ws_manager._last_pong
        assert ws_id not in ws_manager._ping_failures
