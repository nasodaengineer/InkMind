"""四级压缩记忆架构模型。

L0 — 全文索引（向量 + 全文检索）
L1 — 活跃上下文（滑窗 + 状态卡）
L2 — 压缩记忆（摘要 + 结构化事件清单）
L3 — 长期知识（角色档案 / 世界观手册 / 风格指南）

设计决策参见 docs/adr/0003-four-tier-memory-architecture.md
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator


# ══════════════════════════════════════════════════
#  枚举定义
# ══════════════════════════════════════════════════


class MemoryTier(str, Enum):
    """记忆层级标识。"""

    L0_INDEX = "l0_index"
    """全文索引：向量嵌入 + 关键词倒排"""
    L1_ACTIVE = "l1_active"
    """活跃上下文：滑窗全文 + 状态卡"""
    L2_COMPRESSED = "l2_compressed"
    """压缩记忆：摘要 + 事件清单"""
    L3_PERMANENT = "l3_permanent"
    """长期知识：角色档案 / 世界观手册 / 风格指南"""


class CompressionGranularity(str, Enum):
    """L2 压缩粒度模式。"""

    FIXED = "fixed"
    """固定 N 章一压缩（默认 10 章）"""
    DYNAMIC = "dynamic"
    """按元数据/事件边界动态调整"""
    MANUAL = "manual"
    """手动触发压缩"""


class ContextQueryType(str, Enum):
    """L1/L2 上下文查询类型。"""

    CHARACTER_STATE = "character_state"
    """查询角色当前状态"""
    EVENT_CONTEXT = "event_context"
    """查询某事件的前后文"""
    LOCATION_LOG = "location_log"
    """查询地点变更历史"""
    FORESHADOWING = "foreshadowing"
    """查询未回收的伏笔"""
    PLOT_SUMMARY = "plot_summary"
    """获取情节摘要"""
    TIMELINE = "timeline"
    """获取时间线快照"""


class MemoryNotification(str, Enum):
    """MemoryKeeper 事件通知类型。"""

    COMPRESSION_STARTED = "compression_started"
    """L2 压缩任务已创建，异步执行中"""
    COMPRESSION_COMPLETED = "compression_completed"
    """L2 压缩任务完成"""
    COMPRESSION_FAILED = "compression_failed"
    """L2 压缩任务失败"""
    L1_WINDOW_SHIFTED = "l1_window_shifted"
    """L1 滑窗滚动"""
    L3_ARCHIVE_UPDATED = "l3_archive_updated"
    """长期知识更新"""


# ══════════════════════════════════════════════════
#  时序与标识
# ══════════════════════════════════════════════════


class TimeRange(BaseModel):
    """时间范围标记。"""

    start_chapter: int = Field(ge=1, description="起始章节序号（含）")
    end_chapter: int = Field(ge=1, description="结束章节序号（含）")

    @field_validator("end_chapter")
    @classmethod
    def _end_gte_start(cls, v: int, info) -> int:
        if "start_chapter" in info.data and v < info.data["start_chapter"]:
            raise ValueError("end_chapter 必须 >= start_chapter")
        return v


class CompressionMeta(BaseModel):
    """L2 压缩元数据。用于判断压缩时机和粒度。"""

    chapter_count: int = Field(ge=1, description="本次压缩覆盖的章节数")
    compression_granularity: CompressionGranularity = CompressionGranularity.FIXED
    """使用的压缩模式"""

    trigger_reason: str = Field(
        default="", description="触发压缩的原因（如 '达到10章上限'、'情节分界线'）"
    )

    foreshadowing_pending: int = Field(
        default=0, ge=0, description="压缩覆盖范围内未回收的伏笔数量"
    )


# ══════════════════════════════════════════════════
#  L0 — 全文索引
# ══════════════════════════════════════════════════


class IndexEntry(BaseModel):
    """单条索引条目。"""

    chapter_index: int = Field(ge=1)
    paragraph_index: int = Field(ge=1)
    content_hash: str = Field(min_length=1, description="段落内容校验和")
    vector_id: str | None = Field(None, description="向量嵌入 ID（如 Pinecone/Faiss）")
    keywords: list[str] = Field(default_factory=list, description="关键词标签")
    indexed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class L0Index(BaseModel):
    """L0 全文索引的快照。"""

    novel_id: UUID
    entries: list[IndexEntry] = Field(default_factory=list)
    total_chapters_indexed: int = 0
    last_indexed_at: datetime | None = None


# ══════════════════════════════════════════════════
#  L1 — 活跃上下文
# ══════════════════════════════════════════════════


class CharacterStateCard(BaseModel):
    """单角色当前状态卡。用于 L1 活跃上下文的轻量角色追踪。"""

    character_id: UUID
    name: str
    current_location: str | None = Field(None, description="角色当前位置")
    current_mood: str | None = Field(None, description="角色当前情绪状态")
    current_goal: str | None = Field(None, description="角色当前目标")
    recent_action: str | None = Field(None, description="上一章的主要行动")


class ForeshadowingMarker(BaseModel):
    """伏笔标记。用于动态扩展滑窗。"""

    marker_id: UUID = Field(default_factory=uuid4)
    description: str = Field(..., description="伏笔描述")
    planted_chapter: int = Field(ge=1, description="埋下伏笔的章节")
    expected_payoff_chapter: int | None = Field(
        None, description="预期的回收章节（如有）"
    )
    is_resolved: bool = False
    """是否已回收"""


class SlidingWindowState(BaseModel):
    """L1 滑窗状态。"""

    novel_id: UUID

    # ── 基本配置 ──
    default_window_size: int = Field(default=5, ge=1, le=50)
    """默认滑窗大小（前 N 章全文）"""

    current_expanded_size: int = Field(default=5, ge=1)
    """当前实际滑窗大小（因伏笔等动态扩展后）"""

    expand_reason: str | None = Field(
        default=None,
        description="滑窗扩展的原因（如 '第3章伏笔需第50章回收，滑窗扩大至8章'）",
    )

    # ── 当前章节上下文 ──
    current_chapter_index: int = Field(ge=1)
    """当前正在写的章节序号"""

    recent_chapters: list[int] = Field(
        default_factory=list, description="滑窗内包含的章节序号列表"
    )

    # ── 状态卡 ──
    character_states: dict[UUID, CharacterStateCard] = Field(
        default_factory=dict, description="novel_id → 当前角色状态"
    )

    # ── 伏笔追踪 ──
    pending_foreshadowing: list[ForeshadowingMarker] = Field(
        default_factory=list, description="未回收的伏笔"
    )

    resolved_foreshadowing: list[ForeshadowingMarker] = Field(
        default_factory=list, description="已回收的伏笔"
    )


class ActiveContext(BaseModel):
    """L1 活跃上下文快照。打包给 Writer/Planner 的完整上下文包。"""

    novel_id: UUID
    current_chapter_index: int
    sliding_window: SlidingWindowState

    recent_chapter_titles: list[str] = Field(
        default_factory=list, description="滑窗内各章标题"
    )

    state_cards: list[CharacterStateCard] = Field(
        default_factory=list, description="当前需要追踪的所有角色状态卡"
    )

    recent_summary: str | None = Field(
        default=None,
        description="滑窗内最近几章的简短摘要（供 Writer 快速理解前情）",
    )

    foreshadowing_notes: list[str] = Field(
        default_factory=list,
        description="当前需要关注的伏笔提示",
    )


# ══════════════════════════════════════════════════
#  L2 — 压缩记忆
# ══════════════════════════════════════════════════


class CompressedEvent(BaseModel):
    """L2 压缩记忆中的单条事件。"""

    chapter_index: int = Field(ge=1, description="事件发生的章节序号")
    chapter_title: str = Field(..., description="章节标题")
    event_description: str = Field(
        ..., description="事件描述（一句话，保持叙事性）"
    )
    involved_characters: list[UUID] = Field(
        default_factory=list, description="涉及的角色 ID"
    )
    location: str | None = Field(None, description="事件发生地点")
    is_milestone: bool = False
    """是否为情节里程碑事件"""


class CompressedMemory(BaseModel):
    """L2 单次压缩结果。

    格式：一段总摘要 + 结构化事件清单。使用纯 LLM 生成。
    """

    memory_id: UUID = Field(default_factory=uuid4)
    novel_id: UUID

    # ── 覆盖范围 ──
    range: TimeRange
    """压缩覆盖的章节范围"""

    meta: CompressionMeta
    """压缩元数据"""

    # ── 内容 ──
    summary: str = Field(
        ..., description="一段总摘要。叙事性浓缩，保持可读性。约 200-500 字。"
    )
    """一段总摘要：叙事性浓缩，保持可读性"""

    events: list[CompressedEvent] = Field(
        ..., description="结构化事件清单，按章节顺序排列"
    )
    """结构化事件清单，按章节顺序排列"""

    # ── 衍生信息 ──
    involved_characters: set[UUID] = Field(
        default_factory=set,
        description="本章节范围内所有出场角色 ID 集合",
    )

    key_locations: list[str] = Field(
        default_factory=list, description="本章节范围内出现的地点"
    )

    new_foreshadowing: list[str] = Field(
        default_factory=list, description="本区间新埋下的伏笔描述"
    )

    resolved_foreshadowing: list[str] = Field(
        default_factory=list, description="本区间已回收的伏笔描述"
    )

    # ── 元信息 ──
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    llm_model: str | None = Field(
        default=None, description="用于压缩的 LLM 模型标识"
    )


class L2Archive(BaseModel):
    """L2 压缩记忆归档。按时间顺序存储。"""

    novel_id: UUID
    memories: list[CompressedMemory] = Field(default_factory=list)
    last_compressed_at: datetime | None = None
    total_compressions: int = 0


# ══════════════════════════════════════════════════
#  L3 — 长期知识
# ══════════════════════════════════════════════════


class LongTermEntryType(str, Enum):
    """长期知识条目类型。"""

    CHARACTER_ARCHIVE = "character_archive"
    """角色档案"""
    WORLD_BIBLE = "world_bible"
    """世界观手册"""
    STYLE_GUIDE = "style_guide"
    """风格指南"""
    PLOT_BLUEPRINT = "plot_blueprint"
    """情节蓝图"""
    NOTE = "note"
    """通用笔记"""


class LongTermEntry(BaseModel):
    """单条长期知识条目。"""

    entry_id: UUID = Field(default_factory=uuid4)
    entry_type: LongTermEntryType
    title: str = Field(max_length=200)
    content: str = Field(..., description="完整内容")
    tags: list[str] = Field(default_factory=list)
    version: int = Field(default=1, ge=1)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class L3Archive(BaseModel):
    """L3 长期知识库。"""

    novel_id: UUID
    entries: dict[UUID, LongTermEntry] = Field(default_factory=dict)
    last_updated_at: datetime | None = None


# ══════════════════════════════════════════════════
#  压缩管线管理
# ══════════════════════════════════════════════════


class CompressionTaskStatus(str, Enum):
    """压缩任务状态。"""

    PENDING = "pending"
    """等待执行"""
    RUNNING = "running"
    """正在压缩"""
    COMPLETED = "completed"
    """压缩完成"""
    FAILED = "failed"
    """压缩失败"""


class CompressionTask(BaseModel):
    """异步压缩任务记录。"""

    task_id: UUID = Field(default_factory=uuid4)
    novel_id: UUID
    range: TimeRange
    """待压缩的章节范围"""
    status: CompressionTaskStatus = CompressionTaskStatus.PENDING
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error_message: str | None = Field(
        default=None, description="失败时的错误信息"
    )


class CompressionResult(BaseModel):
    """压缩任务完成后返回的结果。"""

    task_id: UUID
    compressed: CompressedMemory | None = None
    """压缩结果（成功时）"""
    success: bool
    error: str | None = None


class CompressStrategy(BaseModel):
    """压缩策略配置。控制何时以及如何触发 L2 压缩。"""

    default_granularity: int = Field(
        default=10, ge=5, le=100, description="默认每 N 章压缩一次"
    )
    """默认每 N 章压缩一次"""

    enable_dynamic_granularity: bool = True
    """允许根据元数据/事件边界动态调整压缩粒度"""

    dynamic_adjustment_threshold: int = Field(
        default=3,
        ge=1,
        description="连续 N 章的关键事件数超过此阈值时，可提前触发压缩",
    )

    min_event_count_for_milestone: int = Field(
        default=1, ge=1, description="单章事件数超过此值即标记为里程碑章节"
    )

    max_pending_foreshadowing: int = Field(
        default=10,
        ge=1,
        le=100,
        description="未回收伏笔数超过此阈值，强制提前压缩以释放上下文",
    )


# ══════════════════════════════════════════════════
#  完整记忆快照（对外接口）
# ══════════════════════════════════════════════════


class MemorySnapshot(BaseModel):
    """Writer/Planner 所见的完整记忆快照。

    在串行管线中，Writer 写第 N 章时通过此快照获得全部需要的上下文。
    """

    novel_id: UUID

    # ── L1 活跃上下文（直接可用，无需检索）──
    active_context: ActiveContext

    # ── L2 压缩记忆（最近的 1-3 次压缩结果）──
    recent_compressed: list[CompressedMemory] = Field(
        default_factory=list,
        description="最近的压缩记忆摘要（按时间逆序，最多 3 条）",
    )

    # ── L3 长期知识索引（按需检索）──
    permanent_archive: L3Archive | None = Field(
        default=None,
        description="当前章节需要引用的长期知识",
    )

    # ── 动态调整信息 ──
    foreshadowing_notes: list[str] = Field(
        default_factory=list,
        description="当前窗口内外未回收的伏笔提示",
    )
    pending_compression_tasks: int = Field(
        default=0,
        description="后台排队中的压缩任务数",
    )


class MemoryNotificationPayload(BaseModel):
    """MemoryKeeper 发出的事件通知。"""

    novel_id: UUID
    notification_type: MemoryNotification

    # ── 不同类型对应的数据 ──
    compression_result: CompressionResult | None = Field(
        default=None, description="压缩完成/失败时的结果"
    )
    new_snapshot: MemorySnapshot | None = Field(
        default=None,
        description="事件发生后更新了的完整快照",
    )

    message: str = Field(default="", description="人类可读的事件描述")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
