"""Tests for vision_agent.actions.notifier"""

from __future__ import annotations

import time
from email import message_from_string
from email.header import decode_header
from unittest.mock import MagicMock, patch

import pytest

from vision_agent.actions.notifier import (
    EmailConfig,
    EmailNotifier,
    WebhookConfig,
    WebhookNotifier,
)
from vision_agent.core.types import (
    Alert,
    BoundingBox,
    Detection,
    Event,
    LLMAnalysis,
    Severity,
)


# ─── Helpers ──────────────────────────────────────────────────


def _event(
    event_type: str = "intrusion",
    camera_id: str = "cam-01",
    camera_name: str = "Warehouse A",
    rule_name: str = "zone_intrusion",
    severity: Severity = Severity.WARNING,
    timestamp: float = 1720000000.0,
    detections: list[Detection] | None = None,
    snapshot_path: str = "",
) -> Event:
    return Event(
        event_type=event_type,
        camera_id=camera_id,
        camera_name=camera_name,
        rule_name=rule_name,
        severity=severity,
        timestamp=timestamp,
        detections=detections or [],
        snapshot_path=snapshot_path,
    )


def _llm(
    description: str = "Detected unauthorized person in restricted area",
    risk_level: str = "高",
    suggestion: str = "Send security personnel immediately",
) -> LLMAnalysis:
    return LLMAnalysis(
        description=description,
        risk_level=risk_level,
        suggestion=suggestion,
    )


def _detection(class_name: str = "person") -> Detection:
    return Detection(
        frame_id=0,
        class_id=0,
        class_name=class_name,
        confidence=0.95,
        bbox=BoundingBox(10, 10, 50, 50),
        timestamp=1720000000.0,
    )


def _alert(
    event: Event | None = None,
    llm: LLMAnalysis | None = None,
    alert_id: str = "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
) -> Alert:
    return Alert(
        alert_id=alert_id,
        event=event or _event(),
        llm_analysis=llm,
    )


def _mock_response(status_code: int = 200, text: str = '{"errcode":0}') -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    return resp


# ─── 1. WebhookNotifier._build_wechat_message ─────────────────


