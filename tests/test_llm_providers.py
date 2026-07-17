"""LLM Provider 体系完整单元测试。

覆盖范围：
  1. inkmind/models/llm.py — 配置模型默认值、自定义值、协议枚举
  2. inkmind/llm/providers/base.py — 数据类、工具函数、Provider key
  3. inkmind/llm/providers/openai_provider.py — OpenAI 兼容 Provider
  4. inkmind/llm/providers/anthropic_provider.py — Anthropic Provider
  5. inkmind/llm/providers/ollama_provider.py — Ollama Provider
  6. inkmind/llm/factory.py — 工厂 + 路由 + 降级
  7. inkmind/llm/client.py — 统一客户端
"""

from __future__ import annotations

import json
import os
from typing import AsyncGenerator, Dict, List, Optional
from uuid import UUID

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from inkmind.llm.client import LLMClient
from inkmind.llm.factory import ModelRouter, ProviderFactory
from inkmind.llm.providers import PROVIDER_REGISTRY
from inkmind.llm.providers.anthropic_provider import (
    ANTHROPIC_API_VERSION,
    AnthropicProvider,
)
from inkmind.llm.providers.base import (
    LLMResponse,
    ProviderStats,
    _http_client_pool,
    _provider_key,
    extract_api_key_from_env,
)
from inkmind.llm.providers.ollama_provider import OllamaProvider
from inkmind.llm.providers.openai_provider import OpenAIProvider
from inkmind.models.llm import (
    AgentModelBinding,
    LLMConfig,
    ModelRouterConfig,
    ProviderConfig,
    ProviderProtocol,
    RetryConfig,
)


# =========================================================================
# 测试辅助函数
# =========================================================================


def _make_asgi_app(response_data: dict, status_code: int = 200):
    """创建一个 HTTP JSON 响应的模拟 ASGI 应用。"""

    async def app(scope, receive, send):
        assert scope["type"] == "http"
        # 消费请求体
        more_body = True
        while more_body:
            event = await receive()
            if event["type"] == "http.request":
                more_body = event.get("more_body", False)

        body = json.dumps(response_data).encode()
        await send(
            {
                "type": "http.response.start",
                "status": status_code,
                "headers": [(b"content-type", b"application/json")],
            }
        )
        await send({"type": "http.response.body", "body": body})

    return app


def _make_stream_asgi_app(sse_chunks: List[dict]):
    """创建一个模拟 SSE 流式响应的 ASGI 应用。"""

    async def app(scope, receive, send):
        assert scope["type"] == "http"
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
        for chunk in sse_chunks:
            line = f"data: {json.dumps(chunk)}\n\n"
            await send(
                {
                    "type": "http.response.body",
                    "body": line.encode(),
                    "more_body": True,
                }
            )
        # 发送结束标记
        await send(
            {
                "type": "http.response.body",
                "body": b"data: [DONE]\n\n",
                "more_body": False,
            }
        )

    return app


@pytest.fixture(autouse=True)
def _clean_http_pool():
    """每个测试前后清理全局 HTTP 客户端池，避免跨测试污染。"""
    for key, client in list(_http_client_pool.items()):
        if not client.is_closed:
            import asyncio

            try:
                asyncio.get_event_loop().run_until_complete(client.aclose())
            except RuntimeError:
                pass
        del _http_client_pool[key]
    yield
    for key, client in list(_http_client_pool.items()):
        if not client.is_closed:
            import asyncio

            try:
                asyncio.get_event_loop().run_until_complete(client.aclose())
            except RuntimeError:
                pass
        del _http_client_pool[key]


def _make_openai_cfg(
    name: str = "deepseek",
    api_key_env: str = "DEEPSEEK_API_KEY",
    models: Optional[List[str]] = None,
) -> ProviderConfig:
    return ProviderConfig(
        name=name,
        protocol=ProviderProtocol.OPENAI,
        base_url="https://api.deepseek.com",
        api_key_env=api_key_env,
        models=models or ["deepseek-v4-pro", "deepseek-v4-flash"],
    )


def _make_anthropic_cfg(
    name: str = "anthropic",
    api_key_env: str = "ANTHROPIC_API_KEY",
    models: Optional[List[str]] = None,
) -> ProviderConfig:
    return ProviderConfig(
        name=name,
        protocol=ProviderProtocol.ANTHROPIC,
        base_url="https://api.anthropic.com",
        api_key_env=api_key_env,
        models=models or ["claude-3-opus-20240229", "claude-3-sonnet-20240229"],
    )


def _make_ollama_cfg(
    name: str = "ollama-local",
    models: Optional[List[str]] = None,
) -> ProviderConfig:
    return ProviderConfig(
        name=name,
        protocol=ProviderProtocol.OLLAMA,
        base_url="http://localhost:11434",
        api_key_env="",
        models=models or ["llama3", "mistral"],
    )


def _mock_client_for_provider(
    provider, app, base_url: str = "https://api.deepseek.com"
):
    """用 ASGITransport mock 替换 provider 内部的 httpx 客户端。"""
    transport = ASGITransport(app=app)
    client = AsyncClient(transport=transport, base_url=base_url)
    provider._client = client
    return client


# =========================================================================
# 1. 配置模型 — inkmind/models/llm.py
# =========================================================================


