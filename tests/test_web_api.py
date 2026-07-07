"""Tests for vision_agent.web.api.app — Web API module

Covers:
- Path whitelist middleware
- Bearer Token authentication (require_auth / verify_token)
- GET /health (ok / degraded / unhealthy)
- GET /api/cameras
- GET /api/alerts (pagination, filtering)
- GET /api/alerts/{id} (detail, 404)
- PUT /api/alerts/{id}/status (valid transitions, invalid transitions, missing field)
- GET /api/stats
- GET /api/config (sanitization)
- SanitizeFilter (log sanitization)
- sanitize_config() function
- verify_token() function
"""

from __future__ import annotations

import logging
import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from starlette.testclient import TestClient

from vision_agent.core.types import (
    Alert,
    AlertStatus,
    CameraState,
    CameraStatus,
    Event,
    LLMAnalysis,
    Severity,
)
from vision_agent.web.api.app import SanitizeFilter, sanitize_config, verify_token, create_app


# ─── Constants ────────────────────────────────────────────────

VALID_TOKEN = "test-secret-token-123"
API_TOKEN_CONFIG = {"api_token": VALID_TOKEN, "cors_origins": ["*"]}


# ─── Helpers ──────────────────────────────────────────────────


def _make_alert(
    alert_id: str = "alert-001",
    event_type: str = "intrusion",
    camera_id: str = "cam-1",
    camera_name: str = "Front Door",
    severity: Severity = Severity.WARNING,
    status: AlertStatus = AlertStatus.PENDING,
    snapshot_path: str = "",
    video_clip_path: str = "",
    llm_risk_level: str = "medium",
) -> Alert:
    """Build an Alert instance with sensible defaults."""
    return Alert(
        alert_id=alert_id,
        event=Event(
            event_id="evt-001",
            event_type=event_type,
            camera_id=camera_id,
            camera_name=camera_name,
            rule_name="zone_intrusion",
            severity=severity,
            snapshot_path=snapshot_path,
        ),
        llm_analysis=LLMAnalysis(risk_level=llm_risk_level) if llm_risk_level else None,
        video_clip_path=video_clip_path,
        status=status,
        created_at=time.time(),
    )


def _make_camera_state(camera_id: str = "cam-1") -> CameraState:
    return CameraState(
        camera_id=camera_id,
        status=CameraStatus.CONNECTED,
        current_fps=25.0,
        gpu_latency_ms=12.5,
        queue_size=2,
        total_frames=1000,
        total_alerts=5,
    )


def _make_health(status: str = "ok", active_cameras: int = 2) -> SimpleNamespace:
    return SimpleNamespace(
        status=status,
        uptime_seconds=3600.0,
        gpu_utilization=0.65,
        gpu_memory_used_mb=2048,
        gpu_memory_total_mb=8192,
        queue_depth=3,
        inference_latency_p50_ms=15.0,
        inference_latency_p99_ms=45.0,
        active_cameras=active_cameras,
        total_cameras=3,
        today_alerts=12,
        llm_success_rate=0.98,
    )


def _mock_database(
    alerts: list[Alert] | None = None,
    total: int = 1,
    stats: dict | None = None,
) -> MagicMock:
    """Create a mock DatabaseManager."""
    db = MagicMock()
    alerts = alerts or [_make_alert()]

    db.list_alerts.return_value = (alerts, total)
    db.get_alert.side_effect = lambda aid: next(
        (a for a in alerts if a.alert_id == aid), None
    )
    db.get_stats.return_value = stats or {
        "total_count": 42,
        "by_severity": {"warning": 30, "critical": 12},
        "by_status": {"pending": 10, "resolved": 32},
    }
    return db


def _mock_pipeline(
    health_status: str = "ok",
    active_cameras: int = 2,
) -> MagicMock:
    """Create a mock Pipeline instance."""
    pl = MagicMock()
    pl.health.return_value = _make_health(health_status, active_cameras)
    pl.uptime_seconds = 3600.0
    pl.get_camera_states.return_value = {
        "cam-1": _make_camera_state("cam-1"),
        "cam-2": _make_camera_state("cam-2"),
    }
    return pl


