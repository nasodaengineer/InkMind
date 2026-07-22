"""章节列表端点。"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from inkmind.api.deps import get_db
from inkmind.storage.repositories import ChapterRepository

router = APIRouter(tags=["chapters"])


class ChapterItem(BaseModel):
    id: str
    index: int
    title: str
    status: str
    summary: str
    version: int
    updated_at: str
    word_count: int


@router.get("/api/novels/{novel_id}/chapters")
async def list_chapters(
    novel_id: UUID,
    session: AsyncSession = Depends(get_db),
) -> list[ChapterItem]:
    """获取某部小说的所有章节（含状态）。"""
    repo = ChapterRepository(session)
    chapters = await repo.get_chapters_by_novel(novel_id)
    return [
        ChapterItem(
            id=str(ch.id),
            index=ch.index,
            title=ch.title,
            status=ch.status.value if hasattr(ch.status, "value") else str(ch.status),
            summary=ch.summary,
            version=ch.version,
            updated_at=ch.updated_at.isoformat(),
            word_count=len(ch.content),
        )
        for ch in chapters
    ]
