"""Tests for vision_agent.llm.analyzer"""

from __future__ import annotations

import json
import time
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from vision_agent.core.types import Alert, BoundingBox, Detection, Event, LLMAnalysis, Severity
from vision_agent.llm.analyzer import (
    LLMAnalyzer,
    LLMConfig,
    _build_fallback_analysis,
    _build_prompt,
    _encode_snapshot,
    _parse_response,
    _validate_risk_level,
)


# ─── Helpers ──────────────────────────────────────────────────


def _make_event(**overrides) -> Event:
    defaults = dict(
        event_id="ev-001",
        event_type="intrusion",
        camera_id="cam1",
        camera_name="前门摄像头",
        rule_name="入侵检测",
        severity=Severity.WARNING,
        timestamp=1700000000.0,
        detections=[],
        tracks=[],
        metadata={},
        snapshot_path="",
    )
    defaults.update(overrides)
    return Event(**defaults)


def _make_detection(class_name: str = "person", frame_id: int = 0) -> Detection:
    return Detection(
        frame_id=frame_id,
        class_id=0,
        class_name=class_name,
        confidence=0.9,
        bbox=BoundingBox(10, 10, 50, 50),
    )


def _make_alert(event: Event | None = None) -> Alert:
    return Alert(event=event or _make_event())


class MockProvider:
    """Mock LLMProviderProtocol with a configurable return value."""

    def __init__(self, response: str | None = '{"description":"mock","risk_level":"低","suggestion":"none","context":""}'):
        self._response = response
        self.call_count = 0
        self.last_prompt = None
        self.last_image_b64 = None
        self.last_model = None

    def chat_with_image(
        self, prompt: str, image_base64: str | None = None, model: str | None = None
    ) -> str | None:
        self.call_count += 1
        self.last_prompt = prompt
        self.last_image_b64 = image_base64
        self.last_model = model
        return self._response

    @property
    def success_rate(self) -> float:
        return 1.0


# ─── 1. _build_prompt ─────────────────────────────────────────


class TestBuildPrompt:
    def test_contains_event_type_cn(self):
        event = _make_event(event_type="intrusion")
        prompt = _build_prompt(event, "系统提示")
        assert "区域闯入" in prompt

    def test_contains_unknown_event_type_fallback(self):
        event = _make_event(event_type="unknown_type")
        prompt = _build_prompt(event, "系统提示")
        assert "unknown_type" in prompt

    def test_contains_camera_name(self):
        event = _make_event(camera_name="后门摄像头", camera_id="cam2")
        prompt = _build_prompt(event, "系统提示")
        assert "后门摄像头" in prompt

    def test_camera_id_fallback_when_name_missing(self):
        event = _make_event(camera_name="", camera_id="cam_99")
        prompt = _build_prompt(event, "系统提示")
        assert "cam_99" in prompt

    def test_contains_rule_name(self):
        event = _make_event(rule_name="离岗检测规则")
        prompt = _build_prompt(event, "系统提示")
        assert "离岗检测规则" in prompt

    def test_contains_severity(self):
        event = _make_event(severity=Severity.CRITICAL)
        prompt = _build_prompt(event, "系统提示")
        assert "critical" in prompt

    def test_contains_formatted_timestamp(self):
        event = _make_event(timestamp=1700000000.0)
        prompt = _build_prompt(event, "系统提示")
        assert "2023-11-15" in prompt

    def test_no_timestamp_shows_unknown(self):
        event = _make_event(timestamp=0.0)
        prompt = _build_prompt(event, "系统提示")
        # timestamp=0.0 is falsy -> "未知"
        assert "未知" in prompt

    def test_detection_targets_aggregated(self):
        d1 = _make_detection("person")
        d2 = _make_detection("person")
        d3 = _make_detection("car")
        event = _make_event(detections=[d1, d2, d3])
        prompt = _build_prompt(event, "系统提示")
        assert "person" in prompt
        assert "car" in prompt

    def test_tracks_count_in_prompt(self):
        event = _make_event(tracks=[MagicMock(), MagicMock()])
        prompt = _build_prompt(event, "系统提示")
        assert "2" in prompt

    def test_metadata_in_prompt(self):
        event = _make_event(metadata={"zone": "A区", "threshold": 5})
        prompt = _build_prompt(event, "系统提示")
        assert "A区" in prompt

    def test_rag_context_included(self):
        event = _make_event()
        prompt = _build_prompt(event, "系统提示", rag_context="历史案例：类似事件发生在...")
        assert "参考资料" in prompt
        assert "历史案例" in prompt

    def test_no_rag_context_when_none(self):
        event = _make_event()
        prompt = _build_prompt(event, "系统提示", rag_context=None)
        assert "参考资料" not in prompt

    def test_system_prompt_at_start(self):
        event = _make_event()
        prompt = _build_prompt(event, "自定义系统提示")
        assert prompt.startswith("自定义系统提示")

    def test_no_detections_omits_target_section(self):
        event = _make_event(detections=[])
        prompt = _build_prompt(event, "系统提示")
        assert "检测目标" not in prompt


