"""InkMind 记忆管理模块。

提供四级压缩记忆架构的核心逻辑：
- L0 全文索引
- L1 活跃上下文（滑窗管理）
- L2 压缩记忆（触发与执行）
- L3 长期知识
"""

from inkmind.memory.compressor import MemoryKeeperCore

__all__ = ["MemoryKeeperCore"]
