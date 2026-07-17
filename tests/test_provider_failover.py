"""Provider 降级链集成测试。

验证当一个 Provider 失败时，系统能自动切换到备用 Provider。
"""

from __future__ import annotations

import pytest

from inkmind.llm.providers.base import ProviderStats


class TestProviderFailover:
    def test_stats_tracking_on_failover_chain(self):
        """降级链中 stats 应累加正确。"""
        # 验证 ProviderStats 含有所有必需字段
        stats = ProviderStats()
        assert hasattr(stats, "total_calls")
        assert hasattr(stats, "successful_calls")
        assert hasattr(stats, "failed_calls")
        assert hasattr(stats, "fallback_used")
        assert hasattr(stats, "min_latency")
        assert hasattr(stats, "max_latency")
        assert hasattr(stats, "avg_latency")
        assert hasattr(stats, "total_prompt_tokens")
        assert hasattr(stats, "total_completion_tokens")
        assert hasattr(stats, "total_tokens")
        assert hasattr(stats, "estimated_cost")

        # 测试 record_success
        stats.record_success(0.5, {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30})
        assert stats.min_latency == 0.5
        assert stats.max_latency == 0.5
        assert stats.avg_latency == 0.5
        assert stats.total_prompt_tokens == 10
        assert stats.total_completion_tokens == 20
        assert stats.total_tokens == 30

        stats.record_success(1.5, {"prompt_tokens": 5, "completion_tokens": 15})
        assert stats.min_latency == 0.5
        assert stats.max_latency == 1.5
        assert stats.avg_latency == 1.0
        assert stats.total_prompt_tokens == 15
        assert stats.total_completion_tokens == 35
        # total_tokens 未提供时，自动按 prompt_tokens + completion_tokens 计算
        assert stats.total_tokens == 50

        # record_success 不增加调用计数（已在 chat() 中处理）
        stats.record_success(0.3)
        assert stats.min_latency == 0.3
        assert stats.max_latency == 1.5