# ─── 2. _encode_snapshot ──────────────────────────────────────


class TestEncodeSnapshot:
    def test_encode_small_frame(self):
        """Mock cv2 to test encoding without real cv2."""
        fake_buffer = b"\xff\xd8fake_jpeg"
        mock_cv2 = MagicMock()
        mock_cv2.INTER_AREA = 1
        mock_cv2.IMWRITE_JPEG_QUALITY = 1
        mock_cv2.imencode.return_value = (True, fake_buffer)

        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        with patch.dict("sys.modules", {"cv2": mock_cv2}):
            result = _encode_snapshot(frame, quality=85, max_size=1280)

        assert result is not None
        # imencode should be called, resize should NOT (frame is smaller than max_size)
        mock_cv2.imencode.assert_called_once()
        mock_cv2.resize.assert_not_called()

    def test_encode_large_frame_gets_resized(self):
        fake_buffer = b"\xff\xd8fake_jpeg"
        mock_cv2 = MagicMock()
        mock_cv2.INTER_AREA = 1
        mock_cv2.IMWRITE_JPEG_QUALITY = 1
        mock_cv2.imencode.return_value = (True, fake_buffer)
        mock_cv2.resize.return_value = np.zeros((720, 1280, 3), dtype=np.uint8)

        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        with patch.dict("sys.modules", {"cv2": mock_cv2}):
            _encode_snapshot(frame, quality=85, max_size=1280)

        mock_cv2.resize.assert_called_once()

    def test_encode_returns_none_when_cv2_missing(self):
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        with patch.dict("sys.modules", {"cv2": None}):
            result = _encode_snapshot(frame)
        assert result is None

    def test_encode_returns_none_on_exception(self):
        mock_cv2 = MagicMock()
        mock_cv2.INTER_AREA = 1
        mock_cv2.imencode.side_effect = RuntimeError("encode failed")
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        with patch.dict("sys.modules", {"cv2": mock_cv2}):
            result = _encode_snapshot(frame)
        assert result is None

    def test_encode_is_base64_string(self):
        import base64

        fake_jpeg = b"\xff\xd8\xff\xe0test_jpeg_data"
        mock_cv2 = MagicMock()
        mock_cv2.INTER_AREA = 1
        mock_cv2.IMWRITE_JPEG_QUALITY = 1
        mock_cv2.imencode.return_value = (True, fake_jpeg)

        frame = np.zeros((100, 100, 3), dtype=np.uint8)
        with patch.dict("sys.modules", {"cv2": mock_cv2}):
            result = _encode_snapshot(frame)

        assert result == base64.b64encode(fake_jpeg).decode("utf-8")


# ─── 3. _parse_response ──────────────────────────────────────


class TestParseResponseJson:
    """Strategy 1: JSON extraction."""

    def test_pure_json_object(self):
        raw = json.dumps({
            "description": "检测到1人进入禁区",
            "risk_level": "高",
            "suggestion": "立即派人查看",
            "context": "无",
        })
        result = _parse_response(raw)
        assert result.description == "检测到1人进入禁区"
        assert result.risk_level == "高"
        assert result.suggestion == "立即派人查看"
        assert result.raw_response == raw

    def test_json_in_code_block(self):
        raw = 'some text\n```json\n{"description":"desc","risk_level":"紧急","suggestion":"act","context":""}\n```\nmore text'
        result = _parse_response(raw)
        assert result.description == "desc"
        assert result.risk_level == "紧急"

    def test_json_missing_optional_fields(self):
        raw = '{"description":"only desc"}'
        result = _parse_response(raw)
        assert result.description == "only desc"
        assert result.risk_level == "中"  # default
        assert result.suggestion == ""  # default

    def test_json_truncates_long_description(self):
        long_desc = "A" * 3000
        raw = json.dumps({"description": long_desc, "risk_level": "低", "suggestion": "", "context": ""})
        result = _parse_response(raw)
        assert len(result.description) == 2000

    def test_json_non_dict_ignored_falls_to_regex(self):
        """A JSON array (non-dict) is not parsed as analysis; regex takes over."""
        raw = '描述：有人闯入\n风险等级：高\n建议：查看监控'
        result = _parse_response(raw)
        # Falls through JSON (not valid JSON), regex extracts fields
        assert result.description == "有人闯入"

    def test_json_array_input_falls_through(self):
        """A valid JSON array is non-dict, so _try_parse_json returns None."""
        raw = '[1, 2, 3]'
        result = _parse_response(raw)
        # JSON array parsed but isinstance(data, dict) is False
        # Falls to text fallback
        assert result.risk_level == "中"


