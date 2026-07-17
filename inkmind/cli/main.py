"""InkMind CLI 入口 — argparse 解析 + 命令分发。"""

from __future__ import annotations

import argparse
import sys


def build_parser() -> argparse.ArgumentParser:
    """构建参数解析器。"""
    parser = argparse.ArgumentParser(
        prog="inkmind",
        description="InkMind — AI 小说协作写作系统",
        usage="inkmind <command> [options]",
    )

    parser.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="以 JSON 格式输出（默认: 可读文本）",
    )

    subparsers = parser.add_subparsers(dest="command", metavar="<command>")

    # 注册所有子命令
    from inkmind.cli.commands import (
        init,
        write,
        plan,
        review,
        next,
        status,
        shell,
        commit,
        restore,
        version,
    )

    for mod in (init, write, plan, review, next, status, shell, commit, restore, version):
        mod.setup(subparsers)

    return parser


def main() -> None:
    """CLI 主入口。"""
    parser = build_parser()

    # 提前提取 --json 标志（argparse 子命令不继承父解析器的参数）
    # 精确匹配 --json 作为独立标志，不误伤 --title "--json"
    json_indices = [i for i, a in enumerate(sys.argv[1:], 1) if a == "--json"]
    json_mode = len(json_indices) > 0
    for i in reversed(json_indices):
        del sys.argv[i]

    if len(sys.argv) == 1:
        parser.print_help()
        sys.exit(1)

    args = parser.parse_args()
    args.json = json_mode

    # 命令→模块映射
    from inkmind.cli.commands import (
        init,
        write,
        plan,
        review,
        next,
        status,
        shell,
        commit,
        restore,
        version,
    )

    cmd_map = {
        "init": init,
        "write": write,
        "plan": plan,
        "review": review,
        "next": next,
        "status": status,
        "shell": shell,
        "commit": commit,
        "restore": restore,
        "version": version,
    }

    cmd = cmd_map.get(args.command)
    if cmd:
        cmd.run(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
