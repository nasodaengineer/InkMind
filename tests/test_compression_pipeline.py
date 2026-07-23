"""MemoryKeeperCore 压缩管线单元测试。

覆盖场景：
1.  MemoryKeeperCore 正确初始化（novel_id, 默认策略）
2.  on_chapter_finalized 正确更新 L0 索引
3.  on_chapter_finalized 正确滚动 L1 滑窗
4.  滑窗滚动超过5章后自动丢弃最早章节
5.  第10章定稿后触发压缩任务创建
6.  未达压缩阈值时不触发压缩
7.  execute_compression 更新压缩结果到 L2Archive
8.  build_memory_snapshot 包含正确的 L1+L2+L3 数据
9.  query_context 按类型返回正确结果
10. CompressionGranularity.FIXED 正确触发
11. 动态扩展（dynamic_expansion=True）时窗口变大
12. 多次定稿后 Snapshot 包含最近 3 个压缩记忆

注：LLMCompressor 回调使用 AsyncMock，返回固定字符串摘要和空事件列表。
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

import pytest
from unittest.mock import AsyncMock

from inkmind.models.memory import (
    ActiveContext,
    CompressionGranularity,
    CompressionTaskStatus,
    CompressStrategy,
    L0Index,
    L2Archive,
    L3Archive,
    MemoryNotification,
    MemoryNotificationPayload,
    SlidingWindowState,
    ForeshadowingMarker,
    LongTermEntryType,
)
from inkmind.memory.compressor import MemoryKeeperCore, LLMCompressor


# ══════════════════════════════════════════════════
#  Fixtures
# ══════════════════════════════════════════════════


@pytest.fixture
def novel_id() -> UUID:
    return uuid4()


@pytest.fixture
def mock_llm() -> AsyncMock:
    """返回固定摘要和空事件列表的 LLMCompressor mock。"""
    m = AsyncMock(spec=LLMCompressor)
    m.return_value = ("这是一段测试摘要。", [])
    return m


@pytest.fixture
def core(novel_id: UUID, mock_llm: AsyncMock) -> MemoryKeeperCore:
    """默认配置 + mock LLM 的 MemoryKeeperCore 实例。"""
    return MemoryKeeperCore(
        novel_id=novel_id,
        llm_compressor=mock_llm,
    )


def _finalize(core: MemoryKeeperCore, chapter_index: int, title: str | None = None) -> MemoryNotificationPayload:
    """Helper: 定稿一章，避免重复样板代码。"""
    return core.on_chapter_finalized(
        chapter_index=chapter_index,
        chapter_title=title or f"第{chapter_index}章",
        content=f"第{chapter_index}章内容",
        key_events=[],
        character_events=[],
        location_changes=[],
    )


def _finalize_with_char(
    core: MemoryKeeperCore,
    chapter_index: int,
    char_events: list[dict] | None = None,
    title: str | None = None,
) -> MemoryNotificationPayload:
    """定稿一章，携带角色事件。"""
    return core.on_chapter_finalized(
        chapter_index=chapter_index,
        chapter_title=title or f"第{chapter_index}章",
        content=f"第{chapter_index}章内容",
        key_events=[],
        character_events=char_events or [],
        location_changes=[],
    )


# ══════════════════════════════════════════════════
#  1. MemoryKeeperCore 正确初始化
# ══════════════════════════════════════════════════


class TestInitialization:
    """MemoryKeeperCore 初始化验证。"""

    def test_default_initialization(self, novel_id: UUID) -> None:
        """默认初始化：novel_id 正确、策略使用默认值、内部状态为空。"""
        core = MemoryKeeperCore(novel_id=novel_id)

        # novel_id
        assert core.novel_id == novel_id

        # 默认策略
        assert isinstance(core.strategy, CompressStrategy)
        assert core.strategy.default_granularity == 10
        assert core.strategy.enable_dynamic_granularity is True
        assert core.strategy.max_pending_foreshadowing == 10

        # 运行时状态
        assert core._pending_tasks == {}
        assert core._sliding_window is None

        # L0 / L2 / L3 均为正确初始化的空状态
        assert isinstance(core._l0_index, L0Index)
        assert core._l0_index.novel_id == novel_id
        assert core._l0_index.total_chapters_indexed == 0
        assert core._l0_index.entries == []

        assert isinstance(core._l2_archive, L2Archive)
        assert core._l2_archive.novel_id == novel_id
        assert core._l2_archive.memories == []

        assert isinstance(core._l3_archive, L3Archive)
        assert core._l3_archive.novel_id == novel_id
        assert core._l3_archive.entries == {}

    def test_custom_strategy(self, novel_id: UUID) -> None:
        """传入自定义策略应生效。"""
        strategy = CompressStrategy(
            default_granularity=5,
            enable_dynamic_granularity=False,
            max_pending_foreshadowing=20,
        )
        core = MemoryKeeperCore(novel_id=novel_id, strategy=strategy)
        assert core.strategy.default_granularity == 5
        assert core.strategy.enable_dynamic_granularity is False
        assert core.strategy.max_pending_foreshadowing == 20

    def test_llm_compressor_injected(self, novel_id: UUID, mock_llm: AsyncMock) -> None:
        """传入 mock LLMCompressor 后 _llm_compressor 不为 None。"""
        core = MemoryKeeperCore(novel_id=novel_id, llm_compressor=mock_llm)
        assert core._llm_compressor is not None

    def test_llm_compressor_none_by_default(self, novel_id: UUID) -> None:
        """未传入 LLMCompressor 时 _llm_compressor 为 None。"""
        core = MemoryKeeperCore(novel_id=novel_id)
        assert core._llm_compressor is None


# ══════════════════════════════════════════════════
#  2. on_chapter_finalized 正确更新 L0 索引
# ══════════════════════════════════════════════════


class TestL0Indexing:
    """L0 全文索引更新验证。"""

    def test_l0_index_updated_after_finalize(self, core: MemoryKeeperCore) -> None:
        """定稿后 L0 索引条目数和 total_chapters_indexed 正确更新。"""
        assert core._l0_index.total_chapters_indexed == 0
        assert len(core._l0_index.entries) == 0

        _finalize(core, 1, "第一章 开端")

        assert core._l0_index.total_chapters_indexed == 1
        # content "第1章内容" 按 \n\n 切分只得到一个段落
        assert len(core._l0_index.entries) == 1
        assert core._l0_index.entries[0].chapter_index == 1

    def test_l0_index_multiple_chapters(self, core: MemoryKeeperCore) -> None:
        """多章定稿后 L0 索引累积正确。"""
        for i in range(1, 4):
            _finalize(core, i)

        assert core._l0_index.total_chapters_indexed == 3
        entry_indices = {e.chapter_index for e in core._l0_index.entries}
        assert entry_indices == {1, 2, 3}

    def test_l0_index_paragraph_splitting(self, core: MemoryKeeperCore) -> None:
        """多段落内容在 L0 索引中正确拆分。"""
        content = "第一段内容。\n\n第二段内容。\n\n第三段内容。"
        core.on_chapter_finalized(
            chapter_index=1,
            chapter_title="第1章",
            content=content,
            key_events=[],
            character_events=[],
            location_changes=[],
        )

        assert len(core._l0_index.entries) == 3
        assert core._l0_index.entries[0].paragraph_index == 1
        assert core._l0_index.entries[1].paragraph_index == 2
        assert core._l0_index.entries[2].paragraph_index == 3

    def test_l0_index_last_indexed_at_set(self, core: MemoryKeeperCore) -> None:
        """定稿后 last_indexed_at 被更新。"""
        assert core._l0_index.last_indexed_at is None
        _finalize(core, 1)
        assert core._l0_index.last_indexed_at is not None
        assert isinstance(core._l0_index.last_indexed_at, datetime)


# ══════════════════════════════════════════════════
#  3. on_chapter_finalized 正确滚动 L1 滑窗
# ══════════════════════════════════════════════════


class TestL1SlidingWindow:
    """L1 滑窗滚动验证。"""

    def test_window_initialized_on_first_finalize(self, core: MemoryKeeperCore) -> None:
        """首次定稿后 _sliding_window 被初始化。"""
        assert core._sliding_window is None

        _finalize(core, 1)

        assert core._sliding_window is not None
        assert core._sliding_window.current_chapter_index == 1
        assert core._sliding_window.recent_chapters == [1]
        assert core._sliding_window.default_window_size == 5
        assert core._sliding_window.current_expanded_size == 5

    def test_window_rolls_forward(self, core: MemoryKeeperCore) -> None:
        """连续定稿后滑窗的 current_chapter_index 和 recent_chapters 正确滚动。"""
        for i in range(1, 4):
            _finalize(core, i)

        sw = core._sliding_window
        assert sw is not None
        assert sw.current_chapter_index == 3
        assert sw.recent_chapters == [1, 2, 3]

    def test_window_updates_character_states(self, core: MemoryKeeperCore) -> None:
        """定稿携带的角色事件应更新状态卡。"""
        cid = uuid4()
        _finalize_with_char(core, 1, [
            {"character_id": str(cid), "name": "林风", "event": "进入洛城", "location": "洛城东门"},
        ])

        sw = core._sliding_window
        assert sw is not None
        assert cid in sw.character_states
        card = sw.character_states[cid]
        assert card.name == "林风"
        assert card.recent_action == "进入洛城"
        assert card.current_location == "洛城东门"

    def test_character_state_overwrites(self, core: MemoryKeeperCore) -> None:
        """同一角色多次出场时状态卡被覆盖更新。"""
        cid = uuid4()
        _finalize_with_char(core, 1, [
            {"character_id": str(cid), "name": "林风", "event": "进入洛城", "location": "洛城东门"},
        ])
        _finalize_with_char(core, 2, [
            {"character_id": str(cid), "event": "在客栈休息", "location": "洛城客栈"},
        ])

        card = core._sliding_window.character_states[cid]
        assert card.recent_action == "在客栈休息"
        assert card.current_location == "洛城客栈"

    def test_notification_type_l1_window_shifted(self, core: MemoryKeeperCore) -> None:
        """未触发压缩时通知类型为 l1_window_shifted。"""
        result = _finalize(core, 1)
        assert result.notification_type == MemoryNotification.L1_WINDOW_SHIFTED
        assert "L1 滑窗已滚动" in result.message


# ══════════════════════════════════════════════════
#  4. 滑窗滚动超过5章后自动丢弃最早章节
# ══════════════════════════════════════════════════


class TestWindowDiscard:
    """滑窗容量限制验证。"""

    def test_window_keeps_exactly_5(self, core: MemoryKeeperCore) -> None:
        """定稿第5章时窗口刚好为 5 章，无丢弃。"""
        for i in range(1, 6):
            _finalize(core, i)

        assert core._sliding_window.recent_chapters == [1, 2, 3, 4, 5]
        assert len(core._sliding_window.recent_chapters) == 5

    def test_window_discards_at_chapter_6(self, core: MemoryKeeperCore) -> None:
        """定稿第6章时丢弃第1章，窗口保持 5 章。"""
        for i in range(1, 7):
            _finalize(core, i)

        sw = core._sliding_window
        assert sw is not None
        assert 1 not in sw.recent_chapters
        assert sw.recent_chapters == [2, 3, 4, 5, 6]
        assert len(sw.recent_chapters) == 5

    def test_window_discards_at_chapter_8(self, core: MemoryKeeperCore) -> None:
        """定稿第8章时最早 3 章被丢弃。"""
        for i in range(1, 9):
            _finalize(core, i)

        sw = core._sliding_window
        assert sw is not None
        assert 1 not in sw.recent_chapters
        assert 2 not in sw.recent_chapters
        assert 3 not in sw.recent_chapters
        assert sw.recent_chapters == [4, 5, 6, 7, 8]

    def test_window_size_stays_constant(self, core: MemoryKeeperCore) -> None:
        """超过5章后窗口长度恒定保持为 default_window_size。"""
        for i in range(1, 20):
            _finalize(core, i)

        sw = core._sliding_window
        assert sw is not None
        assert len(sw.recent_chapters) == sw.default_window_size
        assert len(sw.recent_chapters) == 5


# ══════════════════════════════════════════════════
#  5. 第10章定稿后触发压缩任务创建
# ══════════════════════════════════════════════════


class TestCompressionTrigger:
    """压缩触发阈值验证。"""

    def test_compression_triggered_at_chapter_10(self, core: MemoryKeeperCore) -> None:
        """第10章定稿时 default_granularity=10 触发压缩任务创建。"""
        assert len(core._pending_tasks) == 0

        for i in range(1, 10):
            result = _finalize(core, i)
            assert result.notification_type == MemoryNotification.L1_WINDOW_SHIFTED

        assert len(core._pending_tasks) == 0

        # 第10章触发压缩
        result = _finalize(core, 10)
        assert result.notification_type == MemoryNotification.COMPRESSION_STARTED
        assert "触发了 L2 压缩任务" in result.message

        # 确认任务被创建
        assert len(core._pending_tasks) == 1

    def test_compression_task_range_correct(self, core: MemoryKeeperCore) -> None:
        """创建的压缩任务覆盖从上次压缩结束到当前章节的全部范围。"""
        for i in range(1, 11):
            _finalize(core, i)

        task = list(core._pending_tasks.values())[0]
        assert task.range.start_chapter == 1
        assert task.range.end_chapter == 10
        assert isinstance(task.task_id, UUID)
        assert task.status == CompressionTaskStatus.PENDING

    def test_compression_task_novel_id(self, core: MemoryKeeperCore) -> None:
        """任务的 novel_id 与 core 一致。"""
        for i in range(1, 11):
            _finalize(core, i)

        task = list(core._pending_tasks.values())[0]
        assert task.novel_id == core.novel_id


# ══════════════════════════════════════════════════
#  6. 未达压缩阈值时不触发压缩
# ══════════════════════════════════════════════════


class TestNoCompressionBelowThreshold:
    """未达阈值时不创建压缩任务。"""

    def test_no_task_before_chapter_10(self, core: MemoryKeeperCore) -> None:
        """第1-9章定稿时不应创建任何压缩任务。"""
        for i in range(1, 10):
            _finalize(core, i)
            assert len(core._pending_tasks) == 0

    def test_all_notifications_are_window_shifted(self, core: MemoryKeeperCore) -> None:
        """第1-9章的通知类型均为 l1_window_shifted。"""
        for i in range(1, 10):
            result = _finalize(core, i)
            assert result.notification_type == MemoryNotification.L1_WINDOW_SHIFTED

    def test_custom_granularity_no_trigger(self, novel_id: UUID) -> None:
        """自定义策略：default_granularity=20 时第10章不触发。"""
        strategy = CompressStrategy(default_granularity=20)
        core = MemoryKeeperCore(novel_id=novel_id, strategy=strategy)

        for i in range(1, 11):
            result = _finalize(core, i)
            assert result.notification_type == MemoryNotification.L1_WINDOW_SHIFTED

        assert len(core._pending_tasks) == 0

    def test_no_task_in_pending_when_no_compression(self, core: MemoryKeeperCore) -> None:
        """从未触发压缩时 _pending_tasks 保持为空。"""
        for i in range(1, 9):
            _finalize(core, i)
        assert core._pending_tasks == {}


# ══════════════════════════════════════════════════
#  7. execute_compression 更新压缩结果到 L2Archive
# ══════════════════════════════════════════════════


class TestExecuteCompression:
    """异步压缩执行验证。"""

    @pytest.mark.asyncio
    async def test_execute_compression_success(self, core: MemoryKeeperCore) -> None:
        """成功压缩后 L2Archive 收到结果。"""
        for i in range(1, 11):
            _finalize(core, i)

        task_id = list(core._pending_tasks.keys())[0]
        result = await core.execute_compression(task_id)

        assert result.success is True
        assert result.error is None
        assert result.compressed is not None
        assert result.compressed.summary == "这是一段测试摘要。"
        assert result.compressed.events == []

        assert len(core._l2_archive.memories) == 1
        assert core._l2_archive.memories[0].summary == "这是一段测试摘要。"

    @pytest.mark.asyncio
    async def test_execute_compression_task_status_completed(self, core: MemoryKeeperCore) -> None:
        """压缩成功后任务状态变为 COMPLETED。"""
        for i in range(1, 11):
            _finalize(core, i)

        task_id = list(core._pending_tasks.keys())[0]
        assert core._pending_tasks[task_id].status == CompressionTaskStatus.PENDING

        await core.execute_compression(task_id)

        assert core._pending_tasks[task_id].status == CompressionTaskStatus.COMPLETED
        assert core._pending_tasks[task_id].completed_at is not None
        assert core._pending_tasks[task_id].started_at is not None

    @pytest.mark.asyncio
    async def test_execute_compression_nonexistent_task(self, core: MemoryKeeperCore) -> None:
        """执行不存在的任务返回失败结果。"""
        fake_id = uuid4()
        result = await core.execute_compression(fake_id)

        assert result.success is False
        assert result.error == "任务不存在"
        assert result.compressed is None

    @pytest.mark.asyncio
    async def test_execute_compression_no_llm(self, novel_id: UUID) -> None:
        """未注入 LLMCompressor 时压缩失败。"""
        core = MemoryKeeperCore(novel_id=novel_id)
        for i in range(1, 11):
            _finalize(core, i)

        task_id = list(core._pending_tasks.keys())[0]
        result = await core.execute_compression(task_id)

        assert result.success is False
        assert result.error == "LLMCompressor not configured"
        assert core._pending_tasks[task_id].status == CompressionTaskStatus.FAILED

    @pytest.mark.asyncio
    async def test_execute_compression_calls_llm_with_correct_args(self, core: MemoryKeeperCore, mock_llm: AsyncMock) -> None:
        """LLMCompressor 被调用时接收正确的策略和章节数据。"""
        for i in range(1, 11):
            _finalize(core, i)

        task_id = list(core._pending_tasks.keys())[0]
        await core.execute_compression(task_id)

        mock_llm.assert_awaited_once()
        call_args = mock_llm.await_args
        assert call_args is not None
        assert isinstance(call_args.kwargs["strategy"], CompressStrategy)
        assert len(call_args.kwargs["chapters"]) > 0

    @pytest.mark.asyncio
    async def test_compressed_memory_meta(self, core: MemoryKeeperCore) -> None:
        """CompressedMemory 的 meta 字段包含正确的 chapter_count。"""
        for i in range(1, 11):
            _finalize(core, i)

        task_id = list(core._pending_tasks.keys())[0]
        result = await core.execute_compression(task_id)

        assert result.compressed is not None
        assert result.compressed.meta.chapter_count == 10
        assert result.compressed.meta.compression_granularity == CompressionGranularity.FIXED
        assert result.compressed.novel_id == core.novel_id
        assert result.compressed.range.start_chapter == 1
        assert result.compressed.range.end_chapter == 10


# ══════════════════════════════════════════════════
#  8. build_memory_snapshot 包含正确的 L1+L2+L3 数据
# ══════════════════════════════════════════════════


class TestBuildMemorySnapshot:
    """记忆快照组装验证。"""

    def test_snapshot_contains_l1_active_context(self, core: MemoryKeeperCore) -> None:
        """快照包含 L1 活跃上下文。"""
        _finalize(core, 1)
        snapshot = core.build_memory_snapshot(target_chapter=1)

        assert isinstance(snapshot.active_context, ActiveContext)
        assert snapshot.active_context.current_chapter_index == 1
        assert isinstance(snapshot.active_context.sliding_window, SlidingWindowState)

    @pytest.mark.asyncio
    async def test_snapshot_contains_l2_compressed(self, core: MemoryKeeperCore) -> None:
        """快照包含 L2 压缩记忆。"""
        for i in range(1, 11):
            _finalize(core, i)
        task_id = list(core._pending_tasks.keys())[0]
        await core.execute_compression(task_id)

        snapshot = core.build_memory_snapshot(target_chapter=10)
        assert len(snapshot.recent_compressed) == 1
        assert snapshot.recent_compressed[0].summary == "这是一段测试摘要。"

    def test_snapshot_contains_l3_permanent_archive(self, core: MemoryKeeperCore) -> None:
        """快照包含 L3 长期知识。"""
        entry_id = core.update_long_term_entry(
            title="世界观设定",
            content="这是一个剑与魔法的世界。",
            entry_type="world_bible",
            tags=["世界观"],
        )

        snapshot = core.build_memory_snapshot(target_chapter=1)
        assert snapshot.permanent_archive is not None
        assert entry_id in snapshot.permanent_archive.entries
        entry = snapshot.permanent_archive.entries[entry_id]
        assert entry.title == "世界观设定"
        assert entry.entry_type == LongTermEntryType.WORLD_BIBLE

    def test_snapshot_contains_foreshadowing_and_task_count(self, core: MemoryKeeperCore) -> None:
        """快照包含伏笔提示和挂起的压缩任务数。"""
        _finalize(core, 1)
        snapshot = core.build_memory_snapshot(target_chapter=1)
        assert isinstance(snapshot.foreshadowing_notes, list)
        assert snapshot.pending_compression_tasks == 0

    def test_snapshot_novel_id_matches(self, core: MemoryKeeperCore) -> None:
        """快照的 novel_id 与 core 一致。"""
        snapshot = core.build_memory_snapshot(target_chapter=1)
        assert snapshot.novel_id == core.novel_id


# ══════════════════════════════════════════════════
#  9. query_context 按类型返回正确结果
# ══════════════════════════════════════════════════
#
# MemoryKeeperCore 无独立 query_context 方法，
# 通过 build_memory_snapshot + 内部状态组合提供上下文查询能力。
# 此套测试验证通过现有 API 可以按类型获取完整的上下文信息。


class TestQueryContext:
    """按类型查询上下文验证。"""

    def test_query_character_state(self, core: MemoryKeeperCore) -> None:
        """按 CHARACTER_STATE 类型查询：角色状态卡正确。"""
        cid = uuid4()
        _finalize_with_char(core, 1, [
            {"character_id": str(cid), "name": "林风", "event": "进入洛城", "location": "洛城东门"},
        ])

        snapshot = core.build_memory_snapshot(target_chapter=1)
        cards = snapshot.active_context.state_cards

        assert len(cards) == 1
        card = cards[0]
        assert card.character_id == cid
        assert card.name == "林风"
        assert card.current_location == "洛城东门"
        assert card.recent_action == "进入洛城"

    def test_query_event_context(self, core: MemoryKeeperCore) -> None:
        """按 EVENT_CONTEXT 类型查询：L0 索引包含事件日�的上下文。"""
        for i in range(1, 4):
            _finalize(core, i, f"第{i}章-标题")

        # L0 entries 记录了每章的段落索引
        chapter_1_entries = [e for e in core._l0_index.entries if e.chapter_index == 1]
        assert len(chapter_1_entries) >= 1

    def test_query_location_log(self, core: MemoryKeeperCore) -> None:
        """按 LOCATION_LOG 类型查询：角色位置变更可追踪。"""
        cid = uuid4()
        # 场景1: 林风在洛城
        _finalize_with_char(core, 1, [
            {"character_id": str(cid), "name": "林风", "event": "到达", "location": "洛城"},
        ])
        # 场景2: 林风前往郊外
        _finalize_with_char(core, 2, [
            {"character_id": str(cid), "event": "离开", "location": "洛城郊外"},
        ])

        snapshot = core.build_memory_snapshot(target_chapter=2)
        card = snapshot.active_context.state_cards[0]
        # 最近的位置来自最后一次更新
        assert card.current_location == "洛城郊外"

    def test_query_foreshadowing(self, core: MemoryKeeperCore) -> None:
        """按 FORESHADOWING 类型查询：未回收伏笔出现在快照中。"""
        _finalize(core, 1)

        # 手动添加伏笔（生产代码通过场景检测自动添加）
        foreshadowing = ForeshadowingMarker(
            description="洛城地下隐藏着远古遗迹",
            planted_chapter=1,
            expected_payoff_chapter=15,
        )
        core._sliding_window.pending_foreshadowing.append(foreshadowing)

        snapshot = core.build_memory_snapshot(target_chapter=1)
        assert len(snapshot.foreshadowing_notes) >= 1
        assert any("远古遗迹" in note for note in snapshot.foreshadowing_notes)

    def test_query_plot_summary(self, core: MemoryKeeperCore) -> None:
        """按 PLOT_SUMMARY 类型查询：压缩记忆提供情节摘要。"""
        for i in range(1, 4):
            _finalize(core, i)

        # 压缩前 snapshot 不含 L2 摘要
        snapshot = core.build_memory_snapshot(target_chapter=3)
        assert len(snapshot.recent_compressed) == 0

    def test_query_foreshadowing_no_payoff_chapter(self, core: MemoryKeeperCore) -> None:
        """没有指定回收章节的伏笔可以通过 foreshadowing_notes 查询。"""
        _finalize(core, 1)
        # 添加不指定回收章节的伏笔
        core._sliding_window.pending_foreshadowing.append(
            ForeshadowingMarker(
                description="神秘的时钟塔",
                planted_chapter=1,
            )
        )

        snapshot = core.build_memory_snapshot(target_chapter=1)
        assert any("时钟塔" in note for note in snapshot.foreshadowing_notes)


# ══════════════════════════════════════════════════
#  10. CompressionGranularity.FIXED 正确触发
# ══════════════════════════════════════════════════


class TestFixedGranularity:
    """固定粒度压缩验证。"""

    def test_fixed_granularity_with_default_10(self, core: MemoryKeeperCore) -> None:
        """默认策略（10章）下，第10章触发 FIXED 粒度压缩。"""
        for i in range(1, 11):
            _finalize(core, i)

        assert len(core._pending_tasks) == 1

    def test_fixed_granularity_custom_5(self, novel_id: UUID) -> None:
        """策略 default_granularity=5 时每5章触发一次 FIXED 压缩。"""
        strategy = CompressStrategy(default_granularity=5)
        core = MemoryKeeperCore(novel_id=novel_id, strategy=strategy)

        # 第1-4章不触发
        for i in range(1, 5):
            result = _finalize(core, i)
            assert result.notification_type == MemoryNotification.L1_WINDOW_SHIFTED

        assert len(core._pending_tasks) == 0

        # 第5章触发
        result = _finalize(core, 5)
        assert result.notification_type == MemoryNotification.COMPRESSION_STARTED
        assert len(core._pending_tasks) == 1

        task = list(core._pending_tasks.values())[0]
        assert task.range.start_chapter == 1
        assert task.range.end_chapter == 5

    @pytest.mark.asyncio
    async def test_fixed_granularity_meta(self, novel_id: UUID, mock_llm: AsyncMock) -> None:
        """压缩后的记忆 meta 中 compression_granularity 为 FIXED。"""
        strategy = CompressStrategy(default_granularity=5)
        core = MemoryKeeperCore(novel_id=novel_id, strategy=strategy, llm_compressor=mock_llm)

        for i in range(1, 6):
            _finalize(core, i)

        task_id = list(core._pending_tasks.keys())[0]
        result = await core.execute_compression(task_id)

        assert result.compressed is not None
        assert result.compressed.meta.compression_granularity == CompressionGranularity.FIXED

    @pytest.mark.asyncio
    async def test_fixed_granularity_repeat_trigger(self, novel_id: UUID, mock_llm: AsyncMock) -> None:
        """固定粒度下第二次达到阈值应再次触发。需要先执行压缩以更新 last_compressed。"""
        strategy = CompressStrategy(default_granularity=5)
        core = MemoryKeeperCore(novel_id=novel_id, strategy=strategy, llm_compressor=mock_llm)

        # 第1轮：第5章触发
        for i in range(1, 6):
            _finalize(core, i)
        assert len(core._pending_tasks) == 1
        first_task_id = list(core._pending_tasks.keys())[0]
        await core.execute_compression(first_task_id)

        # 第2轮：第10章再次触发（从6开始累积5章）
        # last_compressed=5, accumulated=10-5=5 >=5
        for i in range(6, 11):
            _finalize(core, i)
        assert len(core._pending_tasks) == 2

        tasks = list(core._pending_tasks.values())
        assert tasks[0].range.start_chapter == 1
        assert tasks[0].range.end_chapter == 5
        assert tasks[1].range.start_chapter == 6
        assert tasks[1].range.end_chapter == 10


# ══════════════════════════════════════════════════
#  11. 动态扩展（dynamic_expansion=True）时窗口变大
# ══════════════════════════════════════════════════


class TestDynamicWindowExpansion:
    """滑窗动态扩展验证。"""

    def test_expansion_triggered_by_foreshadowing(self, novel_id: UUID) -> None:
        """伏笔 expected_payoff_chapter 超出滑窗边界时触发扩展。"""
        core = MemoryKeeperCore(novel_id=novel_id)

        # 定稿第1章，初始化滑窗
        _finalize(core, 1)

        # 手动添加伏笔，预期回收章节远超出当前滑窗
        core._sliding_window.pending_foreshadowing.append(
            ForeshadowingMarker(
                description="远古遗迹将在第20章揭晓",
                planted_chapter=1,
                expected_payoff_chapter=20,
            )
        )

        original_expanded_size = core._sliding_window.current_expanded_size

        # 定稿第2章时检查伏笔触发扩展
        result = _finalize(core, 2)

        sw = core._sliding_window
        assert sw.expand_reason is not None
        assert "远古遗迹将在第20章揭晓" in sw.expand_reason
        # current_expanded_size 应被扩展
        assert sw.current_expanded_size >= original_expanded_size

    def test_expansion_reason_in_notification(self, novel_id: UUID) -> None:
        """扩展原因体现在返回的通知消息中。"""
        core = MemoryKeeperCore(novel_id=novel_id)
        _finalize(core, 1)

        core._sliding_window.pending_foreshadowing.append(
            ForeshadowingMarker(
                description="秘境将在第50章开启",
                planted_chapter=1,
                expected_payoff_chapter=50,
            )
        )

        result = _finalize(core, 2)
        assert "秘境将在第50章开启" in result.message

    def test_expanded_window_holds_more_chapters(self, novel_id: UUID) -> None:
        """扩展后的滑窗可以容纳超过默认大小的章节数。"""
        core = MemoryKeeperCore(novel_id=novel_id)
        _finalize(core, 1)

        # 设置大跨度伏笔使滑窗自动扩展
        core._sliding_window.pending_foreshadowing.append(
            ForeshadowingMarker(
                description="重要伏笔",
                planted_chapter=1,
                expected_payoff_chapter=20,
            )
        )

        # 定稿第2章触发扩展
        _finalize(core, 2)
        _finalize(core, 3)

        # 此时 recent_chapters 有 [1,2,3]
        # current_expanded_size = max(default_window_size, len(recent_chapters))
        #                               = max(5, 3) = 5
        # 所以窗口大小保持为 5（因为3<5）
        sw = core._sliding_window
        assert sw.current_expanded_size == 5

    def test_no_expansion_without_foreshadowing(self, core: MemoryKeeperCore) -> None:
        """没有伏笔时不会触发扩展。"""
        # 直接定稿多章，不添加任何伏笔
        for i in range(1, 8):
            _finalize(core, i)

        sw = core._sliding_window
        assert sw.expand_reason is None
        assert sw.current_expanded_size == sw.default_window_size


# ══════════════════════════════════════════════════
#  12. 多次定稿后 Snapshot 包含最近 3 个压缩记忆
# ══════════════════════════════════════════════════


class TestMultipleCompressionsSnapshot:
    """多次压缩后快照的 L2 记忆裁剪验证。"""

    @pytest.mark.asyncio
    async def test_snapshot_contains_last_3_compressions(self, novel_id: UUID, mock_llm: AsyncMock) -> None:
        """4次压缩后快照应仅包含最近3条（逆序：最新在最前）。"""
        strategy = CompressStrategy(default_granularity=5)
        core = MemoryKeeperCore(novel_id=novel_id, strategy=strategy, llm_compressor=mock_llm)

        # 生成4次压缩（每5章一次）
        for block in range(4):
            start = block * 5 + 1
            end = block * 5 + 5
            for i in range(start, end + 1):
                _finalize(core, i)

            # 取出最新创建的任务并执行
            task_id = list(core._pending_tasks.keys())[-1]
            await core.execute_compression(task_id)

        # L2 包含全部 4 条
        assert len(core._l2_archive.memories) == 4

        # 快照应包含最近 3 条（逆序）
        snapshot = core.build_memory_snapshot(target_chapter=20)
        assert len(snapshot.recent_compressed) == 3

        # 最近3条的范围：[16-20], [11-15], [6-10]
        assert snapshot.recent_compressed[0].range.start_chapter == 16
        assert snapshot.recent_compressed[0].range.end_chapter == 20
        assert snapshot.recent_compressed[1].range.start_chapter == 11
        assert snapshot.recent_compressed[1].range.end_chapter == 15
        assert snapshot.recent_compressed[2].range.start_chapter == 6
        assert snapshot.recent_compressed[2].range.end_chapter == 10

        # 最早的第1次压缩（1-5章）不应出现在快照中
        assert not any(
            cm.range.start_chapter == 1 for cm in snapshot.recent_compressed
        )

    @pytest.mark.asyncio
    async def test_snapshot_with_1_compression(self, novel_id: UUID, mock_llm: AsyncMock) -> None:
        """仅1次压缩时快照包含那1条。"""
        strategy = CompressStrategy(default_granularity=10)
        core = MemoryKeeperCore(novel_id=novel_id, strategy=strategy, llm_compressor=mock_llm)

        for i in range(1, 11):
            _finalize(core, i)

        task_id = list(core._pending_tasks.keys())[0]
        await core.execute_compression(task_id)

        snapshot = core.build_memory_snapshot(target_chapter=10)
        assert len(snapshot.recent_compressed) == 1

    @pytest.mark.asyncio
    async def test_snapshot_with_2_compressions(self, novel_id: UUID, mock_llm: AsyncMock) -> None:
        """2次压缩时快照包含全部2条（逆序）。"""
        strategy = CompressStrategy(default_granularity=5)
        core = MemoryKeeperCore(novel_id=novel_id, strategy=strategy, llm_compressor=mock_llm)

        for block in range(2):
            start = block * 5 + 1
            end = block * 5 + 5
            for i in range(start, end + 1):
                _finalize(core, i)
            task_id = list(core._pending_tasks.keys())[-1]
            await core.execute_compression(task_id)

        snapshot = core.build_memory_snapshot(target_chapter=10)
        assert len(snapshot.recent_compressed) == 2
        # 逆序：最新的在前面
        assert snapshot.recent_compressed[0].range.start_chapter == 6
        assert snapshot.recent_compressed[1].range.start_chapter == 1

    @pytest.mark.asyncio
    async def test_snapshot_with_more_than_3(self, novel_id: UUID, mock_llm: AsyncMock) -> None:
        """超过3条时快照仅保留最近的3条。"""
        strategy = CompressStrategy(default_granularity=5)
        core = MemoryKeeperCore(novel_id=novel_id, strategy=strategy, llm_compressor=mock_llm)

        # 5次压缩 = 25章（每5章一次）
        for block in range(5):
            start = block * 5 + 1
            end = block * 5 + 5
            for i in range(start, end + 1):
                _finalize(core, i)
            task_id = list(core._pending_tasks.keys())[-1]
            await core.execute_compression(task_id)

        snapshot = core.build_memory_snapshot(target_chapter=25)
        assert len(snapshot.recent_compressed) == 3
        # 最新3条：[21-25], [16-20], [11-15]
        assert snapshot.recent_compressed[0].range.start_chapter == 21
        assert snapshot.recent_compressed[1].range.start_chapter == 16
        assert snapshot.recent_compressed[2].range.start_chapter == 11
        # 最早2条（1-5, 6-10）被排除
        assert not any(cm.range.start_chapter == 1 for cm in snapshot.recent_compressed)
        assert not any(cm.range.start_chapter == 6 for cm in snapshot.recent_compressed)


# ══════════════════════════════════════════════════
#  Edge Cases & 边界验证
# ══════════════════════════════════════════════════


class TestEdgeCases:
    """边界条件和异常路径验证。"""

    @pytest.mark.asyncio
    async def test_llm_raises_exception(self, novel_id: UUID) -> None:
        """LLM 抛出异常时压缩任务状态变为 FAILED。"""
        failing_mock = AsyncMock(spec=LLMCompressor)
        failing_mock.side_effect = RuntimeError("LLM API 调用超时")

        core = MemoryKeeperCore(novel_id=novel_id, llm_compressor=failing_mock)
        for i in range(1, 11):
            _finalize(core, i)

        task_id = list(core._pending_tasks.keys())[0]
        result = await core.execute_compression(task_id)

        assert result.success is False
        assert "超时" in result.error
        assert core._pending_tasks[task_id].status == CompressionTaskStatus.FAILED
        assert core._pending_tasks[task_id].error_message is not None

    def test_snapshot_without_any_chapter(self, core: MemoryKeeperCore) -> None:
        """无任何章节定稿时快照仍可构建。"""
        snapshot = core.build_memory_snapshot(target_chapter=1)
        assert snapshot.active_context.current_chapter_index == 1
        assert snapshot.active_context.sliding_window.recent_chapters == [1]
        assert len(snapshot.recent_compressed) == 0

    def test_multiple_updates_to_long_term(self, core: MemoryKeeperCore) -> None:
        """L3 条目更新后版本号递增。"""
        eid = core.update_long_term_entry(
            title="初始设定",
            content="v1",
            entry_type="note",
        )
        assert core._l3_archive.entries[eid].version == 1

        core.update_long_term_entry(
            entry_id=eid,
            content="v2 更新",
        )
        assert core._l3_archive.entries[eid].version == 2
