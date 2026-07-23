"""批注 REST 端点。无单体 GET（按 issue #40 要求）。"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from inkmind.agents.prompts import serialize_annotations
from inkmind.api.deps import get_db
from inkmind.models.agent import AnnotationRef, QuoteContext
from inkmind.models.annotation import (
    AnchorFingerprint,
    Comment,
    CommentIntent,
    CommentThread,
    ThreadStatus,
    can_transition,
)
from inkmind.storage.repositories import AnnotationRepository

router = APIRouter(
    prefix="/api/novels/{novel_id}/chapters/{chapter_id}/annotations", tags=["annotations"]
)


# ── Request / Response schemas ──


class CreateAnnotationRequest(BaseModel):
    intent: CommentIntent = CommentIntent.note
    body: str = Field(min_length=1)
    anchor: AnchorFingerprint | None = None


class ThreadResponse(BaseModel):
    id: str
    chapter_id: str
    novel_id: str
    intent: str
    status: str
    anchor: dict | None
    comments: list[dict]
    created_at: str
    updated_at: str
    resolved_at: str | None


class UpdateThreadRequest(BaseModel):
    status: ThreadStatus | None = None
    anchor: AnchorFingerprint | None = None


class AddCommentRequest(BaseModel):
    body: str = Field(min_length=1)
    author: str = "user"


class RelocateRequest(BaseModel):
    thread_id: str
    anchor: AnchorFingerprint
    score: float = Field(ge=0.0, le=1.0)


class RelocateBatchRequest(BaseModel):
    items: list[RelocateRequest]


class PreviewRequest(BaseModel):
    thread_ids: list[str] = Field(min_length=1)


class PreviewResponse(BaseModel):
    serialized: str
    thread_count: int


class ConfirmRelocateItem(BaseModel):
    thread_id: str
    action: str = Field(description="confirm（确认落位）或 reject（标记 orphaned）")
    anchor: AnchorFingerprint | None = Field(default=None, description="手动重锚时提供新 anchor")


class ConfirmRelocateRequest(BaseModel):
    items: list[ConfirmRelocateItem]


class ApplyRelocationItem(BaseModel):
    thread_id: str
    anchor: AnchorFingerprint
    score: float = Field(ge=0.0, le=1.0)


class ApplyRelocationRequest(BaseModel):
    items: list[ApplyRelocationItem]


# ── Helpers ──


def _thread_to_response(t: CommentThread) -> ThreadResponse:
    return ThreadResponse(
        id=str(t.id),
        chapter_id=str(t.chapter_id),
        novel_id=str(t.novel_id),
        intent=t.intent.value,
        status=t.status.value,
        anchor=t.anchor.model_dump(mode="json") if t.anchor else None,
        comments=[
            {
                "id": str(c.id),
                "author": c.author,
                "body": c.body,
                "created_at": c.created_at.isoformat(),
            }
            for c in t.comments
        ],
        created_at=t.created_at.isoformat(),
        updated_at=t.updated_at.isoformat(),
        resolved_at=t.resolved_at.isoformat() if t.resolved_at else None,
    )


# ── Endpoints ──


@router.get("")
async def list_annotations(
    novel_id: UUID,
    chapter_id: UUID,
    include_resolved: bool = False,
    session: AsyncSession = Depends(get_db),
) -> list[ThreadResponse]:
    repo = AnnotationRepository(session)
    threads = await repo.list_threads(chapter_id, include_resolved=include_resolved)
    return [_thread_to_response(t) for t in threads]


@router.post("", status_code=201)
async def create_annotation(
    novel_id: UUID,
    chapter_id: UUID,
    req: CreateAnnotationRequest,
    session: AsyncSession = Depends(get_db),
) -> ThreadResponse:
    now = datetime.now(timezone.utc)
    thread = CommentThread(
        id=uuid4(),
        chapter_id=chapter_id,
        novel_id=novel_id,
        intent=req.intent,
        status=ThreadStatus.open,
        anchor=req.anchor,
        comments=[Comment(id=uuid4(), author="user", body=req.body, created_at=now)],
        created_at=now,
        updated_at=now,
    )
    repo = AnnotationRepository(session)
    await repo.save_thread(thread)
    return _thread_to_response(thread)


@router.patch("/{thread_id}")
async def update_thread(
    novel_id: UUID,
    chapter_id: UUID,
    thread_id: UUID,
    req: UpdateThreadRequest,
    session: AsyncSession = Depends(get_db),
) -> ThreadResponse:
    repo = AnnotationRepository(session)
    thread = await repo.get_thread(thread_id)
    if thread is None:
        raise HTTPException(status_code=404, detail="Thread not found")

    if req.status is not None:
        if not can_transition(thread.status, req.status):
            raise HTTPException(
                status_code=422,
                detail=f"非法状态转换: {thread.status.value} → {req.status.value}",
            )
        resolved_at = None
        if req.status == ThreadStatus.resolved:
            resolved_at = datetime.now(timezone.utc)
        elif req.status == ThreadStatus.open:
            resolved_at = None
        await repo.update_thread_status(thread_id, req.status, resolved_at=resolved_at)

    if req.anchor is not None:
        await repo.update_anchor(thread_id, req.anchor.model_dump(mode="json"))

    updated = await repo.get_thread(thread_id)
    return _thread_to_response(updated)  # type: ignore[arg-type]


@router.delete("/{thread_id}", status_code=204)
async def delete_thread(
    novel_id: UUID,
    chapter_id: UUID,
    thread_id: UUID,
    session: AsyncSession = Depends(get_db),
) -> None:
    repo = AnnotationRepository(session)
    deleted = await repo.delete_thread(thread_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Thread not found")


@router.post("/{thread_id}/comments", status_code=201)
async def add_comment(
    novel_id: UUID,
    chapter_id: UUID,
    thread_id: UUID,
    req: AddCommentRequest,
    session: AsyncSession = Depends(get_db),
) -> dict:
    repo = AnnotationRepository(session)
    thread = await repo.get_thread(thread_id)
    if thread is None:
        raise HTTPException(status_code=404, detail="Thread not found")

    comment = Comment(id=uuid4(), author=req.author, body=req.body)  # type: ignore[arg-type]
    await repo.add_comment(thread_id, comment)
    return {
        "id": str(comment.id),
        "author": comment.author,
        "body": comment.body,
        "created_at": comment.created_at.isoformat(),
    }


@router.post("/relocate")
async def relocate_batch(
    novel_id: UUID,
    chapter_id: UUID,
    req: RelocateBatchRequest,
    session: AsyncSession = Depends(get_db),
) -> dict:
    """批量重定位。score ≥0.9 → open, 0.5–0.9 → relocated_fuzzy, <0.5 → orphaned。"""
    repo = AnnotationRepository(session)
    results = {"relocated": 0, "fuzzy": 0, "orphaned": 0}

    for item in req.items:
        tid = UUID(item.thread_id)
        thread = await repo.get_thread(tid)
        if thread is None:
            continue

        anchor_data = item.anchor.model_dump(mode="json")
        anchor_data["relocate_score"] = item.score
        await repo.update_anchor(tid, anchor_data)

        if item.score >= 0.9:
            new_status = ThreadStatus.open
            results["relocated"] += 1
        elif item.score >= 0.5:
            new_status = ThreadStatus.relocated_fuzzy
            results["fuzzy"] += 1
        else:
            new_status = ThreadStatus.orphaned
            results["orphaned"] += 1

        if can_transition(thread.status, new_status):
            await repo.update_thread_status(tid, new_status)
        elif thread.status == ThreadStatus.open:
            await repo.update_thread_status(tid, ThreadStatus.pending_relocate)

    return results


@router.post("/preview")
async def preview_serialization(
    novel_id: UUID,
    chapter_id: UUID,
    req: PreviewRequest,
    session: AsyncSession = Depends(get_db),
) -> PreviewResponse:
    """五区序列化预览（编排器左勾选右预览，与服务端权威渲染一致）。"""
    repo = AnnotationRepository(session)

    # 加载章节内容用于位置排序
    from inkmind.storage.repositories import ChapterRepository

    ch_repo = ChapterRepository(session)
    chapter = await ch_repo.get_by_id(chapter_id)
    content = chapter.content if chapter else ""

    annotations: list[AnnotationRef] = []
    for tid_str in req.thread_ids:
        thread = await repo.get_thread(UUID(tid_str))
        if thread is None:
            continue
        anchor_quote = thread.anchor.exact if thread.anchor else ""
        prefix = thread.anchor.prefix if thread.anchor else ""
        suffix = thread.anchor.suffix if thread.anchor else ""
        annotations.append(
            AnnotationRef(
                thread_id=thread.id,
                intent=thread.intent.value,
                status=thread.status.value,
                anchored_quote=anchor_quote,
                quote_context=QuoteContext(prefix=prefix, suffix=suffix),
                comments=[c.body for c in thread.comments],
            )
        )

    serialized = serialize_annotations(annotations, content)
    return PreviewResponse(serialized=serialized, thread_count=len(annotations))


@router.post("/confirm-relocate")
async def confirm_relocate(
    novel_id: UUID,
    chapter_id: UUID,
    req: ConfirmRelocateRequest,
    session: AsyncSession = Depends(get_db),
) -> dict:
    """合并端点：fuzzy 确认（relocated_fuzzy → open）+ 手动重锚。"""
    repo = AnnotationRepository(session)
    results = {"confirmed": 0, "reanchored": 0, "rejected": 0, "skipped": 0}

    for item in req.items:
        tid = UUID(item.thread_id)
        thread = await repo.get_thread(tid)
        if thread is None:
            results["skipped"] += 1
            continue

        if item.action == "confirm":
            if thread.status == ThreadStatus.relocated_fuzzy:
                await repo.update_thread_status(tid, ThreadStatus.open)
                results["confirmed"] += 1
            elif item.anchor is not None:
                await repo.update_anchor(tid, item.anchor.model_dump(mode="json"))
                if can_transition(thread.status, ThreadStatus.open):
                    await repo.update_thread_status(tid, ThreadStatus.open)
                results["reanchored"] += 1
            else:
                results["skipped"] += 1
        elif item.action == "reject":
            if thread.status == ThreadStatus.relocated_fuzzy:
                await repo.update_thread_status(tid, ThreadStatus.resolved)
                results["rejected"] += 1
            else:
                results["skipped"] += 1
        else:
            results["skipped"] += 1

    return results


@router.post("/apply-relocation")
async def apply_relocation(
    novel_id: UUID,
    chapter_id: UUID,
    req: ApplyRelocationRequest,
    session: AsyncSession = Depends(get_db),
) -> dict:
    """批量回写重定位结果（三档分流）。

    score ≥0.9 → open（自动落位）
    0.5–0.9 → relocated_fuzzy（待确认）
    <0.5 → orphaned（未定位区）
    回稿绝不自动 resolve。
    """
    repo = AnnotationRepository(session)
    results = {"relocated": 0, "fuzzy": 0, "orphaned": 0, "skipped": 0}

    for item in req.items:
        tid = UUID(item.thread_id)
        thread = await repo.get_thread(tid)
        if thread is None:
            results["skipped"] += 1
            continue

        anchor_data = item.anchor.model_dump(mode="json")
        anchor_data["relocate_score"] = item.score
        await repo.update_anchor(tid, anchor_data)

        if item.score >= 0.9:
            new_status = ThreadStatus.open
            results["relocated"] += 1
        elif item.score >= 0.5:
            new_status = ThreadStatus.relocated_fuzzy
            results["fuzzy"] += 1
        else:
            new_status = ThreadStatus.orphaned
            results["orphaned"] += 1

        if can_transition(thread.status, new_status):
            await repo.update_thread_status(tid, new_status)
        elif thread.status == ThreadStatus.pending_relocate:
            await repo.update_thread_status(tid, new_status)
        else:
            results["skipped"] += 1

    return results
