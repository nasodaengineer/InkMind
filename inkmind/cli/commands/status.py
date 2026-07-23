"""inkmind status — 显示小说/章节当前状态。"""

from __future__ import annotations


from sqlalchemy import func, select

from inkmind.cli.base_command import BaseCommand
from inkmind.cli.db import get_session
from inkmind.storage.models import ChapterModel, NovelModel, PipelineStateModel

COMMAND = "status"
HELP = "显示小说/章节当前状态"
USAGE = "inkmind status [--db PATH] [--novel-id UUID]"


def setup(subparsers) -> None:
    parser = subparsers.add_parser(COMMAND, help=HELP, usage=USAGE)
    parser.add_argument("--novel-id", type=str, default="", help="小说 ID")
    parser.add_argument("--db", type=str, default="", help="数据库路径（覆盖配置）")
    parser.add_argument("--verbose", action="store_true", help="显示详细章节列表")


class StatusCommand(BaseCommand):
    """显示小说/章节当前状态。"""

    @classmethod
    async def _run(cls, args, formatter, cfg, db_path, novel_id):
        if novel_id is None:
            formatter.error(
                "未指定 novel_id。请通过 --novel-id 参数、inkmind.toml 的 project.novel_id "
                "或 INKMIND_NOVEL_ID 环境变量设置。"
            )
            return

        async with get_session(db_path) as session:
            result = await session.execute(
                select(NovelModel).where(NovelModel.uuid == str(novel_id))
            )
            novel = result.scalar_one_or_none()
            if novel is None:
                formatter.error(f"小说 {novel_id} 不存在")
                return

            result = await session.execute(
                select(PipelineStateModel).where(PipelineStateModel.novel_id == str(novel_id))
            )
            result.scalar_one_or_none()

            result = await session.execute(
                select(
                    ChapterModel.status,
                    func.count(ChapterModel.uuid),
                )
                .where(ChapterModel.novel_id == str(novel_id))
                .group_by(ChapterModel.status)
            )
            status_counts = dict(result.all())

            result = await session.execute(
                select(ChapterModel)
                .where(ChapterModel.novel_id == str(novel_id))
                .order_by(ChapterModel.chapter_index)
            )
            chapters = list(result.scalars().all())

            total = len(chapters)
            finalized = status_counts.get("finalized", 0)

        data = {
            "novel_id": str(novel_id),
            "title": novel.title,
            "description": novel.description,
            "status": novel.status,
            "chapters": {
                "total": total,
                "finalized": finalized,
                "by_status": dict(status_counts),
            },
        }

        if args.verbose:
            data["chapter_list"] = [
                {
                    "index": c.chapter_index,
                    "title": c.title,
                    "status": c.status,
                    "version": c.version,
                    "is_baseline": c.is_baseline,
                    "word_count": len(c.content),
                }
                for c in chapters
            ]

        formatter.print_dict(
            data,
            text_fn=lambda d: _format_text(d, args.verbose, chapters),
        )


def _format_text(data: dict, verbose: bool, chapters: list) -> str:
    lines = []
    lines.append(f"📖 {data['title']}")
    if data["description"]:
        lines.append(f"   {data['description']}")
    lines.append(f"  ├ 状态: {data['status']}")
    lines.append(f"  ├ 章节: {data['chapters']['total']} 总 / {data['chapters']['finalized']} 定稿")
    if data["chapters"]["by_status"]:
        status_parts = []
        for s, c in sorted(data["chapters"]["by_status"].items()):
            emoji = {
                "planned": "📋",
                "writing": "✍️",
                "draft_ready": "📄",
                "reviewing": "🔍",
                "approved": "✅",
                "finalized": "⭐",
                "revising": "🔄",
            }.get(s, "❓")
            status_parts.append(f"{emoji} {s}: {c}")
        sp = " | ".join(status_parts)
        lines.append(f"  └ 状态分布: {sp}")
    if verbose and chapters:
        lines.append("")
        lines.append("📑 章节列表:")
        for c in chapters:
            emoji = {
                "planned": "📋",
                "writing": "✍️",
                "draft_ready": "📄",
                "reviewing": "🔍",
                "approved": "✅",
                "finalized": "⭐",
                "revising": "🔄",
            }.get(c.status, "❓")
            marker = "★" if c.is_baseline else " "
            wc = f"（{len(c.content)} 字）" if c.content else ""
            lines.append(f"  {c.chapter_index:>3}. {emoji} {c.title} [{c.status}] {marker} {wc}")
    return "\n".join(lines)


run = StatusCommand.execute