class TestParseResponseRegex:
    """Strategy 2: regex fallback."""

    def test_regex_description(self):
        raw = "描述：3人聚集在门口\n其他无关文字"
        result = _parse_response(raw)
        assert result.description == "3人聚集在门口"

    def test_regex_risk_level(self):
        raw = "风险等级：高\n建议：立即处理"
        result = _parse_response(raw)
        assert result.risk_level == "高"
        assert result.suggestion == "立即处理"

    def test_regex_colon_variants(self):
        raw = "description: test desc\nrisk_level: 低\nsuggestion: do something"
        result = _parse_response(raw)
        assert result.description == "test desc"
        assert result.risk_level == "低"

    def test_regex_no_match_falls_through(self):
        raw = "This is just a random text with no structured fields"
        result = _parse_response(raw)
        # Falls to text fallback
        assert result.description == raw
        assert result.risk_level == "中"


class TestParseResponseTextFallback:
    """Strategy 3: plain text fallback."""

    def test_text_fallback_description(self):
        raw = "some unstructured LLM output about an incident"
        result = _parse_response(raw)
        assert result.description == raw
        assert result.risk_level == "中"
        assert "无法结构化" in result.suggestion

    def test_text_fallback_truncates_long_text(self):
        raw = "X" * 3000
        result = _parse_response(raw)
        assert len(result.description) == 2000

    def test_empty_string_fallback(self):
        raw = ""
        result = _parse_response(raw)
        assert result.description == "LLM 返回为空"
        assert result.risk_level == "中"


class TestValidateRiskLevel:
    def test_valid_values(self):
        for val in ("低", "中", "高", "紧急"):
            assert _validate_risk_level(val) == val

    def test_english_mapping(self):
        assert _validate_risk_level("low") == "低"
        assert _validate_risk_level("medium") == "中"
        assert _validate_risk_level("high") == "高"
        assert _validate_risk_level("critical") == "紧急"

    def test_invalid_value_defaults_to_medium(self):
        assert _validate_risk_level("unknown") == "中"
        assert _validate_risk_level("") == "中"

    def test_case_insensitive_english(self):
        assert _validate_risk_level("HIGH") == "高"
        assert _validate_risk_level("Low") == "低"


# ─── 4. _build_fallback_analysis ──────────────────────────────


class TestBuildFallbackAnalysis:
    def test_basic_intrusion_event(self):
        event = _make_event(
            event_type="intrusion",
            camera_name="大门摄像头",
            severity=Severity.CRITICAL,
        )
        result = _build_fallback_analysis(event)
        assert "区域闯入" in result.description
        assert "大门摄像头" in result.description
        assert result.risk_level == "紧急"
        assert "人工查看" in result.suggestion
        assert result.raw_response == ""

    def test_warning_severity_maps_to_medium(self):
        event = _make_event(severity=Severity.WARNING)
        result = _build_fallback_analysis(event)
        assert result.risk_level == "中"

    def test_info_severity_maps_to_low(self):
        event = _make_event(severity=Severity.INFO)
        result = _build_fallback_analysis(event)
        assert result.risk_level == "低"

    def test_unknown_severity_defaults_to_medium(self):
        event = _make_event(severity=Severity.INFO)
        # Manually set an invalid severity to test fallback
        event.severity = "invalid"  # type: ignore
        result = _build_fallback_analysis(event)
        assert result.risk_level == "中"

    def test_with_detections(self):
        d1 = _make_detection("person")
        d2 = _make_detection("person")
        d3 = _make_detection("car")
        event = _make_event(detections=[d1, d2, d3])
        result = _build_fallback_analysis(event)
        assert "person" in result.description
        assert "car" in result.description

    def test_without_detections(self):
        event = _make_event(detections=[])
        result = _build_fallback_analysis(event)
        # No "×" means no target class counts are embedded
        assert "×" not in result.description

    def test_camera_id_fallback(self):
        event = _make_event(camera_name="", camera_id="cam_xyz")
        result = _build_fallback_analysis(event)
        assert "cam_xyz" in result.description

    def test_context_mentions_rule_engine(self):
        event = _make_event()
        result = _build_fallback_analysis(event)
        assert "规则引擎" in result.context

    def test_unknown_event_type_passes_through(self):
        event = _make_event(event_type="custom_unknown")
        result = _build_fallback_analysis(event)
        assert "custom_unknown" in result.description


