"""SQLAlchemy 引擎与会话管理。

管理 SQLite 数据库连接池、会话工厂和生命周期。
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator, AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool, StaticPool

from inkmind.storage.models import Base

_engine = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


class DatabaseManager:
    """数据库连接管理器。

    单例模式，管理异步 SQLAlchemy 引擎的生命周期。
    """

    def __init__(self, db_path: str | None = None):
        global _engine, _session_factory

        if _engine is not None:
            self._engine = _engine
            self._session_factory = _session_factory
            return

        path = db_path or os.getenv("INKMIND_DB", "inkmind.db")
        db_dir = Path(path).parent
        if db_dir.name:
            db_dir.mkdir(parents=True, exist_ok=True)

        # 检测 :memory: 共享内存模式（aiosqlite 每连接独立，需共享缓存）
        if path == ":memory:" or path == "file::memory:":
            db_url = "sqlite+aiosqlite:///file::memory:?cache=shared&mode=memory&uri=true"
            poolclass = StaticPool
        else:
            db_url = (
                f"sqlite+aiosqlite:///{path}"
                if not path.startswith("sqlite")
                else path
            )
            poolclass = NullPool

        _engine = create_async_engine(
            db_url,
            poolclass=poolclass,
            echo=os.getenv("INKMIND_DB_ECHO", "0") == "1",
        )
        self._engine = _engine

        _session_factory = async_sessionmaker(
            self._engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
        self._session_factory = _session_factory

    async def create_tables(self) -> None:
        """创建所有 ORM 表。"""
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def drop_tables(self) -> None:
        """删除所有 ORM 表（测试用）。"""
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        """获取一个异步会话。"""
        async with self._session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise
            finally:
                await session.close()

    async def close(self) -> None:
        """关闭引擎，释放连接。"""
        if self._engine:
            await self._engine.dispose()

    @property
    def session_factory(self) -> async_sessionmaker[AsyncSession]:
        return self._session_factory


_default_manager: DatabaseManager | None = None


def get_manager(db_path: str | None = None) -> DatabaseManager:
    """获取全局默认 DatabaseManager 实例。"""
    global _default_manager
    if _default_manager is None:
        _default_manager = DatabaseManager(db_path)
    return _default_manager


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """快捷获取异步会话（依赖注入入口）。"""
    manager = get_manager()
    async with manager.session() as session:
        yield session
