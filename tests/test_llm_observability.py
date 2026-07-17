"""ADR-0010 观测层测试。

覆盖范围：
  10-A  每次调用产生不可变 ProviderStats 快照（frozen dataclass）
  10-C  LLMClient.record_stats / aggregate_stats / reset_stats
  10-E  RateLimiter + ProviderConfig.max_calls_per_minute
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from typing import List, Optional

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from inkmind.llm.client import LLMClient
from inkmind.llm.factory import ModelRouter, ProviderFactory
from inkmind.llm.providers.base import (
    LLMResponse,
    ProviderStats,
    ProviderStatsAccumulator,
    _http_client_pool,
)
from inkmind.llm.providers.openai_provider import OpenAIProvider
from inkmind.llm.rate_limiter import RateLimiter
from inkmind.models.llm import (
    LLMConfig,
    ProviderConfig,
    ProviderProtocol,
    RetryConfig,
)


# =========================================================================
# 测试辅助
# =========================================================================


@pytest.fixture(autouse=True)
def _clean_http_pool():
    """每个测试前后清理全局 HTTP 客户端池，避免跨测试污染。"""
    import asyncio as _asyncio

    def _drain():
        for key, client in list(_http_client_pool.items()):
            if not client.is_closed:
                try:
                    _asyncio.get_event_loop().run_until_complete(client.aclose())
                except RuntimeError:
                    pass
            del _http_client_pool[key]

    _drain()
    yield
    _drain()


def _make_cfg(
    name: str = "deepseek",
    models: Optional[List[str]] = None,
    max_calls_per_minute: int = 0,
) -> ProviderConfig:
    return ProviderConfig(
        name=name,
        protocol=ProviderProtocol.OPENAI,
        base_url="https://api.deepseek.com",
        api_key_env="DEEPSEEK_API_KEY",
        models=models or ["deepseek-v4-pro", "deepseek-v4-flash"],
        max_calls_per_minute=max_calls_per_minute,
    )


def _json_app(payload: dict, status_code: int = 200):
    """返回固定 JSON 响应的 ASGI 应用。"""

    async def app(scope, receive, send):
        more_body = True
        while more_body:
            event = await receive()
            if event["type"] == "http.request":
                more_body = event.get("more_body", False)
        await send(
            {
                "type": "http.response.start",
                "status": status_code,
                "headers": [(b"content-type", b"application/json")],
            }
        )
        await send(
            {"type": "http.response.body", "body": json.dumps(payload).encode()}
        )

    return app


def _stream_app(chunks: List[dict]):
    """模拟 SSE 流式响应的 ASGI 应用。"""

    async def app(scope, receive, send):
        more_body = True
        while more_body:
            event = await receive()
            if event["type"] == "http.request":
                more_body = event.get("more_body", False)
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"text/event-stream")],
            }
        )
        for chunk in chunks:
            await send(
                {
                    "type": "http.response.body",
                    "body": f"data: {json.dumps(chunk)}\n\n".encode(),
                    "more_body": True,
                }
            )
        await send(
            {
                "type": "http.response.body",
                "body": b"data: [DONE]\n\n",
                "more_body": False,
            }
        )

    return app


def _mock_client(provider, app, base_url: str = "https://api.deepseek.com"):
    provider._client = AsyncClient(transport=ASGITransport(app=app), base_url=base_url)
    return provider._client


def _make_snapshot(
    provider_name: str = "deepseek",
    model_name: str = "deepseek-v4-flash",
    latency_ms: float = 100.0,
    prompt_tokens: int = 10,
    completion_tokens: int = 5,
    estimated_cost: float = 0.01,
    success: bool = True,
    error_type: Optional[str] = None,
    degraded: bool = False,
    retry_count: int = 0,
) -> ProviderStats:
    return ProviderStats(
        provider_name=provider_name,
        model_name=model_name,
        latency_ms=latency_ms,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=prompt_tokens + completion_tokens,
        estimated_cost=estimated_cost,
        success=success,
        error_type=error_type,
        degraded=degraded,
        retry_count=retry_count,
        timestamp=datetime.now(timezone.utc),
    )


# =========================================================================
# 10-A — 不可变 Stats 快照
# =========================================================================


class TestFrozenProviderStats:
    """ProviderStats 是每次调用一份的不可变快照（ADR-0010 §10-A）。"""

    def test_fields(self):
        ts = datetime.now(timezone.utc)
        s = ProviderStats(
            provider_name="deepseek",
            model_name="deepseek-v4-flash",
            latency_ms=123.4,
            prompt_tokens=10,
            completion_tokens=20,
            total_tokens=30,
            estimated_cost=0.005,
            success=True,
            error_type=None,
            degraded=False,
            retry_count=0,
            timestamp=ts,
        )
        assert s.provider_name == "deepseek"
        assert s.model_name == "deepseek-v4-flash"
        assert s.latency_ms == 123.4
        assert s.prompt_tokens == 10
        assert s.completion_tokens == 20
        assert s.total_tokens == 30
        assert s.estimated_cost == 0.005
        assert s.success is True
        assert s.error_type is None
        assert s.degraded is False
        assert s.retry_count == 0
        assert s.timestamp is ts

    def test_immutable(self):
        s = _make_snapshot()
        with pytest.raises(FrozenInstanceError):
            s.success = False  # type: ignore[misc]
        with pytest.raises(FrozenInstanceError):
            s.latency_ms = 1.0  # type: ignore[misc]

    def test_accumulator_keeps_mutable_totals(self):
        """原可变累计器保留为 ProviderStatsAccumulator（get_stats 兼容）。"""
        acc = ProviderStatsAccumulator()
        acc.total_calls += 1
        acc.successful_calls += 1
        acc.fallback_used += 1
        assert acc.total_calls == 1
        assert acc.successful_calls == 1
        assert acc.fallback_used == 1


class TestCallInstrumentation:
    """BaseProvider 埋点：每次 chat/chat_stream 产生一份快照。"""

    @pytest.mark.asyncio
    async def test_chat_success_snapshot(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-key")
        provider = OpenAIProvider(_make_cfg())
        app = _json_app(
            {
                "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                "model": "deepseek-v4-flash",
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 20,
                    "total_tokens": 30,
                },
            }
        )
        _mock_client(provider, app)

        await provider.chat("测试")

        assert len(provider.stats_history) == 1
        snap = provider.stats_history[0]
        assert snap.provider_name == "deepseek"
        assert snap.model_name == "deepseek-v4-pro"  # 未指定时取 config.models[0]
        assert snap.success is True
        assert snap.error_type is None
        assert snap.prompt_tokens == 10
        assert snap.completion_tokens == 20
        assert snap.total_tokens == 30
        assert snap.latency_ms >= 0.0
        assert snap.retry_count == 0
        assert snap.degraded is False
        assert isinstance(snap.timestamp, datetime)

    @pytest.mark.asyncio
    async def test_chat_failure_snapshot_error_type_rate_limit(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-key")
        provider = OpenAIProvider(
            _make_cfg(), retry=RetryConfig(max_retries=0)
        )
        _mock_client(provider, _json_app({"error": "slow down"}, status_code=429))

        with pytest.raises(httpx.HTTPStatusError):
            await provider.chat("测试")

        assert len(provider.stats_history) == 1
        snap = provider.stats_history[0]
        assert snap.success is False
        assert snap.error_type == "rate_limit"

    @pytest.mark.asyncio
    async def test_chat_failure_snapshot_error_type_auth(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-key")
        provider = OpenAIProvider(
            _make_cfg(), retry=RetryConfig(max_retries=0)
        )
        _mock_client(provider, _json_app({"error": "bad key"}, status_code=401))

        with pytest.raises(httpx.HTTPStatusError):
            await provider.chat("测试")

        assert provider.stats_history[0].error_type == "auth"

    @pytest.mark.asyncio
    async def test_chat_failure_snapshot_error_type_timeout(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-key")
        provider = OpenAIProvider(
            _make_cfg(), retry=RetryConfig(max_retries=0)
        )

        async def always_timeout(client, messages, model, **kwargs):
            raise httpx.ReadTimeout("")

        provider._do_chat = always_timeout  # type: ignore[assignment]
        with pytest.raises(httpx.ReadTimeout):
            await provider.chat("测试")

        snap = provider.stats_history[0]
        assert snap.success is False
        assert snap.error_type == "timeout"

    @pytest.mark.asyncio
    async def test_chat_failure_snapshot_error_type_unknown(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-key")
        provider = OpenAIProvider(
            _make_cfg(), retry=RetryConfig(max_retries=0)
        )
        _mock_client(provider, _json_app({"error": "boom"}, status_code=500))

        with pytest.raises(httpx.HTTPStatusError):
            await provider.chat("测试")

        assert provider.stats_history[0].error_type == "unknown"

    @pytest.mark.asyncio
    async def test_retry_count_in_snapshot(self, monkeypatch):
        """重试后成功：一份快照，retry_count 记录实际重试次数。"""
        monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-key")
        provider = OpenAIProvider(
            _make_cfg(),
            retry=RetryConfig(max_retries=2, base_delay_s=0.01),
        )
        calls = {"n": 0}

        async def flaky(client, messages, model, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise httpx.ReadTimeout("")
            return LLMResponse(content="ok", model=model, provider="deepseek")

        provider._do_chat = flaky  # type: ignore[assignment]
        resp = await provider.chat("测试")

        assert resp.content == "ok"
        assert len(provider.stats_history) == 1
        snap = provider.stats_history[0]
        assert snap.success is True
        assert snap.retry_count == 1

    @pytest.mark.asyncio
    async def test_chat_stream_success_snapshot(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-key")
        provider = OpenAIProvider(_make_cfg())
        app = _stream_app([{"choices": [{"delta": {"content": "Hi"}}]}])
        _mock_client(provider, app)

        chunks = [c async for c in provider.chat_stream("测试")]
        assert chunks == ["Hi"]
        assert len(provider.stats_history) == 1
        snap = provider.stats_history[0]
        assert snap.success is True
        assert snap.error_type is None

    @pytest.mark.asyncio
    async def test_chat_stream_failure_snapshot(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-key")
        provider = OpenAIProvider(
            _make_cfg(), retry=RetryConfig(max_retries=0)
        )
        _mock_client(provider, _json_app({"error": "boom"}, status_code=500))

        with pytest.raises(httpx.HTTPStatusError):
            _ = [c async for c in provider.chat_stream("测试")]

        assert len(provider.stats_history) == 1
        snap = provider.stats_history[0]
        assert snap.success is False
        assert snap.error_type == "unknown"

    @pytest.mark.asyncio
    async def test_degraded_flag_set_by_router(self, monkeypatch):
        """降级链：主模型失败 → 备用模型快照 degraded=True。"""
        monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-key")

        async def app(scope, receive, send):
            body = b""
            while True:
                event = await receive()
                if event["type"] == "http.request":
                    body += event.get("body", b"")
                    if not event.get("more_body"):
                        break
            model = json.loads(body.decode())["model"]
            if model == "deepseek-v4-pro":
                status, payload = 422, {"error": "bad"}
            else:
                status, payload = 200, {
                    "choices": [
                        {"message": {"content": "ok"}, "finish_reason": "stop"}
                    ],
                    "model": model,
                }
            await send(
                {
                    "type": "http.response.start",
                    "status": status,
                    "headers": [(b"content-type", b"application/json")],
                }
            )
            await send(
                {
                    "type": "http.response.body",
                    "body": json.dumps(payload).encode(),
                }
            )

        config = LLMConfig()
        factory = ProviderFactory(config)
        router = ModelRouter(factory, config)
        provider = factory.get_provider("deepseek")
        _mock_client(provider, app)

        resp = await router.chat_with_fallback("planner", "写大纲")
        assert resp.content == "ok"

        hist = provider.stats_history
        assert len(hist) == 2
        assert hist[0].success is False
        assert hist[0].degraded is False
        assert hist[1].success is True
        assert hist[1].degraded is True


# =========================================================================
# 10-C — LLMClient Stats 聚合
# =========================================================================


class TestLLMClientStatsAggregation:
    """LLMClient.record_stats / aggregate_stats / reset_stats（ADR-0010 §10-C）。"""

    def test_record_and_aggregate(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-key")
        client = LLMClient()
        client.record_stats(_make_snapshot(latency_ms=100.0))
        client.record_stats(_make_snapshot(latency_ms=300.0, degraded=True))
        client.record_stats(
            _make_snapshot(
                provider_name="anthropic",
                latency_ms=200.0,
                success=False,
                error_type="timeout",
            )
        )

        agg = client.aggregate_stats()
        assert agg["total_calls"] == 3
        assert agg["total_tokens"] == 45
        assert agg["total_cost"] == pytest.approx(0.03)
        assert agg["avg_latency_ms"] == pytest.approx(200.0)
        assert agg["success_rate"] == pytest.approx(2 / 3)
        assert agg["degradation_rate"] == pytest.approx(1 / 3)
        breakdown = agg["provider_breakdown"]
        assert set(breakdown.keys()) == {"deepseek", "anthropic"}
        assert breakdown["deepseek"]["calls"] == 2
        assert breakdown["deepseek"]["total_tokens"] == 30
        assert breakdown["deepseek"]["success_rate"] == pytest.approx(1.0)
        assert breakdown["anthropic"]["calls"] == 1
        assert breakdown["anthropic"]["success_rate"] == pytest.approx(0.0)

    def test_aggregate_empty_history(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-key")
        client = LLMClient()
        agg = client.aggregate_stats()
        assert agg["total_calls"] == 0
        assert agg["total_tokens"] == 0
        assert agg["total_cost"] == 0.0
        assert agg["avg_latency_ms"] == 0.0
        assert agg["success_rate"] == 0.0
        assert agg["degradation_rate"] == 0.0
        assert agg["provider_breakdown"] == {}

    def test_reset_stats(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-key")
        client = LLMClient()
        provider = client.get_provider("deepseek")
        provider.stats_history.append(_make_snapshot())
        client.record_stats(_make_snapshot())

        client.reset_stats()

        assert client.aggregate_stats()["total_calls"] == 0
        assert provider.stats_history == []

    @pytest.mark.asyncio
    async def test_chat_auto_records_to_client(self, monkeypatch):
        """Provider 埋点自动汇入 LLMClient 历史（sink 接线）。"""
        monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-key")
        client = LLMClient()
        provider = client.get_provider("deepseek")
        app = _json_app(
            {
                "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                "model": "deepseek-v4-flash",
                "usage": {"prompt_tokens": 3, "completion_tokens": 4},
            }
        )
        _mock_client(provider, app)

        resp = await client.chat("writer", "写点什么")
        assert resp.content == "ok"

        agg = client.aggregate_stats()
        assert agg["total_calls"] == 1
        assert agg["total_tokens"] == 7
        assert agg["success_rate"] == pytest.approx(1.0)
        assert agg["provider_breakdown"]["deepseek"]["calls"] == 1


# =========================================================================
# 10-E — RateLimiter
# =========================================================================


class TestRateLimiter:
    """滑动窗口速率限制器（ADR-0010 §10-E）。"""

    def test_invalid_max_calls(self):
        with pytest.raises(ValueError):
            RateLimiter(0)

    @pytest.mark.asyncio
    async def test_within_limit_no_wait(self):
        limiter = RateLimiter(max_calls=2, window_seconds=60.0)
        w1 = await limiter.acquire()
        w2 = await limiter.acquire()
        assert w1 == 0.0
        assert w2 == 0.0

    @pytest.mark.asyncio
    async def test_throttles_beyond_limit(self):
        limiter = RateLimiter(max_calls=1, window_seconds=0.3)
        assert await limiter.acquire() == 0.0
        start = time.monotonic()
        waited = await limiter.acquire()
        elapsed = time.monotonic() - start
        assert waited >= 0.2
        assert elapsed >= 0.2

    @pytest.mark.asyncio
    async def test_window_slides(self):
        """窗口滑过后不再等待。"""
        limiter = RateLimiter(max_calls=1, window_seconds=0.1)
        await limiter.acquire()
        await asyncio.sleep(0.15)
        assert await limiter.acquire() == 0.0

    @pytest.mark.asyncio
    async def test_concurrent_acquires_serialized(self):
        """并发 acquire 不超发：窗口内最多 max_calls 个立即返回。"""
        limiter = RateLimiter(max_calls=2, window_seconds=10.0)
        results = await asyncio.gather(*[limiter.acquire() for _ in range(2)])
        assert all(w == 0.0 for w in results)


class TestRateLimiterWiring:
    """ProviderConfig.max_calls_per_minute → BaseProvider 持有 RateLimiter。"""

    def test_default_unlimited(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-key")
        provider = OpenAIProvider(_make_cfg())
        assert provider._rate_limiter is None

    def test_config_creates_limiter(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-key")
        provider = OpenAIProvider(_make_cfg(max_calls_per_minute=120))
        assert provider._rate_limiter is not None
        assert provider._rate_limiter._max_calls == 120
        assert provider._rate_limiter._window == 60.0

    @pytest.mark.asyncio
    async def test_chat_acquires_per_attempt(self, monkeypatch):
        """每次 HTTP 尝试（含重试）都先过速率限制器。"""
        monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-key")
        provider = OpenAIProvider(
            _make_cfg(max_calls_per_minute=600),
            retry=RetryConfig(max_retries=1, base_delay_s=0.01),
        )
        calls = {"n": 0}

        async def flaky(client, messages, model, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise httpx.ReadTimeout("")
            return LLMResponse(content="ok", model=model, provider="deepseek")

        provider._do_chat = flaky  # type: ignore[assignment]

        acquires = {"n": 0}
        orig_acquire = provider._rate_limiter.acquire

        async def spy_acquire():
            acquires["n"] += 1
            return await orig_acquire()

        provider._rate_limiter.acquire = spy_acquire  # type: ignore[method-assign]

        resp = await provider.chat("测试")
        assert resp.content == "ok"
        assert acquires["n"] == 2
