"""离线确定性 LLM 客户端 — 测试与演示缝。

当环境变量 ``INKMIND_LLM_FAKE=1`` 时，``build_llm_client()`` 返回
本模块的 ``ScriptedLLMClient`` 而非真实 ``LLMClient``：

- 不发起任何网络请求，响应完全确定（可断言）
- 默认按 Agent 角色生成结构化响应（planner → 大纲 JSON、writer → 正文、
  editor → approve JSON、memory-keeper → 摘要 JSON）
- planner 默认响应从 prompt 解析起始章节序号，保证多轮索引连续
- writer 默认响应每次调用内容不同，避免触发 digest 幂等误判
- 支持通过 ``responses`` 注入自定义响应队列（驱动修订循环等场景）
- 记录所有调用（``calls``），供测试断言 prompt 内容

生产路径（默认）永远返回真实 LLMClient，本类只服务于离线场景。
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import AsyncGenerator, Dict, List, Optional

from inkmind.llm.providers.base import LLMResponse, ProviderStats, aggregate_snapshots


class ScriptedLLMClient:
    """离线确定性 LLM 客户端（接口与 LLMClient.chat 对齐）。"""

    def __init__(self, responses: Optional[Dict[str, List[str]]] = None) -> None:
        """
        Args:
            responses: 角色 → 响应队列。按调用顺序消费，耗尽后重复最后一个；
                       未覆盖的角色使用默认生成器。
        """
        self._responses: Dict[str, List[str]] = {
            role: list(queue) for role, queue in (responses or {}).items()
        }
        self.calls: List[Dict[str, Optional[str]]] = []
        self._call_counts: Dict[str, int] = {}
        self._stats_history: List[ProviderStats] = []

    async def chat(
        self,
        agent_role: str,
        prompt: str,
        system_prompt: Optional[str] = None,
        **kwargs,
    ) -> LLMResponse:
        """模拟 LLMClient.chat — 记录调用并返回确定性响应。"""
        self.calls.append(
            {
                "agent_role": agent_role,
                "prompt": prompt,
                "system_prompt": system_prompt,
            }
        )
        self._call_counts[agent_role] = self._call_counts.get(agent_role, 0) + 1
        content = self._next_content(agent_role, prompt)
        # ADR-0010 §10-A：离线调用同样产生 Stats 快照（零成本、零延迟）
        self.record_stats(
            ProviderStats(
                provider_name="scripted",
                model_name="scripted-fake",
                latency_ms=0.0,
                prompt_tokens=0,
                completion_tokens=0,
                total_tokens=0,
                estimated_cost=0.0,
                success=True,
                error_type=None,
                degraded=False,
                retry_count=0,
                timestamp=datetime.now(timezone.utc),
            )
        )
        return LLMResponse(
            content=content,
            model="scripted-fake",
            provider="scripted",
            finish_reason="stop",
            usage=None,
        )

    async def chat_stream(
        self,
        agent_role: str,
        prompt: str,
        system_prompt: Optional[str] = None,
        **kwargs,
    ) -> AsyncGenerator[str, None]:
        """模拟流式响应 — 以 token 分片产出确定性内容。"""
        self.calls.append(
            {
                "agent_role": agent_role,
                "prompt": prompt,
                "system_prompt": system_prompt,
            }
        )
        self._call_counts[agent_role] = self._call_counts.get(agent_role, 0) + 1
        content = self._next_content(agent_role, prompt)

        # 分片产出：每次 5-15 个字符
        chunk_size = 10
        for i in range(0, len(content), chunk_size):
            yield content[i : i + chunk_size]

        self.record_stats(
            ProviderStats(
                provider_name="scripted",
                model_name="scripted-fake",
                latency_ms=0.0,
                prompt_tokens=0,
                completion_tokens=0,
                total_tokens=0,
                estimated_cost=0.0,
                success=True,
                error_type=None,
                degraded=False,
                retry_count=0,
                timestamp=datetime.now(timezone.utc),
            )
        )

    def get_stats(self) -> dict:
        """离线客户端无 Provider 统计，返回空 dict（接口对齐）。"""
        return {}

    # ── Stats 聚合（ADR-0010 §10-C，接口与 LLMClient 对齐） ──

    def record_stats(self, stats: ProviderStats) -> None:
        """记录一份调用快照。"""
        self._stats_history.append(stats)

    def aggregate_stats(self) -> dict:
        """返回当前会话的汇总统计。"""
        return aggregate_snapshots(self._stats_history)

    def reset_stats(self) -> None:
        """清空会话 Stats 历史。"""
        self._stats_history.clear()

    def get_raw_stats(self) -> list[ProviderStats]:
        """返回原始 ProviderStats 列表（接口与 LLMClient 对齐）。"""
        return list(self._stats_history)

    def cancel_all(self) -> None:
        """离线客户端无进行中请求，空操作（接口对齐）。"""

    async def shutdown(self) -> None:
        """离线客户端无 HTTP 连接，空操作（接口对齐）。"""

    # ── 内部 ──────────────────────────────────────────────

    def _next_content(self, agent_role: str, prompt: str) -> str:
        queue = self._responses.get(agent_role)
        if queue:
            if len(queue) > 1:
                return queue.pop(0)
            return queue[0]  # 耗尽后重复最后一个
        return self._default_content(agent_role, prompt)

    def _default_content(self, agent_role: str, prompt: str) -> str:
        generator = self._DEFAULT_GENERATORS.get(agent_role)
        if generator is None:
            return f"离线演示响应（{agent_role} · 第{self._call_counts[agent_role]}次）"
        return generator(self, prompt)

    def _default_writer(self, prompt: str) -> str:
        n = self._call_counts["writer"]
        return (
            f"夜色如墨，星光洒落在寂静的山谷之中。（离线演示内容 · 第{n}次生成）\n\n"
            "主角沿着蜿蜒的小路前行，脚步声在空旷的夜色里格外清晰。"
            "远处传来低沉的钟声，仿佛预示着某种即将到来的变化。\n\n"
            "他握紧了手中的旧地图，那是解开一切谜团的关键。"
            "风穿过树梢，带来一丝不属于这个季节的寒意。"
        )

    def _default_editor(self, prompt: str) -> str:
        return '{"verdict": "approve", "issues": []}'

    def _default_memory_keeper(self, prompt: str) -> str:
        return (
            '{"summary": "离线演示摘要：本章已定稿，情节按计划推进", '
            '"key_events": ["章节完成"]}'
        )

    def _default_planner(self, prompt: str) -> str:
        # 从 prompt 解析起始章节序号（build_planner_prompt 输出「起始章节序号：N」），
        # 保证多轮规划时章节索引连续
        m = re.search(r"起始章节序号[：:]\s*(\d+)", prompt)
        start = int(m.group(1)) if m else 1
        chapters = [
            {"index": i, "title": f"第{i}章", "outline": f"第{i}章大纲内容"}
            for i in range(start, start + 5)
        ]
        return json.dumps({"chapters": chapters}, ensure_ascii=False)

    _DEFAULT_GENERATORS = {
        "writer": _default_writer,
        "editor": _default_editor,
        "memory-keeper": _default_memory_keeper,
        "planner": _default_planner,
    }
