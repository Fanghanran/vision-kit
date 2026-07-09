"""摄像头管理 API 集成测试 — toggle/create/delete/list"""

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
    app = create_app(database=None, pipeline=pipeline, config={})
    return TestClient(app)


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
