"""
通知器模块 — 告警事件的多渠道通知

设计来源：docs/modules/actions/notifier.md

职责：
- WebhookNotifier：企业微信/钉钉群机器人 Webhook 通知
- EmailNotifier：SMTP 邮件通知（HTML + 纯文本双版本）
- 多渠道并行发送，单个渠道失败不影响其他渠道
- 通知结果记入 Alert.notified_channels

设计决策：
- Webhook 失败重试 2 次（网络抖动），邮件失败不重试（配置错误居多）
- 截图以 CID 内嵌邮件（不依赖外部链接）
- 通知发送异步化，不阻塞 pipeline 主管线
"""

from __future__ import annotations

import html
import logging
import smtplib
import ssl
import time
from dataclasses import dataclass
from datetime import datetime
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

from vision_agent.core.types import Alert

logger = logging.getLogger(__name__)


# ─── 配置 ────────────────────────────────────────────────────


@dataclass
class WebhookConfig:
    """Webhook 配置（notifier.md 2 节）"""

    enabled: bool = True
    type: str = "wechat"  # wechat / dingtalk
    url: str = ""
    max_retries: int = 2
    retry_interval: float = 2.0
    timeout: int = 10


@dataclass
class EmailConfig:
    """邮件配置（notifier.md 2 节）"""

    enabled: bool = False
    smtp_host: str = ""
    smtp_port: int = 465
    smtp_user: str = ""
    smtp_pass: str = ""
    use_ssl: bool = True
    from_addr: str = ""
    from_name: str = "Vision Agent"
    to_addrs: list[str] | None = None
    timeout: int = 30


# ─── 事件类型中文映射 ────────────────────────────────────────

_EVENT_TYPE_NAMES = {
    "intrusion": "区域闯入",
    "absence": "离岗检测",
    "crowd": "人员聚集",
    "abandoned_object": "遗留物",
    "counting": "人数统计",
    "object_in_zone": "物体进入区域",
    "count_line": "计数线穿越",
    "zone_empty": "区域清空",
}

# 风险等级颜色映射
_RISK_COLORS = {
    "紧急": ("🔴", "#FF0000"),
    "高": ("🟠", "#FF6600"),
    "中": ("🟡", "#FF9900"),
    "低": ("⚪", "#999999"),
}


# ─── WebhookNotifier ─────────────────────────────────────────