def _auth_headers(token: str = VALID_TOKEN) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _create_app(**overrides: Any) -> Any:
    """Create a fully-mocked app with sensible defaults."""
    defaults = dict(
        database=_mock_database(),
        pipeline=_mock_pipeline(),
        config=API_TOKEN_CONFIG,
    )
    defaults.update(overrides)
    return create_app(**defaults)


# ─── verify_token() ──────────────────────────────────────────


class TestVerifyToken:
    def test_valid_token(self):
        assert verify_token("abc", "abc") is True

    def test_invalid_token(self):
        assert verify_token("abc", "xyz") is False

    def test_empty_api_token_skips_auth(self):
        """When api_token is empty/None, authentication is skipped."""
        assert verify_token("anything", "") is True
        assert verify_token("anything", None) is True

    def test_case_sensitive(self):
        assert verify_token("ABC", "abc") is False

    def test_constant_time_comparison(self):
        """verify_token uses hmac.compare_digest (no early exit)."""
        import hmac as _hmac

        with patch("vision_agent.web.api.app.hmac") as mock_hmac:
            mock_hmac.compare_digest.return_value = True
            result = verify_token("tok", "tok")
            mock_hmac.compare_digest.assert_called_once_with("tok", "tok")
            assert result is True


# ─── sanitize_config() ──────────────────────────────────────


class TestSanitizeConfig:
    def test_password_replaced(self):
        cfg = {"database": {"password": "hunter2", "host": "localhost"}}
        result = sanitize_config(cfg)
        assert result["database"]["password"] == "***"
        assert result["database"]["host"] == "localhost"

    def test_api_key_replaced(self):
        cfg = {"api_key": "sk-12345"}
        assert sanitize_config(cfg)["api_key"] == "***"

    def test_token_replaced(self):
        cfg = {"api_token": "secret-value", "timeout": 30}
        result = sanitize_config(cfg)
        assert result["api_token"] == "***"
        assert result["timeout"] == 30

    def test_secret_replaced(self):
        cfg = {"jwt_secret": "s3cr3t"}
        assert sanitize_config(cfg)["jwt_secret"] == "***"

    def test_credential_replaced(self):
        cfg = {"db_credential": "admin:pw"}
        assert sanitize_config(cfg)["db_credential"] == "***"

    def test_rtsp_url_masked(self):
        cfg = {"stream": "rtsp://admin:password123@192.168.1.100/stream1"}
        result = sanitize_config(cfg)
        assert "password123" not in result["stream"]
        assert "rtsp://admin:***@" in result["stream"]

    def test_nested_dict_recursive(self):
        cfg = {"level1": {"level2": {"password": "deep"}}}
        result = sanitize_config(cfg)
        assert result["level1"]["level2"]["password"] == "***"

    def test_list_recursive(self):
        cfg = {
            "cameras": [
                {"name": "cam1", "password": "p1"},
                {"name": "cam2", "password": "p2"},
            ]
        }
        result = sanitize_config(cfg)
        assert result["cameras"][0]["password"] == "***"
        assert result["cameras"][1]["password"] == "***"
        assert result["cameras"][0]["name"] == "cam1"

    def test_list_of_primitives_unchanged(self):
        cfg = {"tags": ["a", "b", "c"]}
        result = sanitize_config(cfg)
        assert result["tags"] == ["a", "b", "c"]

    def test_non_string_values_preserved(self):
        cfg = {"port": 8080, "debug": True, "ratio": 0.5}
        result = sanitize_config(cfg)
        assert result == {"port": 8080, "debug": True, "ratio": 0.5}

    def test_empty_dict(self):
        assert sanitize_config({}) == {}

    def test_case_insensitive_key_match(self):
        cfg = {"API_KEY": "val1", "Api_Token": "val2", "PASSWORD": "val3"}
        result = sanitize_config(cfg)
        assert result["API_KEY"] == "***"
        assert result["Api_Token"] == "***"
        assert result["PASSWORD"] == "***"

    def test_safe_string_not_masked(self):
        cfg = {"hostname": "example.com", "path": "/var/data"}
        result = sanitize_config(cfg)
        assert result["hostname"] == "example.com"
        assert result["path"] == "/var/data"


