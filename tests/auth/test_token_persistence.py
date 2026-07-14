"""P0-v2 Token 持久化改造测试 — 多设备登录、单设备登出隔离、持久化验证、
强制下线、过期 token 清理
"""

import os
import sqlite3
import tempfile
import time

import pytest

pytest.importorskip("fastapi")

from sentinelmind.auth.manager import get_auth_manager


# ─── Fixtures ──────────────────────────────────────────────


@pytest.fixture
def tmp_db():
    """创建临时数据库文件路径，测试结束后自动清理"""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    for p in (path, path + "-shm", path + "-wal"):
        try:
            os.unlink(p)
        except OSError:
            pass


@pytest.fixture
def auth_mgr(tmp_db):
    """重置单例 + 创建独立 AuthManager（每测试隔离）"""
    get_auth_manager.__globals__["_auth_manager"] = None
    mgr = get_auth_manager(db_path=tmp_db)
    yield mgr
    # 再次清理单例，避免影响后续测试
    get_auth_manager.__globals__["_auth_manager"] = None


@pytest.fixture
def device_login(auth_mgr):
    """辅助 fixture：返回一个 lambda，用于以指定 ip 登录 admin"""

    def _login(ip: str = "") -> str:
        token = auth_mgr.login("admin", "admin123", ip=ip)
        assert token is not None, "admin 登录应成功"
        return token

    return _login


# ─── 测试用例 ──────────────────────────────────────────────


