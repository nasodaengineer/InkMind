"""世界观模型。

世界观包含力量体系（PowerSystem）和魔法体系（MagicSystem）作为独立的
结构化实体，而非塞入通用 rules 文本。Location 采用层级树结构。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class PowerAbility(BaseModel):
    """能力/技能定义。"""

    name: str
    description: str
    tier: str | None = Field(default=None, description="等级/阶位，如 '天阶'/'地阶'")
    limitations: list[str] = Field(default_factory=list, description="能力限制")


class PowerSystem(BaseModel):
    """力量体系。"""

    name: str
    description: str
    abilities: list[PowerAbility] = Field(default_factory=list)
    rules: list[str] = Field(default_factory=list, description="体系规则")
    limitations: list[str] = Field(default_factory=list, description="体系限制")


class MagicSystem(BaseModel):
    """魔法体系（独立结构化实体，而非塞入通用 rules 文本）。"""

    name: str
    description: str
    schools: list[str] = Field(default_factory=list, description="魔法学派")
    spells: list[PowerAbility] = Field(default_factory=list)
    rules: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    mana_source: str | None = Field(default=None, description="魔力来源")


class Location(BaseModel):
    """结构化地点（层级树节点）。

    支持从大陆到建筑的多级嵌套。parent_id 形成树结构。
    """

    id: UUID = Field(default_factory=uuid4)
    name: str
    type: Literal["continent", "region", "city", "building", "other"] = "other"
    parent_id: UUID | None = Field(default=None, description="父地点 ID，None 表示根节点")
    description: str = Field(default="")
    notable_features: list[str] = Field(default_factory=list)


class Faction(BaseModel):
    """势力/组织。"""

    id: UUID = Field(default_factory=uuid4)
    name: str
    description: str = Field(default="")
    leader: str | None = Field(default=None, description="首领")
    members: list[str] = Field(default_factory=list, description="成员列表")
    goals: list[str] = Field(default_factory=list)
    relationships: str = Field(default="", description="势力间关系（纯文字描述）")


class TimelineMarker(BaseModel):
    """世界观时间线标记。"""

    label: str
    description: str
    chapter_index: int | None = Field(default=None, description="关联章节")
    is_pivotal: bool = Field(default=False, description="是否为转折点")


class World(BaseModel):
    """世界观设定聚合。"""

    id: UUID = Field(default_factory=uuid4)
    novel_id: UUID
    title: str = Field(max_length=200, description="世界观名称")
    genre_tags: list[str] = Field(default_factory=list)
    setting: str = Field(default="", description="时代/地理/基调设定")
    rules: list[str] = Field(default_factory=list, description="世界观通用规则")
    factions: list[Faction] = Field(default_factory=list)
    timeline_markers: list[TimelineMarker] = Field(default_factory=list)
    power_system: PowerSystem | None = Field(default=None)
    magic_system: MagicSystem | None = Field(default=None)
    location_tree: list[Location] = Field(
        default_factory=list, description="结构化地点层级树"
    )
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
