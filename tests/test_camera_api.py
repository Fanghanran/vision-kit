"""摄像头管理 API 集成测试 — toggle/create/delete/list"""

import os
import tempfile

import pytest
from starlette.testclient import TestClient

from vision_agent.core.camera import CameraConfig, CameraThread, FrameQueue
from vision_agent.web.api.app import create_app


class FakePipeline:
    """模拟 pipeline，提供摄像头管理接口"""

    def __init__(self):
        self.cameras: dict[str, CameraThread] = {}

    def get_camera_thread(self, camera_id: str) -> CameraThread | None:
        return self.cameras.get(camera_id)

    def get_camera_states(self) -> dict:
        from vision_agent.core.types import CameraState, CameraStatus

        states: dict = {}
        for cid, thread in self.cameras.items():
            states[cid] = thread.camera_state
        return states

    def add_camera(self, config: CameraConfig, fps: float = 0):
        q = FrameQueue(maxsize=10)
        thread = CameraThread(config, q)
        self.cameras[config.camera_id] = thread

    def remove_camera(self, camera_id: str):
        if camera_id in self.cameras:
            self.cameras[camera_id].stop()
            del self.cameras[camera_id]

    def health(self):
        from types import SimpleNamespace

        return SimpleNamespace(
            status="ok", uptime_seconds=0, gpu_utilization=0,
            gpu_memory_used_mb=0, gpu_memory_total_mb=0, queue_depth=0,
            inference_latency_p50_ms=0, inference_latency_p99_ms=0,
            active_cameras=len(self.cameras), total_cameras=len(self.cameras),
            today_alerts=0, llm_success_rate=1.0,
        )


@pytest.fixture
def pipeline():
    return FakePipeline()


@pytest.fixture
def client(pipeline):
    # 创建临时 auth db 并获取 admin token
    from vision_agent.auth.manager import get_auth_manager

    get_auth_manager.__globals__["_auth_manager"] = None
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        auth_db = f.name
    auth_mgr = get_auth_manager(db_path=auth_db)
    token = auth_mgr.login("admin", "admin123")

    app = create_app(database=None, pipeline=pipeline, config={})
    tc = TestClient(app)
    tc.headers["Authorization"] = f"Bearer {token}"

    yield tc

    get_auth_manager.__globals__["_auth_manager"] = None
    try:
        os.unlink(auth_db)
    except OSError:
        pass


class TestListCameras:
    def test_list_empty(self, client):
        resp = client.get("/api/cameras")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_one(self, client, pipeline):
        config = CameraConfig(camera_id="cam_01", camera_name="test", source_type="test", fps=10)
        pipeline.add_camera(config)
        resp = client.get("/api/cameras")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["camera_id"] == "cam_01"
        assert "current_fps" in data[0]


class TestToggleCamera:
    def test_start_stopped_camera(self, client, pipeline):
        config = CameraConfig(camera_id="cam_t", camera_name="t", source_type="test", fps=5)
        pipeline.add_camera(config)
        resp = client.post("/api/cameras/cam_t/toggle")
        assert resp.status_code == 200
        assert resp.json()["action"] == "started"

    def test_stop_running_camera(self, client, pipeline):
        config = CameraConfig(camera_id="cam_t2", camera_name="t2", source_type="test", fps=5)
        pipeline.add_camera(config)
        t = pipeline.get_camera_thread("cam_t2")
        t.start()
        import time

        time.sleep(0.5)
        resp = client.post("/api/cameras/cam_t2/toggle")
        assert resp.status_code == 200
        assert resp.json()["action"] == "stopped"

    def test_toggle_nonexistent(self, client):
        resp = client.post("/api/cameras/nonexistent/toggle")
        assert resp.status_code == 404

    def test_toggle_invalid_id(self, client):
        resp = client.post("/api/cameras/bad id/toggle")
        assert resp.status_code == 400


class TestCreateCamera:
    def test_create_test_camera(self, client):
        resp = client.post("/api/cameras", json={"id": "new_cam", "name": "新摄像头", "source_type": "test", "fps": 0})
        assert resp.status_code == 200
        assert resp.json()["camera_id"] == "new_cam"

    def test_create_duplicate(self, client):
        client.post("/api/cameras", json={"id": "dup_cam", "name": "d", "source_type": "test"})
        resp = client.post("/api/cameras", json={"id": "dup_cam", "name": "d2", "source_type": "test"})
        assert resp.status_code == 409

    def test_create_missing_id(self, client):
        resp = client.post("/api/cameras", json={"name": "无ID"})
        assert resp.status_code == 400

    def test_create_invalid_id(self, client):
        resp = client.post("/api/cameras", json={"id": "../../etc", "name": "bad"})
        assert resp.status_code == 400