class TestBuildWechatMessage:
    """企业微信 Markdown 消息格式验证。"""

    def test_contains_msgtype_markdown(self):
        notifier = WebhookNotifier(WebhookConfig())
        alert = _alert()
        msg = notifier._build_wechat_message(alert)
        assert msg["msgtype"] == "markdown"

    def test_contains_camera_name(self):
        notifier = WebhookNotifier(WebhookConfig())
        alert = _alert(event=_event(camera_name="Gate A"))
        content = msg = notifier._build_wechat_message(alert)["markdown"]["content"]
        assert "Gate A" in content

    def test_contains_rule_name(self):
        notifier = WebhookNotifier(WebhookConfig())
        alert = _alert(event=_event(rule_name="zone_intrusion"))
        content = notifier._build_wechat_message(alert)["markdown"]["content"]
        assert "zone_intrusion" in content

    def test_contains_event_type_cn(self):
        """intrusion -> 区域闯入"""
        notifier = WebhookNotifier(WebhookConfig())
        alert = _alert(event=_event(event_type="intrusion"))
        content = notifier._build_wechat_message(alert)["markdown"]["content"]
        assert "区域闯入" in content

    def test_unknown_event_type_uses_raw(self):
        notifier = WebhookNotifier(WebhookConfig())
        alert = _alert(event=_event(event_type="custom_xyz"))
        content = notifier._build_wechat_message(alert)["markdown"]["content"]
        assert "custom_xyz" in content

    def test_contains_timestamp(self):
        notifier = WebhookNotifier(WebhookConfig())
        alert = _alert(event=_event(timestamp=1720000000.0))
        content = notifier._build_wechat_message(alert)["markdown"]["content"]
        # datetime.fromtimestamp(1720000000.0) = 2024-07-03 10:26:40 (UTC+8 depends)
        assert "2024" in content

    def test_timestamp_zero_shows_unknown(self):
        notifier = WebhookNotifier(WebhookConfig())
        alert = _alert(event=_event(timestamp=0.0))
        content = notifier._build_wechat_message(alert)["markdown"]["content"]
        assert "未知" in content

    def test_with_llm_analysis(self):
        notifier = WebhookNotifier(WebhookConfig())
        llm = _llm(
            description="Unauthorized entry",
            risk_level="紧急",
            suggestion="Lock the area",
        )
        alert = _alert(llm=llm)
        content = notifier._build_wechat_message(alert)["markdown"]["content"]
        assert "Unauthorized entry" in content
        assert "紧急" in content
        assert "Lock the area" in content
        assert "LLM 分析" in content

    def test_without_llm_analysis_shows_detections(self):
        notifier = WebhookNotifier(WebhookConfig())
        detections = [_detection("person"), _detection("person"), _detection("car")]
        alert = _alert(
            llm=None,
            event=_event(detections=detections),
        )
        content = notifier._build_wechat_message(alert)["markdown"]["content"]
        assert "person" in content
        assert "car" in content
        assert "检测目标" in content

    def test_without_llm_no_detections_no_detection_section(self):
        notifier = WebhookNotifier(WebhookConfig())
        alert = _alert(llm=None, event=_event(detections=[]))
        content = notifier._build_wechat_message(alert)["markdown"]["content"]
        assert "检测目标" not in content
        assert "LLM 分析" not in content

    def test_contains_alert_id_prefix(self):
        notifier = WebhookNotifier(WebhookConfig())
        alert = _alert(alert_id="a1b2c3d4-e5f6-7890-abcd-ef1234567890")
        content = notifier._build_wechat_message(alert)["markdown"]["content"]
        assert "`a1b2c3d4`" in content

    def test_risk_color_高_uses_orange(self):
        notifier = WebhookNotifier(WebhookConfig())
        alert = _alert(llm=_llm(risk_level="高"))
        content = notifier._build_wechat_message(alert)["markdown"]["content"]
        assert "#FF6600" in content

    def test_risk_color_紧急_uses_red(self):
        notifier = WebhookNotifier(WebhookConfig())
        alert = _alert(llm=_llm(risk_level="紧急"))
        content = notifier._build_wechat_message(alert)["markdown"]["content"]
        assert "#FF0000" in content

    def test_risk_color_unknown_defaults_gray(self):
        notifier = WebhookNotifier(WebhookConfig())
        alert = _alert(llm=_llm(risk_level="unknown_level"))
        content = notifier._build_wechat_message(alert)["markdown"]["content"]
        assert "#999999" in content

    def test_severity_value_in_content(self):
        notifier = WebhookNotifier(WebhookConfig())
        alert = _alert(event=_event(severity=Severity.CRITICAL))
        content = notifier._build_wechat_message(alert)["markdown"]["content"]
        assert "critical" in content

    def test_camera_id_used_when_name_empty(self):
        notifier = WebhookNotifier(WebhookConfig())
        alert = _alert(event=_event(camera_id="cam-99", camera_name=""))
        content = notifier._build_wechat_message(alert)["markdown"]["content"]
        assert "cam-99" in content

    def test_special_characters_in_description(self):
        notifier = WebhookNotifier(WebhookConfig())
        llm = _llm(description='<script>alert("xss")</script> & "quotes"')
        alert = _alert(llm=llm)
        msg = notifier._build_wechat_message(alert)
        # Should not raise, content just contains the raw text
        assert '<script>' in msg["markdown"]["content"]


# ─── 2. WebhookNotifier._build_dingtalk_message ───────────────


