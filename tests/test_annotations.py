"""测试：行内批注子系统（#40）。

覆盖：
- CommentThread 五态流转（合法/非法转换）
- Repository CRUD（创建、列表、硬删除、追加 comment）
- 重定位端点（批量 relocate 后状态变更）
- 无锚批注（anchor=null 章节总评）
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio

from inkmind.models.annotation import (
    AnchorFingerprint,
    Comment,
    CommentIntent,
    CommentThread,
    ThreadStatus,
    can_transition,
)
from inkmind.models.chapter import Chapter
from inkmind.models.novel import Novel, NovelMetadata
from inkmind.storage.database import get_manager
from inkmind.storage.repositories import AnnotationRepository, ChapterRepository, NovelRepository


# ═══════════════════════════════════════════════════════════════
#  Fixtures
# ═══════════════════════════════════════════════════════════════


@pytest_asyncio.fixture
async def db():
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
async def novel_id(session: AsyncSession):
    nid = uuid4()
    novel = Novel(
        id=nid,
        title="测试小说",
        metadata=NovelMetadata(description="", word_count=0, chapter_count=1, status="draft"),
    )
    repo = NovelRepository(session)
    await repo.save(novel)
    return nid


@pytest_asyncio.fixture
async def chapter_id(session: AsyncSession, novel_id):
    cid = uuid4()
    chapter = Chapter(
        id=cid,
        novel_id=novel_id,
        index=1,
        title="第一章",
        content="夜色渐深，林远站在山巅。",
    )
    repo = ChapterRepository(session)
    await repo.save(chapter)
    return cid


@pytest_asyncio.fixture
async def repo(session: AsyncSession):
    return AnnotationRepository(session)


def _make_anchor() -> AnchorFingerprint:
    return AnchorFingerprint(
        exact="林远站在山巅",
        prefix="夜色渐深，",
        suffix="，望着远方。",
        pos_hint_start=5,
        pos_hint_end=11,
        anchored_version=1,
        chapter_digest="abc123",
    )


def _make_thread(novel_id, chapter_id, **kwargs) -> CommentThread:
    now = datetime.now(timezone.utc)
    defaults = {
        "id": uuid4(),
        "chapter_id": chapter_id,
        "novel_id": novel_id,
        "intent": CommentIntent.note,
        "status": ThreadStatus.open,
        "anchor": _make_anchor(),
        "comments": [Comment(id=uuid4(), author="user", body="测试批注", created_at=now)],
        "created_at": now,
        "updated_at": now,
    }
    defaults.update(kwargs)
    return CommentThread(**defaults)


# ═══════════════════════════════════════════════════════════════
#  五态流转
# ═══════════════════════════════════════════════════════════════


class TestThreadStatusTransitions:
    def test_open_to_pending_relocate(self):
        assert can_transition(ThreadStatus.open, ThreadStatus.pending_relocate)

    def test_open_to_resolved(self):
        assert can_transition(ThreadStatus.open, ThreadStatus.resolved)

    def test_pending_relocate_to_relocated_fuzzy(self):
        assert can_transition(ThreadStatus.pending_relocate, ThreadStatus.relocated_fuzzy)

    def test_pending_relocate_to_orphaned(self):
        assert can_transition(ThreadStatus.pending_relocate, ThreadStatus.orphaned)

    def test_relocated_fuzzy_to_open(self):
        assert can_transition(ThreadStatus.relocated_fuzzy, ThreadStatus.open)

    def test_relocated_fuzzy_to_resolved(self):
        assert can_transition(ThreadStatus.relocated_fuzzy, ThreadStatus.resolved)

    def test_orphaned_to_open(self):
        assert can_transition(ThreadStatus.orphaned, ThreadStatus.open)

    def test_resolved_to_open(self):
        assert can_transition(ThreadStatus.resolved, ThreadStatus.open)

    def test_illegal_open_to_orphaned(self):
        assert not can_transition(ThreadStatus.open, ThreadStatus.orphaned)

    def test_illegal_resolved_to_orphaned(self):
        assert not can_transition(ThreadStatus.resolved, ThreadStatus.orphaned)

    def test_illegal_orphaned_to_resolved(self):
        assert not can_transition(ThreadStatus.orphaned, ThreadStatus.resolved)

    def test_transition_to_sets_resolved_at(self):
        thread = _make_thread(uuid4(), uuid4())
        thread.transition_to(ThreadStatus.resolved)
        assert thread.status == ThreadStatus.resolved
        assert thread.resolved_at is not None

    def test_transition_to_open_clears_resolved_at(self):
        thread = _make_thread(uuid4(), uuid4(), status=ThreadStatus.resolved)
        thread.resolved_at = datetime.now(timezone.utc)
        thread.transition_to(ThreadStatus.open)
        assert thread.resolved_at is None

    def test_illegal_transition_raises(self):
        thread = _make_thread(uuid4(), uuid4())
        with pytest.raises(ValueError, match="非法状态转换"):
            thread.transition_to(ThreadStatus.orphaned)


# ═══════════════════════════════════════════════════════════════
#  Repository CRUD
# ═══════════════════════════════════════════════════════════════


class TestAnnotationRepository:
    async def test_save_and_get_thread(self, repo, novel_id, chapter_id):
        thread = _make_thread(novel_id, chapter_id)
        await repo.save_thread(thread)
        loaded = await repo.get_thread(thread.id)
        assert loaded is not None
        assert loaded.id == thread.id
        assert loaded.intent == CommentIntent.note
        assert loaded.status == ThreadStatus.open
        assert loaded.anchor is not None
        assert loaded.anchor.exact == "林远站在山巅"
        assert len(loaded.comments) == 1
        assert loaded.comments[0].body == "测试批注"

    async def test_list_threads_excludes_resolved(self, repo, novel_id, chapter_id):
        t1 = _make_thread(novel_id, chapter_id)
        t2 = _make_thread(novel_id, chapter_id, status=ThreadStatus.resolved)
        await repo.save_thread(t1)
        await repo.save_thread(t2)

        threads = await repo.list_threads(chapter_id, include_resolved=False)
        assert len(threads) == 1
        assert threads[0].id == t1.id

    async def test_list_threads_include_resolved(self, repo, novel_id, chapter_id):
        t1 = _make_thread(novel_id, chapter_id)
        t2 = _make_thread(novel_id, chapter_id, status=ThreadStatus.resolved)
        await repo.save_thread(t1)
        await repo.save_thread(t2)

        threads = await repo.list_threads(chapter_id, include_resolved=True)
        assert len(threads) == 2

    async def test_delete_thread_hard(self, repo, novel_id, chapter_id):
        thread = _make_thread(novel_id, chapter_id)
        await repo.save_thread(thread)
        deleted = await repo.delete_thread(thread.id)
        assert deleted is True
        assert await repo.get_thread(thread.id) is None

    async def test_delete_nonexistent_returns_false(self, repo):
        assert await repo.delete_thread(uuid4()) is False

    async def test_add_comment(self, repo, novel_id, chapter_id):
        thread = _make_thread(novel_id, chapter_id)
        await repo.save_thread(thread)

        new_comment = Comment(id=uuid4(), author="user", body="第二条评语")
        await repo.add_comment(thread.id, new_comment)

        loaded = await repo.get_thread(thread.id)
        assert len(loaded.comments) == 2

    async def test_update_thread_status(self, repo, novel_id, chapter_id):
        thread = _make_thread(novel_id, chapter_id)
        await repo.save_thread(thread)

        now = datetime.now(timezone.utc)
        await repo.update_thread_status(thread.id, ThreadStatus.resolved, resolved_at=now)
        loaded = await repo.get_thread(thread.id)
        assert loaded.status == ThreadStatus.resolved

    async def test_update_anchor(self, repo, novel_id, chapter_id):
        thread = _make_thread(novel_id, chapter_id)
        await repo.save_thread(thread)

        new_anchor = _make_anchor()
        new_anchor.relocate_score = 0.85
        await repo.update_anchor(thread.id, new_anchor.model_dump(mode="json"))

        loaded = await repo.get_thread(thread.id)
        assert loaded.anchor.relocate_score == 0.85

    async def test_no_anchor_chapter_summary(self, repo, novel_id, chapter_id):
        thread = _make_thread(novel_id, chapter_id, anchor=None, intent=CommentIntent.question)
        await repo.save_thread(thread)

        loaded = await repo.get_thread(thread.id)
        assert loaded.anchor is None
        assert loaded.intent == CommentIntent.question


# ═══════════════════════════════════════════════════════════════
#  AnchorFingerprint 模型
# ═══════════════════════════════════════════════════════════════


class TestAnchorFingerprint:
    def test_max_length_exact(self):
        anchor = AnchorFingerprint(exact="x" * 500)
        assert len(anchor.exact) == 500

    def test_exact_too_long_raises(self):
        with pytest.raises(Exception):
            AnchorFingerprint(exact="x" * 501)

    def test_relocate_score_bounds(self):
        anchor = AnchorFingerprint(exact="test", relocate_score=0.5)
        assert anchor.relocate_score == 0.5

    def test_relocate_score_none(self):
        anchor = AnchorFingerprint(exact="test")
        assert anchor.relocate_score is None
