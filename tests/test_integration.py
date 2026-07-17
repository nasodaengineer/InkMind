"""集成测试：跨模块交互全链路。

覆盖工单 #01-#05 的所有核心模块交互：
  - 领域模型 ↔ 序列化 ↔ Repository ↔ UnitOfWork（T1-T5 事务边界）
  - Writer → Planner → Editor → MemoryKeeper 全流水线
  - Idempotency 幂等去重
  - JSONSnapshot 快照导出/恢复
  - RecoveryManager 故障恢复
"""

from __future__ import annotations

import json
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio

from inkmind.models.agent import (
    AgentPacket,
    ChapterStatus,
    DraftPayload,
    PipelineState,
    VerdictPayload,
)
from inkmind.models.chapter import Chapter, ChapterVersion
from inkmind.models.character import Character, CharacterTimelineEntry
from inkmind.models.novel import Novel, NovelMetadata
from inkmind.models.world import World, Faction, Location, PowerSystem, PowerAbility
from inkmind.models.memory import (
    CompressionTask,
    CompressionTaskStatus,
    L0Index,
    L2Archive,
    L3Archive,
    SlidingWindowState,
)
from inkmind.storage import (
    DatabaseManager,
    IdempotencyGuard,
    JSONSnapshot,
    RecoveryManager,
    UnitOfWork,
)
from inkmind.storage.database import get_manager
from inkmind.storage.models import (
    Base,
    ChapterModel,
    ChapterVersionModel,
    ProcessedDigestModel,
    CompressionTaskModel,
    MemoryArchiveModel,
)


# ═══════════════════════════════════════════════════════════════
#  Fixtures
# ═══════════════════════════════════════════════════════════════


@pytest_asyncio.fixture
async def db():
    """内存数据库，每次测试重建表。"""
    mgr = get_manager(":memory:")
    await mgr.create_tables()
    yield mgr
    await mgr.drop_tables()
    await mgr.close()


@pytest_asyncio.fixture
async def session(db):
    async with db.session() as s:
        yield s


@pytest_asyncio.fixture
async def uow(session: AsyncSession):
    return UnitOfWork(session)


@pytest.fixture
def novel_id():
    return uuid4()


@pytest.fixture
def sample_novel(novel_id: UUID) -> Novel:
    return Novel(
        id=novel_id,
        title="星辰之海",
        metadata=NovelMetadata(
            description="一个关于星空与命运的故事",
            word_count=0,
            chapter_count=0,
            status="draft",
        ),
    )


@pytest.fixture
def sample_chapter(novel_id: UUID) -> Chapter:
    return Chapter(
        id=uuid4(),
        novel_id=novel_id,
        index=1,
        title="启程",
        content="夜空中最亮的星，指引着迷途的旅人。",
        status=ChapterStatus.WRITING,
        summary="第一章，主角踏上旅程。",
        key_events=["主角启程"],
        source_trace="deepseek-v4-flash",
    )


@pytest.fixture
def sample_chapter_v2(novel_id: UUID, sample_chapter: Chapter) -> Chapter:
    return Chapter(
        id=sample_chapter.id,
        novel_id=novel_id,
        index=1,
        title="启程",
        content="夜空中最亮的星，指引着迷途的旅人。远方传来悠扬的笛声。",
        status=ChapterStatus.WRITING,
        summary="第一章，主角踏上旅程，听到奇异的笛声。",
        key_events=["主角启程", "听到笛声"],
        source_trace="deepseek-v4-flash",
        version=2,
    )


@pytest.fixture
def sample_character(novel_id: UUID) -> Character:
    return Character(
        id=uuid4(),
        novel_id=novel_id,
        name="林星辰",
        aliases=["星辰", "小林"],
        role="protagonist",
        personality_tags=["勇敢", "好奇", "固执"],
        behavior_rules="遇到困难从不退缩",
        appearance="黑发黑瞳，17岁少年",
        background="来自偏远星村的少年",
        relationships="与村长有师徒关系",
    )


@pytest.fixture
def sample_world(novel_id: UUID) -> World:
    return World(
        id=uuid4(),
        novel_id=novel_id,
        title="星辰大陆",
        genre_tags=["玄幻", "星空"],
        setting="一个以星辰之力为法则的幻想世界",
        rules=["星辰之力的使用需要消耗星能"],
        factions=[
            Faction(
                id=uuid4(),
                name="星辉联盟",
                description="维护星空秩序的联盟",
                leader="星辉大法师",
                members=["林星辰"],
                goals=["维护星空平衡"],
            )
        ],
        power_system=PowerSystem(
            name="星辰之力",
            description="操控星辰能量的力量体系",
            abilities=[PowerAbility(name="星辉术", description="召唤星光")],
            rules=["每使用一次消耗10%星能"],
            limitations=["不能在无星的夜晚使用"],
        ),
    )


@pytest.fixture
def sample_pipeline_state(novel_id: UUID) -> PipelineState:
    return PipelineState(
        novel_id=novel_id,
        total_chapters=3,
        chapters={1: ChapterStatus.PLANNED, 2: ChapterStatus.PLANNED, 3: ChapterStatus.PLANNED},
        current_chapter_index=1,
        iteration=0,
        max_iterations=3,
    )


