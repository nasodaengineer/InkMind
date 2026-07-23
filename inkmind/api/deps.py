"""FastAPI 依赖注入 — 数据库会话管理。"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING

from fastapi import Depends
from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from inkmind.storage.database import DatabaseManager
from inkmind.storage.repositories import ChapterRepository, NovelRepository

if TYPE_CHECKING:
    from inkmind.storage.unit_of_work import UnitOfWork


async def get_db(request: Request) -> AsyncGenerator[AsyncSession, None]:
    """为当前请求创建一个数据库会话。

    从 app.state 取 db_path（serve 启动时设置），
    每次请求独立 DatabaseManager + session，请求结束自动清理。
    """
    db_path: str = getattr(request.app.state, "db_path", None) or ".inkmind/data.db"
    mgr = DatabaseManager(db_path)
    async with mgr.session() as session:
        await mgr.create_tables()
        yield session
    await mgr.close()


def get_novel_repo(session: AsyncSession = Depends(get_db)) -> NovelRepository:
    return NovelRepository(session)


def get_chapter_repo(session: AsyncSession = Depends(get_db)) -> ChapterRepository:
    return ChapterRepository(session)


async def get_uow(
    session: AsyncSession = Depends(get_db),
) -> UnitOfWork:
    """FastAPI 依赖注入：获取 UnitOfWork 实例。"""
    from inkmind.storage.unit_of_work import UnitOfWork as _UnitOfWork

    return _UnitOfWork(session)
