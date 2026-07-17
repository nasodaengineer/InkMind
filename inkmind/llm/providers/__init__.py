"""Provider 注册表 — 通过名称查找对应 Provider 类。"""

from typing import Dict, Type

from inkmind.llm.providers.base import BaseProvider
from inkmind.llm.providers.anthropic_provider import AnthropicProvider
from inkmind.llm.providers.ollama_provider import OllamaProvider
from inkmind.llm.providers.openai_provider import OpenAIProvider

# 协议 → Provider 类映射表
PROVIDER_REGISTRY: Dict[str, Type[BaseProvider]] = {
    "openai": OpenAIProvider,
    "anthropic": AnthropicProvider,
    "ollama": OllamaProvider,
}

__all__ = [
    "BaseProvider",
    "OpenAIProvider",
    "AnthropicProvider",
    "OllamaProvider",
    "PROVIDER_REGISTRY",
]
