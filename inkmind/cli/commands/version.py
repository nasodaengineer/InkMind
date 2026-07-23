"""inkmind version — 显示版本信息。"""

from __future__ import annotations

from inkmind.cli.base_command import BaseCommand

VERSION = "0.1.0"  # fallback

COMMAND = "version"
HELP = "显示版本信息"
USAGE = "inkmind version"


def setup(subparsers) -> None:
    parser = subparsers.add_parser(COMMAND, help=HELP, usage=USAGE)
    parser.add_argument("--verbose", action="store_true", help="显示详细版本信息")


class VersionCommand(BaseCommand):
    """显示版本信息命令。"""

    @classmethod
    def _run(cls, args, formatter, cfg, db_path, novel_id):
        """显示版本号。"""
        try:
            from importlib.metadata import version as _pkg_version

            ver = _pkg_version("inkmind")
        except (ImportError, Exception):
            ver = VERSION

        formatter.print_dict(
            {"inkmind": ver},
            text_fn=lambda d: f"InkMind v{d['inkmind']}",
        )


run = VersionCommand.execute