class TestBuildDingtalkMessage:
    """钉钉 Markdown 消息格式验证。"""

    def test_contains_msgtype_markdown(self):
        notifier = WebhookNotifier(WebhookConfig(type="dingtalk"))
        alert = _alert()
        msg = notifier._build_dingtalk_message(alert)
        assert msg["msgtype"] == "markdown"

    def test_has_title_and_text_fields(self):
        notifier = WebhookNotifier(WebhookConfig(type="dingtalk"))
        alert = _alert()
        msg = notifier._build_dingtalk_message(alert)
        assert "title" in msg["markdown"]
        assert "text" in msg["markdown"]

    def test_title_truncated_to_20_chars(self):
        notifier = WebhookNotifier(WebhookConfig(type="dingtalk"))
        alert = _alert(event=_event(event_type="abandoned_object"))
        title = notifier._build_dingtalk_message(alert)["markdown"]["title"]
        assert len(title) <= 20

    def test_text_contains_camera_name(self):
        notifier = WebhookNotifier(WebhookConfig(type="dingtalk"))
        alert = _alert(event=_event(camera_name="Entrance B"))
        text = notifier._build_dingtalk_message(alert)["markdown"]["text"]
        assert "Entrance B" in text

    def test_text_contains_rule_name(self):
        notifier = WebhookNotifier(WebhookConfig(type="dingtalk"))
        alert = _alert(event=_event(rule_name="crowd_rule"))
        text = notifier._build_dingtalk_message(alert)["markdown"]["text"]
        assert "crowd_rule" in text

    def test_with_llm_analysis(self):
        notifier = WebhookNotifier(WebhookConfig(type="dingtalk"))
        llm = _llm(description="Crowd detected", risk_level="中", suggestion="Monitor")
        alert = _alert(llm=llm)
        text = notifier._build_dingtalk_message(alert)["markdown"]["text"]
        assert "Crowd detected" in text
        assert "Monitor" in text
        assert "LLM 分析" in text

    def test_without_llm_analysis_no_llm_section(self):
        notifier = WebhookNotifier(WebhookConfig(type="dingtalk"))
        alert = _alert(llm=None)
        text = notifier._build_dingtalk_message(alert)["markdown"]["text"]
        assert "LLM 分析" not in text

    def test_contains_alert_id(self):
        notifier = WebhookNotifier(WebhookConfig(type="dingtalk"))
        alert = _alert(alert_id="a1b2c3d4-e5f6-7890-abcd-ef1234567890")
        text = notifier._build_dingtalk_message(alert)["markdown"]["text"]
        assert "a1b2c3d4" in text


# ─── 3. WebhookNotifier.execute ───────────────────────────────


