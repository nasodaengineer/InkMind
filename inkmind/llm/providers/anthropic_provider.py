"""Anthropic Provider — 支持 Claude 系列模型。"""

from __future__ import annotations

from typing import AsyncGenerator, Dict, List, Optional

import httpx

from inkmind.models.llm import ProviderConfig, ProviderProtocol, RetryConfig
from inkmind.llm.providers.base import BaseProvider, LLMResponse


ANTHROPIC_API_VERSION = "2023-06-01"


class AnthropicProvider(BaseProvider):
    """Anthropic 格式的 Provider（Claude 系列模型）。"""

    def __init__(self, config: ProviderConfig, retry: Optional[RetryConfig] = None) -> None:
        if config.protocol != ProviderProtocol.ANTHROPIC:
            raise ValueError(f"AnthropicProvider requires ANTHROPIC protocol, got {config.protocol}")
        super().__init__(config, retry)

    def _build_headers(self) -> Dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "x-api-key": self._resolve_api_key(),
            "anthropic-version": ANTHROPIC_API_VERSION,
        }
        return headers

    def _build_messages(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
    ) -> List[Dict[str, str]]:
        # Anthropic 把 system prompt 放在顶层参数，不在 messages 里
        # 这里返回 user/assistant 消息列表，system 用字段存入 kwargs
        return [{"role": "user", "content": prompt}]

    async def _do_chat(
        self,
        client: httpx.AsyncClient,
        messages: List[Dict[str, str]],
        model: str,
        **kwargs,
    ) -> LLMResponse:
        payload: Dict = {
            "model": model,
            "messages": messages,
            "max_tokens": kwargs.get("max_tokens", 8192),
        }

        # Anthropic 的 system prompt 放在顶层
        system_prompt = kwargs.get("system_prompt")
        if system_prompt:
            payload["system"] = system_prompt

        # 可选参数
        if "temperature" in kwargs:
            payload["temperature"] = kwargs["temperature"]
        if "top_p" in kwargs:
            payload["top_p"] = kwargs["top_p"]
        if "stop_sequences" in kwargs:
            payload["stop_sequences"] = kwargs["stop_sequences"]

        response = await client.post(
            "/v1/messages",
            headers=self._build_headers(),
            json=payload,
        )
        response.raise_for_status()
        data = response.json()

        content_blocks = data.get("content", [])
        content = "".join(
            block.get("text", "") for block in content_blocks if block.get("type") == "text"
        )
        finish_reason = data.get("stop_reason", "end_turn")

        return LLMResponse(
            content=content,
            model=data.get("model", model),
            provider=self.config.name,
            finish_reason=finish_reason,
            usage=data.get("usage"),
        )

    async def _do_chat_stream(
        self,
        client: httpx.AsyncClient,
        messages: List[Dict[str, str]],
        model: str,
        **kwargs,
    ) -> AsyncGenerator[str, None]:
        payload: Dict = {
            "model": model,
            "messages": messages,
            "max_tokens": kwargs.get("max_tokens", 8192),
            "stream": True,
        }

        system_prompt = kwargs.get("system_prompt")
        if system_prompt:
            payload["system"] = system_prompt
        if "temperature" in kwargs:
            payload["temperature"] = kwargs["temperature"]

        async with client.stream(
            "POST",
            "/v1/messages",
            headers=self._build_headers(),
            json=payload,
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue
                json_str = line[6:].strip()
                if json_str == "[DONE]":
                    break
                import json
                chunk = json.loads(json_str)
                if chunk.get("type") == "content_block_delta":
                    delta = chunk.get("delta", {})
                    text = delta.get("text", "")
                    if text:
                        yield text

    # ── 覆写 _build_messages + 重载 chat 以传递 system_prompt ──

    async def chat(
        self,
        prompt: str,
        model: Optional[str] = None,
        system_prompt: Optional[str] = None,
        **kwargs,
    ) -> LLMResponse:
        """覆写：将 system_prompt 传入 kwargs 以保证 Anthropic 的顶层参数。"""
        if system_prompt:
            kwargs["system_prompt"] = system_prompt
        return await super().chat(prompt, model, **kwargs)

    async def chat_stream(
        self,
        prompt: str,
        model: Optional[str] = None,
        system_prompt: Optional[str] = None,
        **kwargs,
    ) -> AsyncGenerator[str, None]:
        """覆写：将 system_prompt 传入 kwargs。"""
        if system_prompt:
            kwargs["system_prompt"] = system_prompt
        async for chunk in super().chat_stream(prompt, model, **kwargs):
            yield chunk
