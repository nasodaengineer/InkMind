"""InkMind 领域模型层。

纯数据模型，无框架耦合，无 IO，无 LLM 调用。
"""

from inkmind.models.agent import (
    AgentPacket,
    AgentType,
    BatchPlanPayload,
    ChapterIndex,
    ChapterOutline,
    ChapterStatus,
    CompressionNotificationPayload,
    ContextQueryPayload,
    ContextResultPayload,
    DraftPayload,
    MemorizeRequestPayload,
    MemorizedPayload,
    PacketPayload,
    PacketType,
    PipelineState,
    PlanRequestPayload,
    ReviewRequestPayload,
    RevisionRequestPayload,
    SnapshotRequestPayload,
    SnapshotResponsePayload,
    Verdict,
    VerdictPayload,
    WriteRequestPayload,
)

from inkmind.models.chapter import Chapter, ChapterMetadata, ChapterVersion

from inkmind.models.character import Character, CharacterTimelineEntry

from inkmind.models.llm import (
    AgentModelBinding,
    LLMConfig,
    ModelRouterConfig,
    ProviderConfig,
    ProviderProtocol,
    RetryConfig,
)

from inkmind.models.memory import (
    ActiveContext,
    CharacterStateCard,
    CompressedEvent,
    CompressedMemory,
    CompressionGranularity,
    CompressionMeta,
    CompressionResult,
    CompressionTask,
    CompressionTaskStatus,
    CompressStrategy,
    ContextQueryType,
    ForeshadowingMarker,
    IndexEntry,
    L0Index,
    L2Archive,
    L3Archive,
    LongTermEntry,
    LongTermEntryType,
    MemoryNotification,
    MemoryNotificationPayload,
    MemorySnapshot,
    MemoryTier,
    SlidingWindowState,
    TimeRange,
)

from inkmind.models.novel import Novel, NovelMetadata

from inkmind.models.world import (
    Faction,
    Location,
    MagicSystem,
    PowerAbility,
    PowerSystem,
    TimelineMarker,
    World,
)

__all__ = [
    # agent
    "AgentPacket",
    "AgentType",
    "BatchPlanPayload",
    "ChapterIndex",
    "ChapterOutline",
    "ChapterStatus",
    "CompressionNotificationPayload",
    "ContextQueryPayload",
    "ContextResultPayload",
    "DraftPayload",
    "MemorizeRequestPayload",
    "MemorizedPayload",
    "PacketPayload",
    "PacketType",
    "PipelineState",
    "PlanRequestPayload",
    "ReviewRequestPayload",
    "RevisionRequestPayload",
    "SnapshotRequestPayload",
    "SnapshotResponsePayload",
    "Verdict",
    "VerdictPayload",
    "WriteRequestPayload",
    # chapter
    "Chapter",
    "ChapterMetadata",
    "ChapterVersion",
    # character
    "Character",
    "CharacterTimelineEntry",
    # llm
    "AgentModelBinding",
    "LLMConfig",
    "ModelRouterConfig",
    "ProviderConfig",
    "ProviderProtocol",
    "RetryConfig",
    # memory
    "ActiveContext",
    "CharacterStateCard",
    "CompressedEvent",
    "CompressedMemory",
    "CompressionGranularity",
    "CompressionMeta",
    "CompressionResult",
    "CompressionTask",
    "CompressionTaskStatus",
    "CompressStrategy",
    "ContextQueryType",
    "ForeshadowingMarker",
    "IndexEntry",
    "L0Index",
    "L2Archive",
    "L3Archive",
    "LongTermEntry",
    "LongTermEntryType",
    "MemoryNotification",
    "MemoryNotificationPayload",
    "MemorySnapshot",
    "MemoryTier",
    "SlidingWindowState",
    "TimeRange",
    # novel
    "Novel",
    "NovelMetadata",
    # world
    "Faction",
    "Location",
    "MagicSystem",
    "PowerAbility",
    "PowerSystem",
    "TimelineMarker",
    "World",
]