class TestWebhookExecute:
    """execute() 方法的集成测试，mock HTTP 请求。"""

    def test_success_appends_to_notified_channels(self):
        notifier = WebhookNotifier(
            WebhookConfig(url="https://example.com/webhook")
        )
        mock_client = MagicMock()
        mock_client.post.return_value = _mock_response(200)
        notifier._client = mock_client

        alert = _alert()
        result = notifier.execute(alert)

        assert result is True
        assert "webhook" in alert.notified_channels

    def test_calls_post_with_correct_url(self):
        notifier = WebhookNotifier(
            WebhookConfig(url="https://hooks.example.com/test")
        )
        mock_client = MagicMock()
        mock_client.post.return_value = _mock_response(200)
        notifier._client = mock_client

        notifier.execute(_alert())
        mock_client.post.assert_called_once()
        call_args = mock_client.post.call_args
        assert call_args[0][0] == "https://hooks.example.com/test"

    def test_empty_url_returns_false(self):
        notifier = WebhookNotifier(WebhookConfig(url=""))
        alert = _alert()
        result = notifier.execute(alert)
        assert result is False
        assert "webhook" not in alert.notified_channels

    def test_4xx_does_not_retry(self):
        notifier = WebhookNotifier(
            WebhookConfig(url="https://example.com/webhook", max_retries=2)
        )
        mock_client = MagicMock()
        mock_client.post.return_value = _mock_response(400, '{"errmsg":"invalid"}')
        notifier._client = mock_client

        result = notifier.execute(_alert())
        assert result is False
        # Should only be called once (no retries for 4xx)
        assert mock_client.post.call_count == 1

    def test_5xx_retries_then_fails(self):
        notifier = WebhookNotifier(
            WebhookConfig(
                url="https://example.com/webhook",
                max_retries=2,
                retry_interval=0.01,
            )
        )
        mock_client = MagicMock()
        mock_client.post.return_value = _mock_response(500)
        notifier._client = mock_client

        with patch("vision_agent.actions.notifier.time.sleep"):
            result = notifier.execute(_alert())

        assert result is False
        # 1 initial + 2 retries = 3 calls
        assert mock_client.post.call_count == 3

    def test_5xx_retry_then_success(self):
        notifier = WebhookNotifier(
            WebhookConfig(
                url="https://example.com/webhook",
                max_retries=2,
                retry_interval=0.01,
            )
        )
        mock_client = MagicMock()
        mock_client.post.side_effect = [
            _mock_response(500),
            _mock_response(200),
        ]
        notifier._client = mock_client

        with patch("vision_agent.actions.notifier.time.sleep"):
            alert = _alert()
            result = notifier.execute(alert)

        assert result is True
        assert "webhook" in alert.notified_channels
        assert mock_client.post.call_count == 2

    def test_network_exception_retries_then_fails(self):
        notifier = WebhookNotifier(
            WebhookConfig(
                url="https://example.com/webhook",
                max_retries=1,
                retry_interval=0.01,
            )
        )
        mock_client = MagicMock()
        mock_client.post.side_effect = ConnectionError("timeout")
        notifier._client = mock_client

        with patch("vision_agent.actions.notifier.time.sleep"):
            result = notifier.execute(_alert())

        assert result is False
        # 1 initial + 1 retry = 2 calls
        assert mock_client.post.call_count == 2

    def test_success_on_first_try_no_retry(self):
        notifier = WebhookNotifier(
            WebhookConfig(
                url="https://example.com/webhook",
                max_retries=3,
                retry_interval=0.01,
            )
        )
        mock_client = MagicMock()
        mock_client.post.return_value = _mock_response(200)
        notifier._client = mock_client

        result = notifier.execute(_alert())
        assert result is True
        assert mock_client.post.call_count == 1

    def test_name_property(self):
        notifier = WebhookNotifier(WebhookConfig())
        assert notifier.name == "webhook"

    def test_499_still_no_retry(self):
        """Status 499 is still 4xx -> no retry."""
        notifier = WebhookNotifier(
            WebhookConfig(url="https://example.com/webhook", max_retries=2)
        )
        mock_client = MagicMock()
        mock_client.post.return_value = _mock_response(499)
        notifier._client = mock_client

        result = notifier.execute(_alert())
        assert result is False
        assert mock_client.post.call_count == 1

    def test_timeout_config_passed(self):
        notifier = WebhookNotifier(
            WebhookConfig(url="https://example.com/webhook", timeout=5)
        )
        mock_client = MagicMock()
        mock_client.post.return_value = _mock_response(200)
        notifier._client = mock_client

        notifier.execute(_alert())
        call_kwargs = mock_client.post.call_args[1]
        assert call_kwargs["timeout"] == 5


# ─── 4. WebhookNotifier._build_message dispatch ───────────────


class TestBuildMessageDispatch:
    """_build_message 根据 type 分发到正确的构建方法。"""

    def test_dispatches_to_wechat(self):
        notifier = WebhookNotifier(WebhookConfig(type="wechat"))
        alert = _alert()
        msg = notifier._build_message(alert)
        # wechat messages have "content" key, dingtalk have "title" + "text"
        assert "content" in msg["markdown"]

    def test_dispatches_to_dingtalk(self):
        notifier = WebhookNotifier(WebhookConfig(type="dingtalk"))
        alert = _alert()
        msg = notifier._build_message(alert)
        assert "title" in msg["markdown"]
        assert "text" in msg["markdown"]


# ─── 5. EmailNotifier._build_email ────────────────────────────


