"""兼容层：从 deps 模块转发依赖项。

新路由引用本模块（与 worktree agent 产出一致），
实际实现在 deps.py 中。
"""

from inkmind.api.deps import get_db as get_session
from inkmind.api.deps import get_uow

__all__ = ["get_session", "get_uow"]