# ═══════════════════════════════════════════════════════════════
#  1. Novel CRUD + T1: Writer 完成章节
# ═══════════════════════════════════════════════════════════════


class TestNovelAndChapterPipeline:
    """T1 事务边界：创建小说 → 保存章节 → Writer 完成章节."""

    async def test_create_and_retrieve_novel(
        self, uow: UnitOfWork, sample_novel: Novel, novel_id: UUID
    ):
        async with uow.transaction():
            await uow.novels.save(sample_novel)
            await uow._session.commit()

        async with uow.transaction():
            retrieved = await uow.novels.get_by_id(novel_id)
            assert retrieved is not None
            assert retrieved.title == "星辰之海"
            assert retrieved.metadata.description == "一个关于星空与命运的故事"
            assert retrieved.metadata.status == "draft"

    async def test_t1_writer_complete_chapter(
        self,
        uow: UnitOfWork,
        sample_novel: Novel,
        sample_chapter: Chapter,
        novel_id: UUID,
    ):
        # 先创建小说
        async with uow.transaction():
            await uow.novels.save(sample_novel)
            await uow.chapters.save(sample_chapter)
            await uow._session.commit()

        # T1: Writer 完成章节（无历史版本）
        async with uow.transaction():
            is_dup, digest = await uow.t1_writer_complete_chapter(sample_chapter)
            await uow._session.commit()

        assert not is_dup
        assert len(digest) == 64  # SHA-256

        # 验证状态变更
        async with uow.transaction():
            ch = await uow.chapters.get_by_novel_and_index(novel_id, 1)
            assert ch is not None
            assert ch.status == ChapterStatus.DRAFT_READY

    async def test_t1_saves_previous_version(
        self,
        uow: UnitOfWork,
        sample_novel: Novel,
        sample_chapter: Chapter,
        sample_chapter_v2: Chapter,
        novel_id: UUID,
    ):
        # 先创建小说和第一章
        async with uow.transaction():
            await uow.novels.save(sample_novel)
            await uow.chapters.save(sample_chapter)
            await uow._session.commit()

        # 创建历史版本
        prev_version = ChapterVersion(
            id=uuid4(),
            chapter_id=sample_chapter.id,
            novel_id=novel_id,
            version=1,
            index=1,
            title=sample_chapter.title,
            content=sample_chapter.content,
            summary=sample_chapter.summary,
            key_events=sample_chapter.key_events,
            source_trace=sample_chapter.source_trace,
        )

        # T1: 第二次写入，保留历史版本
        async with uow.transaction():
            is_dup, digest = await uow.t1_writer_complete_chapter(
                sample_chapter_v2, previous_version=prev_version
            )
            await uow._session.commit()

        assert not is_dup

        # 验证历史版本已保存
        async with uow.transaction():
            versions = await uow.chapters.get_versions(sample_chapter.id)
            assert len(versions) == 1
            assert versions[0].version == 1
            assert "夜空中最亮的星" in versions[0].content

            # 当前内容为 v2
            ch = await uow.chapters.get_by_novel_and_index(novel_id, 1)
            assert ch is not None
            assert "笛声" in ch.content
            assert ch.version == 2

    async def test_t1_idempotent_same_content(
        self,
        uow: UnitOfWork,
        sample_novel: Novel,
        sample_chapter: Chapter,
    ):
        async with uow.transaction():
            await uow.novels.save(sample_novel)
            await uow.chapters.save(sample_chapter)
            await uow._session.commit()

        # 第一次提交
        async with uow.transaction():
            is_dup1, _ = await uow.t1_writer_complete_chapter(sample_chapter)
            await uow._session.commit()
        assert not is_dup1

        # 第二次相同内容提交
        async with uow.transaction():
            is_dup2, _ = await uow.t1_writer_complete_chapter(sample_chapter)
            await uow._session.commit()
        assert is_dup2


# ═══════════════════════════════════════════════════════════════
#  2. T2: Planner 完成规划（批量插入章节 + PipelineState）
# ═══════════════════════════════════════════════════════════════


class TestPlannerPlanning:
    """T2 事务边界：批量创建章节并更新流水线状态."""

    async def test_t2_planner_complete_planning(
        self,
        uow: UnitOfWork,
        sample_novel: Novel,
        novel_id: UUID,
        sample_pipeline_state: PipelineState,
    ):
        # 创建小说
        async with uow.transaction():
            await uow.novels.save(sample_novel)
            await uow._session.commit()

        # 准备三章规划
        chapters = []
        for i in range(1, 4):
            chapters.append(
                Chapter(
                    id=uuid4(),
                    novel_id=novel_id,
                    index=i,
                    title=f"第{i}章",
                    status=ChapterStatus.PLANNED,
                )
            )

        async with uow.transaction():
            await uow.t2_planner_complete_planning(chapters, sample_pipeline_state)
            await uow._session.commit()

        # 验证
        async with uow.transaction():
            stored = await uow.chapters.get_chapters_by_novel(novel_id)
            assert len(stored) == 3
            for ch in stored:
                assert ch.status == ChapterStatus.PLANNED

            pipeline = await uow.pipelines.get_by_novel(novel_id)
            assert pipeline is not None
            assert pipeline.total_chapters == 3
            assert len(pipeline.chapters) == 3

    async def test_t2_rollback_on_failure(self, uow: UnitOfWork, novel_id: UUID):
        """T2 事务错误时回滚，不留下半残数据."""
        chapters = [
            Chapter(id=uuid4(), novel_id=novel_id, index=1, title="半残")
        ]

        # 故意传入无效的 PipelineState（novel_id 不匹配）
        wrong_state = PipelineState(
            novel_id=uuid4(),
            total_chapters=1,
            chapters={1: ChapterStatus.PLANNED},
        )

        with pytest.raises(Exception):
            async with uow.transaction():
                await uow.t2_planner_complete_planning(chapters, wrong_state)
                # 不会到达 commit

        # 验证无数据残留
        async with uow.transaction():
            stored = await uow.chapters.get_chapters_by_novel(novel_id)
            assert len(stored) == 0