class TestProviderProtocol:
    def test_enum_values(self):
        assert ProviderProtocol.OPENAI.value == "openai"
        assert ProviderProtocol.ANTHROPIC.value == "anthropic"
        assert ProviderProtocol.OLLAMA.value == "ollama"

    def test_enum_membership(self):
        assert "openai" in {e.value for e in ProviderProtocol}


class TestProviderConfig:
    def test_default_values(self):
        cfg = ProviderConfig(
            name="test", protocol=ProviderProtocol.OPENAI, base_url="http://test.com"
        )
        assert cfg.api_key_env == ""
        assert cfg.models == []
        assert cfg.max_concurrent == 3
        assert cfg.max_keepalive == 10

    def test_custom_values(self):
        cfg = ProviderConfig(
            name="custom",
            protocol=ProviderProtocol.ANTHROPIC,
            base_url="https://custom.com",
            api_key_env="CUSTOM_KEY",
            models=["model-a", "model-b"],
            max_concurrent=5,
            max_keepalive=20,
        )
        assert cfg.name == "custom"
        assert cfg.protocol == ProviderProtocol.ANTHROPIC
        assert cfg.api_key_env == "CUSTOM_KEY"
        assert cfg.models == ["model-a", "model-b"]
        assert cfg.max_concurrent == 5
        assert cfg.max_keepalive == 20

    def test_max_concurrent_min_one(self):
        """验证 ge=1 约束。"""
        with pytest.raises(ValueError):
            ProviderConfig(
                name="bad", protocol=ProviderProtocol.OPENAI, base_url="x", max_concurrent=0
            )


class TestRetryConfig:
    def test_defaults(self):
        cfg = RetryConfig()
        assert cfg.max_retries == 3
        assert cfg.base_delay_s == 2.0
        assert cfg.non_retryable_statuses == [400, 401, 403, 422]

    def test_custom(self):
        cfg = RetryConfig(max_retries=5, base_delay_s=1.5, non_retryable_statuses=[400, 500])
        assert cfg.max_retries == 5
        assert cfg.base_delay_s == 1.5
        assert cfg.non_retryable_statuses == [400, 500]


class TestAgentModelBinding:
    def test_basic(self):
        b = AgentModelBinding(agent_role="planner", primary_model="deepseek-v4-pro")
        assert b.agent_role == "planner"
        assert b.primary_model == "deepseek-v4-pro"
        assert b.fallback_models == []

    def test_with_fallback(self):
        b = AgentModelBinding(
            agent_role="writer",
            primary_model="deepseek-v4-flash",
            fallback_models=["deepseek-v4-lite"],
        )
        assert b.fallback_models == ["deepseek-v4-lite"]


class TestModelRouterConfig:
    def test_default_bindings(self):
        cfg = ModelRouterConfig()
        roles = {b.agent_role: b.primary_model for b in cfg.bindings}
        assert roles["planner"] == "deepseek-v4-pro"
        assert roles["writer"] == "deepseek-v4-flash"
        assert roles["editor"] == "deepseek-v4-flash"
        assert roles["memory-keeper"] == "deepseek-v4-flash"
        assert roles["designer"] == "deepseek-v4-flash"

    def test_planner_fallback(self):
        cfg = ModelRouterConfig()
        planner = [b for b in cfg.bindings if b.agent_role == "planner"][0]
        assert planner.fallback_models == ["deepseek-v4-flash"]

    def test_writer_no_fallback(self):
        cfg = ModelRouterConfig()
        writer = [b for b in cfg.bindings if b.agent_role == "writer"][0]
        assert writer.fallback_models == []

    def test_custom_bindings(self):
        cfg = ModelRouterConfig(
            bindings=[
                AgentModelBinding(
                    agent_role="custom-agent",
                    primary_model="custom-model",
                    fallback_models=["fallback-model"],
                )
            ]
        )
        assert len(cfg.bindings) == 1
        assert cfg.bindings[0].agent_role == "custom-agent"


class TestLLMConfig:
    def test_default_provider(self):
        cfg = LLMConfig()
        assert "deepseek" in cfg.providers
        ds = cfg.providers["deepseek"]
        assert ds.protocol == ProviderProtocol.OPENAI
        assert ds.base_url == "https://api.deepseek.com"
        assert ds.api_key_env == "DEEPSEEK_API_KEY"
        assert "deepseek-v4-pro" in ds.models
        assert cfg.default_model == "deepseek-v4-flash"

    def test_default_router(self):
        cfg = LLMConfig()
        assert isinstance(cfg.router, ModelRouterConfig)
        assert len(cfg.router.bindings) == 5

    def test_default_retry(self):
        cfg = LLMConfig()
        assert cfg.retry.max_retries == 3

    def test_config_id_is_uuid(self):
        cfg = LLMConfig()
        assert isinstance(cfg.config_id, UUID)

    def test_custom_providers(self):
        cfg = LLMConfig(
            providers={
                "custom": ProviderConfig(
                    name="custom",
                    protocol=ProviderProtocol.OPENAI,
                    base_url="https://custom.ai",
                    api_key_env="CUSTOM_KEY",
                    models=["custom-model"],
                )
            }
        )
        assert "custom" in cfg.providers
        assert cfg.providers["custom"].base_url == "https://custom.ai"