class WebhookNotifier:
    """Webhook 通知器（notifier.md 3.1 节）

    支持企业微信和钉钉两种 Webhook 类型。
    失败重试（网络抖动），4xx 不重试。
    """

    def __init__(self, config: WebhookConfig, database: Any | None = None) -> None:
        self._config = config
        self._database = database
        self._client: Any = None

    @property
    def name(self) -> str:
        return "webhook"

    def execute(self, alert: Alert, snapshot_path: str = "") -> bool:
        """发送 Webhook 通知"""
        # 检查控制面板开关
        if self._database and not self._database.get_control_value("notification.webhook.enabled"):
            return False
        if not self._config.url:
            logger.error("webhook_no_url alert=%s", alert.alert_id)
            return False

        payload = self._build_message(alert)
        success = self._send_request(payload)

        if success:
            alert.notified_channels.append("webhook")
            logger.info(
                "webhook_sent alert=%s camera=%s type=%s",
                alert.alert_id,
                alert.event.camera_id,
                self._config.type,
            )
        return success

    def _build_message(self, alert: Alert) -> dict[str, Any]:
        """根据 Webhook 类型构造消息体"""
        if self._config.type == "dingtalk":
            return self._build_dingtalk_message(alert)
        return self._build_wechat_message(alert)

    def _build_wechat_message(self, alert: Alert) -> dict[str, Any]:
        """企业微信 Markdown 消息"""
        event = alert.event
        llm = alert.llm_analysis
        risk_marker = _RISK_COLORS.get(
            llm.risk_level if llm else "中", ("⚪", "#999999")
        )
        event_cn = _EVENT_TYPE_NAMES.get(event.event_type, event.event_type)
        timestamp = (
            datetime.fromtimestamp(event.timestamp).strftime("%Y-%m-%d %H:%M:%S")
            if event.timestamp
            else "未知"
        )

        lines = [
            f"## {risk_marker[0]} **[{event_cn}]** 告警通知",
            "",
            f"**摄像头**：{event.camera_name or event.camera_id}",
            f"**时间**：{timestamp}",
            f"**规则**：{event.rule_name}",
            f"**严重级别**：{event.severity.value}",
        ]

        if llm and llm.description:
            lines.extend(
                [
                    "",
                    "### LLM 分析",
                    f"**情况描述**：{llm.description}",
                    f'**风险等级**：<font color="{risk_marker[1]}">{llm.risk_level}</font>',
                    f"**建议措施**：{llm.suggestion}",
                ]
            )
        elif event.detections:
            class_counts: dict[str, int] = {}
            for det in event.detections:
                class_counts[det.class_name] = class_counts.get(det.class_name, 0) + 1
            targets = "、".join(f"{cls}×{cnt}" for cls, cnt in class_counts.items())
            lines.extend(["", f"**检测目标**：{targets}"])

        lines.extend(["", f"> 告警ID：`{alert.alert_id[:8]}`"])

        content = "\n".join(lines)

        return {
            "msgtype": "markdown",
            "markdown": {
                "content": content,
                "mentioned_list": [],
            },
        }

    def _build_dingtalk_message(self, alert: Alert) -> dict[str, Any]:
        """钉钉 Markdown 消息"""
        event = alert.event
        llm = alert.llm_analysis
        risk_marker = _RISK_COLORS.get(
            llm.risk_level if llm else "中", ("⚪", "#999999")
        )
        event_cn = _EVENT_TYPE_NAMES.get(event.event_type, event.event_type)
        timestamp = (
            datetime.fromtimestamp(event.timestamp).strftime("%Y-%m-%d %H:%M:%S")
            if event.timestamp
            else "未知"
        )

        title = f"[{event_cn}] 告警通知"[:20]

        lines = [
            f"### {risk_marker[0]} {title}",
            "",
            f"- **摄像头**：{event.camera_name or event.camera_id}",
            f"- **时间**：{timestamp}",
            f"- **规则**：{event.rule_name}",
            f"- **严重级别**：{event.severity.value}",
        ]

        if llm and llm.description:
            lines.extend(
                [
                    "",
                    "**LLM 分析**：",
                    f"- 情况：{llm.description}",
                    f"- 风险：{llm.risk_level}",
                    f"- 建议：{llm.suggestion}",
                ]
            )

        lines.extend(["", f"> 告警ID：{alert.alert_id[:8]}"])

        return {
            "msgtype": "markdown",
            "markdown": {
                "title": title,
                "text": "\n".join(lines),
            },
        }

    def _send_request(self, payload: dict[str, Any]) -> bool:
        """发送 HTTP POST 请求（含重试）"""
        client = self._get_client()
        max_retries = self._config.max_retries

        for attempt in range(max_retries + 1):
            try:
                resp = client.post(
                    self._config.url,
                    json=payload,
                    timeout=self._config.timeout,
                )
                if resp.status_code < 300:
                    return True
                if 400 <= resp.status_code < 500:
                    logger.error(
                        "webhook_failed alert_status=%d body=%s",
                        resp.status_code,
                        resp.text[:200],
                    )
                    return False
                # 5xx: 可重试
                logger.warning(
                    "webhook_retry attempt=%d/%d status=%d",
                    attempt + 1,
                    max_retries,
                    resp.status_code,
                )
            except Exception as e:
                logger.warning(
                    "webhook_retry attempt=%d/%d error=%s",
                    attempt + 1,
                    max_retries,
                    str(e),
                )

            if attempt < max_retries:
                time.sleep(self._config.retry_interval)

        logger.error("webhook_exhausted retries=%d", max_retries)
        return False

    def _get_client(self) -> Any:
        if self._client is None:
            try:
                import httpx

                self._client = httpx.Client()
            except ImportError:
                logger.error("httpx_not_installed")
                raise
        return self._client


# ─── EmailNotifier ───────────────────────────────────────────