# ─── SanitizeFilter ──────────────────────────────────────────


class TestSanitizeFilter:
    def _make_record(self, msg: str) -> logging.LogRecord:
        return logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg=msg, args=(), exc_info=None,
        )

    def test_password_masked(self):
        f = SanitizeFilter()
        rec = self._make_record("password=hunter2 host=localhost")
        f.filter(rec)
        assert "hunter2" not in rec.msg
        assert "password=***" in rec.msg
        assert "host=localhost" in rec.msg

    def test_api_key_masked(self):
        f = SanitizeFilter()
        rec = self._make_record("api_key=sk-abcdef123456")
        f.filter(rec)
        assert "sk-abcdef123456" not in rec.msg
        assert "api_key=***" in rec.msg

    def test_token_masked(self):
        f = SanitizeFilter()
        rec = self._make_record("token=mytokenvalue extra=ok")
        f.filter(rec)
        assert "mytokenvalue" not in rec.msg
        assert "token=***" in rec.msg

    def test_rtsp_url_masked(self):
        f = SanitizeFilter()
        rec = self._make_record("stream=rtsp://admin:secret123@10.0.0.1/stream")
        f.filter(rec)
        assert "secret123" not in rec.msg
        assert "rtsp://admin:***@" in rec.msg

    def test_bearer_token_masked(self):
        f = SanitizeFilter()
        rec = self._make_record("Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.payload.sig")
        f.filter(rec)
        assert "eyJhbGciOiJIUzI1NiJ9.payload.sig" not in rec.msg
        assert "Bearer ***" in rec.msg

    def test_case_insensitive_key_match(self):
        f = SanitizeFilter()
        rec = self._make_record("PASSWORD=plaintext123")
        f.filter(rec)
        assert "plaintext123" not in rec.msg

    def test_bearer_case_insensitive(self):
        f = SanitizeFilter()
        rec = self._make_record("bearer some-token-value")
        f.filter(rec)
        assert "Bearer ***" in rec.msg

    def test_non_string_msg_passes(self):
        """Non-string messages should not cause errors."""
        f = SanitizeFilter()
        rec = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg=42, args=(), exc_info=None,  # type: ignore[arg-type]
        )
        result = f.filter(rec)
        assert result is True
        assert rec.msg == 42

    def test_no_sensitive_data_unchanged(self):
        f = SanitizeFilter()
        original = "Processing frame 1234 for camera cam-1"
        rec = self._make_record(original)
        f.filter(rec)
        assert rec.msg == original

    def test_filter_always_returns_true(self):
        f = SanitizeFilter()
        rec = self._make_record("password=secret")
        assert f.filter(rec) is True


# ─── Path Whitelist Middleware ────────────────────────────────


class TestPathWhitelist:
    def test_health_allowed(self):
        app = _create_app(database=None, pipeline=None)
        client = TestClient(app)
        resp = client.get("/health")
        assert resp.status_code == 200

    def test_api_path_allowed(self):
        app = _create_app()
        client = TestClient(app)
        resp = client.get("/api/cameras", headers=_auth_headers())
        assert resp.status_code == 200

    def test_root_path_allowed(self):
        app = _create_app(database=None, pipeline=None)
        client = TestClient(app)
        resp = client.get("/")
        # Root "/" is in the whitelist but no route defined => 404 from FastAPI
        assert resp.status_code == 404

    def test_disallowed_path_returns_404(self):
        app = _create_app(database=None, pipeline=None)
        client = TestClient(app)
        resp = client.get("/forbidden/secret")
        assert resp.status_code == 404
        assert resp.json()["detail"] == "Not Found"

    def test_dot_env_blocked(self):
        app = _create_app(database=None, pipeline=None)
        client = TestClient(app)
        resp = client.get("/.env")
        assert resp.status_code == 404

    def test_git_blocked(self):
        app = _create_app(database=None, pipeline=None)
        client = TestClient(app)
        resp = client.get("/.git/config")
        assert resp.status_code == 404


