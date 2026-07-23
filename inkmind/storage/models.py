"""SQLAlchemy ORM 表模型。

采用「混合」策略：
- 核心实体（novels, chapters, characters, world, pipeline, agent_queue）用 ORM 表
- 归档数据（L0/L1/L2/L3 记忆）用 JSON 列存入 memory_archives 表
- 所有章节历史版本单独 chapter_versions 表（保留全量 + 基线标记）

支持 SQLite，所有表使用 INTEGER PRIMARY KEY 为内部自增 ID，
业务主键用 UUID（对外暴露）并加 UNIQUE 索引。
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

__all__ = [
    "AgentQueueModel",
    "ChapterModel",
    "ChapterVersionModel",
    "CharacterModel",
    "CompressionTaskModel",
    "MemoryArchiveModel",
    "NovelModel",
    "PipelineStateModel",
    "ProcessedDigestModel",
    "WorldModel",
]


class Base(DeclarativeBase):
    pass


# ═══════════════════════════════════════════════════════
#  1. Novel — 小说聚合根
# ═══════════════════════════════════════════════════════


class NovelModel(Base):
    __tablename__ = "novels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    uuid: Mapped[str] = mapped_column(
        String(36), unique=True, nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="draft")
    word_count: Mapped[int] = mapped_column(Integer, default=0)
    chapter_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        onupdate=func.now(),
    )

    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    chapters = relationship("ChapterModel", back_populates="novel", lazy="selectin")
    characters = relationship("CharacterModel", back_populates="novel", lazy="selectin")


# ═══════════════════════════════════════════════════════
#  2. Chapter — 单章（最新版本）
# ═══════════════════════════════════════════════════════


class ChapterModel(Base):
    __tablename__ = "chapters"
    __table_args__ = (
        UniqueConstraint("novel_id", "chapter_index", name="uq_chapter_novel_index"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    uuid: Mapped[str] = mapped_column(
        String(36), unique=True, nullable=False, index=True
    )
    novel_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("novels.uuid"), nullable=False, index=True
    )
    chapter_index: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(100), nullable=False)
    content: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(
        String(20), default="planned", index=True
    )
    summary: Mapped[str] = mapped_column(Text, default="")
    key_events: Mapped[dict] = mapped_column(JSON, default=list)
    source_trace: Mapped[str] = mapped_column(String(100), default="")
    outline_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    is_baseline: Mapped[bool] = mapped_column(Boolean, default=False)
    content_digest: Mapped[str] = mapped_column(String(64), default="", index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        onupdate=func.now(),
    )

    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    novel = relationship("NovelModel", back_populates="chapters")
    versions = relationship(
        "ChapterVersionModel", back_populates="chapter", lazy="selectin"
    )


# ═══════════════════════════════════════════════════════
#  3. ChapterVersion — 章节历史版本（保留全量 + 基线标记）
# ═══════════════════════════════════════════════════════


class ChapterVersionModel(Base):
    __tablename__ = "chapter_versions"
    __table_args__ = (
        UniqueConstraint(
            "chapter_id", "version", name="uq_chapter_version"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    uuid: Mapped[str] = mapped_column(
        String(36), unique=True, nullable=False, index=True
    )
    chapter_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("chapters.uuid"), nullable=False, index=True
    )
    novel_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("novels.uuid"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    chapter_index: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(100), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str] = mapped_column(Text, default="")
    key_events: Mapped[dict] = mapped_column(JSON, default=list)
    source_trace: Mapped[str] = mapped_column(String(100), default="")
    is_baseline: Mapped[bool] = mapped_column(Boolean, default=False)
    content_digest: Mapped[str] = mapped_column(
        String(64), default="", index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
    )

    chapter = relationship("ChapterModel", back_populates="versions")
    novel = relationship("NovelModel")


# ═══════════════════════════════════════════════════════
#  4. Character — 角色档案
# ═══════════════════════════════════════════════════════


class CharacterModel(Base):
    __tablename__ = "characters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    uuid: Mapped[str] = mapped_column(
        String(36), unique=True, nullable=False, index=True
    )
    novel_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("novels.uuid"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    aliases: Mapped[list] = mapped_column(JSON, nullable=False)
    role: Mapped[str] = mapped_column(String(20), default="supporting")
    personality_tags: Mapped[list] = mapped_column(JSON, default=list)
    behavior_rules: Mapped[str] = mapped_column(Text, default="")
    appearance: Mapped[str] = mapped_column(Text, default="")
    background: Mapped[str] = mapped_column(Text, default="")
    relationships: Mapped[str] = mapped_column(Text, default="")
    arc_notes: Mapped[str] = mapped_column(Text, default="")
    current_state: Mapped[str] = mapped_column(Text, default="")
    knowledge: Mapped[list] = mapped_column(JSON, default=list)
    voice_examples: Mapped[str] = mapped_column(Text, default="")
    timeline: Mapped[dict] = mapped_column(
        JSON, default=list
    )  # list[CharacterTimelineEntry]
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        onupdate=func.now(),
    )

    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    novel = relationship("NovelModel", back_populates="characters")


# ═══════════════════════════════════════════════════════
#  5. World — 世界观
# ═══════════════════════════════════════════════════════


class WorldModel(Base):
    __tablename__ = "worlds"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    uuid: Mapped[str] = mapped_column(
        String(36), unique=True, nullable=False, index=True
    )
    novel_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("novels.uuid"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(200), default="")
    genre_tags: Mapped[list] = mapped_column(JSON, default=list)
    setting: Mapped[str] = mapped_column(Text, default="")
    rules: Mapped[list] = mapped_column(JSON, default=list)
    factions: Mapped[dict] = mapped_column(
        JSON, default=list
    )  # list[Faction]
    timeline_markers: Mapped[dict] = mapped_column(
        JSON, default=list
    )  # list[TimelineMarker]
    power_system: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    magic_system: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    location_tree: Mapped[dict] = mapped_column(
        JSON, default=list
    )  # list[Location]
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        onupdate=func.now(),
    )


# ═══════════════════════════════════════════════════════
#  6. PipelineState — 流水线全局状态
# ═══════════════════════════════════════════════════════


class PipelineStateModel(Base):
    __tablename__ = "pipeline_states"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    novel_id: Mapped[str] = mapped_column(
        String(36), unique=True, nullable=False, index=True
    )
    total_chapters: Mapped[int] = mapped_column(Integer, default=0)
    chapters_state: Mapped[dict] = mapped_column(
        JSON, default=dict
    )  # {chapter_index: status_str}
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
    )
    current_chapter_index: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    iteration: Mapped[int] = mapped_column(Integer, default=0)
    max_iterations: Mapped[int] = mapped_column(Integer, default=3)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        onupdate=func.now(),
    )


# ═══════════════════════════════════════════════════════
#  7. AgentQueue — 出站队列（outbox pattern）
# ═══════════════════════════════════════════════════════


class AgentQueueModel(Base):
    __tablename__ = "agent_queue"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    packet_id: Mapped[str] = mapped_column(
        String(36), unique=True, nullable=False, index=True
    )
    digest: Mapped[str] = mapped_column(
        String(64), nullable=False, index=True
    )
    packet_type: Mapped[str] = mapped_column(String(30), nullable=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    target: Mapped[str] = mapped_column(String(20), nullable=False)
    novel_id: Mapped[str] = mapped_column(
        String(36), nullable=False, index=True
    )
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), default="pending", index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
    )


# ═══════════════════════════════════════════════════════
#  8. CompressionTask — 异步压缩任务
# ═══════════════════════════════════════════════════════


class CompressionTaskModel(Base):
    __tablename__ = "compression_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(
        String(36), unique=True, nullable=False, index=True
    )
    novel_id: Mapped[str] = mapped_column(
        String(36), nullable=False, index=True
    )
    range_start: Mapped[int] = mapped_column(Integer, nullable=False)
    range_end: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), default="pending", index=True
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
    )


# ═══════════════════════════════════════════════════════
#  9. MemoryArchive — 记忆归档（L0/L1/L2/L3）
# ═══════════════════════════════════════════════════════


class MemoryArchiveModel(Base):
    __tablename__ = "memory_archives"
    __table_args__ = (
        UniqueConstraint(
            "novel_id", "tier", name="uq_memory_archive_novel_tier"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    novel_id: Mapped[str] = mapped_column(
        String(36), nullable=False, index=True
    )
    tier: Mapped[str] = mapped_column(
        String(20), nullable=False
    )  # l0_index / l1_active / l2_compressed / l3_permanent
    data: Mapped[dict] = mapped_column(JSON, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        onupdate=func.now(),
    )


# ═══════════════════════════════════════════════════════
#  10. ProcessedDigest — 幂等去重表
# ═══════════════════════════════════════════════════════


class ProcessedDigestModel(Base):
    __tablename__ = "processed_digests"

    digest: Mapped[str] = mapped_column(
        String(64), primary_key=True
    )
    packet_id: Mapped[str] = mapped_column(
        String(36), nullable=False, index=True
    )
    processed_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
    )
