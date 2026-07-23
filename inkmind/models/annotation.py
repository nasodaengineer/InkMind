"""行内批注领域模型。

AnchorFingerprint（W3C TextQuoteSelector 指纹）+ 五态 CommentThread + 多轮 Comment。
纯数据模型，无框架耦合，无 IO，无 LLM 调用。
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class CommentIntent(str, Enum):
    """批注意图四类。note 类不序列化进 prompt。"""

    note = "note"
    question = "question"
    instruction = "instruction"
    reference = "reference"


class ThreadStatus(str, Enum):
    """批注 thread 五态。"""

    open = "open"
    pending_relocate = "pending_relocate"
    relocated_fuzzy = "relocated_fuzzy"
    orphaned = "orphaned"
    resolved = "resolved"


# 合法状态转换表
VALID_TRANSITIONS: dict[ThreadStatus, set[ThreadStatus]] = {
    ThreadStatus.open: {ThreadStatus.pending_relocate, ThreadStatus.resolved},
    ThreadStatus.pending_relocate: {
        ThreadStatus.open,
        ThreadStatus.relocated_fuzzy,
        ThreadStatus.orphaned,
    },
    ThreadStatus.relocated_fuzzy: {ThreadStatus.open, ThreadStatus.resolved},
    ThreadStatus.orphaned: {ThreadStatus.open},
    ThreadStatus.resolved: {ThreadStatus.open},
}


def can_transition(from_status: ThreadStatus, to_status: ThreadStatus) -> bool:
    return to_status in VALID_TRANSITIONS.get(from_status, set())


class AnchorFingerprint(BaseModel):
    """W3C TextQuoteSelector 风格指纹，跨版本唯一真相。

    exact 为选区原文（UI 限 ≤500 字），prefix/suffix 各 64 字符上下文，
    pos_hint 仅会话内快路径参考，anchored_version + chapter_digest 锁定锚定版本。
    """

    exact: str = Field(max_length=500)
    prefix: str = Field(default="", max_length=64)
    suffix: str = Field(default="", max_length=64)
    pos_hint_start: int = Field(default=0, ge=0)
    pos_hint_end: int = Field(default=0, ge=0)
    anchored_version: int = Field(default=1, ge=1)
    chapter_digest: str = Field(default="")
    relocate_score: float | None = Field(default=None, ge=0.0, le=1.0)


class Comment(BaseModel):
    """单条评语。首版仅 user 可写，llm 预留回话通道。"""

    id: UUID = Field(default_factory=uuid4)
    author: Literal["user", "llm"] = "user"
    body: str = Field(min_length=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class CommentThread(BaseModel):
    """批注线程。anchor=null 表示章节总评（无锚批注）。"""

    id: UUID = Field(default_factory=uuid4)
    chapter_id: UUID
    novel_id: UUID
    intent: CommentIntent = CommentIntent.note
    status: ThreadStatus = ThreadStatus.open
    anchor: AnchorFingerprint | None = None
    comments: list[Comment] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    resolved_at: datetime | None = None

    def transition_to(self, new_status: ThreadStatus) -> None:
        """执行状态转换，非法转换抛 ValueError。"""
        if not can_transition(self.status, new_status):
            raise ValueError(f"非法状态转换: {self.status.value} → {new_status.value}")
        self.status = new_status
        self.updated_at = datetime.now(timezone.utc)
        if new_status == ThreadStatus.resolved:
            self.resolved_at = datetime.now(timezone.utc)
        elif new_status == ThreadStatus.open and self.resolved_at is not None:
            self.resolved_at = None