# ─── Token Authentication ────────────────────────────────────


class TestTokenAuth:
    def test_valid_token_grants_access(self):
        app = _create_app()
        client = TestClient(app)
        resp = client.get("/api/cameras", headers=_auth_headers())
        assert resp.status_code == 200

    def test_missing_auth_header_returns_401(self):
        app = _create_app()
        client = TestClient(app)
        resp = client.get("/api/cameras")
        assert resp.status_code == 401
        assert "Invalid or missing token" in resp.json()["detail"]

    def test_malformed_auth_header_returns_401(self):
        app = _create_app()
        client = TestClient(app)
        resp = client.get("/api/cameras", headers={"Authorization": "Basic abc"})
        assert resp.status_code == 401

    def test_wrong_token_returns_403(self):
        app = _create_app()
        client = TestClient(app)
        resp = client.get("/api/cameras", headers=_auth_headers("wrong-token"))
        assert resp.status_code == 403
        assert "Invalid token" in resp.json()["detail"]

    def test_empty_api_token_skips_token_validation(self):
        """When config has empty api_token, token content is not validated (any Bearer value works)."""
        app = _create_app(config={"api_token": "", "cors_origins": ["*"]})
        client = TestClient(app)
        # Bearer header is still required for format, but token content is not validated
        resp = client.get("/api/cameras", headers=_auth_headers("any-value"))
        assert resp.status_code == 200

    def test_no_api_token_key_skips_token_validation(self):
        """When config dict has no 'api_token' key at all, any Bearer value works."""
        app = _create_app(config={"cors_origins": ["*"]})
        client = TestClient(app)
        resp = client.get("/api/cameras", headers=_auth_headers("any-value"))
        assert resp.status_code == 200

    def test_empty_api_token_still_requires_bearer_format(self):
        """Even with empty api_token, the Bearer format is still required."""
        app = _create_app(config={"api_token": "", "cors_origins": ["*"]})
        client = TestClient(app)
        resp = client.get("/api/cameras")
        assert resp.status_code == 401

    def test_health_no_auth_required(self):
        """/health endpoint must not require authentication."""
        app = _create_app(config={"api_token": VALID_TOKEN})
        client = TestClient(app)
        resp = client.get("/health")
        assert resp.status_code == 200


# ─── GET /health ──────────────────────────────────────────────


class TestHealthEndpoint:
    def test_ok_status(self):
        pl = _mock_pipeline("ok")
        app = _create_app(database=None, pipeline=pl)
        client = TestClient(app)
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["active_cameras"] == 2
        assert data["today_alerts"] == 12
        assert data["uptime_seconds"] == 3600.0

    def test_degraded_status(self):
        pl = _mock_pipeline("degraded")
        app = _create_app(database=None, pipeline=pl)
        client = TestClient(app)
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "degraded"

    def test_unhealthy_returns_503(self):
        pl = _mock_pipeline("unhealthy")
        app = _create_app(database=None, pipeline=pl)
        client = TestClient(app)
        resp = client.get("/health")
        assert resp.status_code == 503
        assert resp.json()["status"] == "unhealthy"

    def test_no_pipeline_returns_basic_ok(self):
        app = _create_app(database=None, pipeline=None)
        client = TestClient(app)
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["uptime_seconds"] == 0


# ─── GET /api/cameras ─────────────────────────────────────────