# ─── 5. LLMAnalyzer.analyze ───────────────────────────────────


class TestAnalyzerAnalyze:
    def test_returns_analysis_on_success(self):
        response_json = json.dumps({
            "description": "正常分析",
            "risk_level": "低",
            "suggestion": "无需处理",
            "context": "",
        })
        provider = MockProvider(response=response_json)
        config = LLMConfig(enabled=True)
        analyzer = LLMAnalyzer(config=config, provider=provider)

        event = _make_event()
        result = analyzer.analyze(event)

        assert result is not None
        assert result.description == "正常分析"
        assert result.risk_level == "低"
        assert provider.call_count == 1

    def test_returns_none_when_disabled(self):
        provider = MockProvider()
        config = LLMConfig(enabled=False)
        analyzer = LLMAnalyzer(config=config, provider=provider)

        result = analyzer.analyze(_make_event())
        assert result is None
        assert provider.call_count == 0

    def test_returns_none_when_provider_is_none(self):
        config = LLMConfig(enabled=True)
        analyzer = LLMAnalyzer(config=config, provider=None)

        result = analyzer.analyze(_make_event())
        assert result is None

    def test_returns_none_when_provider_returns_none(self):
        provider = MockProvider(response=None)
        config = LLMConfig(enabled=True)
        analyzer = LLMAnalyzer(config=config, provider=provider)

        result = analyzer.analyze(_make_event())
        assert result is None

    def test_returns_none_when_provider_returns_empty_string(self):
        provider = MockProvider(response="")
        config = LLMConfig(enabled=True)
        analyzer = LLMAnalyzer(config=config, provider=provider)

        result = analyzer.analyze(_make_event())
        assert result is None

    def test_returns_none_on_provider_exception(self):
        provider = MockProvider()
        provider.chat_with_image = MagicMock(side_effect=RuntimeError("API down"))
        config = LLMConfig(enabled=True)
        analyzer = LLMAnalyzer(config=config, provider=provider)

        result = analyzer.analyze(_make_event())
        assert result is None

    def test_passes_model_to_provider(self):
        provider = MockProvider(response='{"description":"","risk_level":"低","suggestion":"","context":""}')
        config = LLMConfig(enabled=True, model="gpt-4o")
        analyzer = LLMAnalyzer(config=config, provider=provider)

        analyzer.analyze(_make_event())
        assert provider.last_model == "gpt-4o"

    def test_snapshot_passed_as_base64(self):
        mock_cv2 = MagicMock()
        mock_cv2.INTER_AREA = 1
        mock_cv2.IMWRITE_JPEG_QUALITY = 1
        mock_cv2.imencode.return_value = (True, b"\xff\xd8fake")

        provider = MockProvider(response='{"description":"d","risk_level":"低","suggestion":"s","context":""}')
        config = LLMConfig(enabled=True)
        analyzer = LLMAnalyzer(config=config, provider=provider)

        frame = np.zeros((100, 100, 3), dtype=np.uint8)
        with patch.dict("sys.modules", {"cv2": mock_cv2}):
            analyzer.analyze(_make_event(), snapshot=frame)

        assert provider.last_image_b64 is not None

    def test_no_snapshot_passes_none_image(self):
        provider = MockProvider(response='{"description":"d","risk_level":"低","suggestion":"s","context":""}')
        config = LLMConfig(enabled=True)
        analyzer = LLMAnalyzer(config=config, provider=provider)

        analyzer.analyze(_make_event(), snapshot=None)
        assert provider.last_image_b64 is None

    def test_rag_context_forwarded_in_prompt(self):
        provider = MockProvider(response='{"description":"d","risk_level":"低","suggestion":"s","context":""}')
        config = LLMConfig(enabled=True)
        analyzer = LLMAnalyzer(config=config, provider=provider)

        analyzer.analyze(_make_event(), rag_context="RAG资料")
        assert "RAG资料" in provider.last_prompt

    def test_success_rate_increments(self):
        provider = MockProvider(response='{"description":"d","risk_level":"低","suggestion":"s","context":""}')
        config = LLMConfig(enabled=True)
        analyzer = LLMAnalyzer(config=config, provider=provider)

        analyzer.analyze(_make_event())
        assert analyzer._total_calls == 1

    def test_name_property(self):
        config = LLMConfig()
        analyzer = LLMAnalyzer(config=config)
        assert analyzer.name == "llm_analyze"

    def test_success_rate_initial(self):
        config = LLMConfig()
        analyzer = LLMAnalyzer(config=config)
        assert analyzer.success_rate == 1.0


