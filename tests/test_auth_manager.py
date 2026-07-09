"""AuthManager v2 新增功能测试 — 登录历史、会话管理、统计、邮箱验证"""

import pytest

from vision_agent.auth.manager import AuthManager
from vision_agent.auth.models import Role, UserStatus


# ─── Fixtures ──────────────────────────────────────────────


@pytest.fixture
def auth_mgr(tmp_path):
    """创建独立 AuthManager 实例，使用临时数据库（每测试隔离）"""
    db_path = tmp_path / "test_auth.db"
    return AuthManager(db_path=str(db_path))


# ─── 登录历史 ──────────────────────────────────────────────


class TestLoginHistory:
    def test_record_login(self, auth_mgr):
        """直接调用 record_login 写入登录历史并验证"""
        auth_mgr.record_login("admin", "192.168.1.1", success=True, reason="")
        history = auth_mgr.get_login_history("admin")
        assert len(history) >= 1
        latest = history[0]
        assert latest["username"] == "admin"
        assert latest["ip"] == "192.168.1.1"
        assert latest["success"] is True
        assert latest["reason"] == ""

    def test_get_login_history(self, auth_mgr):
        """get_login_history 返回成功和失败记录"""
        auth_mgr.record_login("admin", "10.0.0.1", success=True, reason="")
        auth_mgr.record_login("admin", "10.0.0.2", success=False, reason="密码错误")

        history = auth_mgr.get_login_history("admin")
        assert len(history) >= 2
        # 按 created_at DESC 排序，最新在前
        # 两条最近记录中应有成功和失败各一条
        successes = [h for h in history if h["success"]]
        failures = [h for h in history if not h["success"]]
        assert len(successes) >= 1
        assert len(failures) >= 1
        assert failures[0]["reason"] == "密码错误"

    def test_login_history_limit(self, auth_mgr):
        """get_login_history limit 参数生效"""
        # 写入 10 条记录
        for i in range(10):
            auth_mgr.record_login("admin", f"10.0.0.{i}", success=True)
        # 只取 3 条
        history = auth_mgr.get_login_history("admin", limit=3)
        assert len(history) == 3

    def test_login_records_ip(self, auth_mgr):
        """通过 login() 登录时 IP 被记录"""
        token = auth_mgr.login("admin", "admin123", ip="172.16.0.1")
        assert token is not None
        history = auth_mgr.get_login_history("admin", limit=1)
        assert len(history) == 1
        assert history[0]["ip"] == "172.16.0.1"
        assert history[0]["success"] is True

    def test_login_records_failure_wrong_password(self, auth_mgr):
        """密码错误的登录失败被记录"""
        result = auth_mgr.login("admin", "wrong_password", ip="10.0.0.99")
        assert result is None
        history = auth_mgr.get_login_history("admin", limit=1)
        assert len(history) == 1
        assert history[0]["success"] is False
        assert "密码错误" in history[0]["reason"]
        assert history[0]["ip"] == "10.0.0.99"

    def test_login_records_failure_disabled(self, auth_mgr):
        """账户被禁用后登录失败也被记录"""
        auth_mgr.create_user("victim", "pass123", Role.VIEWER)
        auth_mgr.update_user("victim", status=UserStatus.DISABLED.value)
        result = auth_mgr.login("victim", "pass123", ip="10.0.0.88")
        assert result is None
        history = auth_mgr.get_login_history("victim", limit=1)
        assert len(history) == 1
        assert history[0]["success"] is False
        assert "禁用" in history[0]["reason"]


# ─── 会话管理 ──────────────────────────────────────────────


class TestSessionManagement:
    def test_list_active_sessions(self, auth_mgr):
        """登录后 list_active_sessions 能查到活跃会话"""
        # 初始无会话
        assert auth_mgr.list_active_sessions() == []

        auth_mgr.login("admin", "admin123", ip="192.168.1.100")
        sessions = auth_mgr.list_active_sessions()
        assert len(sessions) == 1
        assert sessions[0]["username"] == "admin"
        assert sessions[0]["ip"] == "192.168.1.100"
        assert sessions[0]["remaining_seconds"] > 0

    def test_revoke_session(self, auth_mgr):
        """revoke_session 强制下线后会话消失"""
        auth_mgr.login("admin", "admin123", ip="10.0.0.1")
        assert len(auth_mgr.list_active_sessions()) == 1

        removed = auth_mgr.revoke_sessions("admin")
        assert removed is True
        assert auth_mgr.list_active_sessions() == []

    def test_revoke_session_nonexistent(self, auth_mgr):
        """revoke 一个没有活跃会话的用户返回 False"""
        removed = auth_mgr.revoke_sessions("nobody")
        assert removed is False


# ─── 用户统计 ──────────────────────────────────────────────


class TestUserStats:
    def test_get_user_stats(self, auth_mgr):
        """get_user_stats 统计总数、角色分布、在线数"""
        # 初始：默认 admin（active, admin 角色）
        stats = auth_mgr.get_user_stats()
        assert stats["total_users"] == 1
        assert stats["by_role"] == {"admin": 1}
        assert stats["active_count"] == 1
        assert stats["disabled_count"] == 0
        assert stats["online_count"] == 0

        # 创建更多用户
        auth_mgr.create_user("op1", "p", Role.OPERATOR)
        auth_mgr.create_user("vw1", "p", Role.VIEWER)
        auth_mgr.create_user("vw2", "p", Role.VIEWER)
        # 禁用一个
        auth_mgr.update_user("vw2", status=UserStatus.DISABLED.value)

        stats = auth_mgr.get_user_stats()
        assert stats["total_users"] == 4
        assert stats["by_role"]["admin"] == 1
        assert stats["by_role"]["operator"] == 1
        assert stats["by_role"]["viewer"] == 2
        assert stats["active_count"] == 3
        assert stats["disabled_count"] == 1

    def test_get_user_stats_includes_online(self, auth_mgr):
        """在线用户数正确反映在统计中"""
        auth_mgr.login("admin", "admin123", ip="10.0.0.1")
        stats = auth_mgr.get_user_stats()
        assert stats["online_count"] == 1


