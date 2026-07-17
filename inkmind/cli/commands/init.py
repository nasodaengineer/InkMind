"""inkmind init — 初始化新小说。"""

from __future__ import annotations

import asyncio

from inkmind.cli.base_command import BaseCommand
from inkmind.cli.db import get_uow
from inkmind.cli.formatter import OutputFormatter
from inkmind.models.novel import Novel, NovelMetadata

COMMAND = "init"
HELP = "初始化新小说（交互式填写元数据）"
USAGE = "inkmind init [--title TITLE] [--description DESC]"


def setup(subparsers) -> None:
    parser = subparsers.add_parser(COMMAND, help=HELP, usage=USAGE)
    parser.add_argument("--title", type=str, default="", help="小说标题")
    parser.add_argument("--description", type=str, default="", help="小说简介")
    parser.add_argument("--db", type=str, default="", help="数据库路径（覆盖配置）")


class InitCommand(BaseCommand):
    """初始化新小说命令。"""

    @classmethod
    def execute(cls, args) -> None:
        formatter = OutputFormatter(json_mode=getattr(args, "json", False))
        db_path = args.db or ".inkmind/data.db"

        title = args.title
        if not title:
            try:
                title = input("📖 小说标题: ").strip()
            except EOFError:
                title = ""
            if not title:
                formatter.error("标题不能为空")
                return

        description = args.description
        if not description:
            try:
                # 仅在交互式终端时尝试输入
                import sys as _sys
                if _sys.stdin.isatty():
                    desc_input = input("📝 小说简介（可选）: ").strip()
                    if desc_input:
                        description = desc_input
            except (EOFError, OSError):
                pass

        asyncio.run(cls._do_init(db_path, title, description, formatter))

    @classmethod
    async def _do_init(cls, db_path, title, description, formatter):
        async with get_uow(db_path) as uow:
            novel = Novel(
                title=title,
                metadata=NovelMetadata(description=description),
            )
            await uow.novels.save(novel)
            await uow.commit()

        formatter.success(
            f"小说「{title}」已创建",
            data={
                "novel_id": str(novel.id),
                "title": title,
                "description": description,
                "db_path": db_path,
            },
        )
        formatter.info(f"小说 ID: {novel.id}")
        formatter.info("请使用 --novel-id 或设置 inkmind.toml 的 project.novel_id 绑定此小说")


run = InitCommand.execute