class EmailNotifier:
    """邮件通知器（notifier.md 3.2 节）

    HTML + 纯文本双版本邮件。
    截图以 CID 内嵌（不依赖外部链接）。
    发送失败不重试。
    """

    def __init__(self, config: EmailConfig, database: Any | None = None) -> None:
        self._config = config
        self._database = database

    @property
    def name(self) -> str:
        return "email"

    def execute(self, alert: Alert, snapshot_path: str = "") -> bool:
        """发送邮件通知"""
        # 检查控制面板开关
        if self._database and not self._database.get_control_value("notification.email.enabled"):
            return False
        to_addrs = self._config.to_addrs or []
        if not to_addrs:
            logger.warning("email_no_recipients alert=%s", alert.alert_id)
            return False

        subject, html_body, text_body = self._build_email(alert)

        # 尝试嵌入截图
        snapshot_data = self._load_snapshot(alert.event.snapshot_path or snapshot_path)

        success = self._send_email(subject, html_body, text_body, snapshot_data)
        if success:
            alert.notified_channels.append("email")
            logger.info(
                "email_sent alert=%s to=%s",
                alert.alert_id,
                ",".join(to_addrs),
            )
        return success

    def _build_email(self, alert: Alert) -> tuple[str, str, str]:
        """构造邮件内容：(subject, html_body, text_body)"""
        event = alert.event
        llm = alert.llm_analysis
        event_cn = _EVENT_TYPE_NAMES.get(event.event_type, event.event_type)
        risk_level = llm.risk_level if llm else "中"
        risk_marker = _RISK_COLORS.get(risk_level, ("⚪", "#999999"))
        camera_name = event.camera_name or event.camera_id
        timestamp = (
            datetime.fromtimestamp(event.timestamp).strftime("%Y-%m-%d %H:%M:%S")
            if event.timestamp
            else "未知"
        )

        subject = f"[Vision Agent][{risk_level}] {camera_name} - {event_cn}"

        # HTML 邮件（外部数据转义防注入）
        esc = html.escape
        html_parts = [
            "<html><body>",
            f"<h2 style='color:{risk_marker[1]}'>[{esc(risk_level)}] {esc(event_cn)}</h2>",
            "<table style='border-collapse:collapse'>",
            f"<tr><td><b>摄像头</b></td><td>{esc(camera_name)}</td></tr>",
            f"<tr><td><b>时间</b></td><td>{esc(timestamp)}</td></tr>",
            f"<tr><td><b>规则</b></td><td>{esc(event.rule_name)}</td></tr>",
            f"<tr><td><b>严重级别</b></td><td><span style='color:{risk_marker[1]}'>{esc(event.severity.value)}</span></td></tr>",
            "</table>",
        ]

        if llm and llm.description:
            html_parts.extend(
                [
                    "<hr>",
                    "<h3>LLM 分析</h3>",
                    f"<p><b>情况描述</b>：{esc(llm.description)}</p>",
                    f"<p><b>风险等级</b>：<span style='color:{risk_marker[1]}'>{esc(llm.risk_level)}</span></p>",
                    f"<p><b>建议措施</b>：{esc(llm.suggestion)}</p>",
                ]
            )

        # 截图占位
        html_parts.append('<p><img src="cid:snapshot" style="max-width:640px" /></p>')
        html_parts.extend(
            [
                f"<hr><small>告警ID：{alert.alert_id} | Vision Agent</small>",
                "</body></html>",
            ]
        )

        # 纯文本版本
        text_lines = [
            f"[{risk_level}] {event_cn}",
            f"摄像头：{camera_name}",
            f"时间：{timestamp}",
            f"规则：{event.rule_name}",
            f"严重级别：{event.severity.value}",
        ]
        if llm and llm.description:
            text_lines.extend(
                [
                    "",
                    "LLM 分析：",
                    f"  情况：{llm.description}",
                    f"  风险：{llm.risk_level}",
                    f"  建议：{llm.suggestion}",
                ]
            )
        text_lines.extend(["", f"告警ID：{alert.alert_id} | Vision Agent"])

        return subject, "".join(html_parts), "\n".join(text_lines)

    def _load_snapshot(self, path: str) -> bytes | None:
        """读取截图文件"""
        if not path:
            return None
        try:
            from pathlib import Path

            p = Path(path)
            if p.exists():
                return p.read_bytes()
        except Exception as e:
            logger.warning("snapshot_load_failed path=%s error=%s", path, str(e))
        return None

    def _send_email(
        self,
        subject: str,
        html: str,
        text: str,
        snapshot: bytes | None = None,
    ) -> bool:
        """通过 SMTP 发送邮件"""
        config = self._config
        from_addr = config.from_addr or config.smtp_user

        # 构造邮件
        msg = MIMEMultipart("related")
        alt = MIMEMultipart("alternative")
        alt.attach(MIMEText(text, "plain", "utf-8"))
        alt.attach(MIMEText(html, "html", "utf-8"))
        msg.attach(alt)

        msg["Subject"] = subject
        msg["From"] = f"{config.from_name} <{from_addr}>"
        msg["To"] = ", ".join(config.to_addrs or [])

        # 嵌入截图
        if snapshot:
            try:
                img = MIMEImage(snapshot, name="snapshot.jpg")
                img.add_header("Content-ID", "<snapshot>")
                msg.attach(img)
            except Exception as e:
                logger.warning("snapshot_embed_failed error=%s", str(e))

        # SMTP 发送
        server = None
        try:
            if config.use_ssl:
                context = ssl.create_default_context()
                server = smtplib.SMTP_SSL(
                    config.smtp_host,
                    config.smtp_port,
                    timeout=config.timeout,
                    context=context,
                )
            else:
                server = smtplib.SMTP(
                    config.smtp_host, config.smtp_port, timeout=config.timeout
                )
                server.starttls()

            server.login(config.smtp_user, config.smtp_pass)
            server.sendmail(from_addr, config.to_addrs or [], msg.as_string())
            return True

        except Exception as e:
            logger.error("email_failed error=%s", str(e))
            return False
        finally:
            if server:
                try:
                    server.quit()
                except Exception:
                    pass