# ═══════════════════════════════════════════════════════════════
#  3. T3: Editor 完成评审
# ═══════════════════════════════════════════════════════════════


class TestEditorReview:
    """T3 事务边界：评审章节并更新状态."""

    async def test_t3_approve_chapter(
        self, uow: UnitOfWork, sample_novel: Novel, sample_chapter: Chapter, novel_id: UUID
    ):
        async with uow.transaction():
            await uow.novels.save(sample_novel)
            sample_chapter.status = ChapterStatus.DRAFT_READY
            await uow.chapters.save(sample_chapter)
            await uow._session.commit()

        async with uow.transaction():
            await uow.t3_editor_complete_review(novel_id, 1, is_approved=True)
            await uow._session.commit()

        async with uow.transaction():
            ch = await uow.chapters.get_by_novel_and_index(novel_id, 1)
            assert ch is not None
            assert ch.status == ChapterStatus.APPROVED

    async def test_t3_reject_chapter(
        self, uow: UnitOfWork, sample_novel: Novel, sample_chapter: Chapter, novel_id: UUID
    ):
        async with uow.transaction():
            await uow.novels.save(sample_novel)
            sample_chapter.status = ChapterStatus.DRAFT_READY
            await uow.chapters.save(sample_chapter)
            await uow._session.commit()

        async with uow.transaction():
            await uow.t3_editor_complete_review(novel_id, 1, is_approved=False)
            await uow._session.commit()

        async with uow.transaction():
            ch = await uow.chapters.get_by_novel_and_index(novel_id, 1)
            assert ch is not None
            assert ch.status == ChapterStatus.REVISING

    async def test_t3_approve_with_baseline(
        self, uow: UnitOfWork, sample_novel: Novel, sample_chapter: Chapter, novel_id: UUID
    ):
        async with uow.transaction():
            await uow.novels.save(sample_novel)
            sample_chapter.status = ChapterStatus.DRAFT_READY
            await uow.chapters.save(sample_chapter)
            await uow._session.commit()

        async with uow.transaction():
            await uow.t3_editor_complete_review(
                novel_id, 1, is_approved=True, is_baseline=True
            )
            await uow._session.commit()

        async with uow.transaction():
            ch = await uow.chapters.get_by_novel_and_index(novel_id, 1)
            assert ch is not None
            assert ch.status == ChapterStatus.APPROVED
            assert ch.is_baseline is True


# ═══════════════════════════════════════════════════════════════
#  4. T4: MemoryKeeper 完成压缩
# ═══════════════════════════════════════════════════════════════


class TestMemoryCompression:
    """T4 事务边界：写入压缩数据 + 标记任务完成."""

    async def test_t4_complete_compression(
        self, uow: UnitOfWork, novel_id: UUID
    ):
        task_id = uuid4()
        compressed_data = {
            "summaries": [{"chapter": 1, "summary": "主角启程的章节"}],
            "events": [{"chapter": 1, "key_events": ["主角启程"]}],
        }
        task_update = {
            "status": "completed",
            "completed_at": datetime.now(timezone.utc),
        }

        # 先创建压缩任务
        async with uow.transaction():
            from inkmind.storage.models import CompressionTaskModel

            uow._session.add(
                CompressionTaskModel(
                    task_id=str(task_id),
                    novel_id=str(novel_id),
                    range_start=1,
                    range_end=1,
                    status="running",
                )
            )
            await uow._session.commit()

        async with uow.transaction():
            await uow.t4_memory_keeper_complete_compression(
                novel_id, compressed_data, task_id, task_update
            )
            await uow._session.commit()

        # 验证 L2Archive
        async with uow.transaction():
            result = await uow._session.execute(
                select(MemoryArchiveModel).where(
                    MemoryArchiveModel.novel_id == str(novel_id),
                    MemoryArchiveModel.tier == "l2_compressed",
                )
            )
            archive = result.scalar_one_or_none()
            assert archive is not None
            assert archive.data["summaries"][0]["chapter"] == 1

        # 验证任务标记为已完成
        async with uow.transaction():
            result = await uow._session.execute(
                select(CompressionTaskModel).where(
                    CompressionTaskModel.task_id == str(task_id)
                )
            )
            task = result.scalar_one_or_none()
            assert task is not None
            assert task.status == "completed"
            assert task.completed_at is not None


# ═══════════════════════════════════════════════════════════════
#  5. T5: 滑窗更新
# ═══════════════════════════════════════════════════════════════


