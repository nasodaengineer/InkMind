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


class Volume(BaseModel):
    """卷。

    百万字大纲的三级体系中间层。每卷有独立的阶段目标、主线、支线和卷末悬念。
    卷是规划的容器，章节归属于卷。planned_size 表示预计章数，用于展示派生区间。
    """

    id: UUID = Field(default_factory=uuid4)
    novel_id: UUID
    volume_index: int = Field(ge=1, description="卷序号（从 1 开始）")
    title: str = Field(min_length=1, max_length=200, description="卷标题")
    stage_goal: str = Field(default="", description="阶段目标")
    main_line: str = Field(default="", description="主线")
    side_line: str = Field(default="", description="支线")
    volume_cliffhanger: str = Field(default="", description="卷末悬念")
    planned_size: int = Field(default=10, ge=1, description="预计章数")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class OutlineSpine(BaseModel):
    """总纲（书脊）。

    百万字大纲的最顶层。定义整部小说的核心骨架：主线、核心矛盾、结局、
    卖点、世界观背景、金手指。每部小说只有一个总纲。
    """

    novel_id: UUID
    main_line: str = Field(default="", description="主线")
    core_conflict: str = Field(default="", description="核心矛盾")
    ending: str = Field(default="", description="结局")
    selling_points: str = Field(default="", description="卖点")
    world_background: str = Field(default="", description="世界观背景")
    golden_finger: str = Field(default="", description="金手指")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
