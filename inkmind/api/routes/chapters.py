"""章节端点：CRUD + 定稿 + 版本历史（Issue #38）。"""

from __future__ import annotations

import difflib
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from inkmind.api.deps import get_db
from inkmind.models.agent import ChapterStatus
from inkmind.storage.repositories import ChapterRepository
from inkmind.storage.unit_of_work import UnitOfWork

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


class FinalizeResponse(BaseModel):
    id: str
    index: int
    title: str
    status: str
    version: int
    word_count: int


class VersionItem(BaseModel):
    id: str
    version: int
    title: str
    content: str
    source_trace: str
    is_baseline: bool
    created_at: str
    word_count: int


class VersionListResponse(BaseModel):
    versions: list[VersionItem]
    current_version: int


class DiffLine(BaseModel):
    tag: str
    text: str


class VersionDiffResponse(BaseModel):
    from_version: int
    to_version: int
    paragraphs: list[list[DiffLine]]


# ── 内容编辑白名单 ──────────────────────────────────

_CONTENT_EDITABLE_STATUSES = {ChapterStatus.AWAITING_HUMAN}


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
    """更新章节（状态/内容/标题）。

    内容编辑白名单：仅 AWAITING_HUMAN 状态允许修改 content，
    其余状态返回 409 chapter_not_editable。
    """
    repo = ChapterRepository(session)
    chapter = await repo.get_by_novel_and_index(novel_id, chapter_index)
    if chapter is None:
        raise HTTPException(status_code=404, detail="章节不存在")

    if body.content is not None:
        status = (
            chapter.status
            if isinstance(chapter.status, ChapterStatus)
            else ChapterStatus(chapter.status)
        )
        if status not in _CONTENT_EDITABLE_STATUSES:
            raise HTTPException(
                status_code=409,
                detail="chapter_not_editable",
            )

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


@router.post("/api/novels/{novel_id}/chapters/{chapter_index}/finalize")
async def finalize_chapter(
    novel_id: UUID,
    chapter_index: int,
    session: AsyncSession = Depends(get_db),
) -> FinalizeResponse:
    """定稿：AWAITING_HUMAN → FINALIZED + 触发记忆链路（L0/L1/L2）。

    仅 AWAITING_HUMAN 状态可定稿，其余返回 409。
    """
    uow = UnitOfWork(session)
    assert uow.chapters is not None

    chapter = await uow.chapters.get_by_novel_and_index(novel_id, chapter_index)
    if chapter is None:
        raise HTTPException(status_code=404, detail="章节不存在")

    status = (
        chapter.status
        if isinstance(chapter.status, ChapterStatus)
        else ChapterStatus(chapter.status)
    )
    if status != ChapterStatus.AWAITING_HUMAN:
        raise HTTPException(
            status_code=409,
            detail="chapter_not_editable",
        )

    await uow.t13_human_finalize(novel_id, chapter_index)
    await uow.commit()

    updated = await uow.chapters.get_by_novel_and_index(novel_id, chapter_index)
    if updated is None:
        raise HTTPException(status_code=500, detail="定稿后读取章节失败")

    return FinalizeResponse(
        id=str(updated.id),
        index=updated.index,
        title=updated.title,
        status=updated.status.value if hasattr(updated.status, "value") else str(updated.status),
        version=updated.version,
        word_count=len(updated.content),
    )


@router.get("/api/novels/{novel_id}/chapters/{chapter_id}/versions")
async def list_versions(
    novel_id: UUID,
    chapter_id: UUID,
    session: AsyncSession = Depends(get_db),
) -> VersionListResponse:
    """获取章节历史版本列表（降序）。"""
    repo = ChapterRepository(session)
    chapter = await repo.get_by_id(chapter_id)
    if chapter is None:
        raise HTTPException(status_code=404, detail="章节不存在")

    versions = await repo.get_versions(chapter_id)
    return VersionListResponse(
        versions=[
            VersionItem(
                id=str(v.id),
                version=v.version,
                title=v.title,
                content=v.content,
                source_trace=v.source_trace,
                is_baseline=v.is_baseline,
                created_at=v.created_at.isoformat(),
                word_count=len(v.content),
            )
            for v in versions
        ],
        current_version=chapter.version,
    )


@router.get("/api/novels/{novel_id}/chapters/{chapter_id}/versions/diff")
async def diff_versions(
    novel_id: UUID,
    chapter_id: UUID,
    from_version: int,
    to_version: int,
    session: AsyncSession = Depends(get_db),
) -> VersionDiffResponse:
    """段落对齐的字级 diff。

    将两个版本的正文按段落拆分，逐段做字级 diff。
    """
    repo = ChapterRepository(session)
    chapter = await repo.get_by_id(chapter_id)
    if chapter is None:
        raise HTTPException(status_code=404, detail="章节不存在")

    versions = await repo.get_versions(chapter_id)
    content_map: dict[int, str] = {v.version: v.content for v in versions}
    content_map[chapter.version] = chapter.content

    if from_version not in content_map:
        raise HTTPException(status_code=404, detail=f"版本 {from_version} 不存在")
    if to_version not in content_map:
        raise HTTPException(status_code=404, detail=f"版本 {to_version} 不存在")

    from_content = content_map[from_version]
    to_content = content_map[to_version]

    paragraphs = _compute_paragraph_diff(from_content, to_content)

    return VersionDiffResponse(
        from_version=from_version,
        to_version=to_version,
        paragraphs=paragraphs,
    )


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


# ── Helpers ─────────────────────────────────────────


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


def _compute_paragraph_diff(from_text: str, to_text: str) -> list[list[DiffLine]]:
    """按段落对齐，逐段做字级 diff。"""
    from_paras = [p for p in from_text.split("\n") if p.strip()]
    to_paras = [p for p in to_text.split("\n") if p.strip()]

    matcher = difflib.SequenceMatcher(None, from_paras, to_paras)
    result: list[list[DiffLine]] = []

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for para in from_paras[i1:i2]:
                result.append([DiffLine(tag="equal", text=para)])
        elif tag == "replace":
            old_group = from_paras[i1:i2]
            new_group = to_paras[j1:j2]
            for para in old_group:
                result.append([DiffLine(tag="delete", text=para)])
            for para in new_group:
                result.append([DiffLine(tag="insert", text=para)])
        elif tag == "delete":
            for para in from_paras[i1:i2]:
                result.append([DiffLine(tag="delete", text=para)])
        elif tag == "insert":
            for para in to_paras[j1:j2]:
                result.append([DiffLine(tag="insert", text=para)])

    return result
