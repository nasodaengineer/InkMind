"""集成测试：T12 手动编辑（base_digest 乐观锁 + 人见版本）。

覆盖工单 #39 验收标准：
  - 人工门内手动编辑 → 显式保存 → 新版本（source_trace=manual）
  - 取消编辑不产生任何版本
  - 过期 base_digest 保存返回 409（事务内校验）
  - T12 三方原子：版本归档 + 章节写入 + fingerprint_updates 同生共死
  - 改回旧文不被 digest 去重静默吞掉
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio

from inkmind.errors import StaleVersionError
from inkmind.models.agent import ChapterStatus
from inkmind.models.chapter import Chapter
from inkmind.models.novel import Novel, NovelMetadata
from inkmind.storage import UnitOfWork
from inkmind.storage.database import get_manager
from inkmind.storage.digest import compute_content_digest


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
async def uow(session: AsyncSession):
    return UnitOfWork(session)


@pytest.fixture
def novel_id():
    return uuid4()


@pytest.fixture
def chapter_id():
    return uuid4()


ORIGINAL_CONTENT = "夜空中最亮的星，指引着迷途的旅人。"


@pytest.fixture
def sample_novel(novel_id: UUID) -> Novel:
    return Novel(
        id=novel_id,
        title="星辰之海",
        metadata=NovelMetadata(
            description="测试小说", word_count=0, chapter_count=1, status="draft"
        ),
    )


@pytest.fixture
def sample_chapter(novel_id: UUID, chapter_id: UUID) -> Chapter:
    return Chapter(
        id=chapter_id,
        novel_id=novel_id,
        index=1,
        title="启程",
        content=ORIGINAL_CONTENT,
        status=ChapterStatus.APPROVED,
        source_trace="deepseek-v4-flash",
        content_digest=compute_content_digest(ORIGINAL_CONTENT),
    )


async def _seed(uow: UnitOfWork, novel: Novel, chapter: Chapter) -> None:
    async with uow.transaction():
        await uow.novels.save(novel)
        await uow.chapters.save(chapter)
        await uow._session.commit()


# ═══════════════════════════════════════════════════════════════
#  1. 手动编辑 → 新版本（source_trace=manual）
# ═══════════════════════════════════════════════════════════════


class TestManualEditCreatesVersion:
    """人工门内手动编辑 → 显式保存 → 新版本（source_trace=manual）."""

    async def test_manual_edit_produces_new_version(
        self,
        uow: UnitOfWork,
        sample_novel: Novel,
        sample_chapter: Chapter,
        novel_id: UUID,
        chapter_id: UUID,
    ):
        await _seed(uow, sample_novel, sample_chapter)

        base_digest = compute_content_digest(ORIGINAL_CONTENT)
        new_content = "夜空中最亮的星，照亮了整片大海。"

        async with uow.transaction():
            result = await uow.t12_manual_edit(chapter_id, new_content, base_digest)
            await uow._session.commit()

        assert result.source_trace == "manual"
        assert result.version == 2
        assert result.content == new_content
        assert result.content_digest == compute_content_digest(new_content)

        # 验证旧版已归档
        async with uow.transaction():
            versions = await uow.chapters.get_versions(chapter_id)
            assert len(versions) == 1
            assert versions[0].version == 1
            assert versions[0].content == ORIGINAL_CONTENT
            assert versions[0].source_trace == "deepseek-v4-flash"

    async def test_manual_edit_via_patch(
        self,
        uow: UnitOfWork,
        sample_novel: Novel,
        sample_chapter: Chapter,
        chapter_id: UUID,
    ):
        await _seed(uow, sample_novel, sample_chapter)

        base_digest = compute_content_digest(ORIGINAL_CONTENT)
        new_content = "手动修改后的正文。"

        async with uow.transaction():
            result = await uow.patch_chapter(
                chapter_id, content=new_content, base_digest=base_digest
            )
            await uow._session.commit()

        assert result.source_trace == "manual"
        assert result.version == 2
        assert result.content == new_content


# ═══════════════════════════════════════════════════════════════
#  2. 取消编辑不产生任何版本
# ═══════════════════════════════════════════════════════════════


class TestCancelEditNoVersion:
    """取消编辑不产生任何版本."""

    async def test_no_edit_no_version(
        self,
        uow: UnitOfWork,
        sample_novel: Novel,
        sample_chapter: Chapter,
        chapter_id: UUID,
    ):
        await _seed(uow, sample_novel, sample_chapter)

        # 不调用 t12_manual_edit，直接查版本
        async with uow.transaction():
            versions = await uow.chapters.get_versions(chapter_id)
            assert len(versions) == 0

            ch = await uow.chapters.get_by_id(chapter_id)
            assert ch is not None
            assert ch.version == 1
            assert ch.content == ORIGINAL_CONTENT


# ═══════════════════════════════════════════════════════════════
#  3. 过期 base_digest → 409（StaleVersionError）
# ═══════════════════════════════════════════════════════════════


class TestStaleBaseDigest:
    """过期 base_digest 保存返回 409（事务内校验）."""

    async def test_stale_digest_raises(
        self,
        uow: UnitOfWork,
        sample_novel: Novel,
        sample_chapter: Chapter,
        chapter_id: UUID,
    ):
        await _seed(uow, sample_novel, sample_chapter)

        stale_digest = "0" * 64

        with pytest.raises(StaleVersionError) as exc_info:
            async with uow.transaction():
                await uow.t12_manual_edit(chapter_id, "新内容", stale_digest)

        assert exc_info.value.expected == stale_digest
        assert exc_info.value.actual == compute_content_digest(ORIGINAL_CONTENT)

    async def test_stale_digest_no_side_effects(
        self,
        uow: UnitOfWork,
        sample_novel: Novel,
        sample_chapter: Chapter,
        chapter_id: UUID,
    ):
        """冲突后不留任何副作用（版本不增、内容不变）."""
        await _seed(uow, sample_novel, sample_chapter)

        with pytest.raises(StaleVersionError):
            async with uow.transaction():
                await uow.t12_manual_edit(chapter_id, "新内容", "bad" + "0" * 61)

        async with uow.transaction():
            ch = await uow.chapters.get_by_id(chapter_id)
            assert ch is not None
            assert ch.content == ORIGINAL_CONTENT
            assert ch.version == 1

            versions = await uow.chapters.get_versions(chapter_id)
            assert len(versions) == 0

    async def test_concurrent_edit_second_wins_409(
        self,
        uow: UnitOfWork,
        sample_novel: Novel,
        sample_chapter: Chapter,
        chapter_id: UUID,
    ):
        """模拟两人同时编辑：第一人成功，第二人 409."""
        await _seed(uow, sample_novel, sample_chapter)

        base_digest = compute_content_digest(ORIGINAL_CONTENT)

        # 第一人成功
        async with uow.transaction():
            await uow.t12_manual_edit(chapter_id, "第一人的修改", base_digest)
            await uow._session.commit()

        # 第二人用相同 base_digest → 409
        with pytest.raises(StaleVersionError):
            async with uow.transaction():
                await uow.t12_manual_edit(chapter_id, "第二人的修改", base_digest)


# ═══════════════════════════════════════════════════════════════
#  4. T12 三方原子：版本归档 + 章节写入 + fingerprint 同生共死
# ═══════════════════════════════════════════════════════════════


class TestT12Atomicity:
    """T12 三方原子：版本归档 + 章节写入 + fingerprint_updates 同生共死."""

    async def test_all_three_commit_together(
        self,
        uow: UnitOfWork,
        sample_novel: Novel,
        sample_chapter: Chapter,
        chapter_id: UUID,
    ):
        await _seed(uow, sample_novel, sample_chapter)

        base_digest = compute_content_digest(ORIGINAL_CONTENT)
        new_content = "全新内容，手动编辑。"

        async with uow.transaction():
            await uow.t12_manual_edit(chapter_id, new_content, base_digest)
            await uow._session.commit()

        async with uow.transaction():
            # 验证章节写入
            ch = await uow.chapters.get_by_id(chapter_id)
            assert ch is not None
            assert ch.content == new_content
            assert ch.version == 2

            # 验证 fingerprint_updates
            assert ch.content_digest == compute_content_digest(new_content)

            # 验证版本归档
            versions = await uow.chapters.get_versions(chapter_id)
            assert len(versions) == 1
            assert versions[0].content == ORIGINAL_CONTENT
            assert versions[0].content_digest == base_digest

    async def test_rollback_on_mid_transaction_failure(
        self,
        uow: UnitOfWork,
        sample_novel: Novel,
        sample_chapter: Chapter,
        chapter_id: UUID,
    ):
        """事务中途失败 → 三方全回滚."""
        await _seed(uow, sample_novel, sample_chapter)

        base_digest = compute_content_digest(ORIGINAL_CONTENT)

        try:
            async with uow.transaction():
                await uow.t12_manual_edit(chapter_id, "会回滚的内容", base_digest)
                raise RuntimeError("模拟中断")
        except RuntimeError:
            pass

        async with uow.transaction():
            ch = await uow.chapters.get_by_id(chapter_id)
            assert ch is not None
            assert ch.content == ORIGINAL_CONTENT
            assert ch.version == 1

            versions = await uow.chapters.get_versions(chapter_id)
            assert len(versions) == 0


# ═══════════════════════════════════════════════════════════════
#  5. 改回旧文不被 digest 去重静默吞掉
# ═══════════════════════════════════════════════════════════════


class TestRevertNotSwallowed:
    """改回旧文不被 digest 去重静默吞掉."""

    async def test_revert_to_old_content_creates_version(
        self,
        uow: UnitOfWork,
        sample_novel: Novel,
        sample_chapter: Chapter,
        chapter_id: UUID,
    ):
        await _seed(uow, sample_novel, sample_chapter)

        base_digest = compute_content_digest(ORIGINAL_CONTENT)
        second_content = "第二版内容。"

        # 第一次编辑
        async with uow.transaction():
            await uow.t12_manual_edit(chapter_id, second_content, base_digest)
            await uow._session.commit()

        # 改回旧文（与 v1 相同内容）
        second_digest = compute_content_digest(second_content)
        async with uow.transaction():
            result = await uow.t12_manual_edit(chapter_id, ORIGINAL_CONTENT, second_digest)
            await uow._session.commit()

        # 验证：不被吞掉，版本正常递增
        assert result.version == 3
        assert result.content == ORIGINAL_CONTENT

        async with uow.transaction():
            versions = await uow.chapters.get_versions(chapter_id)
            assert len(versions) == 2

    async def test_t1_dedup_does_not_affect_t12(
        self,
        uow: UnitOfWork,
        sample_novel: Novel,
        sample_chapter: Chapter,
        chapter_id: UUID,
    ):
        """T1 的 content-digest 去重不影响 T12 手动编辑."""
        await _seed(uow, sample_novel, sample_chapter)

        # 先通过 T1 写入（会标记 digest）
        async with uow.transaction():
            is_dup, _ = await uow.t1_writer_complete_chapter(sample_chapter)
            await uow._session.commit()
        assert not is_dup

        # T12 手动编辑相同内容 → 不被 T1 去重吞掉
        base_digest = compute_content_digest(ORIGINAL_CONTENT)
        async with uow.transaction():
            result = await uow.t12_manual_edit(chapter_id, ORIGINAL_CONTENT, base_digest)
            await uow._session.commit()

        assert result.version == 2
        async with uow.transaction():
            versions = await uow.chapters.get_versions(chapter_id)
            assert len(versions) == 1


# ═══════════════════════════════════════════════════════════════
#  6. PATCH 一端两用：大纲字段单行写
# ═══════════════════════════════════════════════════════════════


class TestPatchOutlineOnly:
    """PATCH 不含 content → 大纲字段单行写，不产生版本."""

    async def test_patch_title_and_summary(
        self,
        uow: UnitOfWork,
        sample_novel: Novel,
        sample_chapter: Chapter,
        chapter_id: UUID,
    ):
        await _seed(uow, sample_novel, sample_chapter)

        async with uow.transaction():
            result = await uow.patch_chapter(
                chapter_id,
                title="新标题",
                summary="新摘要",
                key_events=["事件A", "事件B"],
            )
            await uow._session.commit()

        assert result.title == "新标题"
        assert result.summary == "新摘要"
        assert result.key_events == ["事件A", "事件B"]
        assert result.content == ORIGINAL_CONTENT
        assert result.version == 1

        # 不产生版本
        async with uow.transaction():
            versions = await uow.chapters.get_versions(chapter_id)
            assert len(versions) == 0

    async def test_patch_content_without_base_digest_raises(
        self,
        uow: UnitOfWork,
        sample_novel: Novel,
        sample_chapter: Chapter,
        chapter_id: UUID,
    ):
        await _seed(uow, sample_novel, sample_chapter)

        with pytest.raises(ValueError, match="base_digest"):
            async with uow.transaction():
                await uow.patch_chapter(chapter_id, content="新内容")


# ═══════════════════════════════════════════════════════════════
#  7. 手动编辑与批示再修交错
# ═══════════════════════════════════════════════════════════════


class TestInterleaveManualAndRevision:
    """人工门内手动改与后续批示再修可任意交错."""

    async def test_manual_then_revision_then_manual(
        self,
        uow: UnitOfWork,
        sample_novel: Novel,
        sample_chapter: Chapter,
        chapter_id: UUID,
        novel_id: UUID,
    ):
        await _seed(uow, sample_novel, sample_chapter)

        # 手动编辑 v1 → v2
        base = compute_content_digest(ORIGINAL_CONTENT)
        async with uow.transaction():
            await uow.t12_manual_edit(chapter_id, "手动修改一", base)
            await uow._session.commit()

        # 批示再修（T1 Writer 修订）v2 → v3
        async with uow.transaction():
            ch = await uow.chapters.get_by_id(chapter_id)
            assert ch is not None
            ch.content = "AI 修订内容"
            ch.version = 3
            ch.source_trace = "deepseek-v4-flash"
            ch.content_digest = compute_content_digest("AI 修订内容")
            await uow.chapters.save(ch)
            await uow._session.commit()

        # 再次手动编辑 v3 → v4
        base2 = compute_content_digest("AI 修订内容")
        async with uow.transaction():
            result = await uow.t12_manual_edit(chapter_id, "手动修改二", base2)
            await uow._session.commit()

        assert result.version == 4
        assert result.source_trace == "manual"
        assert result.content == "手动修改二"