class TestWindowShift:
    """T5 事务边界：更新滑窗状态 + L1 快照."""

    async def test_t5_window_shift(self, uow: UnitOfWork, novel_id: UUID):
        sliding_window = {
            "current_start": 1,
            "current_end": 3,
            "window_size": 3,
            "active_chapters": [1, 2, 3],
        }
        l1_snapshot = {
            "last_n_chapters": [
                {"index": 1, "summary": "启程"},
                {"index": 2, "summary": "冒险"},
                {"index": 3, "summary": "决战"},
            ]
        }

        async with uow.transaction():
            await uow.t5_window_shift(novel_id, sliding_window, l1_snapshot)
            await uow._session.commit()

        async with uow.transaction():
            result = await uow._session.execute(
                select(MemoryArchiveModel).where(
                    MemoryArchiveModel.novel_id == str(novel_id),
                    MemoryArchiveModel.tier == "l1_active",
                )
            )
            archive = result.scalar_one_or_none()
            assert archive is not None
            assert archive.data["sliding_window"]["current_start"] == 1
            assert archive.data["sliding_window"]["current_end"] == 3
            assert archive.data["snapshot"]["last_n_chapters"][0]["summary"] == "启程"

    async def test_t5_window_shift_update(
        self, uow: UnitOfWork, novel_id: UUID
    ):
        """二次滑窗更新验证 upsert 语义."""
        sliding_window_v1 = {"current_start": 1, "current_end": 2, "window_size": 2}
        l1_snapshot_v1 = {"last_n_chapters": []}

        async with uow.transaction():
            await uow.t5_window_shift(novel_id, sliding_window_v1, l1_snapshot_v1)
            await uow._session.commit()

        sliding_window_v2 = {"current_start": 2, "current_end": 4, "window_size": 3}
        l1_snapshot_v2 = {"last_n_chapters": [{"index": 2, "summary": "更新"}]}

        async with uow.transaction():
            await uow.t5_window_shift(novel_id, sliding_window_v2, l1_snapshot_v2)
            await uow._session.commit()

        async with uow.transaction():
            result = await uow._session.execute(
                select(MemoryArchiveModel).where(
                    MemoryArchiveModel.novel_id == str(novel_id),
                    MemoryArchiveModel.tier == "l1_active",
                )
            )
            archive = result.scalar_one_or_none()
            assert archive is not None
            # 验证已更新为 v2
            assert archive.data["sliding_window"]["current_start"] == 2
            assert archive.data["sliding_window"]["window_size"] == 3


# ═══════════════════════════════════════════════════════════════
#  6. Idempotency 幂等去重
# ═══════════════════════════════════════════════════════════════


class TestIdempotency:
    """per-packet digest 幂等守卫."""

    async def test_digest_computation(self, novel_id: UUID):
        from inkmind.storage.idempotency import compute_packet_digest

        packet = AgentPacket(
            packet_id=uuid4(),
            packet_type="draft",
            source="writer",
            target="editor",
            novel_id=novel_id,
            payload=DraftPayload(
                chapter_index=1,
                content="测试内容测试内容测试内容测试内容测试内容测试内容测试内容测试内容测试内容测试内容"
                "测试内容测试内容测试内容测试内容测试内容测试内容测试内容测试内容测试内容测试内容测试内容"
                "测试内容测试内容测试内容测试内容测试内容测试内容测试内容测试内容测试内容测试内容测试内容",
                paragraph_count=1,
            ),
        )
        digest = compute_packet_digest(packet)
        assert len(digest) == 64
        assert isinstance(digest, str)

        # 相同内容的 packet 产生相同 digest
        digest2 = compute_packet_digest(packet)
        assert digest == digest2

    async def test_is_duplicate_and_mark(
        self, uow: UnitOfWork
    ):
        digest = "abc123" + "x" * 58  # 64 chars total
        packet_id = uuid4()

        # 初始不应是重复
        is_dup = await uow.idempotency.is_duplicate(digest)
        assert not is_dup

        # 标记
        await uow.idempotency.mark_processed(digest, packet_id)
        await uow._session.commit()

        # 现在应是重复
        is_dup = await uow.idempotency.is_duplicate(digest)
        assert is_dup

    async def test_is_already_processed_full_flow(
        self, uow: UnitOfWork, novel_id: UUID
    ):
        packet = AgentPacket(
            packet_id=uuid4(),
            packet_type="draft",
            source="writer",
            target="editor",
            novel_id=novel_id,
            payload=DraftPayload(
                chapter_index=1,
                content="测试内容测试内容测试内容测试内容测试内容测试内容测试内容测试内容测试内容测试内容"
                "测试内容测试内容测试内容测试内容测试内容测试内容测试内容测试内容测试内容测试内容测试内容"
                "测试内容测试内容测试内容测试内容测试内容测试内容测试内容测试内容测试内容测试内容测试内容",
                paragraph_count=1,
            ),
        )

        is_dup, digest = await uow.idempotency.is_already_processed(packet)
        assert not is_dup
        assert len(digest) == 64

        # 标记
        await uow.idempotency.mark_processed(digest, packet.packet_id)
        await uow._session.commit()

        # 再次检查
        is_dup2, _ = await uow.idempotency.is_already_processed(packet)
        assert is_dup2

    async def test_mark_processed_idempotent(
        self, uow: UnitOfWork
    ):
        """重复标记同一 digest 不报错."""
        digest = "same_digest_" + "x" * 52
        packet_id = uuid4()

        await uow.idempotency.mark_processed(digest, packet_id)
        await uow._session.commit()

        # 再次标记不报错
        await uow.idempotency.mark_processed(digest, packet_id)
        await uow._session.commit()