class TestListCameras:
    def test_returns_camera_list(self):
        app = _create_app()
        client = TestClient(app)
        resp = client.get("/api/cameras", headers=_auth_headers())
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) == 2
        assert data[0]["camera_id"] == "cam-1"
        assert data[0]["status"] == "connected"
        assert data[1]["camera_id"] == "cam-2"

    def test_no_pipeline_returns_empty(self):
        app = _create_app(pipeline=None)
        client = TestClient(app)
        resp = client.get("/api/cameras", headers=_auth_headers())
        assert resp.status_code == 200
        assert resp.json() == []


# ─── GET /api/alerts ──────────────────────────────────────────


class TestListAlerts:
    def test_default_pagination(self):
        alert = _make_alert()
        db = _mock_database(alerts=[alert], total=1)
        app = _create_app(database=db)
        client = TestClient(app)
        resp = client.get("/api/alerts", headers=_auth_headers())
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["page"] == 1
        assert data["page_size"] == 20
        assert len(data["items"]) == 1
        assert data["items"][0]["alert_id"] == "alert-001"

    def test_pagination_params(self):
        db = _mock_database(alerts=[], total=0)
        app = _create_app(database=db)
        client = TestClient(app)
        resp = client.get("/api/alerts?page=3&page_size=10", headers=_auth_headers())
        assert resp.status_code == 200
        data = resp.json()
        assert data["page"] == 3
        assert data["page_size"] == 10
        db.list_alerts.assert_called_once_with({}, 3, 10)

    def test_status_filter(self):
        db = _mock_database()
        app = _create_app(database=db)
        client = TestClient(app)
        resp = client.get("/api/alerts?status=pending", headers=_auth_headers())
        assert resp.status_code == 200
        db.list_alerts.assert_called_once_with({"status": "pending"}, 1, 20)

    def test_camera_id_filter(self):
        db = _mock_database()
        app = _create_app(database=db)
        client = TestClient(app)
        resp = client.get("/api/alerts?camera_id=cam-1", headers=_auth_headers())
        assert resp.status_code == 200
        db.list_alerts.assert_called_once_with({"camera_id": "cam-1"}, 1, 20)

    def test_event_type_filter(self):
        db = _mock_database()
        app = _create_app(database=db)
        client = TestClient(app)
        resp = client.get("/api/alerts?event_type=intrusion", headers=_auth_headers())
        assert resp.status_code == 200
        db.list_alerts.assert_called_once_with({"event_type": "intrusion"}, 1, 20)

    def test_severity_filter(self):
        db = _mock_database()
        app = _create_app(database=db)
        client = TestClient(app)
        resp = client.get("/api/alerts?severity=critical", headers=_auth_headers())
        assert resp.status_code == 200
        db.list_alerts.assert_called_once_with({"severity": "critical"}, 1, 20)

    def test_time_range_filter(self):
        db = _mock_database()
        app = _create_app(database=db)
        client = TestClient(app)
        resp = client.get(
            "/api/alerts?start_time=1000.0&end_time=2000.0",
            headers=_auth_headers(),
        )
        assert resp.status_code == 200
        db.list_alerts.assert_called_once_with(
            {"start_time": 1000.0, "end_time": 2000.0}, 1, 20
        )

    def test_multiple_filters_combined(self):
        db = _mock_database()
        app = _create_app(database=db)
        client = TestClient(app)
        resp = client.get(
            "/api/alerts?status=pending&camera_id=cam-1&severity=warning",
            headers=_auth_headers(),
        )
        assert resp.status_code == 200
        db.list_alerts.assert_called_once_with(
            {"status": "pending", "camera_id": "cam-1", "severity": "warning"}, 1, 20
        )

    def test_no_database_returns_empty(self):
        app = _create_app(database=None)
        client = TestClient(app)
        resp = client.get("/api/alerts", headers=_auth_headers())
        assert resp.status_code == 200
        data = resp.json()
        assert data["items"] == []
        assert data["total"] == 0

    def test_item_fields_present(self):
        alert = _make_alert()
        db = _mock_database(alerts=[alert], total=1)
        app = _create_app(database=db)
        client = TestClient(app)
        resp = client.get("/api/alerts", headers=_auth_headers())
        item = resp.json()["items"][0]
        assert "alert_id" in item
        assert "event_type" in item
        assert "camera_id" in item
        assert "camera_name" in item
        assert "severity" in item
        assert "status" in item
        assert "risk_level" in item
        assert "created_at" in item

    def test_risk_level_none_when_no_llm(self):
        alert = _make_alert(llm_risk_level=None)
        db = _mock_database(alerts=[alert], total=1)
        app = _create_app(database=db)
        client = TestClient(app)
        item = client.get("/api/alerts", headers=_auth_headers()).json()["items"][0]
        assert item["risk_level"] is None


