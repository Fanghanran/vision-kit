"""
LLM 分析器模块 — 告警事件的智能分析

设计来源：docs/modules/llm/llm_analyzer.md

职责：
- 接收告警事件 + 截图，调用 LLM 进行语义分析
- 输出结构化 LLMAnalysis（描述/风险/建议/上下文）
- 三级降级：正常模式 → 纯文本模式 → 规则引擎原始结果
- JSON 结构化输出 + 多级解析（JSON→正则→文本兜底）

设计决策：
- LLM 是增强层而非必要层，分析失败不影响告警通知
- execute() 始终返回 True（降级是有意设计，不是错误）
- 截图压缩为 JPEG 85 质量、长边 1280px，控制 token 消耗
"""

from __future__ import annotations

import base64
import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

import numpy as np

from sentinelmind.core.types import Alert, Event, LLMAnalysis, Severity

logger = logging.getLogger(__name__)


# ─── 协议接口 ────────────────────────────────────────────────


@runtime_checkable
class LLMProviderProtocol(Protocol):
    """LLM 提供者接口（llm/provider.py 实现）"""

    def chat_with_image(
        self,
        prompt: str,
        image_base64: str | None = None,
        model: str | None = None,
    ) -> str | None:
        """调用 LLM，可选传入图片 base64。失败返回 None。"""
        ...

    @property
    def success_rate(self) -> float:
        """调用成功率"""
        ...


# ─── 配置 ────────────────────────────────────────────────────


@dataclass
class LLMConfig:
    """LLM 配置（llm_analyzer.md 2 节）"""

    enabled: bool = True
    provider_type: str = "openai_compatible"
    model: str = "gpt-4o-mini"
    timeout: int = 30
    max_retries: int = 2
    monthly_budget: float = 100.0
    budget_alert_threshold: float = 0.8
    system_prompt: str = ""
    snapshot_quality: int = 85
    snapshot_max_size: int = 1280


# ─── 默认 Prompt 模板 ────────────────────────────────────────

_DEFAULT_SYSTEM_PROMPT = """你是视频监控智能分析专家。根据提供的监控截图和事件信息，分析当前情况并输出 JSON 格式的结果。

输出格式要求（严格 JSON）：
{
  "description": "情况描述（简洁明了，100-300字）",
  "risk_level": "风险等级（低/中/高/紧急）",
  "suggestion": "建议处理措施（具体可执行）",
  "context": "补充说明（可选，如背景信息、关联事件等）"
}

注意：
- risk_level 只能是 低/中/高/紧急 四个值之一
- description 要客观描述画面内容和异常情况
- suggestion 要具体可操作，不要泛泛而谈
- 只输出 JSON，不要输出其他内容"""

# 事件类型中文映射
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

# severity → risk_level 映射
_SEVERITY_TO_RISK = {
    Severity.INFO: "低",
    Severity.WARNING: "中",
    Severity.CRITICAL: "紧急",
}


# ─── LLMAnalyzer 主类 ───────────────────────────────────────


