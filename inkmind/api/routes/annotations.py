"""批注 REST 端点。无单体 GET（按 issue #40 要求）。"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from inkmind.api.deps import get_db
from inkmind.models.annotation import (
    AnchorFingerprint,
    Comment,
    CommentIntent,
    CommentThread,
    ThreadStatus,
    can_transition,
)
from inkmind.storage.repositories import AnnotationRepository

router = APIRouter(prefix="/api/novels/{novel_id}/chapters/{chapter_id}/annotations", tags=["annotations"])


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
