"""数据库连接辅助 —— 为 CLI 命令提供统一的 async session 管理。"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession

from inkmind.storage.database import DatabaseManager
from inkmind.storage.unit_of_work import UnitOfWork


@asynccontextmanager
async def get_session(db_path: str) -> AsyncGenerator[AsyncSession, None]:
    """获取一个 async session。自动初始化表结构。"""
    mgr = DatabaseManager(db_path)
    async with mgr.session() as session:
        await mgr.create_tables()
        yield session
    await mgr.close()


@asynccontextmanager
async def get_uow(db_path: str, timeout: float = 5.0) -> AsyncGenerator[UnitOfWork, None]:
    """获取 UnitOfWork 实例。

    保持 session/repos 可用的同时接入 FileLock（ADR-0011 §11-C）：
    写事务 ``uow.commit()`` 在 ``{db_path}.lock`` 文件锁保护下序列化。
    """
    async with get_session(db_path) as session:
        yield UnitOfWork(session, db_path=db_path, timeout=timeout)


def db_path_from_config(cfg) -> str:
    """从 CLIConfig 获取 db_path，确保目录存在。"""
    import os
    from pathlib import Path

    path = Path(cfg.db_path)
    os.makedirs(str(path.parent), exist_ok=True)
    return cfg.db_path