# ═══════════════════════════════════════════════════════════════
#  7. 完整流水线: T1 → T3 → T4 → T5
# ═══════════════════════════════════════════════════════════════


class TestFullPipeline:
    """模拟 Writer → Editor → MemoryKeeper 的完整流水线."""

    async def test_full_pipeline_flow(
        self, uow: UnitOfWork, novel_id: UUID
    ):
        # ── 准备 ──
        novel = Novel(
            id=novel_id,
            title="星辰之海",
            metadata=NovelMetadata(description="测试小说", word_count=0, chapter_count=1, status="draft"),
        )
        chapter = Chapter(
            id=uuid4(),
            novel_id=novel_id,
            index=1,
            title="启程",
            content="夜空中最亮的星。",
            status=ChapterStatus.PLANNED,
        )

        async with uow.transaction():
            await uow.novels.save(novel)
            await uow.chapters.save(chapter)
            await uow._session.commit()

        # ── T1: Writer 完成章节 ──
        async with uow.transaction():
            is_dup, _ = await uow.t1_writer_complete_chapter(chapter)
            await uow._session.commit()
        assert not is_dup

        # 验证状态
        async with uow.transaction():
            ch = await uow.chapters.get_by_novel_and_index(novel_id, 1)
            assert ch is not None
            assert ch.status == ChapterStatus.DRAFT_READY

        # ── T3: Editor 批准 ──
        async with uow.transaction():
            await uow.t3_editor_complete_review(
                novel_id, 1, is_approved=True, is_baseline=True
            )
            await uow._session.commit()

        async with uow.transaction():
            ch = await uow.chapters.get_by_novel_and_index(novel_id, 1)
            assert ch is not None
            assert ch.status == ChapterStatus.APPROVED
            assert ch.is_baseline is True

        # ── T4: MemoryKeeper 压缩 ──
        task_id = uuid4()
        async with uow.transaction():
            from inkmind.storage.models import CompressionTaskModel

            uow._session.add(
                CompressionTaskModel(
                    task_id=str(task_id),
                    novel_id=str(novel_id),
                    range_start=1,
                    range_end=1,
                    status="running",
                )
            )
            await uow._session.commit()

        async with uow.transaction():
            await uow.t4_memory_keeper_complete_compression(
                novel_id,
                {"summaries": [{"chapter": 1, "summary": "主角启程章节"}]},
                task_id,
                {"status": "completed", "completed_at": datetime.now(timezone.utc)},
            )
            await uow._session.commit()

        # 验证压缩完成
        async with uow.transaction():
            result = await uow._session.execute(
                select(CompressionTaskModel).where(
                    CompressionTaskModel.task_id == str(task_id)
                )
            )
            task = result.scalar_one_or_none()
            assert task is not None
            assert task.status == "completed"

        # ── T5: 滑窗更新 ──
        async with uow.transaction():
            await uow.t5_window_shift(
                novel_id,
                {"current_start": 1, "current_end": 1, "window_size": 3},
                {"last_n_chapters": [{"index": 1, "summary": "主角启程"}]},
            )
            await uow._session.commit()

        async with uow.transaction():
            result = await uow._session.execute(
                select(MemoryArchiveModel).where(
                    MemoryArchiveModel.novel_id == str(novel_id),
                    MemoryArchiveModel.tier == "l1_active",
                )
            )
            archive = result.scalar_one_or_none()
            assert archive is not None
            assert archive.data["sliding_window"]["current_end"] == 1

    async def test_pipeline_rollback_on_failure(
        self, uow: UnitOfWork, novel_id: UUID
    ):
        """流水线中途失败时，所有变更回滚."""
        novel = Novel(id=novel_id, title="回滚测试")
        chapter = Chapter(
            id=uuid4(),
            novel_id=novel_id,
            index=1,
            title="第1章",
            content="内容",
            status=ChapterStatus.PLANNED,
        )

        async with uow.transaction():
            await uow.novels.save(novel)
            await uow.chapters.save(chapter)
            await uow._session.commit()

        # T1 完成后故意触发异常（模拟网络中断）
        try:
            async with uow.transaction():
                await uow.t1_writer_complete_chapter(chapter)
                # 在 commit 前抛异常
                raise RuntimeError("模拟中断")
        except RuntimeError:
            pass

        # 验证状态回滚到 PLANNED（未变为 DRAFT_READY）
        async with uow.transaction():
            ch = await uow.chapters.get_by_novel_and_index(novel_id, 1)
            assert ch is not None
            assert ch.status == ChapterStatus.PLANNED


# ═══════════════════════════════════════════════════════════════
#  8. Snapshot 快照导出与恢复
# ═══════════════════════════════════════════════════════════════


