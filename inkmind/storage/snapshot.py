"""JSON 快照（备份 / 恢复 / 跨进程传输）。

JSONSnapshot 提供两个核心能力：
1. dump — 将 SQLite 中的数据导出为 JSON 文件（便携备份）
2. restore — 从 JSON 文件恢复到 SQLite

用于：
- 用户手动备份小说
- 跨进程数据传输
- git-diff 友好的结构透明格式
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from inkmind.storage.models import (
    ChapterModel,
    ChapterVersionModel,
    CharacterModel,
    MemoryArchiveModel,
    NovelModel,
    PipelineStateModel,
    WorldModel,
)


class JSONSnapshot:
    """JSON 快照管理器。"""

    def __init__(self, session: AsyncSession):
        self._session = session

    async def dump(self, novel_id: UUID, output_path: str | Path) -> Path:
        """导出指定 novel 的全部数据为 JSON 文件。

        Returns:
            输出文件的 Path
        """
        path = Path(output_path)
        novel_id_str = str(novel_id)

        # 收集数据
        novel = await self._get_first(NovelModel, uuid=novel_id_str)
        if novel is None:
            raise ValueError(f"Novel {novel_id} not found")

        chapters = await self._get_all(ChapterModel, novel_id=novel_id_str)
        chapter_versions = await self._get_all(ChapterVersionModel, novel_id=novel_id_str)
        characters = await self._get_all(CharacterModel, novel_id=novel_id_str)
        world = await self._get_first(WorldModel, novel_id=novel_id_str)
        pipeline = await self._get_first(PipelineStateModel, novel_id=novel_id_str)
        archives = await self._get_all(MemoryArchiveModel, novel_id=novel_id_str)

        snapshot = {
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "inkmind_version": "0.1.0",
            "novel_id": novel_id_str,
            "novel": self._model_to_dict(novel, exclude=["id"]),
            "chapters": [self._model_to_dict(c, exclude=["id"]) for c in chapters],
            "chapter_versions": [self._model_to_dict(v, exclude=["id"]) for v in chapter_versions],
            "characters": [self._model_to_dict(c, exclude=["id"]) for c in characters],
            "world": (self._model_to_dict(world, exclude=["id"]) if world else None),
            "pipeline_state": (self._model_to_dict(pipeline, exclude=["id"]) if pipeline else None),
            "memory_archives": [self._model_to_dict(a, exclude=["id"]) for a in archives],
        }

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(snapshot, indent=2, default=str, ensure_ascii=False),
            encoding="utf-8",
        )
        return path

    async def restore(self, input_path: str | Path) -> UUID:
        """从 JSON 文件恢复数据。

        Returns:
            恢复的 novel_id
        """
        path = Path(input_path)
        data = json.loads(path.read_text(encoding="utf-8"))

        novel_id_str = data["novel_id"]
        novel_id = UUID(novel_id_str)

        # 恢复 novel
        novel_data = data["novel"]
        novel_data["uuid"] = novel_id_str
        await self._session.execute(
            text(
                """
                INSERT OR REPLACE INTO novels
                (uuid, title, description, status, word_count, chapter_count, created_at, updated_at,
                 is_deleted, deleted_at)
                VALUES (:uuid, :title, :description, :status, :word_count, :chapter_count, :created_at, :updated_at,
                 :is_deleted, :deleted_at)
                """
            ),
            {
                "uuid": novel_id_str,
                "title": novel_data.get("title", ""),
                "description": novel_data.get("description", ""),
                "status": novel_data.get("status", "draft"),
                "word_count": novel_data.get("word_count", 0),
                "chapter_count": novel_data.get("chapter_count", 0),
                "created_at": novel_data.get("created_at"),
                "updated_at": novel_data.get("updated_at"),
                "is_deleted": novel_data.get("is_deleted", False),
                "deleted_at": novel_data.get("deleted_at"),
            },
        )

        # 恢复 chapters
        for ch_data in data.get("chapters", []):
            await self._session.execute(
                text(
                    """
                    INSERT OR REPLACE INTO chapters
                    (uuid, novel_id, chapter_index, title, content, status, summary, key_events,
                     source_trace, outline_id, version, is_baseline, created_at, updated_at,
                     is_deleted, deleted_at, volume_id, rhythm_marker, pov, involved)
                    VALUES (:uuid, :novel_id, :chapter_index, :title, :content, :status, :summary,
                     :key_events, :source_trace, :outline_id, :version, :is_baseline, :created_at, :updated_at,
                     :is_deleted, :deleted_at, :volume_id, :rhythm_marker, :pov, :involved)
                    """
                ),
                {
                    "uuid": ch_data["uuid"],
                    "novel_id": novel_id_str,
                    "chapter_index": ch_data["chapter_index"],
                    "title": ch_data["title"],
                    "content": ch_data.get("content", ""),
                    "status": ch_data.get("status", "planned"),
                    "summary": ch_data.get("summary", ""),
                    "key_events": json.dumps(ch_data.get("key_events", [])),
                    "source_trace": ch_data.get("source_trace", ""),
                    "outline_id": ch_data.get("outline_id"),
                    "version": ch_data.get("version", 1),
                    "is_baseline": ch_data.get("is_baseline", False),
                    "created_at": ch_data.get("created_at"),
                    "updated_at": ch_data.get("updated_at"),
                    "is_deleted": ch_data.get("is_deleted", False),
                    "deleted_at": ch_data.get("deleted_at"),
                    "volume_id": ch_data.get("volume_id"),
                    "rhythm_marker": ch_data.get("rhythm_marker"),
                    "pov": ch_data.get("pov", ""),
                    "involved": json.dumps(ch_data.get("involved", [])),
                },
            )

        # 恢复 chapter_versions
        for cv_data in data.get("chapter_versions", []):
            await self._session.execute(
                text(
                    """
                    INSERT OR REPLACE INTO chapter_versions
                    (uuid, chapter_id, novel_id, version, chapter_index, title, content, summary,
                     key_events, source_trace, is_baseline, content_digest, created_at)
                    VALUES (:uuid, :chapter_id, :novel_id, :version, :chapter_index, :title, :content, :summary,
                     :key_events, :source_trace, :is_baseline, :content_digest, :created_at)
                    """
                ),
                {
                    "uuid": cv_data["uuid"],
                    "chapter_id": cv_data["chapter_id"],
                    "novel_id": novel_id_str,
                    "version": cv_data["version"],
                    "chapter_index": cv_data["chapter_index"],
                    "title": cv_data["title"],
                    "content": cv_data["content"],
                    "summary": cv_data.get("summary", ""),
                    "key_events": json.dumps(cv_data.get("key_events", [])),
                    "source_trace": cv_data.get("source_trace", ""),
                    "is_baseline": cv_data.get("is_baseline", False),
                    "content_digest": cv_data.get("content_digest", ""),
                    "created_at": cv_data.get("created_at"),
                },
            )

        return novel_id

    def _model_to_dict(self, model, exclude: list[str] | None = None) -> dict:
        """将 ORM 模型转为普通 dict。"""
        exclude = exclude or []
        result = {}
        for col in model.__table__.columns:
            if col.name not in exclude:
                val = getattr(model, col.name)
                if isinstance(val, datetime):
                    val = val.isoformat()
                result[col.name] = val
        return result

    async def _get_first(self, model_cls, **filters):
        result = await self._session.execute(select(model_cls).filter_by(**filters))
        return result.scalar_one_or_none()

    async def _get_all(self, model_cls, **filters):
        result = await self._session.execute(select(model_cls).filter_by(**filters))
        return result.scalars().all()
