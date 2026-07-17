"""四级压缩记忆架构模型单元测试。"""

from uuid import UUID, uuid4

from inkmind.models.memory import (
    ActiveContext,
    CharacterStateCard,
    CompressedEvent,
    CompressedMemory,
    CompressionMeta,
    CompressionResult,
    CompressionTask,
    CompressionTaskStatus,
    CompressStrategy,
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


# ═══════════════════════════════════════════════════
#  枚举测试
# ═══════════════════════════════════════════════════


def test_memory_tier_enum_values() -> None:
    """MemoryTier 枚举值正确（L0/L1/L2/L3）。"""
    assert MemoryTier.L0_INDEX.value == "l0_index"
    assert MemoryTier.L1_ACTIVE.value == "l1_active"
    assert MemoryTier.L2_COMPRESSED.value == "l2_compressed"
    assert MemoryTier.L3_PERMANENT.value == "l3_permanent"


def test_memory_tier_enum_members() -> None:
    """MemoryTier 包含全部四个成员。"""
    members = list(MemoryTier)
    assert len(members) == 4
    assert MemoryTier.L0_INDEX in members
    assert MemoryTier.L1_ACTIVE in members
    assert MemoryTier.L2_COMPRESSED in members
    assert MemoryTier.L3_PERMANENT in members


def test_memory_notification_enum_values() -> None:
    """MemoryNotification 枚举值正确（started/completed/failed/shifted/updated）。"""
    assert MemoryNotification.COMPRESSION_STARTED.value == "compression_started"
    assert MemoryNotification.COMPRESSION_COMPLETED.value == "compression_completed"
    assert MemoryNotification.COMPRESSION_FAILED.value == "compression_failed"
    assert MemoryNotification.L1_WINDOW_SHIFTED.value == "l1_window_shifted"
    assert MemoryNotification.L3_ARCHIVE_UPDATED.value == "l3_archive_updated"


def test_memory_notification_enum_members() -> None:
    """MemoryNotification 包含全部五个成员。"""
    members = list(MemoryNotification)
    assert len(members) == 5
    assert MemoryNotification.COMPRESSION_STARTED in members
    assert MemoryNotification.COMPRESSION_COMPLETED in members
    assert MemoryNotification.COMPRESSION_FAILED in members
    assert MemoryNotification.L1_WINDOW_SHIFTED in members
    assert MemoryNotification.L3_ARCHIVE_UPDATED in members


# ═══════════════════════════════════════════════════
#  L0 — 全文索引
# ═══════════════════════════════════════════════════


def test_l0_index_create() -> None:
    """L0Index 创建和索引条目添加。"""
    nid = uuid4()
    idx = L0Index(novel_id=nid)
    assert idx.novel_id == nid
    assert idx.entries == []
    assert idx.total_chapters_indexed == 0
    assert idx.last_indexed_at is None


def test_l0_index_add_entries() -> None:
    """L0Index 可添加索引条目。"""
    nid = uuid4()
    idx = L0Index(novel_id=nid)

    entry1 = IndexEntry(
        chapter_index=1,
        paragraph_index=1,
        content_hash="abc123",
        keywords=["穿越", "森林"],
    )
    entry2 = IndexEntry(
        chapter_index=1,
        paragraph_index=2,
        content_hash="def456",
        keywords=["发现", "神秘生物"],
    )

    idx.entries.append(entry1)
    idx.entries.append(entry2)
    idx.total_chapters_indexed = 1

    assert len(idx.entries) == 2
    assert idx.entries[0].chapter_index == 1
    assert idx.entries[0].paragraph_index == 1
    assert idx.entries[0].content_hash == "abc123"
    assert idx.entries[0].keywords == ["穿越", "森林"]
    assert idx.entries[1].keywords == ["发现", "神秘生物"]
    assert idx.total_chapters_indexed == 1


# ═══════════════════════════════════════════════════
#  L1 — 活跃上下文
# ═══════════════════════════════════════════════════


def test_sliding_window_default_size() -> None:
    """SlidingWindowState 默认窗口为5、可动态扩展。"""
    nid = uuid4()
    sw = SlidingWindowState(
        novel_id=nid,
        current_chapter_index=1,
    )
    assert sw.default_window_size == 5
    assert sw.current_expanded_size == 5
    assert sw.expand_reason is None
    assert sw.recent_chapters == []
    assert sw.character_states == {}
    assert sw.pending_foreshadowing == []
    assert sw.resolved_foreshadowing == []


def test_sliding_window_dynamic_expand() -> None:
    """SlidingWindowState 可动态扩展。"""
    nid = uuid4()
    sw = SlidingWindowState(
        novel_id=nid,
        default_window_size=5,
        current_expanded_size=8,
        expand_reason="第3章伏笔需第50章回收，滑窗扩大至8章",
        current_chapter_index=3,
        recent_chapters=[1, 2, 3],
    )
    assert sw.default_window_size == 5
    assert sw.current_expanded_size == 8
    assert sw.expand_reason is not None
    assert "伏笔" in sw.expand_reason
    assert sw.recent_chapters == [1, 2, 3]


def test_character_state_card_definition() -> None:
    """CharacterStateCard 正确定义（character_id, location, recent_action, status）。"""
    cid = uuid4()
    card = CharacterStateCard(
        character_id=cid,
        name="张三",
        current_location="幽冥森林",
        current_mood="警惕",
        current_goal="寻找出口",
        recent_action="穿越森林",
    )
    assert card.character_id == cid
    assert card.name == "张三"
    assert card.current_location == "幽冥森林"
    assert card.current_mood == "警惕"
    assert card.current_goal == "寻找出口"
    assert card.recent_action == "穿越森林"


def test_character_state_card_optional_fields() -> None:
    """CharacterStateCard 可选字段默认为 None。"""
    cid = uuid4()
    card = CharacterStateCard(
        character_id=cid,
        name="张三",
    )
    assert card.current_location is None
    assert card.current_mood is None
    assert card.current_goal is None
    assert card.recent_action is None


def test_foreshadowing_marker_definition() -> None:
    """ForeshadowingMarker 正确定义（埋设/回收章节、描述、状态）。"""
    marker = ForeshadowingMarker(
        description="张三在森林中发现的古老符文",
        planted_chapter=3,
        expected_payoff_chapter=50,
    )
    assert isinstance(marker.marker_id, UUID)
    assert marker.description == "张三在森林中发现的古老符文"
    assert marker.planted_chapter == 3
    assert marker.expected_payoff_chapter == 50
    assert marker.is_resolved is False


def test_foreshadowing_marker_resolved() -> None:
    """ForeshadowingMarker 可标记为已回收。"""
    marker = ForeshadowingMarker(
        description="古老符文",
        planted_chapter=3,
        expected_payoff_chapter=50,
        is_resolved=True,
    )
    assert marker.is_resolved is True


def test_foreshadowing_marker_no_payoff() -> None:
    """ForeshadowingMarker 支持未指定回收章节。"""
    marker = ForeshadowingMarker(
        description="伏笔",
        planted_chapter=1,
    )
    assert marker.expected_payoff_chapter is None
    assert marker.is_resolved is False


def test_active_context_contains_all_components() -> None:
    """ActiveContext 包含滑窗状态+角色卡片+伏笔表。"""
    nid = uuid4()
    cid = uuid4()

    sw = SlidingWindowState(
        novel_id=nid,
        current_chapter_index=5,
        recent_chapters=[3, 4, 5],
    )

    card = CharacterStateCard(
        character_id=cid,
        name="张三",
        current_location="森林",
        recent_action="探索",
    )

    ctx = ActiveContext(
        novel_id=nid,
        current_chapter_index=5,
        sliding_window=sw,
        state_cards=[card],
        foreshadowing_notes=["注意古老符文伏笔"],
        recent_summary="张三在森林中发现了神秘符文。",
    )

    # 包含滑窗状态
    assert ctx.sliding_window.current_chapter_index == 5
    assert ctx.sliding_window.recent_chapters == [3, 4, 5]

    # 包含角色卡片
    assert len(ctx.state_cards) == 1
    assert ctx.state_cards[0].character_id == cid
    assert ctx.state_cards[0].name == "张三"

    # 包含伏笔表
    assert len(ctx.foreshadowing_notes) == 1
    assert "古老符文" in ctx.foreshadowing_notes[0]

    # 包含摘要
    assert ctx.recent_summary is not None
    assert "神秘符文" in ctx.recent_summary


# ═══════════════════════════════════════════════════
#  L2 — 压缩记忆
# ═══════════════════════════════════════════════════


def test_compressed_event_structured() -> None:
    """CompressedEvent 结构化事件（index, title, event_description, involved_characters）。"""
    cid1 = uuid4()
    cid2 = uuid4()

    event = CompressedEvent(
        chapter_index=3,
        chapter_title="森林深处的秘密",
        event_description="张三在森林中发现古老符文，触发了传送阵",
        involved_characters=[cid1, cid2],
        location="幽冥森林深处",
        is_milestone=True,
    )
    assert event.chapter_index == 3
    assert event.chapter_title == "森林深处的秘密"
    assert event.event_description == "张三在森林中发现古老符文，触发了传送阵"
    assert len(event.involved_characters) == 2
    assert cid1 in event.involved_characters
    assert event.location == "幽冥森林深处"
    assert event.is_milestone is True


def test_compressed_memory_with_summary_and_events() -> None:
    """CompressedMemory 包含总摘要+事件列表。"""
    nid = uuid4()
    cid = uuid4()

    event1 = CompressedEvent(
        chapter_index=1,
        chapter_title="穿越",
        event_description="主角穿越到异世界",
        involved_characters=[cid],
    )
    event2 = CompressedEvent(
        chapter_index=2,
        chapter_title="初次交锋",
        event_description="主角遭遇森林狼",
        involved_characters=[cid],
        location="幽冥森林",
    )

    meta = CompressionMeta(chapter_count=2)
    memory = CompressedMemory(
        novel_id=nid,
        range=TimeRange(start_chapter=1, end_chapter=2),
        meta=meta,
        summary="主角穿越到异世界并在森林中首次遭遇危险。",
        events=[event1, event2],
        involved_characters={cid},
        key_locations=["幽冥森林"],
    )

    assert isinstance(memory.memory_id, UUID)
    assert memory.novel_id == nid
    assert memory.range.start_chapter == 1
    assert memory.range.end_chapter == 2
    assert memory.summary == "主角穿越到异世界并在森林中首次遭遇危险。"
    assert len(memory.events) == 2
    assert memory.events[0].chapter_title == "穿越"
    assert memory.events[1].chapter_title == "初次交锋"
    assert cid in memory.involved_characters
    assert "幽冥森林" in memory.key_locations
    assert memory.llm_model is None


def test_l2_archive_management() -> None:
    """L2Archive 归档管理。"""
    nid = uuid4()

    archive = L2Archive(novel_id=nid)
    assert archive.novel_id == nid
    assert archive.memories == []
    assert archive.last_compressed_at is None
    assert archive.total_compressions == 0

    # 添加第一条压缩记忆
    meta1 = CompressionMeta(chapter_count=5)
    memory1 = CompressedMemory(
        novel_id=nid,
        range=TimeRange(start_chapter=1, end_chapter=5),
        meta=meta1,
        summary="前五章摘要",
        events=[],
    )
    archive.memories.append(memory1)
    archive.total_compressions = 1

    assert len(archive.memories) == 1
    assert archive.total_compressions == 1
    assert archive.memories[0].summary == "前五章摘要"

    # 添加第二条
    meta2 = CompressionMeta(chapter_count=5)
    memory2 = CompressedMemory(
        novel_id=nid,
        range=TimeRange(start_chapter=6, end_chapter=10),
        meta=meta2,
        summary="六到十章摘要",
        events=[],
    )
    archive.memories.append(memory2)
    archive.total_compressions = 2

    assert len(archive.memories) == 2
    assert archive.memories[1].summary == "六到十章摘要"


# ═══════════════════════════════════════════════════
#  L3 — 长期知识
# ═══════════════════════════════════════════════════


def test_long_term_entry_type_values() -> None:
    """LongTermEntryType 包含 CHARACTER_ARCHIVE/WORLD_BIBLE/STYLE_GUIDE 等。"""
    assert LongTermEntryType.CHARACTER_ARCHIVE.value == "character_archive"
    assert LongTermEntryType.WORLD_BIBLE.value == "world_bible"
    assert LongTermEntryType.STYLE_GUIDE.value == "style_guide"
    assert LongTermEntryType.PLOT_BLUEPRINT.value == "plot_blueprint"
    assert LongTermEntryType.NOTE.value == "note"


def test_l3_archive_with_entries() -> None:
    """L3Archive 长期知识条目类型。"""
    nid = uuid4()

    entry = LongTermEntry(
        entry_type=LongTermEntryType.CHARACTER_ARCHIVE,
        title="张三角色档案",
        content="张三，穿越者，性格谨慎，擅长剑术。",
        tags=["主角", "穿越者"],
    )

    archive = L3Archive(novel_id=nid)
    archive.entries[entry.entry_id] = entry
    archive.last_updated_at = None

    assert len(archive.entries) == 1
    stored = archive.entries[entry.entry_id]
    assert stored.entry_type == LongTermEntryType.CHARACTER_ARCHIVE
    assert stored.title == "张三角色档案"
    assert stored.version == 1
    assert "主角" in stored.tags
    assert len(stored.tags) == 2


def test_l3_archive_multiple_types() -> None:
    """L3Archive 支持多种长期知识条目类型。"""
    nid = uuid4()
    archive = L3Archive(novel_id=nid)

    char_entry = LongTermEntry(
        entry_type=LongTermEntryType.CHARACTER_ARCHIVE,
        title="张三",
        content="角色档案内容",
    )
    world_entry = LongTermEntry(
        entry_type=LongTermEntryType.WORLD_BIBLE,
        title="幽冥森林设定",
        content="世界观内容",
    )
    style_entry = LongTermEntry(
        entry_type=LongTermEntryType.STYLE_GUIDE,
        title="文风指南",
        content="风格指南内容",
    )

    archive.entries[char_entry.entry_id] = char_entry
    archive.entries[world_entry.entry_id] = world_entry
    archive.entries[style_entry.entry_id] = style_entry

    assert len(archive.entries) == 3
    types = {e.entry_type for e in archive.entries.values()}
    assert LongTermEntryType.CHARACTER_ARCHIVE in types
    assert LongTermEntryType.WORLD_BIBLE in types
    assert LongTermEntryType.STYLE_GUIDE in types


# ═══════════════════════════════════════════════════
#  压缩管线管理
# ═══════════════════════════════════════════════════


def test_compression_task_status_machine() -> None:
    """CompressionTask 任务状态机（PENDING→RUNNING→COMPLETED）。"""
    nid = uuid4()

    task = CompressionTask(
        novel_id=nid,
        range=TimeRange(start_chapter=1, end_chapter=10),
    )
    assert task.status == CompressionTaskStatus.PENDING
    assert task.started_at is None
    assert task.completed_at is None
    assert task.error_message is None

    # RUNNING
    task.status = CompressionTaskStatus.RUNNING
    assert task.status == CompressionTaskStatus.RUNNING

    # COMPLETED
    task.status = CompressionTaskStatus.COMPLETED
    assert task.status == CompressionTaskStatus.COMPLETED


def test_compression_task_failed() -> None:
    """CompressionTask 任务状态机（PENDING→RUNNING→FAILED）。"""
    nid = uuid4()

    task = CompressionTask(
        novel_id=nid,
        range=TimeRange(start_chapter=1, end_chapter=10),
    )
    assert task.status == CompressionTaskStatus.PENDING

    task.status = CompressionTaskStatus.RUNNING
    assert task.status == CompressionTaskStatus.RUNNING

    task.status = CompressionTaskStatus.FAILED
    task.error_message = "LLM API 调用超时"
    assert task.status == CompressionTaskStatus.FAILED
    assert task.error_message == "LLM API 调用超时"


def test_compression_task_initial_state() -> None:
    """CompressionTask 初始状态为 PENDING。"""
    nid = uuid4()
    task = CompressionTask(
        novel_id=nid,
        range=TimeRange(start_chapter=1, end_chapter=5),
    )
    assert isinstance(task.task_id, UUID)
    assert task.status == CompressionTaskStatus.PENDING
    assert task.error_message is None


def test_compress_strategy_default_config() -> None:
    """CompressStrategy 默认配置。"""
    strategy = CompressStrategy()
    assert strategy.default_granularity == 10
    assert strategy.enable_dynamic_granularity is True
    assert strategy.dynamic_adjustment_threshold == 3
    assert strategy.min_event_count_for_milestone == 1
    assert strategy.max_pending_foreshadowing == 10


def test_compress_strategy_custom_config() -> None:
    """CompressStrategy 可自定义配置。"""
    strategy = CompressStrategy(
        default_granularity=20,
        enable_dynamic_granularity=False,
        dynamic_adjustment_threshold=5,
        min_event_count_for_milestone=2,
        max_pending_foreshadowing=20,
    )
    assert strategy.default_granularity == 20
    assert strategy.enable_dynamic_granularity is False
    assert strategy.dynamic_adjustment_threshold == 5
    assert strategy.min_event_count_for_milestone == 2
    assert strategy.max_pending_foreshadowing == 20


# ═══════════════════════════════════════════════════
#  完整记忆快照
# ═══════════════════════════════════════════════════


def test_memory_snapshot_contains_all_tiers() -> None:
    """MemorySnapshot 包含全部四级数据。"""
    nid = uuid4()
    cid = uuid4()

    # 准备 L1 活跃上下文
    sw = SlidingWindowState(
        novel_id=nid,
        current_chapter_index=5,
        recent_chapters=[3, 4, 5],
    )
    card = CharacterStateCard(
        character_id=cid,
        name="张三",
        current_location="森林",
        recent_action="探索符文",
    )
    ctx = ActiveContext(
        novel_id=nid,
        current_chapter_index=5,
        sliding_window=sw,
        state_cards=[card],
    )

    # 准备 L2 压缩记忆
    meta = CompressionMeta(chapter_count=5)
    memory = CompressedMemory(
        novel_id=nid,
        range=TimeRange(start_chapter=1, end_chapter=5),
        meta=meta,
        summary="前五章摘要",
        events=[],
    )

    # 准备 L3 长期知识
    archive = L3Archive(novel_id=nid)
    entry = LongTermEntry(
        entry_type=LongTermEntryType.CHARACTER_ARCHIVE,
        title="张三",
        content="角色档案",
    )
    archive.entries[entry.entry_id] = entry

    snapshot = MemorySnapshot(
        novel_id=nid,
        active_context=ctx,
        recent_compressed=[memory],
        permanent_archive=archive,
        foreshadowing_notes=["古老符文未回收"],
        pending_compression_tasks=1,
    )

    # L1
    assert snapshot.active_context.current_chapter_index == 5
    assert len(snapshot.active_context.state_cards) == 1

    # L2
    assert len(snapshot.recent_compressed) == 1
    assert snapshot.recent_compressed[0].summary == "前五章摘要"

    # L3
    assert snapshot.permanent_archive is not None
    assert len(snapshot.permanent_archive.entries) == 1

    # 动态调整信息
    assert len(snapshot.foreshadowing_notes) == 1
    assert snapshot.pending_compression_tasks == 1


# ═══════════════════════════════════════════════════
#  通知载荷
# ═══════════════════════════════════════════════════


def test_memory_notification_payload_definition() -> None:
    """MemoryNotificationPayload 正确定义。"""
    nid = uuid4()

    payload = MemoryNotificationPayload(
        novel_id=nid,
        notification_type=MemoryNotification.COMPRESSION_STARTED,
        message="L2 压缩任务已创建",
    )
    assert payload.novel_id == nid
    assert payload.notification_type == MemoryNotification.COMPRESSION_STARTED
    assert payload.message == "L2 压缩任务已创建"
    assert payload.compression_result is None
    assert payload.new_snapshot is None


def test_memory_notification_payload_with_result() -> None:
    """MemoryNotificationPayload 包含压缩结果。"""
    nid = uuid4()
    task_id = uuid4()

    result = CompressionResult(
        task_id=task_id,
        success=True,
    )

    payload = MemoryNotificationPayload(
        novel_id=nid,
        notification_type=MemoryNotification.COMPRESSION_COMPLETED,
        compression_result=result,
        message="第1-10章压缩完成",
    )
    assert payload.notification_type == MemoryNotification.COMPRESSION_COMPLETED
    assert payload.compression_result is not None
    assert payload.compression_result.task_id == task_id
    assert payload.compression_result.success is True
    assert payload.message == "第1-10章压缩完成"


def test_memory_notification_payload_failed() -> None:
    """MemoryNotificationPayload 包含失败信息。"""
    nid = uuid4()
    task_id = uuid4()

    result = CompressionResult(
        task_id=task_id,
        success=False,
        error="LLM 返回空结果",
    )

    payload = MemoryNotificationPayload(
        novel_id=nid,
        notification_type=MemoryNotification.COMPRESSION_FAILED,
        compression_result=result,
        message="压缩失败",
    )
    assert payload.notification_type == MemoryNotification.COMPRESSION_FAILED
    assert payload.compression_result is not None
    assert payload.compression_result.success is False
    assert payload.compression_result.error == "LLM 返回空结果"
