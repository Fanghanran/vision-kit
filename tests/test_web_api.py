"""Web API 测试 — 健康检查/告警/统计/白名单"""

import logging
import os
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from starlette.testclient import TestClient

from sentinelmind.core.types import Alert, AlertStatus, CameraState, CameraStatus, Event, Severity
from sentinelmind.web.api.app import SanitizeFilter, create_app, sanitize_config, verify_token

logging.basicConfig(level=logging.WARNING)


# ─── Fixtures ──────────────────────────────────────────────

class FakeDatabase:
    def __init__(self):
        self.alerts: list[Alert] = []

    def list_alerts(self, filters, page, page_size):
        return self.alerts, len(self.alerts)

    def get_alert(self, alert_id):
        for a in self.alerts:
            if a.alert_id == alert_id:
                return a
        return None

    def get_stats(self, filters=None):
        return {"total_count": 0, "by_severity": {}, "by_status": {}, "groups": []}

    def count_alerts_today(self):
        return 0

    def update_alert(self, alert_id, updates):
        pass


class FakePipeline:
    def __init__(self):
        self.uptime_seconds = 100.0

    def health(self):
        return SimpleNamespace(
            status="ok", uptime_seconds=self.uptime_seconds, gpu_utilization=0.3,
            gpu_memory_used_mb=2000, gpu_memory_total_mb=8000, queue_depth=5,
            inference_latency_p50_ms=12.345, inference_latency_p99_ms=45.678,
            active_cameras=2, total_cameras=4, today_alerts=3, llm_success_rate=0.95,
        )

    def get_camera_states(self):
        return {
            "cam-1": CameraState(camera_id="cam-1", status=CameraStatus.CONNECTED, current_fps=25.0, total_alerts=5),
        }

    def get_camera_thread(self, _):
        return None


@pytest.fixture
def client():
    db = FakeDatabase()
    pipeline = FakePipeline()

    # 创建临时 auth db 并获取 admin token
    from sentinelmind.auth.manager import get_auth_manager

    get_auth_manager.__globals__["_auth_manager"] = None
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        auth_db = f.name
    auth_mgr = get_auth_manager(db_path=auth_db)
    token = auth_mgr.login("admin", "admin123")

    app = create_app(database=db, pipeline=pipeline, config={})
    tc = TestClient(app)
    tc.headers["Authorization"] = f"Bearer {token}"

    yield tc

    get_auth_manager.__globals__["_auth_manager"] = None
    try:
        os.unlink(auth_db)
    except OSError:
        pass


# ─── 健康检查 ─────────────────────────────────────────────

class TestHealth:
    def test_health_ok(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["active_cameras"] == 2
        # 保留两位小数
        assert isinstance(data["inference_latency_p50_ms"], float)
        assert isinstance(data["inference_latency_p99_ms"], float)


# ─── 告警 ─────────────────────────────────────────────────

class TestAlerts:
    def test_list_empty(self, client):
        resp = client.get("/api/alerts")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0

    def test_list_with_filters(self, client):
        resp = client.get("/api/alerts?status=pending&severity=warning&page=1&page_size=10")
        assert resp.status_code == 200

    def test_alert_not_found(self, client):
        resp = client.get("/api/alerts/nonexistent")
        assert resp.status_code == 404

    def test_snapshot_not_found(self, client):
        resp = client.get("/api/alerts/nonexistent/snapshot")
        assert resp.status_code == 404

    def test_status_update_missing_field(self, client):
        resp = client.put("/api/alerts/nonexistent/status", json={})
        assert resp.status_code == 400


# ─── 统计 ─────────────────────────────────────────────────

class TestStats:
    def test_stats_today(self, client):
        resp = client.get("/api/stats?period=today")
        assert resp.status_code == 200
        assert "total_alerts" in resp.json()

    def test_stats_7d(self, client):
        resp = client.get("/api/stats?period=7d")
        assert resp.status_code == 200


# ─── 摄像头 ───────────────────────────────────────────────

class TestCameras:
    def test_list_cameras(self, client):
        resp = client.get("/api/cameras")
        assert resp.status_code == 200
        assert len(resp.json()) == 1

    def test_toggle_camera_not_found(self, client):
        resp = client.post("/api/cameras/nonexistent/toggle")
        assert resp.status_code == 404


# ─── 配置 & 安全 ─────────────────────────────────────────

class TestConfigAndSecurity:
    def test_config_sanitized(self, client):
        resp = client.get("/api/config")
        assert resp.status_code == 200
        data = resp.json()
        # 密码应脱敏
        if "notification" in data:
            email = data.get("notification", {}).get("email", {})
            pw = email.get("password", "")
            if pw:
                assert pw == "***"

    def test_path_whitelist_blocks_unknown(self, client):
        resp = client.get("/unknown/path")
        assert resp.status_code == 404

    def test_cors_headers(self, client):
        resp = client.get("/health", headers={"Origin": "http://localhost:3000"})
        assert "access-control-allow-origin" in resp.headers


# ─── Token 认证（遗留 API Token）──────────────────────────

class TestLegacyTokenAuth:
    def test_verify_token_no_config(self):
        assert verify_token("anything", "") is True  # 开发模式

    def test_verify_token_match(self):
        assert verify_token("tok", "tok") is True

    def test_verify_token_mismatch(self):
        assert verify_token("tok", "other") is False


# ─── 日志脱敏 ────────────────────────────────────────────

class TestSanitize:
    def test_password_masked(self):
        f = SanitizeFilter()
        record = logging.makeLogRecord({"msg": "password=hunter2 login"})
        f.filter(record)
        assert "hunter2" not in str(record.msg)
        assert "***" in str(record.msg)

    def test_bearer_token_masked(self):
        f = SanitizeFilter()
        record = logging.makeLogRecord({"msg": "Authorization: Bearer secret123"})
        f.filter(record)
        assert "secret123" not in str(record.msg)


# ─── 配置脱敏 ────────────────────────────────────────────

class TestSanitizeConfig:
    def test_password_key_masked(self):
        result = sanitize_config({"api_key": "sk-12345"})
        assert result["api_key"] == "***"

    def test_rtsp_url_masked(self):
        result = sanitize_config({"camera": {"rtsp_url": "rtsp://admin:pass@192.168.1.1/stream"}})
        assert "pass" not in str(result)

    def test_nested_dict(self):
        result = sanitize_config({"llm": {"api_key": "sk-secret"}})
        assert result["llm"]["api_key"] == "***"
