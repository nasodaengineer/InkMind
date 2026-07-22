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
    DDL,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
    func,
)
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

__all__ = [
    "AgentQueueModel",
    "AppSettingsModel",
    "ChapterModel",
    "ChapterVersionModel",
    "CharacterModel",
    "CompressionTaskModel",
    "MaterialChunkModel",
    "MaterialFragmentModel",
    "MaterialSourceModel",
    "MemoryArchiveModel",
    "NovelModel",
    "OutlineSpineModel",
    "PipelineStateModel",
    "ProcessedDigestModel",
    "RunsModel",
    "VolumeModel",
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
    volumes = relationship("VolumeModel", back_populates="novel", lazy="selectin")


# ═══════════════════════════════════════════════════════
#  1b. Volume — 卷
# ═══════════════════════════════════════════════════════


class VolumeModel(Base):
    """卷。每部小说包含多卷，章节归属于卷。"""

    __tablename__ = "volumes"
    __table_args__ = (
        UniqueConstraint(
            "novel_id", "volume_index", name="uq_volume_novel_index"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    uuid: Mapped[str] = mapped_column(
        String(36), unique=True, nullable=False, index=True
    )
    novel_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("novels.uuid"), nullable=False, index=True
    )
    volume_index: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    stage_goal: Mapped[str] = mapped_column(Text, default="")
    main_line: Mapped[str] = mapped_column(Text, default="")
    side_line: Mapped[str] = mapped_column(Text, default="")
    volume_cliffhanger: Mapped[str] = mapped_column(Text, default="")
    planned_size: Mapped[int] = mapped_column(Integer, default=10)
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

    novel = relationship("NovelModel", back_populates="volumes")


# ═══════════════════════════════════════════════════════
#  1c. OutlineSpine — 总纲（书脊）
# ═══════════════════════════════════════════════════════


class OutlineSpineModel(Base):
    """总纲（书脊）。每部小说只有一个总纲。"""

    __tablename__ = "outline_spines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    uuid: Mapped[str] = mapped_column(
        String(36), unique=True, nullable=False, index=True
    )
    novel_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("novels.uuid"), unique=True, nullable=False, index=True
    )
    main_line: Mapped[str] = mapped_column(Text, default="")
    core_conflict: Mapped[str] = mapped_column(Text, default="")
    ending: Mapped[str] = mapped_column(Text, default="")
    selling_points: Mapped[str] = mapped_column(Text, default="")
    world_background: Mapped[str] = mapped_column(Text, default="")
    golden_finger: Mapped[str] = mapped_column(Text, default="")
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

    novel = relationship("NovelModel")


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

    # ── Issue #35: 卷与节奏 ──
    volume_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("volumes.uuid"), nullable=True, index=True
    )
    rhythm_marker: Mapped[str | None] = mapped_column(String(20), nullable=True)
    pov: Mapped[str] = mapped_column(String(50), default="")
    involved: Mapped[dict] = mapped_column(JSON, default=list)

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
    volume = relationship("VolumeModel")


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


# ═══════════════════════════════════════════════════════
#  11. Runs — 执行生命周期
# ═══════════════════════════════════════════════════════


class RunsModel(Base):
    __tablename__ = "runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    uuid: Mapped[str] = mapped_column(
        String(36), unique=True, nullable=False, index=True
    )
    novel_id: Mapped[str] = mapped_column(
        String(36), nullable=False, index=True
    )
    chapter_id: Mapped[str | None] = mapped_column(
        String(36), nullable=True, index=True
    )
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), default="running", index=True
    )
    phase: Mapped[str] = mapped_column(String(30), default="")
    partial_content: Mapped[str] = mapped_column(Text, default="")
    llm_stats: Mapped[dict] = mapped_column(JSON, default=dict)
    overwritten_values: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
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


# SQLite partial unique index: (chapter_id, running) 仅当 status='running' 时约束唯一
# __table_args__ 中 Index 的 sqlite_where 无法表达动态值，使用 DDL 事件监听
_runs_partial_index = DDL(
    "CREATE UNIQUE INDEX IF NOT EXISTS ix_runs_chapter_running "
    "ON runs(chapter_id) WHERE status='running'"
)

event.listen(RunsModel.__table__, "after_create", _runs_partial_index)


# ═══════════════════════════════════════════════════════
#  12. AppSettings — 应用级全局设置（单行 JSON 存储）
# ═══════════════════════════════════════════════════════


class AppSettingsModel(Base):
    __tablename__ = "app_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    novel_id: Mapped[str] = mapped_column(
        String(36), unique=True, nullable=False, default="__app__", index=True
    )
    settings_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
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
#  13. MaterialSource — 素材导入源
# ═══════════════════════════════════════════════════════


class MaterialSourceModel(Base):
    __tablename__ = "material_sources"
    __table_args__ = (
        UniqueConstraint(
            "novel_id", "content_digest", name="uq_source_novel_digest"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    uuid: Mapped[str] = mapped_column(
        String(36), unique=True, nullable=False, index=True
    )
    novel_id: Mapped[str] = mapped_column(
        String(36), nullable=False, index=True
    )
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    content_digest: Mapped[str] = mapped_column(
        String(64), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    word_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
    )
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, index=True)

    chunks = relationship("MaterialChunkModel", back_populates="source", lazy="selectin")


# ═══════════════════════════════════════════════════════
#  14. MaterialChunk — 素材拆解块
# ═══════════════════════════════════════════════════════


class MaterialChunkModel(Base):
    __tablename__ = "material_chunks"
    __table_args__ = (
        UniqueConstraint(
            "source_id", "chunk_index", name="uq_chunk_source_index"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    uuid: Mapped[str] = mapped_column(
        String(36), unique=True, nullable=False, index=True
    )
    source_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("material_sources.uuid"), nullable=False, index=True
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_digest: Mapped[str] = mapped_column(String(64), default="", index=True)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
    )

    source = relationship("MaterialSourceModel", back_populates="chunks")


# ═══════════════════════════════════════════════════════
#  15. MaterialFragment — 结构化碎片
# ═══════════════════════════════════════════════════════


class MaterialFragmentModel(Base):
    __tablename__ = "material_fragments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    uuid: Mapped[str] = mapped_column(
        String(36), unique=True, nullable=False, index=True
    )
    source_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("material_sources.uuid"), nullable=False, index=True
    )
    source_chunk_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("material_chunks.uuid"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(String(2000), nullable=False)
    type: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    source: Mapped[str] = mapped_column(String(200), default="")
    source_quote: Mapped[str | None] = mapped_column(String(50), nullable=True)
    reusability_note: Mapped[str] = mapped_column(String(500), default="")
    user_note: Mapped[str] = mapped_column(String(2000), default="")
    user_edited: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
    )
