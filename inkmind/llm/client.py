"""LLM 统一客户端 — Agent 代码的直接入口。

提供：
  - LLMClient 类，封装 ModelRouter + ProviderFactory
  - 简单的 chat/chat_stream 接口
  - Agent 角色直接透传
  - cancel() 可中断所有进行中的请求
"""

from __future__ import annotations

import os
from typing import AsyncGenerator, Optional

from inkmind.llm.factory import ModelRouter, ProviderFactory
from inkmind.llm.providers.base import (
    LLMResponse,
    ProviderStats,
    aggregate_snapshots,
    cleanup_http_clients,
)
from inkmind.models.llm import LLMConfig


class LLMClient:
    """LLM 统一客户端。

    用法：
      client = LLMClient(config)
      response = await client.chat("planner", "写一份大纲...")
      async for chunk in client.chat_stream("writer", "写第一章..."):
          print(chunk, end="")
    """

    def __init__(self, config: Optional[LLMConfig] = None) -> None:
        self.config = config or LLMConfig()
        self.factory = ProviderFactory(self.config)
        self.router = ModelRouter(self.factory, self.config)
        # ADR-0010 §10-C：会话级 Stats 历史，Provider 埋点经 sink 自动汇入
        self._stats_history: list[ProviderStats] = []
        for provider in self.factory.list_providers().values():
            provider._stats_sink = self.record_stats

    # ── 公共接口 ──────────────────────────────────────────

    async def chat(
        self,
        agent_role: str,
        prompt: str,
        system_prompt: Optional[str] = None,
        **kwargs,
    ) -> LLMResponse:
        """按 Agent 角色调用 LLM（带降级）。"""
        return await self.router.chat_with_fallback(
            agent_role=agent_role,
            prompt=prompt,
            system_prompt=system_prompt,
            **kwargs,
        )

    async def chat_stream(
        self,
        agent_role: str,
        prompt: str,
        system_prompt: Optional[str] = None,
        **kwargs,
    ) -> AsyncGenerator[str, None]:
        """按 Agent 角色流式调用 LLM（带降级）。"""
        async for chunk in self.router.chat_stream_with_fallback(
            agent_role=agent_role,
            prompt=prompt,
            system_prompt=system_prompt,
            **kwargs,
        ):
            yield chunk

    def cancel_all(self) -> None:
        """中断所有 Provider 的进行中请求。"""
        for provider in self.factory.list_providers().values():
            provider.cancel()

    def reset_cancel(self) -> None:
        """重置所有 Provider 的中断信号。"""
        for provider in self.factory.list_providers().values():
            provider.reset_cancel()

    # ── Provider 访问 ────────────────────────────────────

    def get_provider(self, name: str):
        """按名称获取原始 Provider 实例（用于调试或高级控制）。"""
        return self.factory.get_provider(name)

    def get_stats(self):
        """获取所有 Provider 的运行时统计（可变累计器视图）。"""
        return {
            name: p.stats for name, p in self.factory.list_providers().items()
        }

    # ── Stats 聚合（ADR-0010 §10-C） ──────────────────────

    def record_stats(self, stats: ProviderStats) -> None:
        """记录一份调用快照（Provider 埋点自动调用，亦可手动追加）。"""
        self._stats_history.append(stats)

    def aggregate_stats(self) -> dict:
        """返回当前会话的汇总统计（total_calls/tokens/cost/延迟/成功率/降级率）。"""
        return aggregate_snapshots(self._stats_history)

    def reset_stats(self) -> None:
        """清空会话 Stats 历史（含各 Provider 的调用快照历史）。"""
        self._stats_history.clear()
        for provider in self.factory.list_providers().values():
            provider.stats_history.clear()

    # ── 生命周期 ──────────────────────────────────────────

    async def shutdown(self) -> None:
        """关闭客户端，清理所有 HTTP 连接。"""
        self.cancel_all()
        await cleanup_http_clients()


def build_llm_client(
    config: Optional[LLMConfig] = None,
    db_path: Optional[str] = None,
):
    """按环境构造 LLM 客户端（CLI 与 Agent 流水线的统一入口）。

    - 默认：真实 ``LLMClient``（DeepSeek 等 Provider，需相应 API Key 环境变量）
    - ``INKMIND_LLM_FAKE=1``：离线 ``ScriptedLLMClient``（测试/演示，无网络）
    - 未传入 config 时尝试从 DB app_settings 读取（db_path 参数指定数据库路径）
    """
    if os.environ.get("INKMIND_LLM_FAKE") == "1":
        from inkmind.llm.scripted import ScriptedLLMClient

        return ScriptedLLMClient()

    if config is None and db_path is not None:
        config = _load_config_from_db(db_path)

    return LLMClient(config)


def _load_config_from_db(db_path: str) -> LLMConfig:
    """从数据库 app_settings 表读取 LLM 配置，不存在则返回代码默认。

    同步版本 — 适合 CLI 入口等非异步上下文（内部启用新事件循环）。
    """
    import asyncio
    return asyncio.run(_load_config_from_db_async(db_path))


async def _load_config_from_db_async(db_path: str) -> LLMConfig:
    """从数据库 app_settings 表读取 LLM 配置的异步版本。

    适合在已有事件循环的上下文中调用（如 FastAPI 端点、Agent 流水线）。
    """
    from inkmind.storage.database import DatabaseManager
    from inkmind.storage.repositories import AppSettingsRepository

    mgr = DatabaseManager(db_path)
    async with mgr.session() as session:
        await mgr.create_tables()
        repo = AppSettingsRepository(session)
        saved = await repo.get()
    await mgr.close()
    return LLMConfig.load_or_default(saved)
