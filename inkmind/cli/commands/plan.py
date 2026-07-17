"""inkmind plan [N] — 规划前 N 章大纲。"""

from __future__ import annotations

from uuid import UUID

from inkmind.cli.base_command import BaseCommand
from inkmind.cli.db import get_uow
from inkmind.models.agent import PipelineState

COMMAND = "plan"
HELP = "规划前 N 章大纲（1 × Planner 规划）"
USAGE = "inkmind plan [N] [--db PATH] [--novel-id UUID]"


def setup(subparsers) -> None:
    parser = subparsers.add_parser(COMMAND, help=HELP, usage=USAGE)
    parser.add_argument("count", type=int, nargs="?", default=5, help="待规划的章节数")
    parser.add_argument("--novel-id", type=str, default="", help="小说 ID")
    parser.add_argument("--db", type=str, default="", help="数据库路径（覆盖配置）")


class PlanCommand(BaseCommand):
    """规划章节命令。"""

    @classmethod
    async def _run(cls, args, formatter, cfg, db_path, novel_id):
        if novel_id is None:
            formatter.error(
                "未指定 novel_id。请通过 --novel-id 参数、inkmind.toml 的 project.novel_id "
                "或 INKMIND_NOVEL_ID 环境变量设置。"
            )
            return

        async with get_uow(db_path) as uow:
            pipeline = await uow.pipelines.get_by_novel(novel_id)

            if pipeline is None:
                pipeline = PipelineState(
                    novel_id=novel_id,
                    total_chapters=args.count,
                    chapters={},
                    current_chapter_index=0,
                    iteration=0,
                    max_iterations=3,
                )
            else:
                pipeline.total_chapters = args.count
                pipeline.current_chapter_index = 0

            await uow.pipelines.save(pipeline)

        formatter.success(
            f"已规划前 {args.count} 章",
            data={"planned_count": args.count, "total_chapters": args.count},
        )


run = PlanCommand.execute
