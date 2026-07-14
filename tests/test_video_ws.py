"""监控面板 — 帧订阅/推送/编码测试（不依赖 TestClient WebSocket）"""

import time

import pytest

from sentinelmind.core.camera import CameraConfig, CameraThread, FrameQueue


class FakePipeline:
    def __init__(self):
        self.cameras: dict[str, CameraThread] = {}

    def get_camera_thread(self, camera_id: str) -> CameraThread | None:
        return self.cameras.get(camera_id)


@pytest.fixture
def cam_thread():
    """创建测试摄像头并启动"""
    config = CameraConfig(camera_id="cam_01", camera_name="test", source_type="test", fps=25)
    q = FrameQueue(maxsize=10)
    thread = CameraThread(config, q)
    thread.start()
    time.sleep(0.3)
    yield thread
    thread.stop()


class TestFrameSubscription:
    def test_subscribe_receives_frames(self, cam_thread):
        """订阅帧队列能收到帧"""
        q = cam_thread.subscribe_frames(maxsize=10)
        try:
            frame = q.get(timeout=3)
            assert frame is not None
            assert frame.camera_id == "cam_01"
            assert frame.frame_seq > 0
            assert frame.frame.shape == (640, 640, 3)
        finally:
            cam_thread.unsubscribe_frames(q)

    def test_subscribe_multiple_frames(self, cam_thread):
        """订阅者能持续收到多帧"""
        q = cam_thread.subscribe_frames(maxsize=10)
        try:
            frames = []
            deadline = time.time() + 2
            while time.time() < deadline and len(frames) < 5:
                frame = q.get(timeout=0.5)
                if frame is not None:
                    frames.append(frame)
            assert len(frames) >= 2
            # 帧序号递增
            for i in range(1, len(frames)):
                assert frames[i].frame_seq > frames[i - 1].frame_seq
        finally:
            cam_thread.unsubscribe_frames(q)

    def test_unsubscribe_removes_from_list(self, cam_thread):
        """取消订阅后订阅者列表移除"""
        q = cam_thread.subscribe_frames(maxsize=5)
        assert len(cam_thread._frame_subscribers) == 1
        cam_thread.unsubscribe_frames(q)
        assert len(cam_thread._frame_subscribers) == 0

    def test_queue_full_drops_oldest(self, cam_thread):
        """小队列满时丢弃旧帧不阻塞"""
        q = cam_thread.subscribe_frames(maxsize=2)
        try:
            time.sleep(1)  # 等待多帧填充
            # 队列应该有帧
            frame = q.get(timeout=1)
            assert frame is not None
        finally:
            cam_thread.unsubscribe_frames(q)

    def test_stop_pushes_none(self, cam_thread):
        """stop() 时向订阅者推送 None 信号"""
        # 创建独立摄像头避免影响其他测试
        cfg = CameraConfig(camera_id="stop_test", camera_name="s", source_type="test", fps=5)
        q2 = FrameQueue(maxsize=10)
        ct = CameraThread(cfg, q2)
        ct.start()
        time.sleep(0.2)

        sub_q = ct.subscribe_frames(maxsize=5)
        ct.stop()

        # stop 后应该收到 None
        got_none = False
        deadline = time.time() + 2
        while time.time() < deadline:
            frame = sub_q.get(timeout=0.5)
            if frame is None:
                got_none = True
                break
        ct.unsubscribe_frames(sub_q)
        assert got_none


class TestJPEGEncodingAvailability:
    def test_cv2_available(self):
        """cv2 可用于 JPEG 编码"""
        try:
            import cv2
            import numpy as np

            frame = np.zeros((64, 64, 3), dtype=np.uint8)
            ok, buf = cv2.imencode(".jpg", frame)
            assert ok
            assert len(buf) > 100
        except ImportError:
            pytest.skip("cv2 not installed")

    def test_pil_available(self):
        """PIL 可作为 JPEG 编码后备"""
        try:
            from PIL import Image
            import numpy as np
            from io import BytesIO

            frame = np.zeros((64, 64, 3), dtype=np.uint8)
            img = Image.fromarray(frame)
            buf = BytesIO()
            img.save(buf, format="JPEG")
            assert len(buf.getvalue()) > 100
        except ImportError:
            pytest.skip("PIL not installed")