class TestJSONSnapshot:
    """JSONSnapshot dump 与 restore."""

    async def test_dump_and_restore(
        self, db: DatabaseManager, novel_id: UUID, tmp_path: Path
    ):
        # ── 准备数据 ──
        async with db.session() as session:
            uow = UnitOfWork(session)

            # Novel
            novel = Novel(
                id=novel_id,
                title="星辰之海",
                metadata=NovelMetadata(
                    description="测试",
                    word_count=100,
                    chapter_count=1,
                    status="draft",
                ),
            )
            await uow.novels.save(novel)

            # Chapter
            ch = Chapter(
                id=uuid4(),
                novel_id=novel_id,
                index=1,
                title="启程",
                content="内容...",
                status=ChapterStatus.APPROVED,
                is_baseline=True,
            )
            await uow.chapters.save(ch)

            # Version
            ver = ChapterVersion(
                id=uuid4(),
                chapter_id=ch.id,
                novel_id=novel_id,
                version=1,
                index=1,
                title="启程",
                content="旧内容...",
        content_digest=hashlib.sha256("旧内容...".encode("utf-8")).hexdigest(),
            )
            await uow.chapters.save_version(ver)

            await session.commit()

        # ── dump ──
        dump_path = tmp_path / "snapshot.json"
        async with db.session() as session:
            snapshot = JSONSnapshot(session)
            output_path = await snapshot.dump(novel_id, dump_path)
            await session.commit()

        assert output_path.exists()
        raw = json.loads(output_path.read_text(encoding="utf-8"))
        assert raw["novel_id"] == str(novel_id)
        assert raw["novel"]["title"] == "星辰之海"
        assert len(raw["chapters"]) == 1
        assert len(raw["chapter_versions"]) == 1

        # ── 恢复到新 novel_id ──
        restore_path = tmp_path / "snapshot.json"
        async with db.session() as session:
            snapshot2 = JSONSnapshot(session)
            restored_id = await snapshot2.restore(restore_path)
            await session.commit()

            # 验证恢复
            uow2 = UnitOfWork(session)
            restored_novel = await uow2.novels.get_by_id(restored_id)
            assert restored_novel is not None
            assert restored_novel.title == "星辰之海"

            restored_chs = await uow2.chapters.get_chapters_by_novel(restored_id)
            assert len(restored_chs) == 1
            assert restored_chs[0].title == "启程"
            assert restored_chs[0].is_baseline is True

    async def test_dump_nonexistent_novel(
        self, db: DatabaseManager, tmp_path: Path
    ):
        async with db.session() as session:
            snapshot = JSONSnapshot(session)
            with pytest.raises(ValueError, match="not found"):
                await snapshot.dump(uuid4(), tmp_path / "nonexistent.json")


# ═══════════════════════════════════════════════════════════════
#  9. Recovery 故障恢复
# ═══════════════════════════════════════════════════════════════


class TestRecovery:
    """RecoveryManager 故障恢复."""

    async def test_recover_full_state(
        self, db: DatabaseManager, novel_id: UUID
    ):
        # ── 准备完整数据 ──
        async with db.session() as session:
            uow = UnitOfWork(session)

            # Novel + Chapter
            novel = Novel(id=novel_id, title="星辰之海")
            await uow.novels.save(novel)
            ch = Chapter(
                id=uuid4(),
                novel_id=novel_id,
                index=1,
                title="启程",
                content="内容...",
                status=ChapterStatus.APPROVED,
            )
            await uow.chapters.save(ch)

            # PipelineState
            pipeline = PipelineState(
                novel_id=novel_id,
                total_chapters=1,
                chapters={1: ChapterStatus.APPROVED},
            )
            await uow.pipelines.save(pipeline)

            # MemoryArchives (L0, L1, L2, L3)
            session.add(MemoryArchiveModel(novel_id=str(novel_id), tier="l0_index", data={"chapters": [1]}))
            session.add(
                MemoryArchiveModel(
                    novel_id=str(novel_id),
                    tier="l1_active",
                    data={
                        "sliding_window": {
                            "novel_id": str(novel_id),
                            "current_chapter_index": 1,
                            "recent_chapters": [1],
                        },
                        "snapshot": {"last_n_chapters": []},
                    },
                )
            )
            session.add(
                MemoryArchiveModel(
                    novel_id=str(novel_id),
                    tier="l2_compressed",
                    data={"summaries": []},
                )
            )
            session.add(
                MemoryArchiveModel(
                    novel_id=str(novel_id),
                    tier="l3_permanent",
                    data={"entries": {}},
                )
            )

            # 待处理的压缩任务
            task_id = uuid4()
            session.add(
                CompressionTaskModel(
                    task_id=str(task_id),
                    novel_id=str(novel_id),
                    range_start=1,
                    range_end=1,
                    status="running",
                    started_at=datetime.now(timezone.utc),
                )
            )

            await session.commit()

        # ── 故障恢复 ──
        async with db.session() as session:
            recovery = RecoveryManager(session)
            state = await recovery.recover(novel_id)
            await session.commit()

        # ── 验证 ──
        assert state.novel_id == novel_id
        assert state.l0_index is not None
        assert state.l2_archive is not None
        assert state.l3_archive is not None
        assert state.sliding_window is not None
        assert state.sliding_window.current_chapter_index == 1
        assert state.pipeline_state is not None
        assert state.pipeline_state.total_chapters == 1

        # 压缩任务被恢复并重置为 PENDING
        assert len(state.pending_tasks) == 1
        assert state.pending_tasks[0].status == CompressionTaskStatus.PENDING
        assert state.has_pending_work is True

        # 验证数据库中的任务状态也被重置
        async with db.session() as session:
            result = await session.execute(
                select(CompressionTaskModel).where(
                    CompressionTaskModel.task_id == str(task_id)
                )
            )
            task = result.scalar_one_or_none()
            assert task is not None
            assert task.status == "pending"
            assert task.started_at is None  # 被重置

    async def test_recover_empty_novel(
        self, db: DatabaseManager, novel_id: UUID
    ):
        """空小说（无额外数据）也能恢复."""
        async with db.session() as session:
            uow = UnitOfWork(session)
            await uow.novels.save(Novel(id=novel_id, title="空测试"))
            await session.commit()

        async with db.session() as session:
            recovery = RecoveryManager(session)
            state = await recovery.recover(novel_id)
            await session.commit()

        assert state.novel_id == novel_id
        assert state.l0_index is None
        assert state.l2_archive is None
        assert state.l3_archive is None
        assert state.sliding_window is None
        assert state.pipeline_state is None
        assert state.has_pending_work is False