# ─── GET /api/alerts/{id} ────────────────────────────────────


class TestGetAlert:
    def test_existing_alert_returns_detail(self):
        alert = _make_alert()
        db = _mock_database(alerts=[alert])
        app = _create_app(database=db)
        client = TestClient(app)
        resp = client.get("/api/alerts/alert-001", headers=_auth_headers())
        assert resp.status_code == 200
        data = resp.json()
        assert data["alert_id"] == "alert-001"
        assert "event" in data
        assert "status" in data

    def test_nonexistent_alert_returns_404(self):
        db = _mock_database(alerts=[])
        app = _create_app(database=db)
        client = TestClient(app)
        resp = client.get("/api/alerts/nonexistent", headers=_auth_headers())
        assert resp.status_code == 404
        assert resp.json()["detail"] == "Alert not found"

    def test_no_database_returns_404(self):
        app = _create_app(database=None)
        client = TestClient(app)
        resp = client.get("/api/alerts/any-id", headers=_auth_headers())
        assert resp.status_code == 404


# ─── PUT /api/alerts/{id}/status ──────────────────────────────


class TestUpdateAlertStatus:
    def test_pending_to_acknowledged(self):
        alert = _make_alert(status=AlertStatus.PENDING)
        db = _mock_database(alerts=[alert])
        updated_alert = _make_alert(status=AlertStatus.ACKNOWLEDGED)
        db.get_alert.side_effect = lambda aid: alert if aid == "alert-001" else None
        db.update_alert.return_value = None
        # After update, get_alert returns the updated version
        call_count = {"n": 0}

        def get_alert_fn(aid):
            call_count["n"] += 1
            if call_count["n"] >= 2:
                return updated_alert
            return alert

        db.get_alert.side_effect = get_alert_fn

        app = _create_app(database=db)
        client = TestClient(app)
        resp = client.put(
            "/api/alerts/alert-001/status",
            json={"status": "acknowledged", "acknowledged_by": "operator-1"},
            headers=_auth_headers(),
        )
        assert resp.status_code == 200
        db.update_alert.assert_called_once()

    def test_pending_to_rejected(self):
        alert = _make_alert(status=AlertStatus.PENDING)
        db = _mock_database(alerts=[alert])
        db.get_alert.return_value = alert
        db.update_alert.return_value = None

        app = _create_app(database=db)
        client = TestClient(app)
        resp = client.put(
            "/api/alerts/alert-001/status",
            json={"status": "rejected", "acknowledged_by": "operator-1"},
            headers=_auth_headers(),
        )
        assert resp.status_code == 200

    def test_acknowledged_to_resolved(self):
        alert = _make_alert(status=AlertStatus.ACKNOWLEDGED)
        db = _mock_database(alerts=[alert])
        db.get_alert.return_value = alert
        db.update_alert.return_value = None

        app = _create_app(database=db)
        client = TestClient(app)
        resp = client.put(
            "/api/alerts/alert-001/status",
            json={"status": "resolved"},
            headers=_auth_headers(),
        )
        assert resp.status_code == 200

    def test_pending_to_resolved_invalid(self):
        """pending -> resolved is not allowed."""
        alert = _make_alert(status=AlertStatus.PENDING)
        db = _mock_database(alerts=[alert])
        db.get_alert.return_value = alert

        app = _create_app(database=db)
        client = TestClient(app)
        resp = client.put(
            "/api/alerts/alert-001/status",
            json={"status": "resolved"},
            headers=_auth_headers(),
        )
        assert resp.status_code == 400
        assert "Cannot transition" in resp.json()["detail"]

    def test_resolved_to_any_invalid(self):
        """resolved is a terminal state."""
        alert = _make_alert(status=AlertStatus.RESOLVED)
        db = _mock_database(alerts=[alert])
        db.get_alert.return_value = alert

        app = _create_app(database=db)
        client = TestClient(app)
        for target in ("acknowledged", "rejected", "resolved"):
            resp = client.put(
                "/api/alerts/alert-001/status",
                json={"status": target},
                headers=_auth_headers(),
            )
            assert resp.status_code == 400, f"resolved->{target} should be 400"

    def test_rejected_to_any_invalid(self):
        """rejected is a terminal state."""
        alert = _make_alert(status=AlertStatus.REJECTED)
        db = _mock_database(alerts=[alert])
        db.get_alert.return_value = alert

        app = _create_app(database=db)
        client = TestClient(app)
        for target in ("acknowledged", "rejected", "resolved"):
            resp = client.put(
                "/api/alerts/alert-001/status",
                json={"status": target},
                headers=_auth_headers(),
            )
            assert resp.status_code == 400, f"rejected->{target} should be 400"

    def test_missing_status_field_returns_400(self):
        db = _mock_database()
        app = _create_app(database=db)
        client = TestClient(app)
        resp = client.put(
            "/api/alerts/alert-001/status",
            json={"acknowledged_by": "op"},
            headers=_auth_headers(),
        )
        assert resp.status_code == 400
        assert "Missing status field" in resp.json()["detail"]

    def test_nonexistent_alert_returns_404(self):
        db = _mock_database(alerts=[])
        app = _create_app(database=db)
        client = TestClient(app)
        resp = client.put(
            "/api/alerts/nonexistent/status",
            json={"status": "acknowledged"},
            headers=_auth_headers(),
        )
        assert resp.status_code == 404

    def test_no_database_returns_404(self):
        app = _create_app(database=None)
        client = TestClient(app)
        resp = client.put(
            "/api/alerts/any/status",
            json={"status": "acknowledged"},
            headers=_auth_headers(),
        )
        assert resp.status_code == 404