# ─── 6. LLMAnalyzer.execute ───────────────────────────────────


class TestAnalyzerExecute:
    def test_sets_llm_analysis_on_alert(self):
        response_json = json.dumps({
            "description": "分析结果",
            "risk_level": "高",
            "suggestion": "立即查看",
            "context": "补充",
        })
        provider = MockProvider(response=response_json)
        config = LLMConfig(enabled=True)
        analyzer = LLMAnalyzer(config=config, provider=provider)

        event = _make_event()
        alert = _make_alert(event)
        result = analyzer.execute(alert)

        assert result is True
        assert alert.llm_analysis is not None
        assert alert.llm_analysis.description == "分析结果"
        assert alert.llm_analysis.risk_level == "高"

    def test_returns_true_when_llm_disabled(self):
        provider = MockProvider()
        config = LLMConfig(enabled=False)
        analyzer = LLMAnalyzer(config=config, provider=provider)

        alert = _make_alert()
        result = analyzer.execute(alert)

        assert result is True
        assert alert.llm_analysis is not None  # fallback
        assert alert.llm_analysis.raw_response == ""
        assert "规则引擎" in alert.llm_analysis.context

    def test_returns_true_when_provider_is_none(self):
        config = LLMConfig(enabled=True)
        analyzer = LLMAnalyzer(config=config, provider=None)

        alert = _make_alert()
        result = analyzer.execute(alert)

        assert result is True
        assert alert.llm_analysis is not None
        assert alert.llm_analysis.raw_response == ""

    def test_fallback_used_when_provider_returns_none(self):
        provider = MockProvider(response=None)
        config = LLMConfig(enabled=True)
        analyzer = LLMAnalyzer(config=config, provider=provider)

        alert = _make_alert()
        result = analyzer.execute(alert)

        assert result is True
        assert alert.llm_analysis is not None
        assert alert.llm_analysis.raw_response == ""
        assert analyzer._fallback_calls == 1

    def test_returns_true_for_empty_event_id(self):
        event = _make_event(event_id="")
        alert = _make_alert(event)
        config = LLMConfig()
        analyzer = LLMAnalyzer(config=config)

        result = analyzer.execute(alert)
        assert result is True

    def test_success_calls_incremented(self):
        provider = MockProvider(response='{"description":"ok","risk_level":"低","suggestion":"s","context":""}')
        config = LLMConfig(enabled=True)
        analyzer = LLMAnalyzer(config=config, provider=provider)

        alert = _make_alert()
        analyzer.execute(alert)
        assert analyzer._success_calls == 1
        assert analyzer._fallback_calls == 0

    def test_snapshot_path_empty_still_works(self):
        """When snapshot_path is empty, _load_snapshot returns None, analysis still runs."""
        provider = MockProvider(response='{"description":"ok","risk_level":"低","suggestion":"s","context":""}')
        config = LLMConfig(enabled=True)
        analyzer = LLMAnalyzer(config=config, provider=provider)

        event = _make_event(snapshot_path="")
        alert = _make_alert(event)
        result = analyzer.execute(alert)
        assert result is True
        assert alert.llm_analysis.description == "ok"


# ─── 7. LLMAnalyzer._load_snapshot ────────────────────────────


