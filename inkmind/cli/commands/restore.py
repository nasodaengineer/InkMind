"""inkmind restore <snapshot.json> — 从快照恢复。"""

from __future__ import annotations

from pathlib import Path

from inkmind.cli.base_command import BaseCommand
from inkmind.cli.db import get_uow
from inkmind.storage.snapshot import JSONSnapshot

COMMAND = "restore"
HELP = "从 JSON 快照恢复"
USAGE = "inkmind restore <snapshot.json> [--db PATH]"


def setup(subparsers) -> None:
    parser = subparsers.add_parser(COMMAND, help=HELP, usage=USAGE)
    parser.add_argument("snapshot", type=str, help="快照文件路径")
    parser.add_argument("--db", type=str, default="", help="数据库路径（覆盖配置）")


class RestoreCommand(BaseCommand):
    """从快照恢复命令。"""

    @classmethod
    async def _run(cls, args, formatter, cfg, db_path, novel_id):
        snap_path = args.snapshot
        if not Path(snap_path).exists():
            formatter.error(f"快照文件不存在: {snap_path}")
            return

        async with get_uow(db_path) as uow:
            snap = JSONSnapshot(uow.session)
            restored_id = await snap.restore(snap_path)
            await uow.commit()

        formatter.success(
            f"快照已恢复: {snap_path}",
            data={"novel_id": str(restored_id)},
        )


run = RestoreCommand.execute
