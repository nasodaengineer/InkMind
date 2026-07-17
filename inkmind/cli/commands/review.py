"""inkmind review — 对当前最新章节启动 1 × Editor 评审。"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select

from inkmind.cli.base_command import BaseCommand
from inkmind.cli.db import get_uow
from inkmind.storage.models import ChapterModel

COMMAND = "review"
HELP = "对当前最新章节启动 1 × Editor 评审"
USAGE = "inkmind review [--db PATH] [--novel-id UUID]"


def setup(subparsers) -> None:
    parser = subparsers.add_parser(COMMAND, help=HELP, usage=USAGE)
    parser.add_argument("--novel-id", type=str, default="", help="小说 ID")
    parser.add_argument("--db", type=str, default="", help="数据库路径（覆盖配置）")


class ReviewCommand(BaseCommand):
    """评审章节命令。"""

    @classmethod
    async def _run(cls, args, formatter, cfg, db_path, novel_id):
        if novel_id is None:
            formatter.error(
                "未指定 novel_id。请通过 --novel-id 参数、inkmind.toml 的 project.novel_id "
                "或 INKMIND_NOVEL_ID 环境变量设置。"
            )
            return

        async with get_uow(db_path) as uow:
            session = uow.session
            result = await session.execute(
                select(ChapterModel)
                .where(
                    ChapterModel.novel_id == str(novel_id),
                    ChapterModel.status.in_(["draft_ready", "writing"]),
                )
                .order_by(ChapterModel.chapter_index.desc())
                .limit(1)
            )
            chapter = result.scalar_one_or_none()

            if chapter is None:
                result = await session.execute(
                    select(ChapterModel)
                    .where(ChapterModel.novel_id == str(novel_id))
                    .order_by(ChapterModel.chapter_index.desc())
                    .limit(1)
                )
                chapter = result.scalar_one_or_none()

            if chapter is None:
                formatter.error("没有找到可评审的章节")
                return

            await uow.t3_editor_complete_review(
                novel_id=novel_id,
                chapter_index=chapter.chapter_index,
                is_approved=True,
            )
            await uow.commit()

        formatter.success(
            f"章节「{chapter.title}」（第 {chapter.chapter_index} 章）评审通过",
            data={
                "chapter_index": chapter.chapter_index,
                "chapter_title": chapter.title,
                "status": "approved",
            },
        )


run = ReviewCommand.execute
