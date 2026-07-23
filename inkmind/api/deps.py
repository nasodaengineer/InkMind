"""FastAPI 依赖注入 — 数据库会话管理。"""

from __future__ import annotations

from collections.abc import AsyncGenerator

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from inkmind.storage.database import DatabaseManager


async def get_db(request: Request) -> AsyncGenerator[AsyncSession, None]:
    db_path: str = getattr(request.app.state, "db_path", None) or ".inkmind/data.db"
    mgr = DatabaseManager(db_path)
    async with mgr.session() as session:
        await mgr.create_tables()
        yield session
    await mgr.close()
