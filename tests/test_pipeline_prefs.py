"""ActionThread 通知偏好功能测试 — _should_notify / _get_admin_preferences"""

import time
from unittest.mock import MagicMock, patch

import pytest

from sentinelmind.core.pipeline import ActionThread, ResultQueue


# ─── Helpers ────────────────────────────────────────────────


def _make_action_thread():
    """创建最小依赖的 ActionThread，所有协作者均为 MagicMock"""
    result_queue = ResultQueue(maxsize=10)
    rule_engine = MagicMock()
    recorder = MagicMock()
    return ActionThread(
        result_queue=result_queue,
        rule_engine=rule_engine,
        recorder=recorder,
    )


# ─── _should_notify 纯逻辑测试 ─────────────────────────────


class TestShouldNotify:
    """_should_notify(self, prefs, notify_type, channel) 的纯函数逻辑"""

    def test_should_notify_enabled(self):
        """偏好开启 + 渠道匹配 → 返回 True"""
        at = _make_action_thread()
        prefs = {"notify_alert": {"enabled": True, "channels": ["webhook"]}}
        assert at._should_notify(prefs, "alert", "webhook") is True

    def test_should_notify_disabled(self):
        """偏好关闭 → 返回 False"""
        at = _make_action_thread()
        prefs = {"notify_alert": {"enabled": False, "channels": ["webhook"]}}
        assert at._should_notify(prefs, "alert", "webhook") is False

    def test_should_notify_wrong_channel(self):
        """渠道不匹配 → 返回 False"""
        at = _make_action_thread()
        prefs = {"notify_alert": {"enabled": True, "channels": ["email"]}}
        assert at._should_notify(prefs, "alert", "webhook") is False

    def test_should_notify_default_fallback(self):
        """读取偏好失败 → 回退默认（告警开启、webhook 渠道）"""
        mock_mgr = MagicMock()
        mock_mgr.get_preferences.side_effect = RuntimeError("DB connection failed")

        at = _make_action_thread()
        at._prefs_cache = None
        at._prefs_cache_time = 0.0

        with patch("sentinelmind.auth.manager.get_auth_manager", return_value=mock_mgr):
            fallback = at._get_admin_preferences()

        # 验证回退默认值结构
        assert fallback["notify_alert"]["enabled"] is True
        assert fallback["notify_alert"]["channels"] == ["webhook"]
        assert fallback["notify_system"]["enabled"] is True
        assert fallback["notify_daily"]["enabled"] is False

        # 用回退值调用 _should_notify：alert + webhook → True
        assert at._should_notify(fallback, "alert", "webhook") is True
        # daily → False
        assert at._should_notify(fallback, "daily", "webhook") is False


# ─── _get_admin_preferences 缓存测试 ───────────────────────


class TestPrefsCache:
    """测试 _get_admin_preferences 的 60 秒缓存行为"""

    def test_prefs_cache_used(self):
        """缓存 60 秒内不重复查询"""
        mock_mgr = MagicMock()
        mock_mgr.get_preferences.return_value = {
            "notify_alert": {"enabled": True, "channels": ["webhook"]},
            "notify_system": {"enabled": True, "channels": ["webhook"]},
            "notify_daily": {"enabled": False, "channels": ["webhook"]},
        }

        at = _make_action_thread()
        at._prefs_cache = None
        at._prefs_cache_time = 0.0

        with patch("sentinelmind.auth.manager.get_auth_manager", return_value=mock_mgr):
            prefs1 = at._get_admin_preferences()
            prefs2 = at._get_admin_preferences()

        # 同一对象，说明走了缓存
        assert prefs1 is prefs2
        # get_preferences 只被调用一次
        assert mock_mgr.get_preferences.call_count == 1

    def test_prefs_cache_expired(self):
        """超过 60 秒后刷新缓存"""
        mock_mgr = MagicMock()
        mock_mgr.get_preferences.return_value = {
            "notify_alert": {"enabled": True, "channels": ["webhook"]},
        }

        at = _make_action_thread()
        at._prefs_cache = None
        at._prefs_cache_time = 0.0

        with patch("sentinelmind.auth.manager.get_auth_manager", return_value=mock_mgr):
            # 第一次调用，填充缓存
            at._get_admin_preferences()
            assert mock_mgr.get_preferences.call_count == 1

            # 模拟缓存过期：把时间往回拨 61 秒
            at._prefs_cache_time = time.time() - 61

            # 第二次调用，缓存过期，重新查询
            at._get_admin_preferences()
            assert mock_mgr.get_preferences.call_count == 2