class TestTokenPersistence:
    def test_multi_device_login_same_user(self, auth_mgr, device_login):
        """同一用户在不同设备登录，各自获得独立 token，都能通过验证"""
        token_a = device_login(ip="192.168.1.10")
        token_b = device_login(ip="192.168.1.20")

        assert token_a != token_b, "不同设备应获得不同 token"

        user_a = auth_mgr.verify_token(token_a)
        user_b = auth_mgr.verify_token(token_b)

        assert user_a is not None, "device A token 应验证通过"
        assert user_b is not None, "device B token 应验证通过"
        assert user_a.username == "admin"
        assert user_b.username == "admin"

    def test_logout_one_device_keeps_other(self, auth_mgr, device_login):
        """device A 登出只失效 A 的 token，device B 仍能访问"""
        token_a = device_login(ip="10.0.0.1")
        token_b = device_login(ip="10.0.0.2")

        # 先确认两个 token 都有效
        assert auth_mgr.verify_token(token_a) is not None
        assert auth_mgr.verify_token(token_b) is not None

        # 登出 device A
        auth_mgr.logout_by_token(token_a)

        # token_a 失效，token_b 仍有效
        assert auth_mgr.verify_token(token_a) is None, "device A token 应已失效"
        assert auth_mgr.verify_token(token_b) is not None, "device B token 仍应有效"

    def test_token_survives_manager_recreation(self, auth_mgr, device_login, tmp_db):
        """模拟 AuthManager 重建（新实例、同 db 文件），旧 token 仍然有效"""
        token = device_login(ip="172.16.0.1")

        # 确认当前实例能验证
        assert auth_mgr.verify_token(token) is not None

        # 重置单例，模拟服务重启后重建 AuthManager
        get_auth_manager.__globals__["_auth_manager"] = None
        new_mgr = get_auth_manager(db_path=tmp_db)

        # 新实例应能验证旧 token
        user = new_mgr.verify_token(token)
        assert user is not None, "重建 AuthManager 后旧 token 仍应有效"
        assert user.username == "admin"

        # 再次清理单例
        get_auth_manager.__globals__["_auth_manager"] = None

    def test_revoke_sessions_clears_all_tokens(self, auth_mgr, device_login):
        """revoke_sessions(username) 删除该用户全部 token"""
        token_a = device_login(ip="10.0.0.1")
        token_b = device_login(ip="10.0.0.2")

        # 确认两个 token 都有效
        assert auth_mgr.verify_token(token_a) is not None
        assert auth_mgr.verify_token(token_b) is not None

        # 强制下线 admin 所有会话
        result = auth_mgr.revoke_sessions("admin")
        assert result is True, "revoke_sessions 应返回 True（有 token 被删除）"

        # 两个 token 都失效
        assert auth_mgr.verify_token(token_a) is None, "token A 应已失效"
        assert auth_mgr.verify_token(token_b) is None, "token B 应已失效"

    def test_verify_token_lazy_deletes_expired(self, auth_mgr, tmp_db):
        """verify_token() 发现过期时自动删除该记录"""
        # 直接插入一条已过期 token（绕过 login 的过期检查）
        conn = sqlite3.connect(tmp_db)
        conn.execute(
            """INSERT INTO active_tokens (token, username, ip, expires_at, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            ("expired_token", "admin", "", time.time() - 1, time.time() - 2),
        )
        conn.commit()
        conn.close()

        # 新连接确认记录存在
        conn = sqlite3.connect(tmp_db)
        row = conn.execute(
            "SELECT 1 FROM active_tokens WHERE token = ?", ("expired_token",)
        ).fetchone()
        assert row is not None, "过期 token 应已插入"
        conn.close()

        # verify_token 返回 None（已过期），同时触发惰性删除
        result = auth_mgr.verify_token("expired_token")
        assert result is None, "过期 token 验证应返回 None"

        # 确认表中已无该记录
        conn = sqlite3.connect(tmp_db)
        row = conn.execute(
            "SELECT 1 FROM active_tokens WHERE token = ?", ("expired_token",)
        ).fetchone()
        conn.close()
        assert row is None, "过期 token 应已被惰性删除"

    def test_cleanup_expired_tokens(self, auth_mgr, tmp_db):
        """_cleanup_expired_tokens() 正确删除过期记录并返回删除数"""
        now = time.time()
        expired_tokens = [
            ("old_1", now - 1000, now - 1100),
            ("old_2", now - 500, now - 600),
            ("old_3", now - 100, now - 200),
        ]

        conn = sqlite3.connect(tmp_db)
        for token, expires, created in expired_tokens:
            conn.execute(
                """INSERT INTO active_tokens (token, username, ip, expires_at, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (token, "admin", "", expires, created),
            )
        # 再插入一条未过期的
        conn.execute(
            """INSERT INTO active_tokens (token, username, ip, expires_at, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            ("valid_token", "admin", "", now + 3600, now),
        )
        conn.commit()
        conn.close()

        # 执行全量清理
        deleted = auth_mgr._cleanup_expired_tokens()
        assert deleted == 3, f"应删除 3 条过期记录，实际删除 {deleted}"

        # 确认只剩未过期 token
        conn = sqlite3.connect(tmp_db)
        rows = conn.execute(
            "SELECT token FROM active_tokens WHERE username = ?", ("admin",)
        ).fetchall()
        conn.close()
        tokens = [r[0] for r in rows]
        assert "valid_token" in tokens, "未过期 token 应保留"
        assert "old_1" not in tokens, "过期 token old_1 应已删除"
        assert "old_2" not in tokens, "过期 token old_2 应已删除"
        assert "old_3" not in tokens, "过期 token old_3 应已删除"

    def test_logout_by_token_deletes_from_table(self, auth_mgr, device_login, tmp_db):
        """login -> verify 通过 -> logout_by_token -> verify 失败"""
        token = device_login(ip="192.168.1.50")

        # 确认 token 有效
        user = auth_mgr.verify_token(token)
        assert user is not None
        assert user.username == "admin"

        # 确认记录在表中
        conn = sqlite3.connect(tmp_db)
        row = conn.execute(
            "SELECT 1 FROM active_tokens WHERE token = ?", (token,)
        ).fetchone()
        assert row is not None, "token 应存在于 active_tokens 表中"
        conn.close()

        # 登出
        auth_mgr.logout_by_token(token)

        # verify 失败
        assert auth_mgr.verify_token(token) is None, "登出后 token 应失效"

        # 确认表中已删除
        conn = sqlite3.connect(tmp_db)
        row = conn.execute(
            "SELECT 1 FROM active_tokens WHERE token = ?", (token,)
        ).fetchone()
        conn.close()
        assert row is None, "token 应从 active_tokens 表中删除"

    def test_list_active_sessions_from_db(self, auth_mgr, device_login):
        """两个设备登录后 list_active_sessions 从 db 返回 2 条"""
        # 初始无活跃会话
        assert auth_mgr.list_active_sessions() == []

        token_a = device_login(ip="192.168.1.10")
        token_b = device_login(ip="192.168.1.20")
        assert token_a != token_b

        sessions = auth_mgr.list_active_sessions()
        assert len(sessions) == 2, f"应有 2 条活跃会话，实际 {len(sessions)}"

        usernames = [s["username"] for s in sessions]
        assert all(u == "admin" for u in usernames)

        ips = {s["ip"] for s in sessions}
        assert ips == {"192.168.1.10", "192.168.1.20"}

        # 每条记录都应有正 remaining_seconds
        for s in sessions:
            assert s["remaining_seconds"] > 0, "活跃会话应有正的剩余时间"