class TestBuildEmail:
    """邮件构造：主题、HTML 内容、纯文本内容。"""

    def test_subject_format(self):
        notifier = EmailNotifier(EmailConfig())
        alert = _alert(
            event=_event(camera_name="Gate A", event_type="intrusion"),
            llm=_llm(risk_level="高"),
        )
        subject, _, _ = notifier._build_email(alert)
        assert subject == "[Vision Agent][高] Gate A - 区域闯入"

    def test_subject_without_llm(self):
        notifier = EmailNotifier(EmailConfig())
        alert = _alert(
            event=_event(camera_name="Gate A", event_type="intrusion"),
            llm=None,
        )
        subject, _, _ = notifier._build_email(alert)
        # risk_level defaults to "中"
        assert subject == "[Vision Agent][中] Gate A - 区域闯入"

    def test_subject_uses_camera_id_when_name_empty(self):
        notifier = EmailNotifier(EmailConfig())
        alert = _alert(
            event=_event(camera_id="cam-99", camera_name="", event_type="crowd"),
        )
        subject, _, _ = notifier._build_email(alert)
        assert "cam-99" in subject

    def test_html_contains_html_tags(self):
        notifier = EmailNotifier(EmailConfig())
        alert = _alert()
        _, html, _ = notifier._build_email(alert)
        assert html.startswith("<html>")
        assert html.endswith("</html>")
        assert "<table" in html

    def test_html_contains_camera_name(self):
        notifier = EmailNotifier(EmailConfig())
        alert = _alert(event=_event(camera_name="Room 101"))
        _, html, _ = notifier._build_email(alert)
        assert "Room 101" in html

    def test_html_contains_rule_name(self):
        notifier = EmailNotifier(EmailConfig())
        alert = _alert(event=_event(rule_name="safety_rule"))
        _, html, _ = notifier._build_email(alert)
        assert "safety_rule" in html

    def test_html_contains_llm_analysis(self):
        notifier = EmailNotifier(EmailConfig())
        llm = _llm(description="Suspicious activity", risk_level="高", suggestion="Alert security")
        alert = _alert(llm=llm)
        _, html, _ = notifier._build_email(alert)
        assert "Suspicious activity" in html
        assert "Alert security" in html
        assert "LLM 分析" in html

    def test_html_no_llm_section_when_none(self):
        notifier = EmailNotifier(EmailConfig())
        alert = _alert(llm=None)
        _, html, _ = notifier._build_email(alert)
        assert "LLM 分析" not in html

    def test_html_contains_cid_snapshot(self):
        notifier = EmailNotifier(EmailConfig())
        alert = _alert()
        _, html, _ = notifier._build_email(alert)
        assert 'cid:snapshot' in html

    def test_html_contains_alert_id(self):
        notifier = EmailNotifier(EmailConfig())
        alert = _alert(alert_id="abc-123-def")
        _, html, _ = notifier._build_email(alert)
        assert "abc-123-def" in html

    def test_html_risk_color_in_h2(self):
        notifier = EmailNotifier(EmailConfig())
        alert = _alert(llm=_llm(risk_level="紧急"))
        _, html, _ = notifier._build_email(alert)
        assert "#FF0000" in html

    def test_text_body_contains_basic_info(self):
        notifier = EmailNotifier(EmailConfig())
        alert = _alert(event=_event(camera_name="Office", rule_name="absence_rule"))
        _, _, text = notifier._build_email(alert)
        assert "Office" in text
        assert "absence_rule" in text

    def test_text_body_contains_llm_analysis(self):
        notifier = EmailNotifier(EmailConfig())
        llm = _llm(description="Person left area", risk_level="中", suggestion="Check logs")
        alert = _alert(llm=llm)
        _, _, text = notifier._build_email(alert)
        assert "Person left area" in text
        assert "Check logs" in text
        assert "LLM 分析" in text

    def test_text_body_no_llm_section_when_none(self):
        notifier = EmailNotifier(EmailConfig())
        alert = _alert(llm=None)
        _, _, text = notifier._build_email(alert)
        assert "LLM 分析" not in text

    def test_text_body_contains_alert_id(self):
        notifier = EmailNotifier(EmailConfig())
        alert = _alert(alert_id="my-alert-id")
        _, _, text = notifier._build_email(alert)
        assert "my-alert-id" in text

    def test_text_body_contains_vision_agent_footer(self):
        notifier = EmailNotifier(EmailConfig())
        _, _, text = notifier._build_email(_alert())
        assert "Vision Agent" in text

    def test_severity_in_html(self):
        notifier = EmailNotifier(EmailConfig())
        alert = _alert(event=_event(severity=Severity.CRITICAL))
        _, html, _ = notifier._build_email(alert)
        assert "critical" in html

    def test_severity_in_text(self):
        notifier = EmailNotifier(EmailConfig())
        alert = _alert(event=_event(severity=Severity.CRITICAL))
        _, _, text = notifier._build_email(alert)
        assert "critical" in text

    def test_unknown_event_type_in_subject(self):
        notifier = EmailNotifier(EmailConfig())
        alert = _alert(event=_event(event_type="custom_xyz", camera_name="Cam1"))
        subject, _, _ = notifier._build_email(alert)
        assert "custom_xyz" in subject

    def test_timestamp_zero_shows_unknown_in_html(self):
        notifier = EmailNotifier(EmailConfig())
        alert = _alert(event=_event(timestamp=0.0))
        _, html, _ = notifier._build_email(alert)
        assert "未知" in html