# ─── GET /api/stats ──────────────────────────────────────────


class TestGetStats:
    def test_default_period(self):
        db = _mock_database()
        pl = _mock_pipeline()
        app = _create_app(database=db, pipeline=pl)
        client = TestClient(app)
        resp = client.get("/api/stats", headers=_auth_headers())
        assert resp.status_code == 200
        data = resp.json()
        assert data["period"] == "today"
        assert data["total_alerts"] == 42
        assert data["alerts_by_severity"] == {"warning": 30, "critical": 12}
        assert data["alerts_by_status"] == {"pending": 10, "resolved": 32}
        assert data["active_cameras"] == 2

    def test_period_7d(self):
        db = _mock_database()
        app = _create_app(database=db)
        client = TestClient(app)
        resp = client.get("/api/stats?period=7d", headers=_auth_headers())
        assert resp.status_code == 200
        assert resp.json()["period"] == "7d"
        # Verify database was called with correct time range (7 days span)
        call_args = db.get_stats.call_args[0][0]
        span = call_args["end_time"] - call_args["start_time"]
        assert 7 * 86400 - 1 <= span <= 7 * 86400 + 1

    def test_period_30d(self):
        db = _mock_database()
        app = _create_app(database=db)
        client = TestClient(app)
        resp = client.get("/api/stats?period=30d", headers=_auth_headers())
        assert resp.status_code == 200
        assert resp.json()["period"] == "30d"

    def test_no_database_returns_zero(self):
        app = _create_app(database=None, pipeline=None)
        client = TestClient(app)
        resp = client.get("/api/stats", headers=_auth_headers())
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_alerts"] == 0
        assert data["period"] == "today"

    def test_no_pipeline_fields_zero(self):
        db = _mock_database()
        app = _create_app(database=db, pipeline=None)
        client = TestClient(app)
        data = client.get("/api/stats", headers=_auth_headers()).json()
        assert data["active_cameras"] == 0
        assert data["system_uptime_hours"] == 0


