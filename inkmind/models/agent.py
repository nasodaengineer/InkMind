"""Agent 流水线通信协议。

跨 Agent 数据包的强类型定义。每个 Agent 间通过标准化的 Packet 通信，
每种 PacketType 有对应的 Payload 模型。

Pipeline 流程（严格串行）:
  1. Planner 批量规划 N 章大纲 → 存储
  2. 对每章依次:
     a. Writer 收到 WriteRequest → 产出 Draft
     b. Editor 收到 ReviewRequest → 产出 Verdict
     c. Verdict 为 approve → 定稿，进入下一章
     d. Verdict 为 needs_revision → Writer 修改, 回到 b
  3. MemoryKeeper 在每章定稿后接收 MemorizeRequest → 更新上下文
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone
from enum import Enum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


# ──────────────────────────────────────────────
#  枚举定义
# ──────────────────────────────────────────────


class AgentType(str, Enum):
    """InkMind 系统中的 Agent 角色。"""

    PLANNER = "planner"
    WRITER = "writer"
    EDITOR = "editor"
    MEMORY_KEEPER = "memory_keeper"
    DESIGNER = "designer"


class PacketType(str, Enum):
    """Agent 间数据包的类型。每个类型关联一个特定的 Payload 模型。"""

    # ── 规划阶段 ──
    PLAN_REQUEST = "plan_request"
    """请求 Planner 生成大纲。payload: PlanRequestPayload"""

    BATCH_PLAN = "batch_plan"
    """Planner 返回批量大纲。payload: BatchPlanPayload"""

    # ── 写作阶段 ──
    WRITE_REQUEST = "write_request"
    """请求 Writer 写一章。payload: WriteRequestPayload"""

    DRAFT = "draft"
    """Writer 返回草稿。payload: DraftPayload"""

    # ── 评审阶段 ──
    REVIEW_REQUEST = "review_request"
    """请求 Editor 评审。payload: ReviewRequestPayload"""

    VERDICT = "verdict"
    """Editor 返回评审结论。payload: VerdictPayload"""

    # ── 修订反馈 ──
    REVISION_REQUEST = "revision_request"
    """Editor 要求修改。payload: RevisionRequestPayload"""

    # ── 上下文询问 ──
    SNAPSHOT_REQUEST = "snapshot_request"
    """(Writer/Planner→MemoryKeeper) 请求当前记忆快照。payload: SnapshotRequestPayload"""

    SNAPSHOT_RESPONSE = "snapshot_response"
    """(MemoryKeeper→Writer/Planner) 返回记忆快照。payload: SnapshotResponsePayload"""

    # ── 记忆持久化 ──
    MEMORIZE_REQUEST = "memorize_request"
    """(Editor→MemoryKeeper) 请求持久化定稿章节。payload: MemorizeRequestPayload"""

    MEMORIZED = "memorized"
    """(MemoryKeeper→Editor) 确认持久化完成。payload: MemorizedPayload"""

    # ── 压缩事件 ──
    COMPRESSION_NOTIFICATION = "compression_notification"
    """(MemoryKeeper→所有) 异步压缩任务状态变更。payload: CompressionNotificationPayload"""

    # ── 上下文查询 ──
    CONTEXT_QUERY = "context_query"
    """(Writer→MemoryKeeper) 查询特定上下文（伏笔/角色/时间线）。payload: ContextQueryPayload"""

    CONTEXT_RESULT = "context_result"
    """(MemoryKeeper→Writer) 返回查询结果。payload: ContextResultPayload"""


class Verdict(str, Enum):
    """Editor 对一章的评审结论——方案 A：简单二值。"""

    APPROVE = "approve"
    """通过（含修改建议）"""

    NEEDS_REVISION = "needs_revision"
    """需要修改（无量化评分，不逐段标注）"""


# ──────────────────────────────────────────────
#  强类型 Payload 模型（每个 PacketType 对应一个）
# ──────────────────────────────────────────────


class ChapterIndex(BaseModel):
    """章节索引和标题。"""

    index: int = Field(ge=1, description="章节序号（从 1 开始）")
    title: str = Field(min_length=1, max_length=100, description="章节标题")


class ChapterOutline(ChapterIndex):
    """单章大纲。Planner 批量产出。"""

    summary: str = Field(min_length=20, max_length=2000, description="本章摘要")
    key_events: list[str] = Field(min_length=1, description="关键事件列表")
    pov_character_id: UUID | None = Field(None, description="视角角色 ID")
    involved_character_ids: list[UUID] = Field(default_factory=list, description="出场角色 ID 列表")


# ── 规划阶段 ──


class PlanLevel(str, Enum):
    """规划操作的粒度级别。"""

    SPINE = "spine"
    """总纲（书脊）起草 — LLM 生成六字段总纲。"""

    VOLUME = "volume"
    """单卷填补 — LLM 补全单卷四字段（保已有字段）。"""

    CHAPTER = "chapter"
    """卷内批量排章 — 5-50 章大纲落指定卷区间。"""

    SPLIT_VOLUMES = "split_volumes"
    """全书拆卷 — 批量生成 2-20 卷的卷纲。"""


class PlanRequestPayload(BaseModel):
    """请求 Planner 规划多章大纲。"""

    novel_id: UUID
    chapter_count: int = Field(ge=1, le=50, default=10, description="待规划的章节数")
    world_id: UUID
    context_summary: str = Field(
        default="", description="压缩后的上下文摘要，供 Planner 保持连贯性"
    )

    # Issue #42: AI 大纲规划新增字段
    level: PlanLevel = Field(default=PlanLevel.CHAPTER, description="规划操作粒度")
    """规划操作类型：spine / volume / chapter / split_volumes"""

    prompt: str | None = Field(default=None, description="可选的提示文本，指导 LLM 生成方向")
    """用户可输入提示词指导 LLM 生成方向（如「偏向悬疑风格」）。"""

    volume_count: int = Field(
        default=5, ge=2, le=20, description="拆卷时生成的卷数（仅 split_volumes 时有效）"
    )
    """拆卷时指定的卷数量，范围 2-20。"""

    confirm_overwrite: bool = Field(
        default=False, description="确认覆盖非空内容（总纲/卷纲非空时需显式确认）"
    )
    """当目标内容已存在时，需显式确认覆盖。"""

    volume_id: UUID | None = Field(
        default=None, description="关联的卷 ID（仅 volume/chapter 时有效）"
    )

    start_index: int = Field(default=1, description="起始章节序号")


class BatchPlanPayload(BaseModel):
    """Planner 批量返回的大纲集合。"""

    outlines: Sequence[ChapterOutline] = Field(
        min_length=1, max_length=50, description="章节大纲列表"
    )


# ── 写作阶段 ──


class WriteRequestPayload(BaseModel):
    """请求 Writer 执行单章写作。"""

    novel_id: UUID
    chapter_outline: ChapterOutline
    context_summary: str = Field(..., description="包括前文摘要、角色状态、世界观等上下文")
    word_count_min: int = Field(default=1000, ge=500, description="目标字数下限")
    word_count_max: int = Field(default=3000, le=5000, description="目标字数上限")


class DraftPayload(BaseModel):
    """Writer 返回的草稿。"""

    chapter_index: int
    content: str = Field(min_length=100, description="章节正文")
    paragraph_count: int = Field(ge=1, description="段落数")


# ── 评审阶段 ──


class ReviewRequestPayload(BaseModel):
    """请求 Editor 评审一章。"""

    novel_id: UUID
    chapter_index: int
    content: str
    chapter_outline: ChapterOutline
    iteration: int = Field(default=0, ge=0, description="当前是第几次评审迭代")


class VerdictPayload(BaseModel):
    """Editor 的评审结论——简单二值。"""

    verdict: Verdict
    """approve 或 needs_revision"""

    issues: list[str] = Field(
        default_factory=list,
        description="问题描述列表（仅在 needs_revision 时有值）",
    )


class QuoteContext(BaseModel):
    """锚定引文的上下文窗口。"""

    prefix: str = Field(default="", max_length=64)
    suffix: str = Field(default="", max_length=64)


class AnnotationRef(BaseModel):
    """批注线程的 wire 视图，随修订协议流转。"""

    thread_id: UUID
    intent: str
    status: str
    anchored_quote: str = Field(default="", description="锚定引文原文（无锚时为空）")
    quote_context: QuoteContext = Field(default_factory=QuoteContext)
    comments: list[str] = Field(default_factory=list, description="评语正文列表")


class RevisionRequestPayload(BaseModel):
    """向 Writer 发出的修订请求。issues 与 annotations 至少其一非空。"""

    novel_id: UUID
    chapter_index: int
    previous_content: str = Field(..., description="上一版草稿")
    issues: list[str] = Field(default_factory=list, description="需要修改的问题列表")
    annotations: list[AnnotationRef] = Field(
        default_factory=list, description="人工批示批注（五区序列化源）"
    )
    iteration: int = Field(ge=1, description="修订迭代次数")
    chapter_outline: ChapterOutline

    def model_post_init(self, __context) -> None:
        if not self.issues and not self.annotations:
            raise ValueError("issues 与 annotations 至少其一非空")


# ── 记忆持久化 ──


class MemorizeRequestPayload(BaseModel):
    """请求 MemoryKeeper 更新记忆。每章定稿后触发。"""

    novel_id: UUID
    chapter_index: int
    chapter_title: str
    chapter_summary: str
    key_events: list[str]
    character_events: list[dict] = Field(
        default_factory=list,
        description="本章中角色的关键事件，格式: [{character_id, event}]",
    )
    location_changes: list[str] = Field(default_factory=list, description="本章涉及的地点变化")


class MemorizedPayload(BaseModel):
    """MemoryKeeper 确认持久化完成。"""

    novel_id: UUID
    chapter_index: int
    digest: str = Field(..., description="更新后的上下文摘要/校验和")
    success: bool = True


# ── 上下文记忆 ──


class SnapshotRequestPayload(BaseModel):
    """(Writer/Planner→MemoryKeeper) 请求当前记忆快照。"""

    novel_id: UUID
    target_chapter: int = Field(ge=1, description="即将开始写的章节序号")


class SnapshotResponsePayload(BaseModel):
    """(MemoryKeeper→Writer/Planner) 返回记忆快照。"""

    novel_id: UUID
    target_chapter: int
    snapshot: dict = Field(..., description="MemorySnapshot 的 dict 表示（避免跨模块循环）")


class CompressionNotificationPayload(BaseModel):
    """(MemoryKeeper→所有) 异步压缩任务状态变更通知。"""

    novel_id: UUID
    task_id: UUID
    notification_type: str = Field(
        ..., description="compression_started / compression_completed / compression_failed"
    )
    message: str = Field(default="", description="人类可读的事件描述")
    compressed_range: tuple[int, int] | None = Field(
        default=None, description="(start_chapter, end_chapter)"
    )


class ContextQueryPayload(BaseModel):
    """(Writer→MemoryKeeper) 查询特定上下文。"""

    novel_id: UUID
    query_type: str = Field(
        ...,
        description="查询类型: character_state / event_context / location_log / foreshadowing / plot_summary / timeline",
    )
    params: dict = Field(default_factory=dict, description="查询参数，如 {character_id: '...'}")


class ContextResultPayload(BaseModel):
    """(MemoryKeeper→Writer) 返回查询结果。"""

    novel_id: UUID
    query_type: str
    results: dict = Field(default_factory=dict, description="查询结果数据")
    error: str | None = Field(default=None, description="查询失败时的错误信息")


# ──────────────────────────────────────────────
#  联合 Payload 类型（按 PacketType 分发）
# ──────────────────────────────────────────────

PacketPayload = (
    PlanRequestPayload
    | BatchPlanPayload
    | WriteRequestPayload
    | DraftPayload
    | ReviewRequestPayload
    | VerdictPayload
    | RevisionRequestPayload
    | MemorizeRequestPayload
    | MemorizedPayload
    | SnapshotRequestPayload
    | SnapshotResponsePayload
    | CompressionNotificationPayload
    | ContextQueryPayload
    | ContextResultPayload
)


# ──────────────────────────────────────────────
#  核心 Packet（通用数据载具）
# ──────────────────────────────────────────────


class AgentPacket(BaseModel):
    """Agent 间通信的标准数据包。

    Protocol:
        packet.packet_type  — 标记载荷的实际类型
        isinstance(packet.payload, DraftPayload)  — 运行时类型窄化

    Usage:
        packet = AgentPacket(
            source=AgentType.PLANNER,
            target=AgentType.WRITER,
            novel_id=...,
            packet_type=PacketType.WRITE_REQUEST,
            payload=WriteRequestPayload(...),
        )
        # dispatch:
        if packet.packet_type == PacketType.DRAFT:
            draft: DraftPayload = packet.payload  # type-safe via type-narrowing
    """

    packet_id: UUID = Field(default_factory=uuid4)
    source: AgentType
    target: AgentType
    novel_id: UUID
    packet_type: PacketType
    payload: PacketPayload
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    version: int = Field(default=1, ge=1)
    iteration: int = Field(default=0, ge=0, description="反馈回路迭代次数")


# ──────────────────────────────────────────────
#  Pipeline 编排状态
# ──────────────────────────────────────────────


class ChapterStatus(str, Enum):
    """单章在流水线中的状态。"""

    PLANNED = "planned"
    """Planner 已完成大纲"""

    WRITING = "writing"
    """Writer 正在生成"""

    DRAFT_READY = "draft_ready"
    """草稿就绪，等待评审"""

    REVIEWING = "reviewing"
    """Editor 正在评审"""

    REVISING = "revising"
    """Writer 正在修订"""

    APPROVED = "approved"
    """评审通过，等待记忆持久化"""

    AWAITING_HUMAN = "awaiting_human"
    """人工门：等待作者确认/编辑/定稿"""

    FINALIZED = "finalized"
    """全部完成"""


class PipelineState(BaseModel):
    """整条流水线的全局状态快照。"""

    novel_id: UUID
    total_chapters: int = 0
    chapters: dict[int, ChapterStatus] = Field(default_factory=dict, description="章节序号 → 状态")
    current_chapter_index: int | None = Field(None, description="流水线当前正在处理的章节")
    iteration: int = Field(default=0, ge=0, description="当前章节的修订迭代次数")
    max_iterations: int = Field(default=3, ge=1, le=10, description="最大允许的修订次数")
