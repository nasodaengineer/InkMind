"""章节列表端点。"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from inkmind.api.deps import get_db
from inkmind.models.agent import ChapterStatus
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


class ChapterDetail(BaseModel):
    id: str
    index: int
    title: str
    content: str
    status: str
    summary: str
    version: int
    word_count: int
    updated_at: str


class ChapterPatchRequest(BaseModel):
    title: str | None = None
    content: str | None = None
    status: str | None = None


class ChapterOutlinePatchRequest(BaseModel):
    title: str | None = None
    summary: str | None = None
    key_events: list[str] | None = None
    rhythm_marker: str | None = None
    pov: str | None = None
    involved: list[str] | None = None


class ChapterOutlineResponse(BaseModel):
    id: str
    index: int
    title: str
    status: str
    summary: str
    key_events: list[str]
    rhythm_marker: str | None
    pov: str
    involved: list[str]


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


@router.get("/api/novels/{novel_id}/chapters/{chapter_id}")
async def get_chapter(
    novel_id: UUID,
    chapter_id: UUID,
    session: AsyncSession = Depends(get_db),
) -> ChapterDetail:
    """获取单章详情（含正文内容）。"""
    repo = ChapterRepository(session)
    chapter = await repo.get_by_id(chapter_id)
    if chapter is None:
        raise HTTPException(status_code=404, detail="章节不存在")
    return _chapter_to_detail(chapter)


@router.patch("/api/novels/{novel_id}/chapters/{chapter_index}")
async def patch_chapter(
    novel_id: UUID,
    chapter_index: int,
    body: ChapterPatchRequest,
    session: AsyncSession = Depends(get_db),
) -> ChapterDetail:
    """更新章节（状态/内容/标题）。"""
    repo = ChapterRepository(session)
    chapter = await repo.get_by_novel_and_index(novel_id, chapter_index)
    if chapter is None:
        raise HTTPException(status_code=404, detail="章节不存在")

    if body.title is not None:
        chapter.title = body.title
    if body.content is not None:
        chapter.content = body.content
    if body.status is not None:
        try:
            chapter.status = ChapterStatus(body.status)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"无效的状态: {body.status}",
            )

    await repo.save(chapter)
    await session.commit()

    updated = await repo.get_by_novel_and_index(novel_id, chapter_index)
    if updated is None:
        raise HTTPException(status_code=500, detail="保存后读取章节失败")
    return _chapter_to_detail(updated)


@router.patch("/api/novels/{novel_id}/chapters/{chapter_index}/outline")
async def patch_chapter_outline(
    novel_id: UUID,
    chapter_index: int,
    body: ChapterOutlinePatchRequest,
    session: AsyncSession = Depends(get_db),
) -> ChapterOutlineResponse:
    """更新章纲字段（summary/key_events/rhythm_marker/pov/involved）。"""
    repo = ChapterRepository(session)

    fields = body.model_dump(exclude_none=True)
    if fields:
        result = await repo.patch_outline(novel_id, chapter_index, fields)
        if result is None:
            raise HTTPException(status_code=404, detail="章节不存在")

    await session.commit()

    chapter = await repo.get_by_novel_and_index(novel_id, chapter_index)
    if chapter is None:
        raise HTTPException(status_code=404, detail="章节不存在")

    return ChapterOutlineResponse(
        id=str(chapter.id),
        index=chapter.index,
        title=chapter.title,
        status=chapter.status.value if hasattr(chapter.status, "value") else str(chapter.status),
        summary=chapter.summary,
        key_events=chapter.key_events,
        rhythm_marker=chapter.rhythm_marker,
        pov=chapter.pov,
        involved=chapter.involved,
    )


def _chapter_to_detail(chapter) -> ChapterDetail:
    """将 Chapter 领域模型转换为响应。"""
    return ChapterDetail(
        id=str(chapter.id),
        index=chapter.index,
        title=chapter.title,
        content=chapter.content,
        status=chapter.status.value if hasattr(chapter.status, "value") else str(chapter.status),
        summary=chapter.summary,
        version=chapter.version,
        word_count=len(chapter.content),
        updated_at=chapter.updated_at.isoformat(),
    )
