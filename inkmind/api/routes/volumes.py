"""卷管理 API。

前缀: /api/novels/{novel_id}/volumes
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from inkmind.api.dependencies import get_uow
from inkmind.models.novel import Volume
from inkmind.storage.unit_of_work import UnitOfWork

router = APIRouter(
    prefix="/api/novels/{novel_id}/volumes",
    tags=["volumes"],
)


# ── 请求/响应模型 ──


class VolumeCreate(BaseModel):
    """创建卷请求。"""

    title: str = Field(min_length=1, max_length=200)
    planned_size: int = Field(default=10, ge=1, le=200)
    stage_goal: str = Field(default="")
    main_line: str = Field(default="")
    side_line: str = Field(default="")
    volume_cliffhanger: str = Field(default="")


class VolumeUpdate(BaseModel):
    """更新卷请求。"""

    title: str | None = None
    stage_goal: str | None = None
    main_line: str | None = None
    side_line: str | None = None
    volume_cliffhanger: str | None = None
    planned_size: int | None = Field(default=None, ge=1, le=200)


class VolumeResponse(BaseModel):
    """卷响应。"""

    id: str
    novel_id: str
    volume_index: int
    title: str
    stage_goal: str
    main_line: str
    side_line: str
    volume_cliffhanger: str
    planned_size: int
    chapter_count: int = 0
    start_index: int = 1
    end_index: int = 10
    created_at: str
    updated_at: str


class ChapterSpineItem(BaseModel):
    """章节点（书脊树用）。"""

    id: str
    chapter_index: int
    title: str
    status: str
    summary: str
    rhythm_marker: str | None = None
    pov: str = ""
    involved: list[str] = []
    foreshadowing_count: int = 0


class VolumeSpineResponse(BaseModel):
    """卷书脊树响应（含章节点）。"""

    volume: VolumeResponse
    chapters: list[ChapterSpineItem]


def _volume_to_response(
    v: Volume, chapter_count: int = 0, start_index: int = 1, end_index: int = 10
) -> VolumeResponse:
    return VolumeResponse(
        id=str(v.id),
        novel_id=str(v.novel_id),
        volume_index=v.volume_index,
        title=v.title,
        stage_goal=v.stage_goal,
        main_line=v.main_line,
        side_line=v.side_line,
        volume_cliffhanger=v.volume_cliffhanger,
        planned_size=v.planned_size,
        chapter_count=chapter_count,
        start_index=start_index,
        end_index=end_index,
        created_at=v.created_at.isoformat() if v.created_at else "",
        updated_at=v.updated_at.isoformat() if v.updated_at else "",
    )


# ── 端点 ──


@router.get("")
async def list_volumes(
    novel_id: UUID,
    uow: UnitOfWork = Depends(get_uow),
) -> list[VolumeResponse]:
    """列出小说的所有卷（含派生章节区间）。"""
    assert uow.volumes is not None
    assert uow.chapters is not None

    volumes = await uow.volumes.get_by_novel(novel_id)
    result = []
    cursor = 1
    for v in volumes:
        count = await uow.chapters.count_by_volume(v.id)
        start = cursor
        end = cursor + v.planned_size - 1
        result.append(_volume_to_response(v, count, start, end))
        cursor = end + 1
    return result


@router.post("", status_code=201)
async def create_volume(
    novel_id: UUID,
    body: VolumeCreate,
    uow: UnitOfWork = Depends(get_uow),
) -> VolumeResponse:
    """创建新卷（尾部追加）。"""
    assert uow.volumes is not None

    next_index = await uow.volumes.get_next_index(novel_id)
    volume = Volume(
        novel_id=novel_id,
        volume_index=next_index,
        title=body.title,
        stage_goal=body.stage_goal,
        main_line=body.main_line,
        side_line=body.side_line,
        volume_cliffhanger=body.volume_cliffhanger,
        planned_size=body.planned_size,
    )
    await uow.volumes.save(volume)
    await uow.commit()
    return _volume_to_response(volume)


@router.get("/{volume_index}")
async def get_volume(
    novel_id: UUID,
    volume_index: int,
    uow: UnitOfWork = Depends(get_uow),
) -> VolumeResponse:
    """获取单卷详情。"""
    assert uow.volumes is not None
    assert uow.chapters is not None

    volume = await uow.volumes.get_by_novel_and_index(novel_id, volume_index)
    if volume is None:
        raise HTTPException(status_code=404, detail="卷不存在")
    count = await uow.chapters.count_by_volume(volume.id)
    return _volume_to_response(volume, count)


@router.patch("/{volume_index}")
async def update_volume(
    novel_id: UUID,
    volume_index: int,
    body: VolumeUpdate,
    uow: UnitOfWork = Depends(get_uow),
) -> VolumeResponse:
    """编辑卷纲字段。

    planned_size 调小须不小于已排章数。
    """
    assert uow.volumes is not None
    assert uow.chapters is not None

    volume = await uow.volumes.get_by_novel_and_index(novel_id, volume_index)
    if volume is None:
        raise HTTPException(status_code=404, detail="卷不存在")

    if body.title is not None:
        volume.title = body.title
    if body.stage_goal is not None:
        volume.stage_goal = body.stage_goal
    if body.main_line is not None:
        volume.main_line = body.main_line
    if body.side_line is not None:
        volume.side_line = body.side_line
    if body.volume_cliffhanger is not None:
        volume.volume_cliffhanger = body.volume_cliffhanger
    if body.planned_size is not None:
        existing_count = await uow.chapters.count_by_volume(volume.id)
        if body.planned_size < existing_count:
            raise HTTPException(
                status_code=400,
                detail=f"planned_size({body.planned_size}) 不得小于已有章数({existing_count})",
            )
        volume.planned_size = body.planned_size

    await uow.volumes.save(volume)
    await uow.commit()

    count = await uow.chapters.count_by_volume(volume.id)
    return _volume_to_response(volume, count)


@router.delete("/{volume_index}", status_code=204)
async def delete_volume(
    novel_id: UUID,
    volume_index: int,
    uow: UnitOfWork = Depends(get_uow),
) -> None:
    """删除卷。仅空卷可删，非空返回 409。"""
    assert uow.volumes is not None
    assert uow.chapters is not None

    volume = await uow.volumes.get_by_novel_and_index(novel_id, volume_index)
    if volume is None:
        raise HTTPException(status_code=404, detail="卷不存在")

    chapter_count = await uow.chapters.count_by_volume(volume.id)
    if chapter_count > 0:
        raise HTTPException(
            status_code=409,
            detail=f"卷非空（含 {chapter_count} 章），无法删除",
        )

    await uow.volumes.delete(volume.id)
    await uow.commit()


@router.get("/{volume_index}/spines")
async def get_volume_spines(
    novel_id: UUID,
    volume_index: int,
    uow: UnitOfWork = Depends(get_uow),
) -> VolumeSpineResponse:
    """获取卷书脊树（含章 + 状态 + 节奏标记 + L1 伏笔徽标）。"""
    assert uow.volumes is not None
    assert uow.chapters is not None

    volume = await uow.volumes.get_by_novel_and_index(novel_id, volume_index)
    if volume is None:
        raise HTTPException(status_code=404, detail="卷不存在")

    chapters = await uow.chapters.get_chapters_by_volume(novel_id, volume.id)

    # L1 派生伏笔徽标
    from collections import Counter

    from sqlalchemy import select

    from inkmind.storage.models import MemoryArchiveModel

    foreshadow_counts: Counter[int] = Counter()
    result = await uow.session.execute(
        select(MemoryArchiveModel).where(
            MemoryArchiveModel.novel_id == str(novel_id),
            MemoryArchiveModel.tier == "l1_active",
        )
    )
    archive = result.scalar_one_or_none()
    if archive and archive.data:
        window = archive.data.get("sliding_window", {})
        for marker in window.get("pending_foreshadowing", []):
            if not marker.get("is_resolved", False):
                planted = marker.get("planted_chapter")
                if planted is not None:
                    foreshadow_counts[planted] += 1

    chapter_items = [
        ChapterSpineItem(
            id=str(ch.id),
            chapter_index=ch.index,
            title=ch.title,
            status=ch.status.value,
            summary=ch.summary,
            rhythm_marker=ch.rhythm_marker,
            pov=ch.pov,
            involved=ch.involved,
            foreshadowing_count=foreshadow_counts.get(ch.index, 0),
        )
        for ch in chapters
    ]

    return VolumeSpineResponse(
        volume=_volume_to_response(volume, len(chapters)),
        chapters=chapter_items,
    )
