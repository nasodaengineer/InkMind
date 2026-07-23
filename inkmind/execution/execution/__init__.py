"""Run 执行编排模块。

提供 RunLoop 类，负责 Run 生命周期的全流程驱动：
- generate: Writer→Editor(≤3次修订)→awaiting_human
- revise: Writer 修订→Editor→awaiting_human
- finalize: 直接落稿
- plan: Planner 生成批量大纲
"""

from inkmind.execution.runner import RunLoop

__all__ = ["RunLoop"]
