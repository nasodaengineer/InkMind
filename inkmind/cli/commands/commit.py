"""inkmind commit — 导出 JSON 快照。"""

from __future__ import annotations

from uuid import UUID

from inkmind.cli.base_command import BaseCommand
from inkmind.cli.db import get_session
from inkmind.storage.snapshot import JSONSnapshot

COMMAND = "commit"
HELP = "导出 JSON 快照（便携备份）"
USAGE = "inkmind commit [--output FILE] [--db PATH] [--novel-id UUID]"


def setup(subparsers) -> None:
    parser = subparsers.add_parser(COMMAND, help=HELP, usage=USAGE)
    parser.add_argument("--output", type=str, default="", help="输出文件路径")
    parser.add_argument("--novel-id", type=str, default="", help="小说 ID")
    parser.add_argument("--db", type=str, default="", help="数据库路径（覆盖配置）")


class CommitCommand(BaseCommand):
    """导出 JSON 快照命令。"""

    @classmethod
    async def _run(cls, args, formatter, cfg, db_path, novel_id):
        if novel_id is None:
            formatter.error(
                "未指定 novel_id。请通过 --novel-id 参数、inkmind.toml 的 project.novel_id "
                "或 INKMIND_NOVEL_ID 环境变量设置。"
            )
            return

        output = args.output or f"inkmind-snapshot-{novel_id}.json"

        async with get_session(db_path) as session:
            snap = JSONSnapshot(session)
            path = await snap.dump(novel_id, output)

        formatter.success(
            f"快照已导出: {path}",
            data={"path": str(path), "novel_id": str(novel_id)},
        )


run = CommitCommand.execute
