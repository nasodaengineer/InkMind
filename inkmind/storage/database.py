"""SQLAlchemy 引擎与会话管理。

管理 SQLite 数据库连接池、会话工厂和生命周期。
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator, AsyncIterator
from uuid import uuid4

from sqlalchemy import text
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
            db_url = f"sqlite+aiosqlite:///{path}" if not path.startswith("sqlite") else path
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
        await self._migrate_schema()

    async def drop_tables(self) -> None:
        """删除所有 ORM 表（测试用）。"""
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)

    async def _migrate_schema(self) -> None:
        """既有库迁移：加列、建表、回填旧章节到默认卷。

        Issue #35: 检测 chapters 无 volume_id 列则 ALTER TABLE 加列；
        volumes / outline_spines 表由 create_tables 自动创建；
        创建默认卷回填所有旧章节的 volume_id。
        Issue #44: 建 FTS5 虚拟表 fragments_fts 用于素材全文搜索。
        """
        async with self._engine.begin() as conn:
            # 1. 检测并添加 chapters 新列
            cols_result = await conn.execute(text("PRAGMA table_info('chapters')"))
            existing_cols = {row[1] for row in cols_result.fetchall()}

            new_cols: dict[str, str] = {
                "volume_id": "VARCHAR(36) REFERENCES volumes(uuid)",
                "rhythm_marker": "VARCHAR(20)",
                "pov": "VARCHAR(50) NOT NULL DEFAULT ''",
                "involved": "JSON NOT NULL DEFAULT '[]'",
            }
            for col_name, col_def in new_cols.items():
                if col_name not in existing_cols:
                    await conn.execute(
                        text(f"ALTER TABLE chapters ADD COLUMN {col_name} {col_def}")
                    )

            # 2. 回填旧章节到默认卷（仅对 volume_id IS NULL 且有关联 novel 的章节）
            tbl_exists = await conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table' AND name='volumes'")
            )
            if not tbl_exists.scalar():
                return

            novels_result = await conn.execute(
                text(
                    "SELECT DISTINCT novel_id FROM chapters "
                    "WHERE volume_id IS NULL AND novel_id IS NOT NULL AND novel_id != ''"
                )
            )
            for (nid,) in novels_result.fetchall():
                # 找该小说的第一卷
                v_result = await conn.execute(
                    text(
                        "SELECT uuid FROM volumes WHERE novel_id = :nid ORDER BY volume_index LIMIT 1"
                    ),
                    {"nid": nid},
                )
                vol_row = v_result.scalar_one_or_none()
                if not vol_row:
                    vol_uuid = str(uuid4())
                    await conn.execute(
                        text(
                            "INSERT INTO volumes (uuid, novel_id, volume_index, title, "
                            "stage_goal, main_line, side_line, volume_cliffhanger, planned_size) "
                            "VALUES (:uuid, :nid, 1, '默认卷', '', '', '', '', 100)"
                        ),
                        {"uuid": vol_uuid, "nid": nid},
                    )
                else:
                    vol_uuid = vol_row

                await conn.execute(
                    text(
                        "UPDATE chapters SET volume_id = :vid "
                        "WHERE novel_id = :nid AND volume_id IS NULL"
                    ),
                    {"vid": vol_uuid, "nid": nid},
                )

            # 2b. volume_id NOT NULL 表重建（SQLite 不支持 ALTER COLUMN）
            cols_info = await conn.execute(text("PRAGMA table_info('chapters')"))
            for row in cols_info.fetchall():
                if row[1] == "volume_id" and row[3] == 0:  # notnull=0
                    await conn.execute(
                        text("""
                        CREATE TABLE chapters_new (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            uuid VARCHAR(36) NOT NULL UNIQUE,
                            novel_id VARCHAR(36) NOT NULL REFERENCES novels(uuid),
                            chapter_index INTEGER NOT NULL,
                            title VARCHAR(100) NOT NULL,
                            content TEXT DEFAULT '',
                            status VARCHAR(20) DEFAULT 'planned',
                            summary TEXT DEFAULT '',
                            key_events JSON DEFAULT '[]',
                            source_trace VARCHAR(100) DEFAULT '',
                            outline_id VARCHAR(36),
                            version INTEGER DEFAULT 1,
                            is_baseline BOOLEAN DEFAULT 0,
                            volume_id VARCHAR(36) NOT NULL REFERENCES volumes(uuid),
                            rhythm_marker VARCHAR(20),
                            pov VARCHAR(50) NOT NULL DEFAULT '',
                            involved JSON NOT NULL DEFAULT '[]',
                            created_at DATETIME DEFAULT (datetime('now')),
                            updated_at DATETIME DEFAULT (datetime('now')),
                            is_deleted BOOLEAN DEFAULT 0,
                            deleted_at DATETIME,
                            UNIQUE(novel_id, chapter_index)
                        )
                    """)
                    )
                    await conn.execute(
                        text("""
                        INSERT INTO chapters_new SELECT
                            id, uuid, novel_id, chapter_index, title, content,
                            status, summary, key_events, source_trace, outline_id,
                            version, is_baseline, volume_id, rhythm_marker, pov,
                            involved, created_at, updated_at, is_deleted, deleted_at
                        FROM chapters
                    """)
                    )
                    await conn.execute(text("DROP TABLE chapters"))
                    await conn.execute(text("ALTER TABLE chapters_new RENAME TO chapters"))
                    await conn.execute(
                        text(
                            "CREATE INDEX IF NOT EXISTS ix_chapters_novel_id ON chapters(novel_id)"
                        )
                    )
                    await conn.execute(
                        text("CREATE INDEX IF NOT EXISTS ix_chapters_status ON chapters(status)")
                    )
                    await conn.execute(
                        text(
                            "CREATE INDEX IF NOT EXISTS ix_chapters_volume_id ON chapters(volume_id)"
                        )
                    )
                    await conn.execute(
                        text(
                            "CREATE INDEX IF NOT EXISTS ix_chapters_is_deleted ON chapters(is_deleted)"
                        )
                    )
                    break

            # 3. Issue #44: 建 FTS5 虚拟表 fragments_fts（素材全文搜索）
            from inkmind.storage.search import FTS5_TABLE_SQL

            try:
                await conn.execute(text(FTS5_TABLE_SQL))
            except Exception:
                # FTS5 不可用时不阻塞
                pass

    async def migrate(self) -> None:
        """外部显式调用迁移入口。"""
        await self._migrate_schema()

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
