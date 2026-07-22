"""章节模型。

Chapter 是 AI 生成的最小原子单位，一次生成一章。
每章保存完整的历史版本（ChapterVersion），支持回退和 Diff。
章节元数据用于判断伏笔动态调整滑窗大小。
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from inkmind.models.agent import ChapterStatus


class ChapterMetadata(BaseModel):
    """章节元数据。用于在元数据中查看伏笔动态调整滑窗。

    当 Writer/Planner 需要判断滑窗是否需要扩展时，通过检查
    key_events 的密集度和 foreshadowing_notes 来动态决策。
    """

    title: str
    status: ChapterStatus = ChapterStatus.PLANNED
    summary: str = Field(default="", description="本章摘要")
    key_events: list[str] = Field(default_factory=list)
    source_trace: str = Field(default="", description="来源追踪: 模型标识或人工修订")
    iteration: int = Field(default=0, ge=0, description="修订迭代次数")
    is_baseline: bool = Field(default=False, description="是否标记为基线版本")


class Chapter(BaseModel):
    """单章，最小写作原子单元。

    每次 Writer 生成后，content 被更新为新版本；旧版本自动
    保存到 ChapterVersion 表，永不丢失。
    """

    id: UUID = Field(default_factory=uuid4)
    novel_id: UUID
    index: int = Field(ge=1, description="章节序号（从 1 开始）")
    title: str = Field(min_length=1, max_length=100)

    # ── 正文 ──
    content: str = Field(default="", description="章节正文（最新版本）")

    # ── 元数据 ──
    status: ChapterStatus = ChapterStatus.PLANNED
    summary: str = Field(default="", description="本章摘要")
    key_events: list[str] = Field(default_factory=list)
    source_trace: str = Field(default="", description="来源追踪: 模型标识")
    outline_id: UUID | None = Field(default=None, description="关联的大纲 ID")

    # ── 卷与节奏（Issue #35） ──
    volume_id: UUID | None = Field(default=None, description="所属卷 ID")
    rhythm_marker: str | None = Field(
        default=None, description="节奏标记: climax(▲) / big_climax(★) / None"
    )
    pov: str = Field(default="", description="视角角色")
    involved: list[str] = Field(default_factory=list, description="出场角色列表")

    # ── 版本管理 ──
    version: int = Field(default=1, ge=1, description="当前版本号")
    is_baseline: bool = Field(default=False, description="是否标记为基线版本")

    # ── 时间戳 ──
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ChapterVersion(BaseModel):
    """章节历史版本。

    ADR-0001 要求章节保留完整历史版本。每次 content 发生变更时，
    旧版本自动存入此模型。支持：
    - 回退到任意历史版本
    - 标记某版本为"基线"（is_baseline=True）
    - 通过 content_digest 进行内容去重
    """

    id: UUID = Field(default_factory=uuid4)
    chapter_id: UUID
    novel_id: UUID
    version: int = Field(ge=1, description="顺序递增的版本号")
    index: int = Field(ge=1)
    title: str
    content: str
    summary: str = Field(default="")
    key_events: list[str] = Field(default_factory=list)
    source_trace: str = Field(default="")
    is_baseline: bool = Field(default=False, description="标记为基线版本")
    content_digest: str = Field(default="", description="内容 SHA256 校验和，用于幂等去重")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
