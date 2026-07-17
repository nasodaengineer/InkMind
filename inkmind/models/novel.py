"""小说聚合根模型。

Novel 是整个写作系统的最顶层聚合。它不直接包含章节/角色/世界观，
而是通过 novel_id 关联其他领域模型。
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class NovelMetadata(BaseModel):
    """小说元数据，用于快速概览。"""

    description: str = Field(default="", description="小说简介")
    word_count: int = Field(default=0, ge=0, description="当前总字数")
    chapter_count: int = Field(default=0, ge=0, description="章节总数")
    status: str = Field(
        default="draft",
        description="写作状态: draft / editing / finished",
    )


class Novel(BaseModel):
    """小说聚合根。

    每个 Novel 对应一部独立作品。所有子领域模型（章节、角色、世界观）
    通过 novel_id 归属于同一部小说。
    """

    id: UUID = Field(default_factory=uuid4)
    title: str = Field(min_length=1, max_length=200, description="小说标题")
    metadata: NovelMetadata = Field(default_factory=NovelMetadata)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