# ─── 6. EmailNotifier.execute ─────────────────────────────────


class TestEmailExecute:
    """execute() 方法的集成测试，mock SMTP。"""

    def test_empty_to_addrs_returns_false(self):
        notifier = EmailNotifier(EmailConfig(to_addrs=[]))
        alert = _alert()
        result = notifier.execute(alert)
        assert result is False
        assert "email" not in alert.notified_channels

    def test_none_to_addrs_returns_false(self):
        notifier = EmailNotifier(EmailConfig(to_addrs=None))
        alert = _alert()
        result = notifier.execute(alert)
        assert result is False

    def test_smtp_success_ssl(self):
        config = EmailConfig(
            enabled=True,
            smtp_host="smtp.example.com",
            smtp_port=465,
            smtp_user="user@example.com",
            smtp_pass="secret",
            use_ssl=True,
            from_addr="user@example.com",
            to_addrs=["admin@example.com"],
        )
        notifier = EmailNotifier(config)

        mock_server = MagicMock()
        with patch("vision_agent.actions.notifier.smtplib.SMTP_SSL", return_value=mock_server):
            alert = _alert()
            result = notifier.execute(alert)

        assert result is True
        assert "email" in alert.notified_channels
        mock_server.login.assert_called_once_with("user@example.com", "secret")
        mock_server.sendmail.assert_called_once()
        mock_server.quit.assert_called_once()

    def test_smtp_success_starttls(self):
        config = EmailConfig(
            enabled=True,
            smtp_host="smtp.example.com",
            smtp_port=587,
            smtp_user="user@example.com",
            smtp_pass="secret",
            use_ssl=False,
            from_addr="user@example.com",
            to_addrs=["admin@example.com"],
        )
        notifier = EmailNotifier(config)

        mock_server = MagicMock()
        with patch("vision_agent.actions.notifier.smtplib.SMTP", return_value=mock_server):
            alert = _alert()
            result = notifier.execute(alert)

        assert result is True
        mock_server.starttls.assert_called_once()
        mock_server.login.assert_called_once()
        mock_server.sendmail.assert_called_once()
        mock_server.quit.assert_called_once()

    def test_smtp_failure_returns_false(self):
        config = EmailConfig(
            enabled=True,
            smtp_host="smtp.example.com",
            smtp_port=465,
            smtp_user="user@example.com",
            smtp_pass="secret",
            use_ssl=True,
            to_addrs=["admin@example.com"],
        )
        notifier = EmailNotifier(config)

        with patch(
            "vision_agent.actions.notifier.smtplib.SMTP_SSL",
            side_effect=Exception("Connection refused"),
        ):
            alert = _alert()
            result = notifier.execute(alert)

        assert result is False
        assert "email" not in alert.notified_channels

    def test_name_property(self):
        notifier = EmailNotifier(EmailConfig())
        assert notifier.name == "email"

    def test_from_addr_falls_back_to_smtp_user(self):
        config = EmailConfig(
            enabled=True,
            smtp_host="smtp.example.com",
            smtp_port=465,
            smtp_user="user@example.com",
            smtp_pass="pass",
            use_ssl=True,
            from_addr="",  # Empty, should fall back to smtp_user
            to_addrs=["admin@example.com"],
        )
        notifier = EmailNotifier(config)

        mock_server = MagicMock()
        with patch("vision_agent.actions.notifier.smtplib.SMTP_SSL", return_value=mock_server):
            notifier.execute(_alert())

        # sendmail is called with from_addr
        call_args = mock_server.sendmail.call_args[0]
        assert call_args[0] == "user@example.com"

    def test_sendmail_to_addrs_passed(self):
        config = EmailConfig(
            enabled=True,
            smtp_host="smtp.example.com",
            smtp_port=465,
            smtp_user="user@example.com",
            smtp_pass="pass",
            use_ssl=True,
            to_addrs=["a@example.com", "b@example.com"],
        )
        notifier = EmailNotifier(config)

        mock_server = MagicMock()
        with patch("vision_agent.actions.notifier.smtplib.SMTP_SSL", return_value=mock_server):
            notifier.execute(_alert())

        call_args = mock_server.sendmail.call_args[0]
        assert call_args[1] == ["a@example.com", "b@example.com"]

    def test_subject_in_email_message(self):
        config = EmailConfig(
            enabled=True,
            smtp_host="smtp.example.com",
            smtp_port=465,
            smtp_user="user@example.com",
            smtp_pass="pass",
            use_ssl=True,
            to_addrs=["admin@example.com"],
        )
        notifier = EmailNotifier(config)

        sent_msg = None

        def capture_sendmail(from_addr, to_addrs, msg_string):
            nonlocal sent_msg
            sent_msg = msg_string

        mock_server = MagicMock()
        mock_server.sendmail.side_effect = capture_sendmail
        with patch("vision_agent.actions.notifier.smtplib.SMTP_SSL", return_value=mock_server):
            alert = _alert(
                event=_event(camera_name="Gate B", event_type="crowd"),
                llm=_llm(risk_level="低"),
            )
            notifier.execute(alert)

        assert sent_msg is not None
        # Parse the MIME message to verify subject and structure
        msg = message_from_string(sent_msg)
        # Decode the MIME-encoded subject header
        raw_subject = msg["Subject"]
        decoded_parts = decode_header(raw_subject)
        subject = decoded_parts[0][0]
        if isinstance(subject, bytes):
            subject = subject.decode(decoded_parts[0][1] or "utf-8")
        assert subject == "[Vision Agent][低] Gate B - 人员聚集"
        assert msg["From"] == "Vision Agent <user@example.com>"
        assert msg["To"] == "admin@example.com"

    def test_email_no_retry_on_failure(self):
        """SMTP failure should not be retried (unlike webhook)."""
        config = EmailConfig(
            enabled=True,
            smtp_host="smtp.example.com",
            smtp_port=465,
            smtp_user="user@example.com",
            smtp_pass="pass",
            use_ssl=True,
            to_addrs=["admin@example.com"],
        )
        notifier = EmailNotifier(config)

        call_count = 0

        def counting_smtp(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            raise Exception("fail")

        with patch("vision_agent.actions.notifier.smtplib.SMTP_SSL", side_effect=counting_smtp):
            notifier.execute(_alert())

        # Only 1 attempt, no retry
        assert call_count == 1


# ─── 7. Edge cases ────────────────────────────────────────────


class TestEdgeCases:
    """边界条件与特殊场景。"""

    def test_wechat_no_llm_no_detections(self):
        notifier = WebhookNotifier(WebhookConfig())
        alert = _alert(
            llm=None,
            event=_event(detections=[]),
        )
        msg = notifier._build_wechat_message(alert)
        # Should build without error
        assert msg["msgtype"] == "markdown"
        content = msg["markdown"]["content"]
        assert "告警ID" in content

    def test_dingtalk_no_llm_no_detections(self):
        notifier = WebhookNotifier(WebhookConfig(type="dingtalk"))
        alert = _alert(
            llm=None,
            event=_event(detections=[]),
        )
        msg = notifier._build_dingtalk_message(alert)
        assert msg["msgtype"] == "markdown"
        text = msg["markdown"]["text"]
        assert "告警ID" in text

    def test_wechat_multiple_detection_classes(self):
        notifier = WebhookNotifier(WebhookConfig())
        detections = [
            _detection("person"),
            _detection("person"),
            _detection("car"),
            _detection("person"),
        ]
        alert = _alert(llm=None, event=_event(detections=detections))
        content = notifier._build_wechat_message(alert)["markdown"]["content"]
        assert "person" in content
        assert "car" in content

    def test_email_with_llm_empty_description(self):
        """LLM present but description empty -> no LLM section in email."""
        notifier = EmailNotifier(EmailConfig())
        llm = LLMAnalysis(description="", risk_level="高", suggestion="Check")
        alert = _alert(llm=llm)
        _, html, text = notifier._build_email(alert)
        # Empty description means llm.description is falsy
        assert "LLM 分析" not in html
        assert "LLM 分析" not in text

    def test_wechat_with_llm_empty_description(self):
        """LLM present but empty description -> falls back to detections or nothing."""
        notifier = WebhookNotifier(WebhookConfig())
        llm = LLMAnalysis(description="", risk_level="中", suggestion="")
        detections = [_detection("person")]
        alert = _alert(llm=llm, event=_event(detections=detections))
        content = notifier._build_wechat_message(alert)["markdown"]["content"]
        # description is empty -> check for detections
        assert "person" in content

    def test_special_chars_in_camera_name(self):
        notifier = WebhookNotifier(WebhookConfig())
        alert = _alert(event=_event(camera_name="摄像头-A&B <test>"))
        msg = notifier._build_wechat_message(alert)
        assert "摄像头-A&B <test>" in msg["markdown"]["content"]

    def test_special_chars_in_email_subject(self):
        notifier = EmailNotifier(EmailConfig())
        alert = _alert(
            event=_event(camera_name="Room-101 & 102", event_type="intrusion"),
            llm=_llm(risk_level="低"),
        )
        subject, _, _ = notifier._build_email(alert)
        assert "Room-101 & 102" in subject

    def test_alert_id_truncated_in_wechat(self):
        notifier = WebhookNotifier(WebhookConfig())
        long_id = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
        alert = _alert(alert_id=long_id)
        content = notifier._build_wechat_message(alert)["markdown"]["content"]
        # Only first 8 chars in backtick
        assert "`a1b2c3d4`" in content

    def test_alert_id_truncated_in_dingtalk(self):
        notifier = WebhookNotifier(WebhookConfig(type="dingtalk"))
        long_id = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
        alert = _alert(alert_id=long_id)
        text = notifier._build_dingtalk_message(alert)["markdown"]["text"]
        assert "a1b2c3d4" in text

    def test_email_from_name_in_message(self):
        config = EmailConfig(
            smtp_host="smtp.example.com",
            smtp_port=465,
            smtp_user="u@e.com",
            smtp_pass="p",
            use_ssl=True,
            from_name="My System",
            to_addrs=["a@e.com"],
        )
        notifier = EmailNotifier(config)

        sent_msg = None

        def capture(from_addr, to_addrs, msg_string):
            nonlocal sent_msg
            sent_msg = msg_string

        mock_server = MagicMock()
        mock_server.sendmail.side_effect = capture
        with patch("vision_agent.actions.notifier.smtplib.SMTP_SSL", return_value=mock_server):
            notifier.execute(_alert())

        assert sent_msg is not None
        assert "My System" in sent_msg