class LLMAnalyzer:
    """LLM 分析器（llm_analyzer.md 2 节）

    对告警事件进行智能分析，输出结构化 LLMAnalysis。
    三级降级：正常 → 纯文本 → 规则引擎原始结果。
    """

    def __init__(
        self,
        config: LLMConfig,
        provider: LLMProviderProtocol | None = None,
        database: Any | None = None,
    ) -> None:
        self._config = config
        self._provider = provider
        self._database = database
        self._system_prompt = config.system_prompt or _DEFAULT_SYSTEM_PROMPT

        # 统计
        self._total_calls = 0
        self._success_calls = 0
        self._fallback_calls = 0

    @property
    def name(self) -> str:
        return "llm_analyze"

    @property
    def success_rate(self) -> float:
        if self._total_calls == 0:
            return 1.0
        return self._success_calls / self._total_calls

    # ─── 公开接口 ──────────────────────────────────────────────

    def execute(self, alert: Alert) -> bool:
        """ActionProtocol 接口（llm_analyzer.md 3.2 节）

        对告警执行 LLM 分析，结果写入 alert.llm_analysis。
        始终返回 True（降级是有意设计，不是错误）。
        """
        event = alert.event
        if not event.event_id:
            return True

        # 尝试读取截图
        snapshot = self._load_snapshot(event.snapshot_path)

        # 调用分析
        start = time.monotonic()
        result = self.analyze(event, snapshot)
        elapsed_ms = (time.monotonic() - start) * 1000

        if result is None:
            result = _build_fallback_analysis(event)
            self._fallback_calls += 1
            logger.info(
                "llm_fallback event=%s camera=%s",
                event.event_id,
                event.camera_id,
            )
        else:
            self._success_calls += 1

        alert.llm_analysis = result
        logger.info(
            "llm_analyze_done event=%s risk=%s elapsed=%.0fms fallback=%s",
            event.event_id,
            result.risk_level,
            elapsed_ms,
            result.raw_response == "",
        )
        return True

    def analyze(
        self,
        event: Event,
        snapshot: np.ndarray | None = None,
        rag_context: str | None = None,
    ) -> LLMAnalysis | None:
        """核心分析方法（llm_analyzer.md 3.1 节）

        Args:
            event: 事件对象
            snapshot: 截图帧（numpy BGR），None 表示纯文本模式
            rag_context: RAG 参考资料（可选）

        Returns:
            LLMAnalysis 或 None（LLM 不可用时）
        """
        if not self._config.enabled:
            return None
        # 检查控制面板开关
        if self._database and not self._database.get_control_value("llm.enabled"):
            return None
        if self._provider is None:
            return None

        self._total_calls += 1

        # 截图编码
        image_b64 = None
        if snapshot is not None:
            image_b64 = _encode_snapshot(
                snapshot,
                quality=self._config.snapshot_quality,
                max_size=self._config.snapshot_max_size,
            )

        # 构造 prompt
        prompt = _build_prompt(event, self._system_prompt, rag_context)

        # 调用 LLM
        try:
            raw_response = self._provider.chat_with_image(
                prompt=prompt,
                image_base64=image_b64,
                model=self._config.model,
            )
        except Exception as e:
            logger.error(
                "llm_call_error event=%s error=%s",
                event.event_id,
                str(e),
            )
            return None

        if not raw_response:
            return None

        # 解析响应
        return _parse_response(raw_response)

    # ─── 内部方法 ──────────────────────────────────────────────

    @staticmethod
    def _load_snapshot(snapshot_path: str) -> np.ndarray | None:
        """读取截图文件"""
        if not snapshot_path:
            return None
        try:
            import cv2

            frame = cv2.imread(snapshot_path)
            if frame is None:
                logger.warning("snapshot_read_failed path=%s", snapshot_path)
            return frame
        except ImportError:
            logger.warning("cv2_not_installed action=skip_snapshot")
            return None
        except Exception as e:
            logger.warning(
                "snapshot_load_error path=%s error=%s", snapshot_path, str(e)
            )
            return None


# ─── 辅助函数 ────────────────────────────────────────────────


def _build_prompt(
    event: Event,
    system_prompt: str,
    rag_context: str | None = None,
) -> str:
    """构造分析 prompt（llm_analyzer.md 3.3 节）"""
    parts = [system_prompt, "\n\n---\n\n"]

    # 事件上下文
    event_type_cn = _EVENT_TYPE_NAMES.get(event.event_type, event.event_type)
    timestamp_str = (
        datetime.fromtimestamp(event.timestamp).strftime("%Y-%m-%d %H:%M:%S")
        if event.timestamp
        else "未知"
    )

    parts.append("## 事件信息\n")
    parts.append(f"- 事件类型：{event_type_cn}\n")
    parts.append(f"- 摄像头：{event.camera_name or event.camera_id}\n")
    parts.append(f"- 触发规则：{event.rule_name}\n")
    parts.append(f"- 严重级别：{event.severity.value}\n")
    parts.append(f"- 发生时间：{timestamp_str}\n")

    # 检测目标统计
    if event.detections:
        class_counts: dict[str, int] = {}
        for det in event.detections:
            class_counts[det.class_name] = class_counts.get(det.class_name, 0) + 1
        targets = "、".join(f"{cls}×{cnt}" for cls, cnt in class_counts.items())
        parts.append(f"- 检测目标：{targets}\n")

    if event.tracks:
        parts.append(f"- 追踪目标数：{len(event.tracks)}\n")

    # metadata
    if event.metadata:
        parts.append(f"- 附加信息：{json.dumps(event.metadata, ensure_ascii=False)}\n")

    # RAG 参考资料
    if rag_context:
        parts.append("\n## 参考资料\n")
        parts.append(f"以下为相关参考资料，请结合分析：\n{rag_context}\n")

    return "".join(parts)


def _encode_snapshot(
    frame: np.ndarray,
    quality: int = 85,
    max_size: int = 1280,
) -> str | None:
    """将 numpy BGR 帧编码为 base64 JPEG（llm_analyzer.md 3.1 节）"""
    try:
        import cv2

        # 等比缩放
        h, w = frame.shape[:2]
        if max(h, w) > max_size:
            scale = max_size / max(h, w)
            new_w, new_h = int(w * scale), int(h * scale)
            frame = cv2.resize(frame, (new_w, new_h), interpolation=cv2.INTER_AREA)

        # JPEG 编码
        encode_params = [cv2.IMWRITE_JPEG_QUALITY, quality]
        _, buffer = cv2.imencode(".jpg", frame, encode_params)
        return base64.b64encode(buffer).decode("utf-8")

    except ImportError:
        logger.warning("cv2_not_installed action=skip_encode")
        return None
    except Exception as e:
        logger.warning("snapshot_encode_error error=%s", str(e))
        return None


