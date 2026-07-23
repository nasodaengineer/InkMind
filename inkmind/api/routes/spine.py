"""总纲（书脊）API。

前缀: /api/novels/{novel_id}/spine
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from inkmind.api.dependencies import get_uow
from inkmind.models.novel import OutlineSpine
from inkmind.storage.unit_of_work import UnitOfWork

router = APIRouter(
    prefix="/api/novels/{novel_id}/spine",
    tags=["spine"],
)


class SpineUpdate(BaseModel):
    """总纲更新请求。"""

    main_line: str | None = None
    core_conflict: str | None = None
    ending: str | None = None
    selling_points: str | None = None
    world_background: str | None = None
    golden_finger: str | None = None


class SpineResponse(BaseModel):
    """总纲响应。"""

    novel_id: str
    main_line: str = ""
    core_conflict: str = ""
    ending: str = ""
    selling_points: str = ""
    world_background: str = ""
    golden_finger: str = ""
    created_at: str = ""
    updated_at: str = ""


def _spine_to_response(s: OutlineSpine) -> SpineResponse:
    return SpineResponse(
        novel_id=str(s.novel_id),
        main_line=s.main_line,
        core_conflict=s.core_conflict,
        ending=s.ending,
        selling_points=s.selling_points,
        world_background=s.world_background,
        golden_finger=s.golden_finger,
        created_at=s.created_at.isoformat() if s.created_at else "",
        updated_at=s.updated_at.isoformat() if s.updated_at else "",
    )


@router.get("")
async def get_spine(
    novel_id: UUID,
    uow: UnitOfWork = Depends(get_uow),
) -> SpineResponse:
    """获取总纲（懒创建：如果不存在则自动创建空总纲）。"""
    spine = await uow.spines.get_by_novel(novel_id)
    if spine is None:
        spine = OutlineSpine(novel_id=novel_id)
        await uow.spines.upsert(spine)
        await uow.commit()
    return _spine_to_response(spine)


@router.patch("")
async def update_spine(
    novel_id: UUID,
    body: SpineUpdate,
    uow: UnitOfWork = Depends(get_uow),
) -> SpineResponse:
    """更新总纲字段。"""
    spine = await uow.spines.get_by_novel(novel_id)
    if spine is None:
        spine = OutlineSpine(novel_id=novel_id)

    if body.main_line is not None:
        spine.main_line = body.main_line
    if body.core_conflict is not None:
        spine.core_conflict = body.core_conflict
    if body.ending is not None:
        spine.ending = body.ending
    if body.selling_points is not None:
        spine.selling_points = body.selling_points
    if body.world_background is not None:
        spine.world_background = body.world_background
    if body.golden_finger is not None:
        spine.golden_finger = body.golden_finger

    await uow.spines.upsert(spine)
    await uow.commit()
    return _spine_to_response(spine)
