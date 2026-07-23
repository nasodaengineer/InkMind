"""CLI 命令注册表。每个子命令对应一个模块。"""

from inkmind.cli.commands import (
    commit,
    init,
    next as next_cmd,
    plan,
    restore,
    review,
    serve,
    shell,
    status,
    version,
    write,
)

__all__ = [
    "commit",
    "init",
    "next_cmd",
    "plan",
    "restore",
    "review",
    "serve",
    "shell",
    "status",
    "version",
    "write",
]
