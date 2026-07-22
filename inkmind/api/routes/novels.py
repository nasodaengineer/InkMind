"""小说 CRUD 端点。"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from inkmind.api.deps import get_db
from inkmind.models.novel import Novel, NovelMetadata
from inkmind.storage.repositories import NovelRepository

router = APIRouter(prefix="/api/novels", tags=["novels"])


# ── 请求/响应模型 ──


class CreateNovelRequest(BaseModel):
    title: str


class NovelResponse(BaseModel):
    id: str
    title: str
    metadata: NovelMetadata
    created_at: str
    updated_at: str


def _novel_to_response(novel: Novel) -> NovelResponse:
    return NovelResponse(
        id=str(novel.id),
        title=novel.title,
        metadata=novel.metadata,
        created_at=novel.created_at.isoformat(),
        updated_at=novel.updated_at.isoformat(),
    )


# ── 端点 ──


@router.get("")
async def list_novels(session: AsyncSession = Depends(get_db)) -> list[NovelResponse]:
    """获取所有小说列表。"""
    repo = NovelRepository(session)
    novels = await repo.get_all()
    return [_novel_to_response(n) for n in novels]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_novel(
    body: CreateNovelRequest,
    session: AsyncSession = Depends(get_db),
) -> NovelResponse:
    """创建新小说。"""
    from datetime import datetime, timezone

    novel = Novel(
        title=body.title,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    repo = NovelRepository(session)
    await repo.save(novel)
    await session.commit()
    return _novel_to_response(novel)


@router.get("/{novel_id}")
async def get_novel(
    novel_id: UUID,
    session: AsyncSession = Depends(get_db),
) -> NovelResponse:
    """获取单部小说详情。"""
    repo = NovelRepository(session)
    novel = await repo.get_by_id(novel_id)
    if novel is None:
        raise HTTPException(status_code=404, detail="novel not found")
    return _novel_to_response(novel)


@router.delete("/{novel_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_novel(
    novel_id: UUID,
    session: AsyncSession = Depends(get_db),
) -> None:
    """删除小说。"""
    repo = NovelRepository(session)
    deleted = await repo.delete(novel_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="novel not found")
    await session.commit()