class TestLoadSnapshot:
    def test_returns_none_for_empty_path(self):
        result = LLMAnalyzer._load_snapshot("")
        assert result is None

    def test_returns_none_when_cv2_not_installed(self):
        with patch.dict("sys.modules", {"cv2": None}):
            result = LLMAnalyzer._load_snapshot("/fake/path.jpg")
        assert result is None

    def test_returns_none_when_imread_returns_none(self):
        mock_cv2 = MagicMock()
        mock_cv2.imread.return_value = None
        with patch.dict("sys.modules", {"cv2": mock_cv2}):
            result = LLMAnalyzer._load_snapshot("/fake/path.jpg")
        assert result is None

    def test_returns_frame_on_success(self):
        fake_frame = np.zeros((480, 640, 3), dtype=np.uint8)
        mock_cv2 = MagicMock()
        mock_cv2.imread.return_value = fake_frame
        with patch.dict("sys.modules", {"cv2": mock_cv2}):
            result = LLMAnalyzer._load_snapshot("/fake/path.jpg")
        assert result is not None
        assert result.shape == (480, 640, 3)


# ─── 8. Edge cases ────────────────────────────────────────────


class TestEdgeCases:
    def test_empty_event_object(self):
        """All fields defaulted — should not crash _build_prompt or _build_fallback."""
        event = Event()
        prompt = _build_prompt(event, "系统提示")
        assert isinstance(prompt, str)

        fallback = _build_fallback_analysis(event)
        assert isinstance(fallback, LLMAnalysis)

    def test_parse_response_with_extra_text_around_json_in_codeblock(self):
        raw = 'Here is my analysis:\n```json\n{"description":"found intruder","risk_level":"紧急","suggestion":"check","context":""}\n```\nEnd of analysis.'
        result = _parse_response(raw)
        assert result.description == "found intruder"
        assert result.risk_level == "紧急"

    def test_parse_response_json_with_surrounding_text_falls_to_regex(self):
        """JSON embedded in surrounding prose (no code block) falls to regex."""
        raw = '分析结果:\n{"description":"found intruder","risk_level":"紧急","suggestion":"check","context":""}\n以上为分析结果.'
        result = _parse_response(raw)
        # Falls through JSON parsing, regex picks up structured fields from text
        assert isinstance(result.description, str)
        assert len(result.description) > 0

    def test_parse_response_malformed_json_falls_to_regex(self):
        raw = "描述：有人闯入\n风险等级：紧急\n建议：立即查看\n这是额外文字"
        result = _parse_response(raw)
        assert result.description == "有人闯入"
        assert result.risk_level == "紧急"
        assert result.suggestion == "立即查看"

    def test_multiple_detections_same_class(self):
        dets = [_make_detection("person") for _ in range(5)]
        event = _make_event(detections=dets)
        prompt = _build_prompt(event, "sys")
        assert "person" in prompt

    def test_custom_system_prompt(self):
        config = LLMConfig(system_prompt="自定义提示词")
        analyzer = LLMAnalyzer(config=config)
        assert analyzer._system_prompt == "自定义提示词"

    def test_default_system_prompt_when_empty(self):
        config = LLMConfig(system_prompt="")
        analyzer = LLMAnalyzer(config=config)
        assert "视频监控" in analyzer._system_prompt

    def test_fallback_analysis_with_zero_timestamp(self):
        """Event with timestamp=0.0 should still produce valid fallback."""
        event = _make_event(timestamp=0.0, severity=Severity.INFO)
        result = _build_fallback_analysis(event)
        assert result.risk_level == "低"
        assert isinstance(result.description, str)

    def test_execute_with_failing_provider_sets_fallback(self):
        provider = MockProvider()
        provider.chat_with_image = MagicMock(side_effect=Exception("timeout"))
        config = LLMConfig(enabled=True)
        analyzer = LLMAnalyzer(config=config, provider=provider)

        alert = _make_alert()
        result = analyzer.execute(alert)
        assert result is True
        assert alert.llm_analysis is not None
        assert alert.llm_analysis.raw_response == ""

    def test_analyze_with_rag_and_snapshot(self):
        """Both rag_context and snapshot should work together."""
        mock_cv2 = MagicMock()
        mock_cv2.INTER_AREA = 1
        mock_cv2.IMWRITE_JPEG_QUALITY = 1
        mock_cv2.imencode.return_value = (True, b"\xff\xd8fake")

        provider = MockProvider(response='{"description":"ok","risk_level":"低","suggestion":"s","context":""}')
        config = LLMConfig(enabled=True)
        analyzer = LLMAnalyzer(config=config, provider=provider)

        frame = np.zeros((100, 100, 3), dtype=np.uint8)
        with patch.dict("sys.modules", {"cv2": mock_cv2}):
            result = analyzer.analyze(
                _make_event(), snapshot=frame, rag_context="RAG参考"
            )

        assert result is not None
        assert "RAG参考" in provider.last_prompt
        assert provider.last_image_b64 is not None
