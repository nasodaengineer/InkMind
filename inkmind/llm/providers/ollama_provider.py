"""Ollama Provider — 支持本地模型（通过 Ollama API）。"""

from __future__ import annotations

from typing import AsyncGenerator, Dict, List, Optional

import httpx

from inkmind.models.llm import ProviderConfig, ProviderProtocol, RetryConfig
from inkmind.llm.providers.base import BaseProvider, LLMResponse


class OllamaProvider(BaseProvider):
    """Ollama 本地模型 Provider。
    Ollama 也使用 OpenAI 兼容的 /v1/chat/completions 端点（Ollama 0.3+）。
    """

    def __init__(self, config: ProviderConfig, retry: Optional[RetryConfig] = None) -> None:
        if config.protocol != ProviderProtocol.OLLAMA:
            raise ValueError(f"OllamaProvider requires OLLAMA protocol, got {config.protocol}")
        super().__init__(config, retry)

    def _build_headers(self) -> Dict[str, str]:
        return {"Content-Type": "application/json"}

    def _build_messages(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
    ) -> List[Dict[str, str]]:
        messages: List[Dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        return messages

    async def _do_chat(
        self,
        client: httpx.AsyncClient,
        messages: List[Dict[str, str]],
        model: str,
        **kwargs,
    ) -> LLMResponse:
        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
        }
        if "temperature" in kwargs:
            payload["temperature"] = kwargs["temperature"]
        if "max_tokens" in kwargs:
            payload["max_tokens"] = kwargs["max_tokens"]

        response = await client.post(
            "/v1/chat/completions",
            headers=self._build_headers(),
            json=payload,
        )
        response.raise_for_status()
        data = response.json()

        choice = data["choices"][0]
        content = choice.get("message", {}).get("content", "")
        finish_reason = choice.get("finish_reason", "stop")

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
        payload = {
            "model": model,
            "messages": messages,
            "stream": True,
        }
        if "temperature" in kwargs:
            payload["temperature"] = kwargs["temperature"]
        if "max_tokens" in kwargs:
            payload["max_tokens"] = kwargs["max_tokens"]

        async with client.stream(
            "POST",
            "/v1/chat/completions",
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
                delta = chunk.get("choices", [{}])[0].get("delta", {})
                content = delta.get("content", "")
                if content:
                    yield content
