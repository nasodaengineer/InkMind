"""OpenAI 兼容 Provider — 支持 DeepSeek、OpenAI 等遵循 OpenAI 格式的 API。"""

from __future__ import annotations

from typing import AsyncGenerator, Dict, List, Optional

import httpx

from inkmind.models.llm import ProviderConfig, ProviderProtocol, RetryConfig
from inkmind.llm.providers.base import BaseProvider, LLMResponse


class OpenAIProvider(BaseProvider):
    """OpenAI 兼容格式的 Provider。
    支持：DeepSeek、OpenAI、Groq 等。
    """

    def __init__(self, config: ProviderConfig, retry: Optional[RetryConfig] = None) -> None:
        if config.protocol != ProviderProtocol.OPENAI:
            raise ValueError(f"OpenAIProvider requires OPENAI protocol, got {config.protocol}")
        super().__init__(config, retry)

    def _build_headers(self) -> Dict[str, str]:
        headers = {
            "Content-Type": "application/json",
        }
        api_key = self._resolve_api_key()
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        return headers

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
        # 合并额外参数（如 temperature, max_tokens, reasoning_effort 等）
        safe_params = {
            "temperature",
            "max_tokens",
            "top_p",
            "frequency_penalty",
            "presence_penalty",
            "stop",
            "reasoning_effort",
        }
        for k, v in kwargs.items():
            if k in safe_params and v is not None:
                payload[k] = v

        # DeepSeek 思考模式支持
        if kwargs.get("thinking_enabled"):
            payload["extra_body"] = {"thinking": {"type": "enabled"}}

        response = await client.post(
            "/chat/completions",
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
        safe_params = {
            "temperature",
            "max_tokens",
            "top_p",
            "frequency_penalty",
            "presence_penalty",
            "stop",
        }
        for k, v in kwargs.items():
            if k in safe_params and v is not None:
                payload[k] = v

        async with client.stream(
            "POST",
            "/chat/completions",
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
