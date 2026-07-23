"""inkmind shell — 进入交互式 REPL。"""

from __future__ import annotations

import shlex
import subprocess

from inkmind.cli.base_command import BaseCommand
from inkmind.cli.config import CLIConfig
from inkmind.cli.formatter import OutputFormatter

COMMAND = "shell"
HELP = "进入交互式写作 Shell"
USAGE = "inkmind shell [--db PATH] [--novel-id UUID]"


def setup(subparsers) -> None:
    parser = subparsers.add_parser(COMMAND, help=HELP, usage=USAGE)
    parser.add_argument("--novel-id", type=str, default="", help="小说 ID")
    parser.add_argument("--db", type=str, default="", help="数据库路径（覆盖配置）")


class ShellCommand(BaseCommand):
    """交互式写作 Shell 命令。"""

    @classmethod
    def execute(cls, args) -> None:
        formatter = OutputFormatter(json_mode=False)
        cfg = CLIConfig.load(novel_id=getattr(args, "novel_id", None) or None, json_output=False)
        db_path = args.db or cfg.db_path
        novel_id = getattr(args, "novel_id", None) or cfg.novel_id
        cls._run(args, formatter, cfg, db_path, novel_id)

    @classmethod
    def _run(cls, args, formatter, cfg, db_path, novel_id):
        """交互式写作 Shell。"""
        import shutil

        width = shutil.get_terminal_size().columns
        width = min(width, 80)

        print()
        print(chr(0x256D) + chr(0x2500) * (width - 2) + chr(0x256E))
        print(chr(0x2502) + ("InkMind v0.1.0 — 交互式写作 Shell".center(width - 2)) + chr(0x2502))
        print(
            chr(0x2502)
            + ("输入 help 查看命令列表，exit 或 Ctrl+C 退出".center(width - 2))
            + chr(0x2502)
        )
        print(chr(0x2570) + chr(0x2500) * (width - 2) + chr(0x256F))
        print()
        if novel_id is None:
            print(chr(0x2139) + chr(0xFE0F) + " 未指定小说 ID，请先创建或指定一个小说。")
            print()

        prefix = ["python", "-m", "inkmind"]
        if novel_id:
            prefix += ["--novel-id", novel_id]

        while True:
            try:
                cmd = input("inkmind> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break

            if not cmd:
                continue
            if cmd in ("exit", "quit", "q"):
                break
            if cmd == "help":
                _print_help()
                continue

            full_cmd = prefix + shlex.split(cmd)
            try:
                subprocess.run(full_cmd, check=False)
            except KeyboardInterrupt:
                print()
            except Exception as e:
                print(f"错误: {e}")


def _print_help() -> None:
    print("可用命令:")
    print("  init --title <标题>      — 创建新小说")
    print('  write "<章节标题>"       — 写作新章节')
    print("  next                     — 完整一轮流水线")
    print("  status                   — 显示状态")
    print("  commit                   — 导出快照")
    print("  help                     — 显示此帮助")
    print("  exit / quit / q          — 退出 Shell")


run = ShellCommand.execute
