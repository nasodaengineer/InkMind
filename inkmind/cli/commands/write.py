"""inkmind write <title> — 写作一章。"""

from __future__ import annotations

from uuid import uuid4

from inkmind.cli.base_command import BaseCommand
from inkmind.cli.db import get_uow
from inkmind.models.agent import ChapterStatus, PipelineState
from inkmind.models.chapter import Chapter


COMMAND = "write"
HELP = "写作新章节（1 × Writer 写作）"
USAGE = "inkmind write <章节标题> [--db PATH] [--novel-id UUID]"


def setup(subparsers) -> None:
    parser = subparsers.add_parser(COMMAND, help=HELP, usage=USAGE)
    parser.add_argument("title", type=str, help="章节标题")
    parser.add_argument("--novel-id", type=str, default="", help="小说 ID")
    parser.add_argument("--db", type=str, default="", help="数据库路径（覆盖配置）")


class WriteCommand(BaseCommand):
    """写章节命令。"""

    @classmethod
    async def _run(cls, args, formatter, cfg, db_path, novel_id):
        if novel_id is None:
            formatter.error(
                "未指定 novel_id。请通过 --novel-id 参数、inkmind.toml 的 project.novel_id "
                "或 INKMIND_NOVEL_ID 环境变量设置。"
            )
            return

        if not args.title.strip():
            formatter.error("章节标题不能为空")
            return

        async with get_uow(db_path) as uow:
            async with uow.transaction():
                # 获取 pipeline 状态，计算下一章索引
                pipeline = await uow.pipelines.get_by_novel(novel_id)
                next_index = 1
                if pipeline:
                    next_index = (pipeline.current_chapter_index or 0) + 1

                # 创建 Chapter 领域模型
                ch_uuid = uuid4()
                chapter = Chapter(
                    id=ch_uuid,
                    novel_id=novel_id,
                    index=next_index,
                    title=args.title,
                    content="",
                    status=ChapterStatus.WRITING,
                    summary="",
                    key_events=[],
                    source_trace="cli:write",
                    version=1,
                    is_baseline=False,
                )

                # 通过 T1 事务边界保存章节（自动推进状态至 DRAFT_READY）
                await uow.t1_writer_complete_chapter(chapter)

                # 更新 pipeline 状态
                if not pipeline:
                    pipeline = PipelineState(
                        novel_id=novel_id,
                        total_chapters=0,
                        chapters={},
                        current_chapter_index=None,
                        iteration=0,
                        max_iterations=3,
                    )
                else:
                    pipeline.current_chapter_index = next_index
                    pipeline.total_chapters = max(pipeline.total_chapters, next_index)

                await uow.pipelines.save(pipeline)
                await uow.commit()

        formatter.success(
            f"章节「{args.title}」（第 {next_index} 章）已创建，状态: 写作中",
            data={
                "chapter_index": next_index,
                "chapter_title": args.title,
                "chapter_id": str(ch_uuid),
                "status": "writing",
            },
        )


run = WriteCommand.execute