# =========================================================================
# 2. 基类 — inkmind/llm/providers/base.py
# =========================================================================


class TestLLMResponse:
    def test_minimal_creation(self):
        resp = LLMResponse(content="hello", model="m1", provider="p1")
        assert resp.content == "hello"
        assert resp.model == "m1"
        assert resp.provider == "p1"
        assert resp.finish_reason == "stop"
        assert resp.usage is None

    def test_full_creation(self):
        resp = LLMResponse(
            content="hello",
            model="m1",
            provider="p1",
            finish_reason="length",
            usage={"total_tokens": 100},
        )
        assert resp.finish_reason == "length"
        assert resp.usage == {"total_tokens": 100}

    def test_with_none_usage(self):
        resp = LLMResponse(content="x", model="m", provider="p", usage=None)
        assert resp.usage is None


class TestProviderStats:
    def test_defaults(self):
        stats = ProviderStats()
        assert stats.total_calls == 0
        assert stats.successful_calls == 0
        assert stats.failed_calls == 0
        assert stats.fallback_used == 0

    def test_increment(self):
        stats = ProviderStats()
        stats.total_calls += 1
        stats.successful_calls += 1
        stats.failed_calls += 1
        stats.fallback_used += 1
        assert stats.total_calls == 1
        assert stats.successful_calls == 1
        assert stats.failed_calls == 1
        assert stats.fallback_used == 1


class TestProviderKey:
    def test_same_key_for_same_config(self):
        cfg1 = _make_openai_cfg()
        cfg2 = _make_openai_cfg()
        assert _provider_key(cfg1) == _provider_key(cfg2)

    def test_different_protocol_different_key(self):
        cfg_openai = _make_openai_cfg()
        cfg_anthropic = ProviderConfig(
            name="test",
            protocol=ProviderProtocol.ANTHROPIC,
            base_url="https://api.deepseek.com",  # 故意相同 base_url
            api_key_env="KEY",
        )
        assert _provider_key(cfg_openai) != _provider_key(cfg_anthropic)

    def test_different_base_url_different_key(self):
        cfg1 = _make_openai_cfg()
        cfg2 = ProviderConfig(
            name="other",
            protocol=ProviderProtocol.OPENAI,
            base_url="https://other.com",
            api_key_env="KEY",
        )
        assert _provider_key(cfg1) != _provider_key(cfg2)

    def test_key_format(self):
        cfg = _make_openai_cfg()
        key = _provider_key(cfg)
        assert key == "openai::https://api.deepseek.com"


