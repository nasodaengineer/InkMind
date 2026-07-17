"""Provider 工厂 + 模型路由 + 降级策略。"""

from __future__ import annotations

from typing import AsyncGenerator, Dict, List, Optional, Tuple

from inkmind.llm.providers.base import BaseProvider, LLMResponse
from inkmind.llm.providers import PROVIDER_REGISTRY
from inkmind.models.llm import (
    AgentModelBinding,
    LLMConfig,
    ProviderConfig,
    ProviderProtocol,
    RetryConfig,
)


class ProviderFactory:
    """Provider 实例工厂。
    
    职责：
      - 根据 LLMConfig 创建所有 Provider 实例
      - 按名称 / 协议查找 Provider
      - 管理 Provider 生命周期
    """

    def __init__(self, config: LLMConfig) -> None:
        self.config = config
        self._providers: Dict[str, BaseProvider] = {}
        self._init_providers()

    def _init_providers(self) -> None:
        """根据配置初始化所有 Provider 实例。"""
        for name, provider_cfg in self.config.providers.items():
            provider_cls = PROVIDER_REGISTRY.get(provider_cfg.protocol.value)
            if provider_cls is None:
                raise ValueError(
                    f"Unknown protocol '{provider_cfg.protocol.value}' for provider '{name}'. "
                    f"Available: {list(PROVIDER_REGISTRY.keys())}"
                )
            self._providers[name] = provider_cls(provider_cfg, self.config.retry)

    def get_provider(self, name: str) -> BaseProvider:
        """按名称获取 Provider 实例。"""
        provider = self._providers.get(name)
        if provider is None:
            raise KeyError(f"Provider '{name}' not found. Available: {list(self._providers.keys())}")
        return provider

    def get_providers_by_model(self, model: str) -> List[BaseProvider]:
        """查找支持指定模型的所有 Provider。"""
        results: List[BaseProvider] = []
        for name, p in self._providers.items():
            if model in p.config.models:
                results.append(p)
        return results

    def list_providers(self) -> Dict[str, BaseProvider]:
        """返回所有 Provider 的快照。"""
        return dict(self._providers)


class ModelRouter:
    """模型路由器 — 将 Agent 角色映射到模型 + 处理降级。"""

    def __init__(self, factory: ProviderFactory, config: LLMConfig) -> None:
        self.factory = factory
        self.config = config
        self._binding_map: Dict[str, AgentModelBinding] = {
            b.agent_role: b for b in config.router.bindings
        }

    def resolve_model(self, agent_role: str) -> str:
        """将 Agent 角色解析为主模型名。"""
        binding = self._binding_map.get(agent_role)
        if binding is not None:
            return binding.primary_model
        return self.config.default_model

    def resolve_fallback_models(self, agent_role: str) -> List[str]:
        """获取 Agent 的降级候选模型列表。"""
        binding = self._binding_map.get(agent_role)
        if binding is not None:
            return binding.fallback_models
        return []

    def get_provider_for_model(self, model: str) -> BaseProvider:
        """查找能运行指定模型的 Provider。"""
        providers = self.factory.get_providers_by_model(model)
        if not providers:
            raise RuntimeError(
                f"No provider supports model '{model}'. "
                f"Available models: {self._available_models()}"
            )
        return providers[0]

    def _available_models(self) -> List[str]:
        models: List[str] = []
        for p in self.factory.list_providers().values():
            models.extend(p.config.models)
        return list(sorted(set(models)))

    def _candidate_models(self, agent_role: str) -> List[str]:
        """构建候选模型列表：主模型 → 降级模型 → 兜底默认模型。"""
        binding = self._binding_map.get(agent_role)
        candidates: List[str] = []

        if binding:
            candidates.append(binding.primary_model)
            candidates.extend(binding.fallback_models)
        if self.config.default_model not in candidates:
            candidates.append(self.config.default_model)
        return candidates

    @staticmethod
    def _all_models_failed_error(
        agent_role: str,
        candidates: List[str],
        errors: List[Tuple[str, str, str]],
        stream: bool = False,
    ) -> RuntimeError:
        """组装「所有模型失败」错误（含每个候选的异常信息）。"""
        error_details = "; ".join(
            f"[{m}@{p}]: {err}" for m, p, err in errors
        )
        suffix = " (stream)" if stream else ""
        return RuntimeError(
            f"All models failed for agent '{agent_role}'{suffix}. "
            f"Tried {len(candidates)} model(s): {error_details}"
        )

    async def chat_with_fallback(
        self,
        agent_role: str,
        prompt: str,
        system_prompt: Optional[str] = None,
        **kwargs,
    ) -> LLMResponse:
        """带降级的 chat 调用。

        策略：
          1. 主模型 → Provider
          2. 降级模型列表（顺序尝试）
          3. 兜底默认模型
        """
        candidates = self._candidate_models(agent_role)
        errors: List[Tuple[str, str, str]] = []  # (model, provider_name, error_msg)

        for idx, model in enumerate(candidates):
            provider = self.get_provider_for_model(model)
            try:
                return await provider.chat(
                    prompt, model=model, system_prompt=system_prompt,
                    degraded=idx > 0, **kwargs,
                )
            except Exception as e:
                provider.stats.fallback_used += 1
                errors.append((model, provider.config.name, str(e) or type(e).__name__))
                continue

        raise self._all_models_failed_error(agent_role, candidates, errors)

    async def chat_stream_with_fallback(
        self,
        agent_role: str,
        prompt: str,
        system_prompt: Optional[str] = None,
        **kwargs,
    ) -> AsyncGenerator[str, None]:
        """带降级的流式 chat 调用。"""
        candidates = self._candidate_models(agent_role)
        errors: List[Tuple[str, str, str]] = []
        last_error: Optional[Exception] = None

        for idx, model in enumerate(candidates):
            provider = self.get_provider_for_model(model)
            try:
                async for chunk in provider.chat_stream(
                    prompt, model=model, system_prompt=system_prompt,
                    degraded=idx > 0, **kwargs,
                ):
                    yield chunk
                return
            except Exception as e:
                provider.stats.fallback_used += 1
                errors.append((model, provider.config.name, str(e) or type(e).__name__))
                last_error = e
                continue

        raise self._all_models_failed_error(
            agent_role, candidates, errors, stream=True
        ) from last_error