def _parse_response(raw: str) -> LLMAnalysis:
    """解析 LLM 返回（llm_analyzer.md 3.4 节）

    三级策略：JSON 提取 → 正则回退 → 文本兜底
    """
    # 策略 1：JSON 提取
    analysis = _try_parse_json(raw)
    if analysis:
        analysis.raw_response = raw
        return analysis

    # 策略 2：正则回退
    analysis = _try_parse_regex(raw)
    if analysis:
        analysis.raw_response = raw
        return analysis

    # 策略 3：文本兜底
    return LLMAnalysis(
        description=raw[:2000] if raw else "LLM 返回为空",
        risk_level="中",
        suggestion="LLM 分析结果无法结构化，请查看原始返回",
        context="",
        raw_response=raw,
    )


def _try_parse_json(raw: str) -> LLMAnalysis | None:
    """尝试从文本中提取 JSON"""
    candidates: list[str] = []

    # 匹配 ```json ``` 块
    json_match = re.search(r"```json\s*(.*?)\s*```", raw, re.DOTALL)
    if json_match:
        candidates.append(json_match.group(1))

    # 匹配嵌入文本中的 JSON 对象 {...}
    brace_match = re.search(r"\{[^{}]*\}", raw, re.DOTALL)
    if brace_match:
        candidates.append(brace_match.group(0))

    # 整个文本
    candidates.append(raw.strip())

    for json_str in candidates:
        try:
            data = json.loads(json_str)
            if isinstance(data, dict) and "description" in data:
                return LLMAnalysis(
                    description=str(data.get("description", ""))[:2000],
                    risk_level=_validate_risk_level(str(data.get("risk_level", "中"))),
                    suggestion=str(data.get("suggestion", ""))[:2000],
                    context=str(data.get("context", "")),
                )
        except (json.JSONDecodeError, TypeError):
            continue

    return None


def _try_parse_regex(raw: str) -> LLMAnalysis | None:
    """正则提取字段"""
    patterns = {
        "description": r"(?:情况描述|描述|description)[：:]\s*(.+?)(?:\n|$)",
        "risk_level": r"(?:风险等级|风险|risk_level)[：:]\s*(.+?)(?:\n|$)",
        "suggestion": r"(?:建议|建议措施|suggestion)[：:]\s*(.+?)(?:\n|$)",
    }

    results: dict[str, str] = {}
    for field_name, pattern in patterns.items():
        match = re.search(pattern, raw, re.IGNORECASE)
        if match:
            results[field_name] = match.group(1).strip()

    if results.get("description") or results.get("risk_level"):
        return LLMAnalysis(
            description=results.get("description", "")[:2000],
            risk_level=_validate_risk_level(results.get("risk_level", "中")),
            suggestion=results.get("suggestion", "")[:2000],
        )

    return None


def _validate_risk_level(value: str) -> str:
    """校验 risk_level 枚举值"""
    valid = {"低", "中", "高", "紧急"}
    if value in valid:
        return value
    # 尝试映射
    mapping = {"low": "低", "medium": "中", "high": "高", "critical": "紧急"}
    return mapping.get(value.lower(), "中")


def _build_fallback_analysis(event: Event) -> LLMAnalysis:
    """LLM 不可用时的降级分析（llm_analyzer.md 3.5 节）"""
    event_type_cn = _EVENT_TYPE_NAMES.get(event.event_type, event.event_type)
    risk_level = _SEVERITY_TO_RISK.get(event.severity, "中")

    # 从 detections 统计目标
    target_info = ""
    if event.detections:
        class_counts: dict[str, int] = {}
        for det in event.detections:
            class_counts[det.class_name] = class_counts.get(det.class_name, 0) + 1
        targets = "、".join(f"{cls}×{cnt}" for cls, cnt in class_counts.items())
        target_info = f"，检测到 {targets}"

    camera_name = event.camera_name or event.camera_id
    description = f"[{event_type_cn}]：{camera_name} 检测到异常{target_info}"

    return LLMAnalysis(
        description=description,
        risk_level=risk_level,
        suggestion="LLM 分析不可用，请人工查看截图确认情况",
        context="此为规则引擎原始结果，未经 LLM 分析",
        raw_response="",
    )