class TestExtractApiKeyFromEnv:
    def test_returns_empty_when_no_env_var(self, monkeypatch):
        cfg = _make_openai_cfg(api_key_env="")
        assert extract_api_key_from_env(cfg) == ""

    def test_returns_value_when_set(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-123")
        cfg = _make_openai_cfg()
        assert extract_api_key_from_env(cfg) == "sk-123"

    def test_returns_empty_when_not_set(self, monkeypatch):
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        cfg = _make_openai_cfg()
        assert extract_api_key_from_env(cfg) == ""

    def test_no_key_env_returns_empty(self):
        cfg = _make_ollama_cfg()
        assert extract_api_key_from_env(cfg) == ""


class TestProviderProtocolValidation:
    """测试 Provider 构造时的协议校验。"""

    def test_openai_wrong_protocol(self):
        cfg = _make_openai_cfg()
        cfg.protocol = ProviderProtocol.ANTHROPIC
        with pytest.raises(ValueError, match="OpenAIProvider requires OPENAI protocol"):
            OpenAIProvider(cfg)

    def test_anthropic_wrong_protocol(self):
        cfg = _make_anthropic_cfg()
        cfg.protocol = ProviderProtocol.OPENAI
        with pytest.raises(ValueError, match="AnthropicProvider requires ANTHROPIC protocol"):
            AnthropicProvider(cfg)

    def test_ollama_wrong_protocol(self):
        cfg = _make_ollama_cfg()
        cfg.protocol = ProviderProtocol.OPENAI
        with pytest.raises(ValueError, match="OllamaProvider requires OLLAMA protocol"):
            OllamaProvider(cfg)


class TestResolveApiKey:
    def test_missing_key_raises(self, monkeypatch):
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        provider = OpenAIProvider(_make_openai_cfg())
        with pytest.raises(RuntimeError, match="Environment variable.*DEEPSEEK_API_KEY"):
            provider._resolve_api_key()

    def test_empty_key_env_returns_empty(self, monkeypatch):
        cfg = _make_ollama_cfg()
        provider = OllamaProvider(cfg)
        assert provider._resolve_api_key() == ""


# =========================================================================
# 3. OpenAI Provider — inkmind/llm/providers/openai_provider.py
# =========================================================================


class TestOpenAIProvider:
    def test_build_headers(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
        provider = OpenAIProvider(_make_openai_cfg())
        headers = provider._build_headers()
        assert headers["Content-Type"] == "application/json"
        assert headers["Authorization"] == "Bearer sk-test"

    def test_build_headers_no_key(self):
        cfg = _make_openai_cfg(api_key_env="")
        provider = OpenAIProvider(cfg)
        headers = provider._build_headers()
        assert "Authorization" not in headers
        assert headers["Content-Type"] == "application/json"

    def test_build_messages_with_system_prompt(self):
        provider = OpenAIProvider(_make_openai_cfg())
        messages = provider._build_messages("user query", "system instruction")
        assert len(messages) == 2
        assert messages[0] == {"role": "system", "content": "system instruction"}
        assert messages[1] == {"role": "user", "content": "user query"}

    def test_build_messages_without_system_prompt(self):
        provider = OpenAIProvider(_make_openai_cfg())
        messages = provider._build_messages("user query")
        assert len(messages) == 1
        assert messages[0] == {"role": "user", "content": "user query"}

    @pytest.mark.asyncio
    async def test_chat(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-key")
        provider = OpenAIProvider(_make_openai_cfg())
        app = _make_asgi_app(
            {
                "choices": [
                    {
                        "message": {"content": "hello"},
                        "finish_reason": "stop",
                    }
                ],
                "model": "deepseek-v4-flash",
                "usage": {"total_tokens": 10},
            }
        )
        _mock_client_for_provider(provider, app)

        response = await provider.chat("测试提示词")
        assert response.content == "hello"
        assert response.model == "deepseek-v4-flash"
        assert response.provider == "deepseek"
        assert response.finish_reason == "stop"
        assert response.usage == {"total_tokens": 10}

    @pytest.mark.asyncio
    async def test_chat_with_system_prompt(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-key")
        provider = OpenAIProvider(_make_openai_cfg())
        app = _make_asgi_app(
            {
                "choices": [
                    {
                        "message": {"content": "response with system"},
                        "finish_reason": "stop",
                    }
                ],
                "model": "deepseek-v4-flash",
            }
        )
        _mock_client_for_provider(provider, app)

        response = await provider.chat("user msg", system_prompt="be helpful")
        assert response.content == "response with system"

    @pytest.mark.asyncio
    async def test_chat_stream(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-key")
        provider = OpenAIProvider(_make_openai_cfg())
        chunks = [
            {"choices": [{"delta": {"content": "Hello"}}]},
            {"choices": [{"delta": {"content": " World"}}]},
        ]
        app = _make_stream_asgi_app(chunks)
        _mock_client_for_provider(provider, app)

        collected = []
        async for chunk in provider.chat_stream("test"):
            collected.append(chunk)
        assert collected == ["Hello", " World"]

    @pytest.mark.asyncio
    async def test_chat_stream_empty_content_skipped(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-key")
        provider = OpenAIProvider(_make_openai_cfg())
        chunks = [
            {"choices": [{"delta": {"content": "A"}}]},
            {"choices": [{"delta": {"content": ""}}]},
            {"choices": [{"delta": {"content": "B"}}]},
        ]
        app = _make_stream_asgi_app(chunks)
        _mock_client_for_provider(provider, app)

        collected = []
        async for chunk in provider.chat_stream("test"):
            collected.append(chunk)
        assert collected == ["A", "B"]

    @pytest.mark.asyncio
    async def test_provider_stats_tracked(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-key")
        provider = OpenAIProvider(_make_openai_cfg())
        app = _make_asgi_app(
            {
                "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                "model": "m",
            }
        )
        _mock_client_for_provider(provider, app)

        await provider.chat("test")
        assert provider.stats.total_calls == 1
        assert provider.stats.successful_calls == 1
        assert provider.stats.failed_calls == 0


# =========================================================================
# 4. Anthropic Provider — inkmind/llm/providers/anthropic_provider.py
# =========================================================================


class TestAnthropicProvider:
    def test_build_headers(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        provider = AnthropicProvider(_make_anthropic_cfg())
        headers = provider._build_headers()
        assert headers["Content-Type"] == "application/json"
        assert headers["x-api-key"] == "sk-ant-test"
        assert headers["anthropic-version"] == ANTHROPIC_API_VERSION

    def test_build_messages_only_user(self):
        """Anthropic 的 system prompt 不在 messages 中。"""
        provider = AnthropicProvider(_make_anthropic_cfg())
        messages = provider._build_messages("user query", "system instruction")
        assert len(messages) == 1
        assert messages[0] == {"role": "user", "content": "user query"}

    def test_build_messages_no_system(self):
        provider = AnthropicProvider(_make_anthropic_cfg())
        messages = provider._build_messages("user query")
        assert len(messages) == 1

    @pytest.mark.asyncio
    async def test_chat(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-ant-key")
        provider = AnthropicProvider(_make_anthropic_cfg())
        app = _make_asgi_app(
            {
                "content": [{"type": "text", "text": "hello from claude"}],
                "stop_reason": "end_turn",
                "model": "claude-3-opus-20240229",
                "usage": {"input_tokens": 10, "output_tokens": 20},
            }
        )
        _mock_client_for_provider(
            provider, app, base_url="https://api.anthropic.com"
        )

        response = await provider.chat("测试提示词")
        assert response.content == "hello from claude"
        assert response.model == "claude-3-opus-20240229"
        assert response.finish_reason == "end_turn"
        assert response.usage == {"input_tokens": 10, "output_tokens": 20}

    @pytest.mark.asyncio
    async def test_chat_with_system_prompt(self, monkeypatch):
        """Anthropic 的 system_prompt 通过 kwargs 传入。"""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-ant-key")
        provider = AnthropicProvider(_make_anthropic_cfg())
        app = _make_asgi_app(
            {
                "content": [{"type": "text", "text": "polite response"}],
                "stop_reason": "end_turn",
            }
        )
        _mock_client_for_provider(
            provider, app, base_url="https://api.anthropic.com"
        )

        response = await provider.chat("hello", system_prompt="be polite")
        assert response.content == "polite response"

    @pytest.mark.asyncio
    async def test_chat_stream(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-ant-key")
        provider = AnthropicProvider(_make_anthropic_cfg())
        chunks = [
            {"type": "content_block_delta", "delta": {"text": "Hello"}},
            {"type": "content_block_delta", "delta": {"text": " World"}},
        ]
        app = _make_stream_asgi_app(chunks)
        _mock_client_for_provider(
            provider, app, base_url="https://api.anthropic.com"
        )

        collected = []
        async for chunk in provider.chat_stream("test"):
            collected.append(chunk)
        assert collected == ["Hello", " World"]

    @pytest.mark.asyncio
    async def test_chat_stream_only_content_block_delta(self, monkeypatch):
        """流式响应中只提取 type=content_block_delta 的文本。"""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-ant-key")
        provider = AnthropicProvider(_make_anthropic_cfg())
        chunks = [
            {"type": "ping"},  # 应跳过
            {"type": "content_block_delta", "delta": {"text": "A"}},
            {"type": "content_block_stop"},  # 应跳过
            {"type": "content_block_delta", "delta": {"text": "B"}},
        ]
        app = _make_stream_asgi_app(chunks)
        _mock_client_for_provider(
            provider, app, base_url="https://api.anthropic.com"
        )

        collected = []
        async for chunk in provider.chat_stream("test"):
            collected.append(chunk)
        assert collected == ["A", "B"]


# =========================================================================
# 5. Ollama Provider — inkmind/llm/providers/ollama_provider.py
# =========================================================================


class TestOllamaProvider:
    def test_build_headers_no_authorization(self):
        """Ollama 不需要 Authorization header。"""
        provider = OllamaProvider(_make_ollama_cfg())
        headers = provider._build_headers()
        assert headers == {"Content-Type": "application/json"}
        assert "Authorization" not in headers

    def test_build_messages_with_system_prompt(self):
        provider = OllamaProvider(_make_ollama_cfg())
        messages = provider._build_messages("user query", "system instruction")
        assert len(messages) == 2
        assert messages[0] == {"role": "system", "content": "system instruction"}
        assert messages[1] == {"role": "user", "content": "user query"}

    def test_build_messages_without_system_prompt(self):
        provider = OllamaProvider(_make_ollama_cfg())
        messages = provider._build_messages("user query")
        assert len(messages) == 1
        assert messages[0] == {"role": "user", "content": "user query"}

    @pytest.mark.asyncio
    async def test_chat(self, monkeypatch):
        monkeypatch.setenv("OLLAMA_HOST", "http://localhost:11434")
        provider = OllamaProvider(_make_ollama_cfg())
        app = _make_asgi_app(
            {
                "choices": [
                    {
                        "message": {"content": "hello from ollama"},
                        "finish_reason": "stop",
                    }
                ],
                "model": "llama3",
                "usage": {"total_tokens": 5},
            }
        )
        _mock_client_for_provider(
            provider, app, base_url="http://localhost:11434"
        )

        response = await provider.chat("测试提示词")
        assert response.content == "hello from ollama"
        assert response.model == "llama3"
        assert response.provider == "ollama-local"
        assert response.finish_reason == "stop"
        assert response.usage == {"total_tokens": 5}

    @pytest.mark.asyncio
    async def test_chat_stream(self, monkeypatch):
        monkeypatch.setenv("OLLAMA_HOST", "http://localhost:11434")
        provider = OllamaProvider(_make_ollama_cfg())
        chunks = [
            {"choices": [{"delta": {"content": "Hello"}}]},
            {"choices": [{"delta": {"content": " Ollama"}}]},
        ]
        app = _make_stream_asgi_app(chunks)
        _mock_client_for_provider(
            provider, app, base_url="http://localhost:11434"
        )

        collected = []
        async for chunk in provider.chat_stream("test"):
            collected.append(chunk)
        assert collected == ["Hello", " Ollama"]

    @pytest.mark.asyncio
    async def test_model_selection(self, monkeypatch):
        """指定 model 参数时，使用传入的模型名。"""
        monkeypatch.setenv("OLLAMA_HOST", "http://localhost:11434")
        provider = OllamaProvider(_make_ollama_cfg())
        app = _make_asgi_app(
            {
                "choices": [
                    {
                        "message": {"content": "hello"},
                        "finish_reason": "stop",
                    }
                ],
                "model": "mistral",
            }
        )
        _mock_client_for_provider(
            provider, app, base_url="http://localhost:11434"
        )

        response = await provider.chat("test", model="mistral")
        assert response.model == "mistral"


# =========================================================================
# 6. 工厂 + 路由 — inkmind/llm/factory.py
# =========================================================================


class TestProviderFactory:
    def test_init_with_default_config(self):
        config = LLMConfig()
        factory = ProviderFactory(config)
        assert "deepseek" in factory.list_providers()

    def test_init_with_custom_config(self):
        config = LLMConfig(
            providers={
                "custom-llama": ProviderConfig(
                    name="custom-llama",
                    protocol=ProviderProtocol.OLLAMA,
                    base_url="http://localhost:11434",
                    api_key_env="",
                    models=["llama3"],
                )
            }
        )
        factory = ProviderFactory(config)
        providers = factory.list_providers()
        assert "custom-llama" in providers
        assert isinstance(providers["custom-llama"], OllamaProvider)

    def test_get_provider_existing(self):
        factory = ProviderFactory(LLMConfig())
        provider = factory.get_provider("deepseek")
        assert isinstance(provider, OpenAIProvider)

    def test_get_provider_not_found(self):
        factory = ProviderFactory(LLMConfig())
        with pytest.raises(KeyError, match="Provider 'nonexistent' not found"):
            factory.get_provider("nonexistent")

    def test_get_providers_by_model_found(self):
        factory = ProviderFactory(LLMConfig())
        providers = factory.get_providers_by_model("deepseek-v4-pro")
        assert len(providers) == 1
        assert providers[0].config.name == "deepseek"

    def test_get_providers_by_model_not_found(self):
        factory = ProviderFactory(LLMConfig())
        providers = factory.get_providers_by_model("nonexistent-model")
        assert providers == []

    def test_init_unknown_protocol(self):
        """未知协议应抛 ValueError。
        使用 model_construct 绕过 Pydantic 枚举校验，触发工厂层的验证。
        """
        cfg = ProviderConfig.model_construct(
            name="bad",
            protocol="unknown_protocol",
            base_url="http://x",
            api_key_env="",
            models=[],
        )
        with pytest.raises((ValueError, AttributeError)):
            ProviderFactory(LLMConfig(providers={"bad": cfg}))

    def test_init_with_multiple_providers(self, monkeypatch):
        """多 Provider 初始化。"""
        monkeypatch.setenv("DEEPSEEK_API_KEY", "key1")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "key2")
        config = LLMConfig(
            providers={
                "deepseek": _make_openai_cfg(),
                "anthropic": _make_anthropic_cfg(),
                "ollama": _make_ollama_cfg(),
            }
        )
        factory = ProviderFactory(config)
        names = set(factory.list_providers().keys())
        assert names == {"deepseek", "anthropic", "ollama"}
        assert isinstance(factory.get_provider("anthropic"), AnthropicProvider)


class TestModelRouter:
    def test_resolve_model_found(self):
        config = LLMConfig()
        factory = ProviderFactory(config)
        router = ModelRouter(factory, config)
        assert router.resolve_model("planner") == "deepseek-v4-pro"
        assert router.resolve_model("writer") == "deepseek-v4-flash"

    def test_resolve_model_not_found_returns_default(self):
        config = LLMConfig()
        factory = ProviderFactory(config)
        router = ModelRouter(factory, config)
        assert router.resolve_model("unknown-role") == "deepseek-v4-flash"

    def test_resolve_fallback_models_found(self):
        config = LLMConfig()
        factory = ProviderFactory(config)
        router = ModelRouter(factory, config)
        assert router.resolve_fallback_models("planner") == ["deepseek-v4-flash"]

    def test_resolve_fallback_models_empty(self):
        config = LLMConfig()
        factory = ProviderFactory(config)
        router = ModelRouter(factory, config)
        assert router.resolve_fallback_models("writer") == []

    def test_resolve_fallback_models_not_found(self):
        config = LLMConfig()
        factory = ProviderFactory(config)
        router = ModelRouter(factory, config)
        assert router.resolve_fallback_models("unknown") == []

    def test_get_provider_for_model_found(self):
        config = LLMConfig()
        factory = ProviderFactory(config)
        router = ModelRouter(factory, config)
        provider = router.get_provider_for_model("deepseek-v4-pro")
        assert isinstance(provider, OpenAIProvider)

    def test_get_provider_for_model_not_found(self):
        config = LLMConfig()
        factory = ProviderFactory(config)
        router = ModelRouter(factory, config)
        with pytest.raises(RuntimeError, match="No provider supports model 'nonexistent'"):
            router.get_provider_for_model("nonexistent")

    @pytest.mark.asyncio
    async def test_chat_with_fallback_primary_succeeds(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-key")
        config = LLMConfig()
        factory = ProviderFactory(config)
        router = ModelRouter(factory, config)

        # Mock primary model provider
        provider = factory.get_provider("deepseek")
        app = _make_asgi_app(
            {
                "choices": [{"message": {"content": "plan result"}, "finish_reason": "stop"}],
                "model": "deepseek-v4-pro",
            }
        )
        _mock_client_for_provider(provider, app)

        response = await router.chat_with_fallback("planner", "写大纲")
        assert response.content == "plan result"

    @pytest.mark.asyncio
    async def test_chat_with_fallback_all_fail(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-key")
        config = LLMConfig()
        factory = ProviderFactory(config)
        router = ModelRouter(factory, config)

        # Mock provider to return 422 (non-retryable)
        provider = factory.get_provider("deepseek")
        app = _make_asgi_app({"error": "invalid"}, status_code=422)
        _mock_client_for_provider(provider, app)

        with pytest.raises(RuntimeError, match="All models failed for agent 'planner'"):
            await router.chat_with_fallback("planner", "写大纲")


# =========================================================================
# 7. 统一客户端 — inkmind/llm/client.py
# =========================================================================


class TestLLMClient:
    def test_init_with_default_config(self):
        client = LLMClient()
        assert isinstance(client.config, LLMConfig)
        assert isinstance(client.factory, ProviderFactory)
        assert isinstance(client.router, ModelRouter)

    def test_init_with_custom_config(self):
        config = LLMConfig()
        client = LLMClient(config)
        assert client.config is config

    def test_get_provider(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-key")
        client = LLMClient()
        provider = client.get_provider("deepseek")
        assert isinstance(provider, OpenAIProvider)

    def test_get_provider_not_found(self):
        client = LLMClient()
        with pytest.raises(KeyError):
            client.get_provider("nonexistent")

    def test_cancel_all(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-key")
        client = LLMClient()
        provider = client.get_provider("deepseek")
        assert not provider._cancel_event.is_set()

        client.cancel_all()
        assert provider._cancel_event.is_set()

    def test_cancel_all_reset(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-key")
        client = LLMClient()
        client.cancel_all()
        client.reset_cancel()
        provider = client.get_provider("deepseek")
        assert not provider._cancel_event.is_set()

    def test_get_stats(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-key")
        client = LLMClient()
        stats = client.get_stats()
        assert "deepseek" in stats
        assert stats["deepseek"].total_calls == 0
        assert stats["deepseek"].successful_calls == 0

    @pytest.mark.asyncio
    async def test_chat_delegates_to_router(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-key")
        client = LLMClient()

        # Mock the underlying provider
        provider = client.get_provider("deepseek")
        app = _make_asgi_app(
            {
                "choices": [{"message": {"content": "hello"}, "finish_reason": "stop"}],
                "model": "deepseek-v4-flash",
            }
        )
        _mock_client_for_provider(provider, app)

        response = await client.chat("planner", "写大纲")
        assert response.content == "hello"

    @pytest.mark.asyncio
    async def test_chat_stream_delegates_to_router(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-key")
        client = LLMClient()

        provider = client.get_provider("deepseek")
        chunks = [
            {"choices": [{"delta": {"content": "Hello"}}]},
            {"choices": [{"delta": {"content": " World"}}]},
        ]
        app = _make_stream_asgi_app(chunks)
        _mock_client_for_provider(provider, app)

        collected = []
        async for chunk in client.chat_stream("planner", "写大纲"):
            collected.append(chunk)
        assert collected == ["Hello", " World"]

    @pytest.mark.asyncio
    async def test_shutdown_cleans_up(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-key")
        client = LLMClient()
        await client.shutdown()
        # 验证所有 provider 都被 cancel 了
        for p in client.factory.list_providers().values():
            assert p._cancel_event.is_set()


# =========================================================================
# 边界与异常情况
# =========================================================================


class TestEdgeCases:
    def test_provider_registry_contains_all_protocols(self):
        assert "openai" in PROVIDER_REGISTRY
        assert "anthropic" in PROVIDER_REGISTRY
        assert "ollama" in PROVIDER_REGISTRY

    def test_llm_config_default_provider_has_correct_models(self):
        config = LLMConfig()
        ds = config.providers["deepseek"]
        assert "deepseek-v4-pro" in ds.models
        assert "deepseek-v4-flash" in ds.models
        assert len(ds.models) == 2

    @pytest.mark.asyncio
    async def test_openai_chat_non_retryable_status_raises(self, monkeypatch):
        """非重试状态码（400）应直接抛出异常。"""
        monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-key")
        provider = OpenAIProvider(_make_openai_cfg())
        app = _make_asgi_app({"error": "bad request"}, status_code=400)
        _mock_client_for_provider(provider, app)

        with pytest.raises(httpx.HTTPStatusError):
            await provider.chat("test")

    @pytest.mark.asyncio
    async def test_openai_chat_retryable_status_retries_then_fails(self, monkeypatch):
        """可重试状态码（500）应重试后最终失败。"""
        monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-key")
        cfg = _make_openai_cfg()
        provider = OpenAIProvider(
            cfg,
            retry=RetryConfig(max_retries=1, base_delay_s=0.01, non_retryable_statuses=[400]),
        )
        app = _make_asgi_app({"error": "server error"}, status_code=500)
        _mock_client_for_provider(provider, app)

        with pytest.raises(httpx.HTTPStatusError):
            await provider.chat("test")
        # 重试+初始 = 2 次调用
        assert provider.stats.total_calls == 2
        assert provider.stats.failed_calls == 2

    @pytest.mark.asyncio
    async def test_cancel_interrupts_chat(self, monkeypatch):
        """cancel 后应立即中断请求。"""
        monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-key")
        provider = OpenAIProvider(_make_openai_cfg())
        provider._cancel_event.set()  # 预置中断

        app = _make_asgi_app({"choices": [{"message": {"content": "x"}}], "model": "m"})
        _mock_client_for_provider(provider, app)

        with pytest.raises(RuntimeError, match="Request cancelled by caller"):
            await provider.chat("test")

    @pytest.mark.asyncio
    async def test_cancel_reset_works(self, monkeypatch):
        """reset_cancel 后请求可以正常执行。"""
        monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-key")
        provider = OpenAIProvider(_make_openai_cfg())
        provider._cancel_event.set()
        provider.reset_cancel()
        assert not provider._cancel_event.is_set()

        app = _make_asgi_app(
            {
                "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                "model": "m",
            }
        )
        _mock_client_for_provider(provider, app)

        response = await provider.chat("test")
        assert response.content == "ok"

    def test_empty_fallback_list_returns_empty_list(self):
        """resolve_fallback_models 对空降级列表应返回空列表。"""
        config = LLMConfig()
        factory = ProviderFactory(config)
        router = ModelRouter(factory, config)
        # writer 的 fallback_models 为空
        assert router.resolve_fallback_models("writer") == []

    def test_model_router_available_models(self):
        config = LLMConfig()
        factory = ProviderFactory(config)
        router = ModelRouter(factory, config)
        models = router._available_models()
        assert "deepseek-v4-pro" in models
        assert "deepseek-v4-flash" in models

    def test_provider_stats_dataclass_immutable_pattern(self):
        """验证 ProviderStats 字段按预期工作。"""
        stats = ProviderStats(total_calls=5, successful_calls=3, failed_calls=2)
        assert stats.total_calls == 5
        assert stats.successful_calls == 3
        assert stats.failed_calls == 2
        assert stats.fallback_used == 0


# =========================================================================
# 超时处理与错误诊断（工单 06 真实 API E2E 发现的缺陷）
# =========================================================================


class TestTimeoutHandling:
    """真实 DeepSeek E2E 发现：httpx 默认 5s 超时导致长文生成必失败。

    设计约定（CLAUDE.md @provider）：默认 3 次重试，固定 2s 间隔，
    无超时可中断 —— 即客户端不设超时，取消通过 cancel() 完成。
    """

    @pytest.mark.asyncio
    async def test_pooled_client_disables_timeout(self):
        """连接池创建的客户端应禁用 httpx 默认超时（无超时可中断）。"""
        from inkmind.llm.providers.base import _get_or_create_http_client

        client = await _get_or_create_http_client(_make_openai_cfg())
        assert client.timeout.read is None
        assert client.timeout.connect is None

    @pytest.mark.asyncio
    async def test_chat_timeout_is_retryable(self, monkeypatch):
        """ReadTimeout 应触发重试（与 ConnectError 同策略），第二次成功。"""
        monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-key")
        provider = OpenAIProvider(
            _make_openai_cfg(),
            retry=RetryConfig(max_retries=2, base_delay_s=0.01),
        )
        calls = {"n": 0}

        async def flaky(client, messages, model, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise httpx.ReadTimeout("")
            return LLMResponse(content="ok", model=model, provider="deepseek")

        provider._do_chat = flaky  # type: ignore[assignment]
        resp = await provider.chat("test")
        assert resp.content == "ok"
        assert calls["n"] == 2
        assert provider.stats.failed_calls == 1
        assert provider.stats.successful_calls == 1

    @pytest.mark.asyncio
    async def test_chat_timeout_exhausts_retries(self, monkeypatch):
        """持续超时应在 max_retries+1 次尝试后抛出，并计入失败统计。"""
        monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-key")
        provider = OpenAIProvider(
            _make_openai_cfg(),
            retry=RetryConfig(max_retries=2, base_delay_s=0.01),
        )

        async def always_timeout(client, messages, model, **kwargs):
            raise httpx.ReadTimeout("")

        provider._do_chat = always_timeout  # type: ignore[assignment]
        with pytest.raises(httpx.ReadTimeout):
            await provider.chat("test")
        assert provider.stats.total_calls == 3
        assert provider.stats.failed_calls == 3

    @pytest.mark.asyncio
    async def test_chat_stream_timeout_is_retryable(self, monkeypatch):
        """流式调用同样应重试超时。"""
        monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-key")
        provider = OpenAIProvider(
            _make_openai_cfg(),
            retry=RetryConfig(max_retries=1, base_delay_s=0.01),
        )
        calls = {"n": 0}

        async def flaky_stream(client, messages, model, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise httpx.ReadTimeout("")
            yield "ok"

        provider._do_chat_stream = flaky_stream  # type: ignore[assignment]
        chunks = [c async for c in provider.chat_stream("test")]
        assert chunks == ["ok"]
        assert calls["n"] == 2

    @pytest.mark.asyncio
    async def test_fallback_error_message_includes_exception_type(self, monkeypatch):
        """所有模型失败时，错误信息必须包含异常类型名。

        str(ReadTimeout('')) 为空串，仅拼接 str(e) 会丢失全部诊断信息。
        """
        monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-key")
        config = LLMConfig()
        factory = ProviderFactory(config)
        router = ModelRouter(factory, config)
        provider = factory.get_provider("deepseek")
        provider.retry = RetryConfig(max_retries=0)

        async def always_timeout(client, messages, model, **kwargs):
            raise httpx.ReadTimeout("")

        provider._do_chat = always_timeout  # type: ignore[assignment]
        with pytest.raises(RuntimeError) as exc_info:
            await router.chat_with_fallback("writer", "test")
        assert "ReadTimeout" in str(exc_info.value)