class TestDeleteCamera:
    def test_delete_existing(self, client):
        client.post("/api/cameras", json={"id": "to_del", "name": "删", "source_type": "test"})
        resp = client.delete("/api/cameras/to_del")
        assert resp.status_code == 200

    def test_delete_nonexistent(self, client):
        resp = client.delete("/api/cameras/nonexistent")
        assert resp.status_code == 404


class TestReplayTimeline:
    def test_timeline_invalid_date(self, client):
        resp = client.get("/api/cameras/cam_01/timeline", params={"date": "bad-date"})
        # 日期格式无效 → 400
        assert resp.status_code == 400

    def test_replay_invalid_camera_id(self, client):
        resp = client.get("/api/cameras/bad id/replay", params={"start": 0, "end": 1})
        assert resp.status_code == 400


# ═══════════════════════════════════════════════════════════════
# 摄像头统计 API — GET /api/cameras/stats
# ═══════════════════════════════════════════════════════════════


class TestCameraStats:
    """GET /api/cameras/stats — 返回在线/离线/告警中/总计数量"""

    def test_stats_no_pipeline_returns_zeros(self):
        """无 pipeline 时 stats 端点返回全零值（不依赖 pipeline）"""
        from vision_agent.auth.manager import get_auth_manager

        get_auth_manager.__globals__["_auth_manager"] = None
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            auth_db = f.name
        auth_mgr = get_auth_manager(db_path=auth_db)
        token = auth_mgr.login("admin", "admin123")

        app = create_app(database=None, pipeline=None, config={})
        client = TestClient(app)
        client.headers["Authorization"] = f"Bearer {token}"
        resp = client.get("/api/cameras/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data == {"total": 0, "online": 0, "offline": 0, "alerting": 0}

        get_auth_manager.__globals__["_auth_manager"] = None
        try:
            os.unlink(auth_db)
        except OSError:
            pass

    def test_stats_with_camera(self, client, pipeline):
        """有摄像头时 stats 返回正确的统计计数"""
        config = CameraConfig(
            camera_id="stats_cam", camera_name="统计摄像头",
            source_type="test", fps=10,
        )
        pipeline.add_camera(config)
        resp = client.get("/api/cameras/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 1
        # 新添加的摄像头默认处于 CONNECTING 状态
        assert "online" in data
        assert "offline" in data
        assert "alerting" in data


# ═══════════════════════════════════════════════════════════════
# 摄像头详情 API — GET /api/cameras/{camera_id}
# ═══════════════════════════════════════════════════════════════


class TestCameraDetail:
    """GET /api/cameras/{camera_id} — 返回单路摄像头完整信息"""

    def test_detail_returns_full_info(self, client, pipeline):
        """详情端点返回 source_type、resolution、rtsp_url 等完整字段"""
        config = CameraConfig(
            camera_id="detail_cam",
            camera_name="详情摄像头",
            source_type="rtsp",
            rtsp_url="rtsp://192.168.1.100:554/stream1",
            fps=15,
            width=1920,
            height=1080,
        )
        pipeline.add_camera(config)
        resp = client.get("/api/cameras/detail_cam")
        assert resp.status_code == 200
        data = resp.json()
        # 基本信息
        assert data["camera_id"] == "detail_cam"
        assert data["status"] in ("connecting", "connected", "disconnected", "error")
        # source_type（新接口特有字段）
        assert data["source_type"] == "rtsp"
        # resolution（新接口特有字段）
        assert data["resolution"] == [1920, 1080]
        # rtsp_url（新接口特有字段）
        assert data["rtsp_url"] == "rtsp://192.168.1.100:554/stream1"
        # 运行指标
        assert "fps" in data
        assert "queue_size" in data
        assert "total_detections" in data
        assert "total_alerts" in data
        assert "uptime_seconds" in data
        assert "error_message" in data

    def test_detail_not_found(self, client):
        """GET /api/cameras/{id} 不存在的摄像头返回 404"""
        resp = client.get("/api/cameras/nonexistent_camera")
        assert resp.status_code == 404

    def test_detail_invalid_id(self, client):
        """GET /api/cameras/{id} 非法 ID 返回 400"""
        resp = client.get("/api/cameras/bad id")
        assert resp.status_code == 400
