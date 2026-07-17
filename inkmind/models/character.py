"""角色模型。

Character 是角色档案的完整表示。性格通过 标签[] + 自由文本 表达，
角色关系纯文字描述（无量化亲密度）。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class CharacterTimelineEntry(BaseModel):
    """角色在单章的关键事件记录。

    用于跨章节一致性维护。每当 Editor 定稿一章后，MemoryKeeper
    更新所有出场角色的 TimelineEntry。
    """

    chapter_index: int = Field(ge=1, description="章节序号")
    key_events: list[str] = Field(default_factory=list, description="本章角色关键事件")
    current_state: str | None = Field(
        default=None, description="该章结束时角色的状态描述"
    )


class Character(BaseModel):
    """角色档案。

    角色状态通过 current_state 自由文本承载，无阶段性状态机。
    timeline 字段追踪角色在每章的关键事件，用于跨章节一致性。
    """

    id: UUID = Field(default_factory=uuid4)
    novel_id: UUID
    name: str = Field(min_length=1, max_length=50)
    aliases: list[str] = Field(description="别称列表（必填，用于指代消歧）")
    role: Literal["protagonist", "antagonist", "supporting", "minor"] = Field(
        default="supporting", description="角色类型：主角/反派/配角/龙套"
    )
    personality_tags: list[str] = Field(
        default_factory=list, description="性格标签，如 ['勇敢', '多疑']"
    )
    behavior_rules: str = Field(default="", description="行为规则（自由文本，供 AI 参考）")
    appearance: str = Field(default="", description="外貌描述")
    background: str = Field(default="", description="背景故事")
    relationships: str = Field(default="", description="角色关系（纯文字描述，不量化）")
    arc_notes: str = Field(default="", description="角色弧光笔记")
    current_state: str = Field(default="", description="角色当前状态（自由文本）")
    knowledge: list[str] = Field(default_factory=list, description="角色掌握的知识/信息")
    voice_examples: str = Field(default="", description="角色语料示例，供 Writer 保持语言风格")
    timeline: list[CharacterTimelineEntry] = Field(
        default_factory=list, description="角色每章的关键事件时间线"
    )
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