# ═══════════════════════════════════════════════════════════════
#  10. 跨实体完整性：Novel + Character + World
# ═══════════════════════════════════════════════════════════════


class TestCrossEntityIntegrity:
    """确保 Novel、Character、World 之间的关联完整性."""

    async def test_save_and_retrieve_all_entities(
        self,
        uow: UnitOfWork,
        sample_novel: Novel,
        sample_character: Character,
        sample_world: World,
        novel_id: UUID,
    ):
        async with uow.transaction():
            await uow.novels.save(sample_novel)
            await uow.characters.save(sample_character)
            await uow.worlds.save(sample_world)
            await uow._session.commit()

        # 验证各自可检索
        async with uow.transaction():
            novel = await uow.novels.get_by_id(novel_id)
            assert novel is not None

            chars = await uow.characters.get_by_novel(novel_id)
            assert len(chars) == 1
            assert chars[0].name == "林星辰"

            world = await uow.worlds.get_by_novel(novel_id)
            assert world is not None
            assert world.title == "星辰大陆"

    async def test_multiple_characters_for_novel(
        self, uow: UnitOfWork, sample_novel: Novel, novel_id: UUID
    ):
        async with uow.transaction():
            await uow.novels.save(sample_novel)
            await uow._session.commit()

        chars = []
        for name in ["林星辰", "苏月", "黑风"]:
            chars.append(
                Character(
                    id=uuid4(),
                    novel_id=novel_id,
                    name=name,
                    aliases=[name[0]],
                    role="protagonist" if name == "林星辰" else "supporting",
                )
            )

        async with uow.transaction():
            for c in chars:
                await uow.characters.save(c)
            await uow._session.commit()

        async with uow.transaction():
            stored = await uow.characters.get_by_novel(novel_id)
            assert len(stored) == 3
            names = {c.name for c in stored}
            assert names == {"林星辰", "苏月", "黑风"}

    async def test_character_with_timeline(
        self, uow: UnitOfWork, sample_novel: Novel, novel_id: UUID
    ):
        char = Character(
            id=uuid4(),
            novel_id=novel_id,
            name="林星辰",
            aliases=["星辰"],
            role="protagonist",
            timeline=[
                CharacterTimelineEntry(
                    chapter_index=1,
                    key_events=["启程"],
                    current_state="充满期待",
                ),
                CharacterTimelineEntry(
                    chapter_index=2,
                    key_events=["遭遇险境"],
                    current_state="受伤但仍坚持",
                ),
            ],
        )

        async with uow.transaction():
            await uow.novels.save(sample_novel)
            await uow.characters.save(char)
            await uow._session.commit()

        async with uow.transaction():
            stored = await uow.characters.get_by_id(char.id)
            assert stored is not None
            assert len(stored.timeline) == 2
            assert stored.timeline[0].chapter_index == 1
            assert stored.timeline[0].key_events == ["启程"]
            assert stored.timeline[1].current_state == "受伤但仍坚持"

    async def test_world_with_power_system(
        self, uow: UnitOfWork, sample_novel: Novel, novel_id: UUID
    ):
        world = World(
            id=uuid4(),
            novel_id=novel_id,
            title="星辰大陆",
            genre_tags=["玄幻"],
            setting="星辰之力世界",
            power_system=PowerSystem(
                name="星辰之力",
                description="操控星辰能量",
                abilities=[PowerAbility(name="星辉术", description="召唤星光", tier="入门")],
                rules=["消耗星能"],
                limitations=["无星之夜"],
            ),
        )

        async with uow.transaction():
            await uow.novels.save(sample_novel)
            await uow.worlds.save(world)
            await uow._session.commit()

        async with uow.transaction():
            stored = await uow.worlds.get_by_novel(novel_id)
            assert stored is not None
            assert stored.power_system is not None
            assert stored.power_system.name == "星辰之力"
            assert len(stored.power_system.abilities) == 1
            assert stored.power_system.abilities[0].name == "星辉术"
            assert stored.power_system.abilities[0].tier == "入门"


# ═══════════════════════════════════════════════════════════════
#  11. 章节版本管理
# ═══════════════════════════════════════════════════════════════


