"""LLM Provider 配置模型 — Provider 注册、模型路由、限流与重试策略。"""

from __future__ import annotations

from enum import Enum
from typing import Dict, List, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class ProviderProtocol(str, Enum):
    """Provider 通信协议类型。"""
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    OLLAMA = "ollama"


class ProviderConfig(BaseModel):
    """单个 Provider 的注册配置（方案A：以 Provider 为中心）。"""
    name: str = Field(description="Provider 唯一标识，如 'deepseek'、'anthropic'、'ollama-local'")
    protocol: ProviderProtocol = Field(description="通信协议：openai / anthropic / ollama")
    base_url: str = Field(description="API 基础地址，如 'https://api.deepseek.com'")
    api_key_env: str = Field(default="", description="环境变量名，如 'DEEPSEEK_API_KEY'；为空表示无需 API Key")
    models: List[str] = Field(default_factory=list, description="该 Provider 支持的模型名列表")
    max_concurrent: int = Field(default=3, ge=1, description="最大并发请求数（连接池大小）")
    max_keepalive: int = Field(default=10, ge=1, description="httpx 连接池 keepalive 上限")
    max_calls_per_minute: int = Field(
        default=0, ge=0, description="每分钟最大调用次数（速率限制）；0 表示不限制"
    )


class RetryConfig(BaseModel):
    """重试策略 — 固定间隔，无超时（可中断）。"""
    max_retries: int = Field(default=3, ge=0, description="最大重试次数")
    base_delay_s: float = Field(default=2.0, ge=0, description="固定退避延迟（秒）")
    non_retryable_statuses: List[int] = Field(
        default_factory=lambda: [400, 401, 403, 422],
        description="不重试的 HTTP 状态码",
    )


class AgentModelBinding(BaseModel):
    """单个 Agent 的模型绑定（主模型 + 降级候选列表）。"""
    agent_role: str = Field(description="Agent 角色，如 'planner'、'writer'")
    primary_model: str = Field(description="主模型名，如 'deepseek-v4-pro'")
    fallback_models: List[str] = Field(default_factory=list, description="降级候选模型列表")


class ModelRouterConfig(BaseModel):
    """完整模型路由配置 — Agent → 模型映射表。"""
    bindings: List[AgentModelBinding] = Field(
        default_factory=lambda: [
            AgentModelBinding(
                agent_role="planner",
                primary_model="deepseek-v4-pro",
                fallback_models=["deepseek-v4-flash"],
            ),
            AgentModelBinding(
                agent_role="writer",
                primary_model="deepseek-v4-flash",
                fallback_models=[],
            ),
            AgentModelBinding(
                agent_role="editor",
                primary_model="deepseek-v4-flash",
                fallback_models=[],
            ),
            AgentModelBinding(
                agent_role="memory-keeper",
                primary_model="deepseek-v4-flash",
                fallback_models=[],
            ),
            AgentModelBinding(
                agent_role="designer",
                primary_model="deepseek-v4-flash",
                fallback_models=[],
            ),
        ],
        description="Agent → 模型绑定表，按角色分配主模型与降级候选",
    )


class LLMConfig(BaseModel):
    """顶层 LLM 总配置。"""
    config_id: UUID = Field(default_factory=uuid4)
    providers: Dict[str, ProviderConfig] = Field(
        default_factory=lambda: {
            "deepseek": ProviderConfig(
                name="deepseek",
                protocol=ProviderProtocol.OPENAI,
                base_url="https://api.deepseek.com",
                api_key_env="DEEPSEEK_API_KEY",
                models=["deepseek-v4-pro", "deepseek-v4-flash"],
                max_concurrent=3,
            ),
        },
        description="Provider 字典，key 为 Provider 名",
    )
    router: ModelRouterConfig = Field(default_factory=ModelRouterConfig)
    retry: RetryConfig = Field(default_factory=RetryConfig)
    default_model: str = Field(default="deepseek-v4-flash", description="兜底默认模型")

    @classmethod
    def from_settings_dict(cls, data: dict) -> LLMConfig:
        """从 app_settings dict 反序列化重建 LLMConfig。

        输入结构:
        {providers: {...}, model_router: {bindings: [...]}, retry: {...}, default_model: str}
        """
        d = dict(data)
        # 将 model_router 重命名为 router（内部命名）
        if "model_router" in d:
            d["router"] = d.pop("model_router")
        return cls.model_validate(d)

    @classmethod
    def load_or_default(cls, settings_dict: dict | None = None) -> LLMConfig:
        """加载配置：传入 app_settings dict 则反序列化，否则返回代码默认。"""
        if settings_dict:
            return cls.from_settings_dict(settings_dict)
        return cls()