# ─── 邮箱唯一性验证 ────────────────────────────────────────


class TestEmailValidation:
    def test_create_user_duplicate_email(self, auth_mgr):
        """重复邮箱注册被拦截"""
        auth_mgr.create_user("u1", "pass1", Role.VIEWER, email="dup@test.com")
        with pytest.raises(ValueError, match="邮箱"):
            auth_mgr.create_user("u2", "pass2", Role.VIEWER, email="dup@test.com")

    def test_create_user_empty_email_allowed(self, auth_mgr):
        """空邮箱允许多个用户创建"""
        auth_mgr.create_user("u3", "pass3", email="")
        auth_mgr.create_user("u4", "pass4", email="")
        # 不抛异常即为通过
        assert auth_mgr.get_user("u3") is not None
        assert auth_mgr.get_user("u4") is not None

    def test_update_user_duplicate_email(self, auth_mgr):
        """修改邮箱为已存在的邮箱时被拦截"""
        auth_mgr.create_user("u5", "pass5", email="a@test.com")
        auth_mgr.create_user("u6", "pass6", email="b@test.com")

        with pytest.raises(ValueError, match="邮箱"):
            auth_mgr.update_user("u5", email="b@test.com")

    def test_update_user_same_email(self, auth_mgr):
        """修改为自己的原邮箱应通过（不变）"""
        auth_mgr.create_user("u7", "pass7", email="same@test.com")
        # 修改为相同邮箱不应报错
        updated = auth_mgr.update_user("u7", email="same@test.com")
        assert updated["email"] == "same@test.com"


# ─── 用户偏好 ──────────────────────────────────────────────


class TestPreferences:
    def test_get_preferences_default(self, auth_mgr):
        """新用户返回默认偏好设置"""
        auth_mgr.create_user("newuser", "pass123", Role.VIEWER)
        prefs = auth_mgr.get_preferences("newuser")
        assert prefs["notify_alert"]["enabled"] is True
        assert prefs["notify_alert"]["channels"] == ["webhook"]
        assert prefs["notify_system"]["enabled"] is True
        assert prefs["notify_system"]["channels"] == ["webhook"]
        assert prefs["notify_daily"]["enabled"] is False
        assert prefs["notify_daily"]["channels"] == ["webhook"]

    def test_update_preferences(self, auth_mgr):
        """修改偏好并持久化"""
        auth_mgr.create_user("pref_user", "pass123", Role.VIEWER)
        updated = auth_mgr.update_preferences("pref_user", {
            "notify_alert": {"enabled": False, "channels": ["email"]},
            "notify_daily": {"enabled": True},
        })
        assert updated["notify_alert"]["enabled"] is False
        assert updated["notify_alert"]["channels"] == ["email"]
        assert updated["notify_daily"]["enabled"] is True
        # 重新读取验证持久化
        reloaded = auth_mgr.get_preferences("pref_user")
        assert reloaded["notify_alert"]["enabled"] is False
        assert reloaded["notify_alert"]["channels"] == ["email"]

    def test_update_preferences_partial(self, auth_mgr):
        """部分更新不影响其他字段"""
        auth_mgr.create_user("partial_user", "pass123", Role.VIEWER)
        # 只更新一个字段
        auth_mgr.update_preferences("partial_user", {
            "notify_alert": {"enabled": False},
        })
        prefs = auth_mgr.get_preferences("partial_user")
        # 只改了 alert.enabled，其他保持默认
        assert prefs["notify_alert"]["enabled"] is False
        assert prefs["notify_alert"]["channels"] == ["webhook"]  # 未修改
        assert prefs["notify_system"]["enabled"] is True          # 未修改
        assert prefs["notify_daily"]["enabled"] is False          # 未修改


# ─── 用户详情 ──────────────────────────────────────────────


class TestUserDetail:
    def test_get_user_detail(self, auth_mgr):
        """返回完整信息含 last_login、active_sessions、preferences"""
        # 创建用户并登录产生会话和登录历史
        auth_mgr.create_user("detail_user", "pass123", Role.VIEWER, email="det@test.com")
        auth_mgr.login("detail_user", "pass123", ip="10.0.0.55")

        detail = auth_mgr.get_user_detail("detail_user")
        assert detail is not None
        assert detail["username"] == "detail_user"
        assert detail["email"] == "det@test.com"
        assert detail["role"] == "viewer"
        # last_login 应有值（登录过）
        assert detail["last_login"] is not None
        assert detail["last_login"]["ip"] == "10.0.0.55"
        assert detail["last_login"]["success"] is True
        # active_sessions 至少 1（刚登录）
        assert detail["active_sessions"] >= 1
        # preferences 存在
        assert "preferences" in detail
        assert detail["preferences"]["notify_alert"]["enabled"] is True

    def test_get_user_detail_nonexistent(self, auth_mgr):
        """不存在的用户返回 None"""
        detail = auth_mgr.get_user_detail("ghost")
        assert detail is None
