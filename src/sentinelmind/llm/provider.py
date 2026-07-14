"""
LLM 提供者模块 — 封装 LLM API 调用的全部非业务复杂性

设计来源：docs/modules/llm/llm_provider.md

职责：
- 断路器保护（三状态机：CLOSED/OPEN/HALF_OPEN）
- 指数退避重试（1s→3s，429 解析 Retry-After）
- 月度预算追踪与告警
- 响应缓存（SHA256 键 + TTL + LRU）
- 超时控制

设计决策：
- 使用 httpx（支持同步/异步，精细超时配置）
- 断路器冷却 50 分钟（LLM 不可用通常是持续性问题）
- 预算仅存内存（重启清零，风险可控）
- 温度 > 0.7 不缓存（高随机性输出缓存价值低）
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)


# ─── 配置 ────────────────────────────────────────────────────


@dataclass
class LLMProviderConfig:
    """LLM 提供者配置（llm_provider.md 2 节）"""

    api_base: str = ""
    api_key: str = ""
    model: str = "gpt-4o-mini"
    timeout: int = 30
    max_retries: int = 2
    monthly_budget: float = 100.0
    budget_alert_threshold: float = 0.8
    cache_enabled: bool = True
    cache_ttl: int = 3600
    cache_max_size: int = 256
    circuit_failure_threshold: int = 5
    circuit_cooldown_seconds: int = 3000


# ─── 断路器 ──────────────────────────────────────────────────


class CircuitBreaker:
    """断路器状态机（llm_provider.md 3.1 节）

    三状态：CLOSED → OPEN → HALF_OPEN → CLOSED
    """

    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"

    def __init__(
        self,
        failure_threshold: int = 5,
        cooldown_seconds: int = 3000,
    ) -> None:
        self._state = self.CLOSED
        self._failure_count = 0
        self._failure_threshold = failure_threshold
        self._cooldown_seconds = cooldown_seconds
        self._last_failure_time = 0.0
        self._half_open_pending = False
        self._lock = threading.Lock()

    @property
    def state(self) -> str:
        return self._state

    def allow_request(self) -> bool:
        """检查是否允许请求通过"""
        with self._lock:
            if self._state == self.CLOSED:
                return True
            if self._state == self.OPEN:
                if time.time() - self._last_failure_time >= self._cooldown_seconds:
                    self._state = self.HALF_OPEN
                    self._half_open_pending = True
                    logger.info("circuit_half_open testing recovery")
                    return True
                return False
            if self._state == self.HALF_OPEN:
                if not self._half_open_pending:
                    return True
                return False
            return True

    def record_success(self) -> None:
        """记录成功"""
        with self._lock:
            if self._state == self.HALF_OPEN:
                self._state = self.CLOSED
                logger.info("circuit_closed service recovered")
            self._failure_count = 0
            self._half_open_pending = False

    def record_failure(self) -> None:
        """记录失败"""
        with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.time()
            self._half_open_pending = False

            if self._state == self.HALF_OPEN:
                self._state = self.OPEN
                logger.warning(
                    "circuit_reopened failures=%d cooldown=%ds",
                    self._failure_count,
                    self._cooldown_seconds,
                )
            elif self._failure_count >= self._failure_threshold:
                self._state = self.OPEN
                logger.error(
                    "circuit_open failures=%d cooldown=%ds",
                    self._failure_count,
                    self._cooldown_seconds,
                )

    def reset(self) -> None:
        """手动重置断路器"""
        with self._lock:
            self._state = self.CLOSED
            self._failure_count = 0
            self._half_open_pending = False


# ─── 预算追踪 ────────────────────────────────────────────────


class BudgetTracker:
    """月度预算追踪（llm_provider.md 3.3 节）"""

    # 模型定价表（美元/1K tokens）
    PRICING: dict[str, tuple[float, float]] = {
        "gpt-4o-mini": (0.00015, 0.0006),
        "gpt-4o": (0.005, 0.015),
        "gpt-4-turbo": (0.01, 0.03),
        "gpt-3.5-turbo": (0.0005, 0.0015),
    }
    DEFAULT_PRICE = (0.002, 0.006)  # 默认定价

    def __init__(
        self,
        monthly_budget: float = 100.0,
        alert_threshold: float = 0.8,
    ) -> None:
        self._monthly_budget = monthly_budget
        self._alert_threshold = alert_threshold
        self._monthly_cost = 0.0
        self._current_month = datetime.now().month
        self._budget_alerted = False
        self._lock = threading.Lock()

    def can_afford(self) -> bool:
        """检查是否还有预算"""
        with self._lock:
            self._check_month_reset()
            return self._monthly_cost < self._monthly_budget

    def record_usage(
        self,
        model: str,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
    ) -> float:
        """记录使用量，返回本次成本"""
        with self._lock:
            self._check_month_reset()
            cost = self._calculate_cost(model, prompt_tokens, completion_tokens)
            self._monthly_cost += cost
            self._check_budget_alert()
            return cost

    @property
    def monthly_cost(self) -> float:
        return self._monthly_cost

    @property
    def monthly_budget(self) -> float:
        return self._monthly_budget

    def _calculate_cost(
        self,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
    ) -> float:
        prompt_price, completion_price = self.DEFAULT_PRICE
        for key, prices in self.PRICING.items():
            if key in model:
                prompt_price, completion_price = prices
                break
        return (
            prompt_tokens * prompt_price + completion_tokens * completion_price
        ) / 1000

    def _check_month_reset(self) -> None:
        current_month = datetime.now().month
        if current_month != self._current_month:
            self._monthly_cost = 0.0
            self._current_month = current_month
            self._budget_alerted = False

    def _check_budget_alert(self) -> None:
        if self._budget_alerted:
            return
        threshold = self._monthly_budget * self._alert_threshold
        if self._monthly_cost >= threshold:
            pct = int(self._monthly_cost / self._monthly_budget * 100)
            logger.warning(
                "budget_alert used=$%.2f/$%.2f (%d%%)",
                self._monthly_cost,
                self._monthly_budget,
                pct,
            )
            self._budget_alerted = True


# ─── 响应缓存 ────────────────────────────────────────────────


class ResponseCache:
    """LLM 响应缓存（llm_provider.md 3.4 节）

    SHA256 键 + TTL + LRU 淘汰
    """

    def __init__(
        self,
        ttl: int = 3600,
        max_size: int = 256,
    ) -> None:
        self._ttl = ttl
        self._max_size = max_size
        self._store: dict[str, tuple[str, float]] = {}  # key → (value, expire_at)
        self._access_order: list[str] = []  # LRU 顺序
        self._lock = threading.Lock()

    def get(self, key: str) -> str | None:
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            value, expire_at = entry
            if time.time() > expire_at:
                del self._store[key]
                self._access_order.remove(key)
                return None
            # LRU：移到末尾
            if key in self._access_order:
                self._access_order.remove(key)
            self._access_order.append(key)
            return value

    def set(self, key: str, value: str) -> None:
        with self._lock:
            if key in self._store:
                self._access_order.remove(key)
            self._store[key] = (value, time.time() + self._ttl)
            self._access_order.append(key)
            self._enforce_max_size()

    def _enforce_max_size(self) -> None:
        while len(self._store) > self._max_size:
            oldest_key = self._access_order.pop(0)
            self._store.pop(oldest_key, None)


# ─── OpenAI 兼容提供者 ──────────────────────────────────────


class OpenAICompatibleProvider:
    """OpenAI 兼容 API 提供者（llm_provider.md 2 节）

    带断路器、重试、预算控制、缓存的完整实现。
    """

    def __init__(self, config: LLMProviderConfig) -> None:
        self._config = config
        self._model = config.model

        # 内部组件
        self._circuit = CircuitBreaker(
            failure_threshold=config.circuit_failure_threshold,
            cooldown_seconds=config.circuit_cooldown_seconds,
        )
        self._budget = BudgetTracker(
            monthly_budget=config.monthly_budget,
            alert_threshold=config.budget_alert_threshold,
        )
        self._cache = (
            ResponseCache(
                ttl=config.cache_ttl,
                max_size=config.cache_max_size,
            )
            if config.cache_enabled
            else None
        )

        # HTTP 客户端（延迟初始化）
        self._client: Any = None
        self._client_lock = threading.Lock()

    @property
    def success_rate(self) -> float:
        """调用成功率（由 analyzer 读取）"""
        return 1.0  # 简化实现，由 analyzer 自行统计

    def get_model_name(self) -> str:
        return self._model

    def get_monthly_cost(self) -> float:
        return self._budget.monthly_cost

    def reset_circuit_breaker(self) -> None:
        self._circuit.reset()

    def close(self) -> None:
        if self._client:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None

    # ─── 核心接口 ──────────────────────────────────────────────

    def chat(
        self,
        messages: list[dict[str, Any]],
        temperature: float = 0.3,
        max_tokens: int = 1024,
    ) -> str | None:
        """纯文本对话"""
        # 缓存查找
        cache_key = self._cache_key_messages(messages, temperature)
        if self._cache and temperature <= 0.7:
            cached = self._cache.get(cache_key)
            if cached:
                logger.debug("cache_hit key=%s...", cache_key[:16])
                return cached

        # 构造请求体
        body = {
            "model": self._model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        result = self._call_api(body)
        if result and self._cache and temperature <= 0.7:
            self._cache.set(cache_key, result)
        return result

    def chat_with_image(
        self,
        prompt: str,
        image_base64: str | None = None,
        model: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 1024,
    ) -> str | None:
        """带图片的对话"""
        effective_model = model or self._model

        # 缓存查找
        cache_key = self._cache_key_image(prompt, image_base64, temperature)
        if self._cache and temperature <= 0.7:
            cached = self._cache.get(cache_key)
            if cached:
                logger.debug("cache_hit key=%s...", cache_key[:16])
                return cached

        # 构造消息
        if image_base64:
            content = [
                {"type": "text", "text": prompt},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"},
                },
            ]
            messages = [{"role": "user", "content": content}]
        else:
            messages = [{"role": "user", "content": prompt}]

        body = {
            "model": effective_model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        result = self._call_api(body)
        if result and self._cache and temperature <= 0.7:
            self._cache.set(cache_key, result)
        return result

    # ─── 内部方法 ──────────────────────────────────────────────

    def _call_api(self, body: dict[str, Any]) -> str | None:
        """发送 API 请求（含断路器、预算、重试）"""
        # 预算检查
        if not self._budget.can_afford():
            logger.error(
                "budget_exhausted used=$%.2f/$%.2f",
                self._budget.monthly_cost,
                self._budget.monthly_budget,
            )
            return None

        # 断路器检查
        if not self._circuit.allow_request():
            logger.warning("circuit_open blocking request")
            return None

        # 重试循环
        max_retries = self._config.max_retries
        for attempt in range(max_retries + 1):
            try:
                response = self._send_request(body)
                if response is not None:
                    self._circuit.record_success()
                    return response
                # response 为 None 表示不可重试的错误（如 4xx）
                self._circuit.record_failure()
                return None
            except Exception as e:
                if attempt < max_retries and self._is_retryable(e):
                    wait = self._get_retry_delay(attempt, e)
                    logger.warning(
                        "llm_retry attempt=%d/%d reason=%s wait=%.1fs",
                        attempt + 1,
                        max_retries,
                        str(e),
                        wait,
                    )
                    time.sleep(wait)
                else:
                    self._circuit.record_failure()
                    logger.error(
                        "llm_request_failed attempts=%d error=%s",
                        attempt + 1,
                        str(e),
                    )
                    return None

        return None  # 不可达，但满足类型检查

    def _send_request(self, body: dict[str, Any]) -> str | None:
        """发送单次 HTTP 请求"""
        client = self._get_client()
        headers = {
            "Authorization": f"Bearer {self._config.api_key}",
            "Content-Type": "application/json",
        }
        url = f"{self._config.api_base}/chat/completions"

        try:
            resp = client.post(url, json=body, headers=headers)
        except Exception as e:
            raise e  # 由上层处理重试

        if resp.status_code == 200:
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            # 记录使用量
            usage = data.get("usage", {})
            self._budget.record_usage(
                model=self._model,
                prompt_tokens=usage.get("prompt_tokens", 0),
                completion_tokens=usage.get("completion_tokens", 0),
            )
            logger.info(
                "llm_request_ok model=%s tokens=%d latency_ms=%.0f",
                self._model,
                usage.get("total_tokens", 0),
                0,  # 实际延迟由上层统计
            )
            return content

        # 错误响应
        if resp.status_code == 429:
            retry_after = resp.headers.get("Retry-After")
            raise _RetryableError(f"429 Too Many Requests, retry_after={retry_after}")

        if resp.status_code >= 500:
            raise _RetryableError(f"{resp.status_code} Server Error")

        # 4xx（除 429）不重试
        logger.error(
            "llm_request_failed status=%d body=%s",
            resp.status_code,
            resp.text[:200],
        )
        return None

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        with self._client_lock:
            if self._client is not None:
                return self._client
            try:
                import httpx

                self._client = httpx.Client(
                    timeout=httpx.Timeout(
                        connect=5.0,
                        read=float(self._config.timeout),
                        write=5.0,
                        pool=5.0,
                    )
                )
            except ImportError:
                logger.error("httpx_not_installed")
                raise
        return self._client

    @staticmethod
    def _is_retryable(error: Exception) -> bool:
        """判断错误是否可重试"""
        if isinstance(error, _RetryableError):
            return True
        # httpx 连接/超时错误
        error_name = type(error).__name__
        return any(
            kw in error_name for kw in ("Timeout", "Connect", "Connection", "Transport")
        )

    @staticmethod
    def _get_retry_delay(attempt: int, error: Exception) -> float:
        """计算重试延迟（指数退避 + Retry-After）"""
        # 429 Retry-After
        if isinstance(error, _RetryableError) and "retry_after=" in str(error):
            try:
                retry_after = float(str(error).split("retry_after=")[1].split(")")[0])
                if retry_after > 0:
                    return retry_after
            except (ValueError, IndexError):
                pass
        # 指数退避：1s → 3s
        delays = [1.0, 3.0]
        return delays[min(attempt, len(delays) - 1)]

    # ─── 缓存键 ───────────────────────────────────────────────

    @staticmethod
    def _cache_key_messages(messages: list[dict], temperature: float) -> str:
        content = json.dumps(messages, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(content.encode()).hexdigest()

    @staticmethod
    def _cache_key_image(
        prompt: str,
        image_base64: str | None,
        temperature: float,
    ) -> str:
        raw = prompt
        if image_base64:
            raw += image_base64[:1024]
        return hashlib.sha256(raw.encode()).hexdigest()


# ─── 内部异常 ────────────────────────────────────────────────


class _RetryableError(Exception):
    """可重试的错误"""