# ─── GET /api/config ─────────────────────────────────────────


class TestGetConfig:
    def test_returns_sanitized_config(self):
        config = {
            "api_token": "super-secret",
            "cors_origins": ["http://localhost:3000"],
            "stream_url": "rtsp://admin:pw123@192.168.1.1/stream",
        }
        app = _create_app(config=config)
        client = TestClient(app)
        resp = client.get("/api/config", headers=_auth_headers("super-secret"))
        assert resp.status_code == 200
        data = resp.json()
        # api_token should be sanitized
        assert data["api_token"] == "***"
        # rtsp url should be sanitized
        assert "pw123" not in data["stream_url"]
        assert "rtsp://admin:***@" in data["stream_url"]
        # non-sensitive values preserved
        assert data["cors_origins"] == ["http://localhost:3000"]


# ─── Integration: Auth required on all /api/* endpoints ──────


class TestAuthIntegration:
    """Verify that all authenticated endpoints properly enforce auth."""

    @pytest.mark.parametrize(
        "method, path",
        [
            ("GET", "/api/cameras"),
            ("GET", "/api/alerts"),
            ("GET", "/api/alerts/some-id"),
            ("GET", "/api/stats"),
            ("GET", "/api/config"),
        ],
    )
    def test_endpoints_require_auth(self, method: str, path: str):
        app = _create_app()
        client = TestClient(app)
        resp = client.request(method, path)
        assert resp.status_code == 401

    @pytest.mark.parametrize(
        "method, path",
        [
            ("GET", "/api/cameras"),
            ("GET", "/api/alerts"),
            ("GET", "/api/stats"),
            ("GET", "/api/config"),
        ],
    )
    def test_endpoints_accept_valid_token(self, method: str, path: str):
        app = _create_app()
        client = TestClient(app)
        resp = client.request(method, path, headers=_auth_headers())
        assert resp.status_code == 200


# ─── App State ────────────────────────────────────────────────


class TestAppState:
    def test_ws_manager_attached(self):
        app = _create_app()
        assert hasattr(app.state, "ws_manager")

    def test_database_attached(self):
        db = MagicMock()
        app = _create_app(database=db)
        assert app.state.database is db

    def test_pipeline_attached(self):
        pl = MagicMock()
        app = _create_app(pipeline=pl)
        assert app.state.pipeline is pl


# ─── Edge Cases ───────────────────────────────────────────────


class TestEdgeCases:
    def test_alerts_pagination_page_size_boundary(self):
        """page_size max is 100 by Query definition."""
        db = _mock_database()
        app = _create_app(database=db)
        client = TestClient(app)
        resp = client.get("/api/alerts?page_size=100", headers=_auth_headers())
        assert resp.status_code == 200

    def test_alerts_page_must_be_ge_1(self):
        app = _create_app()
        client = TestClient(app)
        resp = client.get("/api/alerts?page=0", headers=_auth_headers())
        assert resp.status_code == 422

    def test_alerts_page_size_must_be_ge_1(self):
        app = _create_app()
        client = TestClient(app)
        resp = client.get("/api/alerts?page_size=0", headers=_auth_headers())
        assert resp.status_code == 422

    def test_alerts_page_size_le_100(self):
        app = _create_app()
        client = TestClient(app)
        resp = client.get("/api/alerts?page_size=101", headers=_auth_headers())
        assert resp.status_code == 422