class TestChapterVersionManagement:
    """章节历史版本完整生命周期."""

    async def test_multiple_versions(
        self,
        uow: UnitOfWork,
        sample_novel: Novel,
        sample_chapter: Chapter,
        novel_id: UUID,
    ):
        # 创建小说和章节
        async with uow.transaction():
            await uow.novels.save(sample_novel)
            await uow.chapters.save(sample_chapter)
            await uow._session.commit()

        # 写入 3 个版本
        contents = [
            "第一版内容",
            "第二版内容，增加了一段描述",
            "第三版内容，修改了结尾",
        ]
        chapter_id = sample_chapter.id
        for i, content in enumerate(contents, 1):
            if i > 1:
                # 保存旧版本
                prev_ver = ChapterVersion(
                    id=uuid4(),
                    chapter_id=chapter_id,
                    novel_id=novel_id,
                    version=i - 1,
                    index=1,
                    title=sample_chapter.title,
                    content=contents[i - 2],
                    content_digest=hashlib.sha256(contents[i - 2].encode()).hexdigest(),
                )
                async with uow.transaction():
                    await uow.chapters.save_version(prev_ver)
                    await uow._session.commit()

            # 更新当前章节
            ch = Chapter(
                id=chapter_id,
                novel_id=novel_id,
                index=1,
                title=sample_chapter.title,
                content=content,
                status=ChapterStatus.WRITING,
                version=i,
            )
            async with uow.transaction():
                await uow.chapters.save(ch)
                await uow._session.commit()

        # 验证
        async with uow.transaction():
            versions = await uow.chapters.get_versions(chapter_id)
            assert len(versions) == 2  # v1 和 v2 被保存，v3 是当前版
            assert versions[0].version == 2  # 降序排列
            assert versions[1].version == 1

            current = await uow.chapters.get_by_novel_and_index(novel_id, 1)
            assert current is not None
            assert current.content == "第三版内容，修改了结尾"
            assert current.version == 3

    async def test_baseline_marking(
        self,
        uow: UnitOfWork,
        sample_novel: Novel,
        sample_chapter: Chapter,
        novel_id: UUID,
    ):
        async with uow.transaction():
            await uow.novels.save(sample_novel)
            await uow.chapters.save(sample_chapter)
            await uow._session.commit()

        # 保存 v1 并标记为基线
        v1 = ChapterVersion(
            id=uuid4(),
            chapter_id=sample_chapter.id,
            novel_id=novel_id,
            version=1,
            index=1,
            title=sample_chapter.title,
            content=sample_chapter.content,
            is_baseline=True,
            content_digest=hashlib.sha256(sample_chapter.content.encode()).hexdigest(),
        )

        v2_content = "第二版内容"
        v2 = ChapterVersion(
            id=uuid4(),
            chapter_id=sample_chapter.id,
            novel_id=novel_id,
            version=2,
            index=1,
            title=sample_chapter.title,
            content=v2_content,
            content_digest=hashlib.sha256(v2_content.encode()).hexdigest(),
        )

        async with uow.transaction():
            await uow.chapters.save_version(v1)
            await uow.chapters.save_version(v2)
            await uow._session.commit()

        async with uow.transaction():
            versions = await uow.chapters.get_versions(sample_chapter.id)
            baselines = [v for v in versions if v.is_baseline]
            assert len(baselines) == 1
            assert baselines[0].version == 1


# ═══════════════════════════════════════════════════════════════
#  12. PipelineState 更新
# ═══════════════════════════════════════════════════════════════


class TestPipelineStateManagement:
    """流水线状态的完整生命周期."""

    async def test_pipeline_state_crud(
        self, uow: UnitOfWork, sample_novel: Novel, novel_id: UUID
    ):
        async with uow.transaction():
            await uow.novels.save(sample_novel)
            await uow._session.commit()

        # 初始状态
        state = PipelineState(
            novel_id=novel_id,
            total_chapters=5,
            chapters={i: ChapterStatus.PLANNED for i in range(1, 6)},
            current_chapter_index=1,
            iteration=0,
            max_iterations=3,
        )

        async with uow.transaction():
            await uow.pipelines.save(state)
            await uow._session.commit()

        async with uow.transaction():
            stored = await uow.pipelines.get_by_novel(novel_id)
            assert stored is not None
            assert stored.total_chapters == 5
            assert stored.current_chapter_index == 1

        # 更新状态
        state.chapters[1] = ChapterStatus.WRITING
        state.current_chapter_index = 2
        state.iteration = 1

        async with uow.transaction():
            await uow.pipelines.save(state)
            await uow._session.commit()

        async with uow.transaction():
            stored = await uow.pipelines.get_by_novel(novel_id)
            assert stored is not None
            assert stored.chapters[1] == ChapterStatus.WRITING
            assert stored.current_chapter_index == 2
            assert stored.iteration == 1

    async def test_pipeline_state_delete(
        self, uow: UnitOfWork, sample_novel: Novel, novel_id: UUID
    ):
        async with uow.transaction():
            await uow.novels.save(sample_novel)
            await uow.pipelines.save(
                PipelineState(novel_id=novel_id, total_chapters=1)
            )
            await uow._session.commit()

        async with uow.transaction():
            deleted = await uow.pipelines.delete(novel_id)
            assert deleted is True

        # 再次删除应返回 False
        async with uow.transaction():
            deleted = await uow.pipelines.delete(novel_id)
            assert deleted is False
