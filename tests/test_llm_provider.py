"""Tests for vision_agent.llm.provider"""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

from vision_agent.llm.provider import (
    BudgetTracker,
    CircuitBreaker,
    LLMProviderConfig,
    OpenAICompatibleProvider,
    ResponseCache,
    _RetryableError,
)


# ─── Helpers ──────────────────────────────────────────────────


def _make_config(**overrides) -> LLMProviderConfig:
    defaults = dict(
        api_base="https://api.example.com/v1",
        api_key="sk-test-key",
        model="gpt-4o-mini",
        timeout=10,
        max_retries=2,
        monthly_budget=100.0,
        budget_alert_threshold=0.8,
        cache_enabled=True,
        cache_ttl=3600,
        cache_max_size=256,
        circuit_failure_threshold=3,
        circuit_cooldown_seconds=300,
    )
    defaults.update(overrides)
    return LLMProviderConfig(**defaults)


def _make_mock_response(
    status_code: int = 200,
    json_data: dict | None = None,
    text: str = "",
    headers: dict | None = None,
) -> MagicMock:
    """Create a mock httpx.Response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    resp.headers = headers or {}
    if json_data is not None:
        resp.json.return_value = json_data
    return resp


def _success_response(content: str = "Hello!", prompt_tokens: int = 10, completion_tokens: int = 5) -> MagicMock:
    return _make_mock_response(
        status_code=200,
        json_data={
            "choices": [{"message": {"content": content}}],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            },
        },
    )


def _make_provider(config: LLMProviderConfig | None = None) -> tuple[OpenAICompatibleProvider, MagicMock]:
    """Create a provider with a mock httpx client injected."""
    cfg = config or _make_config()
    provider = OpenAICompatibleProvider(cfg)
    mock_client = MagicMock()
    provider._client = mock_client
    return provider, mock_client


# ─── CircuitBreaker ───────────────────────────────────────────


class TestCircuitBreaker:
    def test_initial_state_is_closed(self):
        cb = CircuitBreaker(failure_threshold=3, cooldown_seconds=10)
        assert cb.state == CircuitBreaker.CLOSED
        assert cb.allow_request() is True

    def test_closed_to_open_after_threshold_failures(self):
        cb = CircuitBreaker(failure_threshold=3, cooldown_seconds=10)
        for _ in range(3):
            cb.record_failure()
        assert cb.state == CircuitBreaker.OPEN
        assert cb.allow_request() is False

    def test_open_blocks_until_cooldown(self, monkeypatch):
        cb = CircuitBreaker(failure_threshold=2, cooldown_seconds=100)
        monkeypatch.setattr(time, "time", lambda: 1000.0)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitBreaker.OPEN

        # still in cooldown
        monkeypatch.setattr(time, "time", lambda: 1050.0)
        assert cb.allow_request() is False
        assert cb.state == CircuitBreaker.OPEN

    def test_open_to_half_open_after_cooldown(self, monkeypatch):
        cb = CircuitBreaker(failure_threshold=2, cooldown_seconds=100)
        monkeypatch.setattr(time, "time", lambda: 1000.0)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitBreaker.OPEN

        # cooldown elapsed
        monkeypatch.setattr(time, "time", lambda: 1100.0)
        assert cb.allow_request() is True
        assert cb.state == CircuitBreaker.HALF_OPEN

    def test_half_open_blocks_second_request(self, monkeypatch):
        cb = CircuitBreaker(failure_threshold=1, cooldown_seconds=10)
        monkeypatch.setattr(time, "time", lambda: 1000.0)
        cb.record_failure()
        monkeypatch.setattr(time, "time", lambda: 1020.0)
        # first request in half-open
        assert cb.allow_request() is True
        # second request should be blocked while pending
        assert cb.allow_request() is False

    def test_half_open_success_closes(self, monkeypatch):
        cb = CircuitBreaker(failure_threshold=1, cooldown_seconds=10)
        monkeypatch.setattr(time, "time", lambda: 1000.0)
        cb.record_failure()
        monkeypatch.setattr(time, "time", lambda: 1020.0)
        cb.allow_request()  # transition to HALF_OPEN
        cb.record_success()
        assert cb.state == CircuitBreaker.CLOSED
        assert cb.allow_request() is True

    def test_half_open_failure_reopens(self, monkeypatch):
        cb = CircuitBreaker(failure_threshold=1, cooldown_seconds=10)
        monkeypatch.setattr(time, "time", lambda: 1000.0)
        cb.record_failure()
        monkeypatch.setattr(time, "time", lambda: 1020.0)
        cb.allow_request()  # HALF_OPEN
        cb.record_failure()
        assert cb.state == CircuitBreaker.OPEN

    def test_reset_returns_to_closed(self):
        cb = CircuitBreaker(failure_threshold=1, cooldown_seconds=10)
        cb.record_failure()
        assert cb.state == CircuitBreaker.OPEN
        cb.reset()
        assert cb.state == CircuitBreaker.CLOSED
        assert cb.allow_request() is True

    def test_success_resets_failure_count(self):
        cb = CircuitBreaker(failure_threshold=5, cooldown_seconds=10)
        cb.record_failure()
        cb.record_failure()
        cb.record_success()
        # failure count is reset; need full threshold again to open
        for _ in range(4):
            cb.record_failure()
        assert cb.state == CircuitBreaker.CLOSED
        cb.record_failure()
        assert cb.state == CircuitBreaker.OPEN


# ─── BudgetTracker ────────────────────────────────────────────


class TestBudgetTracker:
    def test_initial_cost_is_zero(self):
        bt = BudgetTracker(monthly_budget=100.0)
        assert bt.monthly_cost == 0.0
        assert bt.monthly_budget == 100.0
        assert bt.can_afford() is True

    def test_record_usage_known_model(self):
        bt = BudgetTracker(monthly_budget=100.0)
        # gpt-4o-mini: (0.00015, 0.0006) per 1K tokens
        cost = bt.record_usage("gpt-4o-mini", prompt_tokens=1000, completion_tokens=1000)
        expected = (1000 * 0.00015 + 1000 * 0.0006) / 1000
        assert abs(cost - expected) < 1e-10
        assert abs(bt.monthly_cost - expected) < 1e-10

    def test_record_usage_unknown_model_uses_default(self):
        bt = BudgetTracker(monthly_budget=100.0)
        # DEFAULT_PRICE = (0.002, 0.006)
        cost = bt.record_usage("unknown-model", prompt_tokens=1000, completion_tokens=500)
        expected = (1000 * 0.002 + 500 * 0.006) / 1000
        assert abs(cost - expected) < 1e-10

    def test_record_usage_model_name_contains_known_key(self):
        bt = BudgetTracker(monthly_budget=100.0)
        # "gpt-4o" is a key in PRICING, "gpt-4o-mini-custom" should match
        cost = bt.record_usage("gpt-4o-mini-custom", prompt_tokens=1000, completion_tokens=1000)
        expected = (1000 * 0.00015 + 1000 * 0.0006) / 1000
        assert abs(cost - expected) < 1e-10

    def test_budget_exhausted(self):
        bt = BudgetTracker(monthly_budget=0.001)
        bt.record_usage("gpt-4o-mini", prompt_tokens=10000, completion_tokens=10000)
        assert bt.can_afford() is False

    def test_budget_alert_triggers_once(self):
        # Use a tiny budget so a realistic token count exceeds the 80% threshold.
        # gpt-4o-mini: (0.00015, 0.0006) per 1K tokens
        # 50000 prompt + 50000 completion => cost = 0.0375
        bt = BudgetTracker(monthly_budget=0.01, alert_threshold=0.8)

        # first call: cost > threshold -> alert fires
        assert bt._budget_alerted is False
        bt.record_usage("gpt-4o-mini", prompt_tokens=50000, completion_tokens=50000)
        assert bt._budget_alerted is True

        # second call: alert flag already set -> no re-alert
        bt.record_usage("gpt-4o-mini", prompt_tokens=50000, completion_tokens=50000)
        assert bt._budget_alerted is True  # still True, no duplicate alert

    def test_monthly_reset(self):
        bt = BudgetTracker(monthly_budget=100.0)
        bt._current_month = 1
        bt._monthly_cost = 50.0
        bt._budget_alerted = True

        # simulate month change by patching datetime.now()
        fake_now = MagicMock()
        fake_now.month = 2
        with patch("vision_agent.llm.provider.datetime") as mock_dt:
            mock_dt.now.return_value = fake_now
            assert bt.can_afford() is True
            assert bt.monthly_cost == 0.0

    def test_record_usage_default_price_for_zero_tokens(self):
        bt = BudgetTracker(monthly_budget=100.0)
        cost = bt.record_usage("gpt-4o-mini", prompt_tokens=0, completion_tokens=0)
        assert cost == 0.0


# ─── ResponseCache ────────────────────────────────────────────


class TestResponseCache:
    def test_get_missing_returns_none(self):
        cache = ResponseCache(ttl=60, max_size=10)
        assert cache.get("missing") is None

    def test_set_then_get(self):
        cache = ResponseCache(ttl=60, max_size=10)
        cache.set("key", "value")
        assert cache.get("key") == "value"

    def test_ttl_expired_returns_none(self, monkeypatch):
        cache = ResponseCache(ttl=60, max_size=10)
        monkeypatch.setattr(time, "time", lambda: 1000.0)
        cache.set("key", "value")
        monkeypatch.setattr(time, "time", lambda: 1061.0)
        assert cache.get("key") is None

    def test_ttl_not_expired(self, monkeypatch):
        cache = ResponseCache(ttl=60, max_size=10)
        monkeypatch.setattr(time, "time", lambda: 1000.0)
        cache.set("key", "value")
        monkeypatch.setattr(time, "time", lambda: 1059.0)
        assert cache.get("key") == "value"

    def test_lru_eviction(self):
        cache = ResponseCache(ttl=3600, max_size=3)
        cache.set("a", "1")
        cache.set("b", "2")
        cache.set("c", "3")
        # cache full; adding "d" should evict "a" (oldest)
        cache.set("d", "4")
        assert cache.get("a") is None
        assert cache.get("d") == "4"

    def test_lru_access_moves_to_end(self):
        cache = ResponseCache(ttl=3600, max_size=3)
        cache.set("a", "1")
        cache.set("b", "2")
        cache.set("c", "3")
        # access "a" to make it most-recently-used
        cache.get("a")
        # adding "d" should evict "b" (the actual LRU)
        cache.set("d", "4")
        assert cache.get("a") == "1"
        assert cache.get("b") is None

    def test_overwrite_existing_key(self):
        cache = ResponseCache(ttl=3600, max_size=3)
        cache.set("a", "old")
        cache.set("a", "new")
        assert cache.get("a") == "new"

    def test_expired_entry_removes_from_access_order(self, monkeypatch):
        cache = ResponseCache(ttl=10, max_size=10)
        monkeypatch.setattr(time, "time", lambda: 1000.0)
        cache.set("key", "value")
        monkeypatch.setattr(time, "time", lambda: 1020.0)
        assert cache.get("key") is None
        # after expired get, internal state should be clean
        assert len(cache._store) == 0


# ─── OpenAICompatibleProvider.chat() ─────────────────────────


class TestProviderChat:
    def test_chat_success(self):
        provider, mock_client = _make_provider()
        mock_client.post.return_value = _success_response("Test answer")

        result = provider.chat([{"role": "user", "content": "hello"}])
        assert result == "Test answer"
        mock_client.post.assert_called_once()

    def test_chat_caches_result(self):
        provider, mock_client = _make_provider()
        mock_client.post.return_value = _success_response("Cached answer")

        messages = [{"role": "user", "content": "question"}]
        r1 = provider.chat(messages)
        r2 = provider.chat(messages)
        assert r1 == "Cached answer"
        assert r2 == "Cached answer"
        # second call should be a cache hit; only one HTTP call
        assert mock_client.post.call_count == 1

    def test_chat_high_temperature_skips_cache(self):
        provider, mock_client = _make_provider()
        mock_client.post.return_value = _success_response("Answer 1")
        messages = [{"role": "user", "content": "q"}]

        r1 = provider.chat(messages, temperature=0.8)
        mock_client.post.return_value = _success_response("Answer 2")
        r2 = provider.chat(messages, temperature=0.8)
        assert r1 == "Answer 1"
        assert r2 == "Answer 2"
        assert mock_client.post.call_count == 2

    def test_chat_cache_disabled(self):
        config = _make_config(cache_enabled=False)
        provider, mock_client = _make_provider(config)
        mock_client.post.return_value = _success_response("A1")
        messages = [{"role": "user", "content": "q"}]

        provider.chat(messages)
        mock_client.post.return_value = _success_response("A2")
        provider.chat(messages)
        assert mock_client.post.call_count == 2

    def test_chat_circuit_breaker_blocks(self, monkeypatch):
        provider, mock_client = _make_provider()
        # open the circuit
        provider._circuit._state = CircuitBreaker.OPEN
        monkeypatch.setattr(time, "time", lambda: 1000.0)
        provider._circuit._last_failure_time = 1000.0
        # cooldown not elapsed
        result = provider.chat([{"role": "user", "content": "q"}])
        assert result is None
        mock_client.post.assert_not_called()

    def test_chat_budget_exhausted(self):
        provider, mock_client = _make_provider()
        provider._budget._monthly_cost = 9999.0
        provider._budget._monthly_budget = 1.0
        result = provider.chat([{"role": "user", "content": "q"}])
        assert result is None
        mock_client.post.assert_not_called()

    def test_chat_tracks_cost(self):
        provider, mock_client = _make_provider()
        mock_client.post.return_value = _success_response("ok", prompt_tokens=1000, completion_tokens=500)

        provider.chat([{"role": "user", "content": "q"}])
        # gpt-4o-mini pricing
        expected = (1000 * 0.00015 + 500 * 0.0006) / 1000
        assert abs(provider.get_monthly_cost() - expected) < 1e-10


# ─── OpenAICompatibleProvider.chat_with_image() ───────────────


class TestProviderChatWithImage:
    def test_chat_with_image_success(self):
        provider, mock_client = _make_provider()
        mock_client.post.return_value = _success_response("Image analysis")

        result = provider.chat_with_image("describe this", image_base64="AAAA")
        assert result == "Image analysis"
        # verify the body was constructed with vision content
        call_args = mock_client.post.call_args
        body = call_args.kwargs.get("json") or call_args[1].get("json")
        messages = body["messages"]
        assert messages[0]["role"] == "user"
        content = messages[0]["content"]
        assert isinstance(content, list)
        assert content[0]["type"] == "text"
        assert content[1]["type"] == "image_url"

    def test_chat_with_image_no_image(self):
        provider, mock_client = _make_provider()
        mock_client.post.return_value = _success_response("Text only")

        result = provider.chat_with_image("hello", image_base64=None)
        assert result == "Text only"
        call_args = mock_client.post.call_args
        body = call_args.kwargs.get("json") or call_args[1].get("json")
        messages = body["messages"]
        assert isinstance(messages[0]["content"], str)

    def test_chat_with_image_custom_model(self):
        provider, mock_client = _make_provider()
        mock_client.post.return_value = _success_response("ok")

        provider.chat_with_image("q", image_base64="AAA", model="gpt-4o")
        # effective_model is used in the request body; self._model is not mutated
        call_args = mock_client.post.call_args
        body = call_args.kwargs.get("json") or call_args[1].get("json")
        assert body["model"] == "gpt-4o"

    def test_chat_with_image_caches_result(self):
        provider, mock_client = _make_provider()
        mock_client.post.return_value = _success_response("cached img")

        r1 = provider.chat_with_image("desc", image_base64="AAAA")
        r2 = provider.chat_with_image("desc", image_base64="AAAA")
        assert r1 == "cached img"
        assert r2 == "cached img"
        assert mock_client.post.call_count == 1


# ─── Retry Logic ──────────────────────────────────────────────


class TestRetryLogic:
    def test_retryable_error_is_retryable(self):
        assert OpenAICompatibleProvider._is_retryable(_RetryableError("429")) is True

    def test_timeout_error_is_retryable(self):
        class TimeoutError(Exception):
            pass
        err = TimeoutError("timed out")
        assert OpenAICompatibleProvider._is_retryable(err) is True

    def test_connection_error_is_retryable(self):
        class ConnectError(Exception):
            pass
        err = ConnectError("refused")
        assert OpenAICompatibleProvider._is_retryable(err) is True

    def test_generic_error_not_retryable(self):
        err = ValueError("bad value")
        assert OpenAICompatibleProvider._is_retryable(err) is False

    def test_retry_delay_exponential_backoff(self):
        d0 = OpenAICompatibleProvider._get_retry_delay(0, Exception("x"))
        d1 = OpenAICompatibleProvider._get_retry_delay(1, Exception("x"))
        assert d0 == 1.0
        assert d1 == 3.0

    def test_retry_delay_uses_retry_after(self):
        err = _RetryableError("429 Too Many Requests, retry_after=15")
        d = OpenAICompatibleProvider._get_retry_delay(0, err)
        assert d == 15.0

    def test_retry_on_429_then_success(self):
        provider, mock_client = _make_provider()
        rate_limit = _make_mock_response(
            status_code=429,
            text="rate limited",
            headers={"Retry-After": "0"},
        )
        success = _success_response("recovered")
        mock_client.post.side_effect = [rate_limit, success]

        with patch("vision_agent.llm.provider.time.sleep"):
            result = provider.chat([{"role": "user", "content": "q"}])
        assert result == "recovered"
        assert mock_client.post.call_count == 2

    def test_retry_on_5xx_then_success(self):
        provider, mock_client = _make_provider()
        server_err = _make_mock_response(status_code=500, text="Internal Server Error")
        success = _success_response("ok")
        mock_client.post.side_effect = [server_err, success]

        with patch("vision_agent.llm.provider.time.sleep"):
            result = provider.chat([{"role": "user", "content": "q"}])
        assert result == "ok"

    def test_non_retryable_4xx_fails_immediately(self):
        provider, mock_client = _make_provider()
        bad_request = _make_mock_response(status_code=400, text="Bad Request")
        mock_client.post.return_value = bad_request

        result = provider.chat([{"role": "user", "content": "q"}])
        assert result is None
        # no retry for 4xx (except 429)
        assert mock_client.post.call_count == 1

    def test_retry_exhausted_returns_none(self):
        provider, mock_client = _make_provider()
        server_err = _make_mock_response(status_code=503, text="Unavailable")
        mock_client.post.return_value = server_err

        with patch("vision_agent.llm.provider.time.sleep"):
            result = provider.chat([{"role": "user", "content": "q"}])
        # max_retries=2, so 3 attempts total
        assert result is None
        assert mock_client.post.call_count == 3

    def test_retryable_exception_triggers_retry(self):
        provider, mock_client = _make_provider()
        success = _success_response("ok")

        call_count = 0

        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise _RetryableError("transient error")
            return success

        mock_client.post.side_effect = side_effect

        with patch("vision_agent.llm.provider.time.sleep"):
            result = provider.chat([{"role": "user", "content": "q"}])
        assert result == "ok"
        assert mock_client.post.call_count == 2

    def test_non_retryable_exception_fails(self):
        provider, mock_client = _make_provider()
        mock_client.post.side_effect = ValueError("bad input")

        result = provider.chat([{"role": "user", "content": "q"}])
        assert result is None
        assert mock_client.post.call_count == 1


# ─── Provider misc ────────────────────────────────────────────


class TestProviderMisc:
    def test_get_model_name(self):
        provider, _ = _make_provider()
        assert provider.get_model_name() == "gpt-4o-mini"

    def test_success_rate(self):
        provider, _ = _make_provider()
        assert provider.success_rate == 1.0

    def test_close(self):
        provider, mock_client = _make_provider()
        provider.close()
        mock_client.close.assert_called_once()
        assert provider._client is None

    def test_close_no_client(self):
        config = _make_config()
        provider = OpenAICompatibleProvider(config)
        provider._client = None
        provider.close()  # should not raise

    def test_reset_circuit_breaker(self, monkeypatch):
        provider, _ = _make_provider()
        provider._circuit._state = CircuitBreaker.OPEN
        provider.reset_circuit_breaker()
        assert provider._circuit._state == CircuitBreaker.CLOSED

    def test_cache_key_messages_deterministic(self):
        msgs = [{"role": "user", "content": "hello"}]
        k1 = OpenAICompatibleProvider._cache_key_messages(msgs, 0.3)
        k2 = OpenAICompatibleProvider._cache_key_messages(msgs, 0.3)
        assert k1 == k2
        assert len(k1) == 64  # SHA256 hex

    def test_cache_key_image_includes_prompt_and_image(self):
        k1 = OpenAICompatibleProvider._cache_key_image("desc", "AAAA", 0.3)
        k2 = OpenAICompatibleProvider._cache_key_image("desc", "BBBB", 0.3)
        assert k1 != k2

    def test_cache_key_image_no_image(self):
        k = OpenAICompatibleProvider._cache_key_image("desc", None, 0.3)
        assert len(k) == 64
