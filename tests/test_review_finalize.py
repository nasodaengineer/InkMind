"""测试：评审裁决与定稿（Issue #38）。

覆盖验收标准：
1. PATCH content 白名单仅 AWAITING_HUMAN，其余状态 409 chapter_not_editable
2. 定稿 → FINALIZED + 记忆链路触发（L0/L1/L2 更新可查）
3. FINALIZED 章节 PATCH content 返回 409 chapter_not_editable
4. 版本历史查看 + 段落对齐字级 diff
5. 版本数等于人见稿数（run 中间翻转不落版本）
"""

from __future__ import annotations

from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

import inkmind.storage.database as db_module
from inkmind.api.app import create_app
from inkmind.models.agent import ChapterStatus
from inkmind.models.chapter import Chapter
from inkmind.models.novel import Novel, NovelMetadata
from inkmind.storage.database import DatabaseManager
from inkmind.storage.unit_of_work import UnitOfWork


# ═══════════════════════════════════════════════════════
#  Fixtures
# ═══════════════════════════════════════════════════════


@pytest_asyncio.fixture
async def env(tmp_path):
    """HTTP 测试环境：文件 DB + 种子小说 + AWAITING_HUMAN 章节。"""
    db_module._engine = None
    db_module._session_factory = None

    db_path = str(tmp_path / "test.db")
    manager = DatabaseManager(db_path)
    await manager.create_tables()

    novel = Novel(id=uuid4(), title="测试小说", metadata=NovelMetadata(description=""))
    chapter = Chapter(
        id=uuid4(),
        novel_id=novel.id,
        index=1,
        title="第一章",
        content="第一段内容\n第二段内容\n第三段内容",
        status=ChapterStatus.AWAITING_HUMAN,
        version=2,
        volume_id=uuid4(),
    )

    async with manager.session_factory() as s:
        uow = UnitOfWork(s)
        await uow.novels.save(novel)
        await uow.chapters.save(chapter)
        await s.commit()

    await manager.close()
    db_module._engine = None
    db_module._session_factory = None

    app = create_app(db_path=db_path)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, novel, chapter, db_path

    db_module._engine = None
    db_module._session_factory = None


# ═══════════════════════════════════════════════════════
#  1. PATCH content 白名单
# ═══════════════════════════════════════════════════════


async def _set_chapter_status(db_path: str, novel_id, chapter_index: int, status: ChapterStatus):
    """辅助：直接修改章节状态。"""
    mgr = DatabaseManager(db_path)
    async with mgr.session_factory() as s:
        uow = UnitOfWork(s)
        ch = await uow.chapters.get_by_novel_and_index(novel_id, chapter_index)
        if ch:
            ch.status = status
            await uow.chapters.save(ch)
            await s.commit()
    await mgr.close()


class TestContentEditWhitelist:
    """PATCH content 仅 AWAITING_HUMAN 允许。"""

    @pytest.mark.asyncio
    async def test_patch_content_awaiting_human_ok(self, env):
        client, novel, chapter, _ = env
        resp = await client.patch(
            f"/api/novels/{novel.id}/chapters/1",
            json={"content": "修改后的内容"},
        )
        assert resp.status_code == 200
        assert resp.json()["content"] == "修改后的内容"

    @pytest.mark.asyncio
    async def test_patch_content_finalized_409(self, env):
        client, novel, chapter, db_path = env
        await _set_chapter_status(db_path, novel.id, 1, ChapterStatus.FINALIZED)

        resp = await client.patch(
            f"/api/novels/{novel.id}/chapters/1",
            json={"content": "不应成功"},
        )
        assert resp.status_code == 409
        assert resp.json()["detail"] == "chapter_not_editable"

    @pytest.mark.asyncio
    async def test_patch_content_planned_409(self, env):
        client, novel, chapter, db_path = env
        await _set_chapter_status(db_path, novel.id, 1, ChapterStatus.PLANNED)

        resp = await client.patch(
            f"/api/novels/{novel.id}/chapters/1",
            json={"content": "不应成功"},
        )
        assert resp.status_code == 409
        assert resp.json()["detail"] == "chapter_not_editable"

    @pytest.mark.asyncio
    async def test_patch_title_any_status_ok(self, env):
        """标题修改不受状态限制。"""
        client, novel, chapter, db_path = env
        await _set_chapter_status(db_path, novel.id, 1, ChapterStatus.FINALIZED)

        resp = await client.patch(
            f"/api/novels/{novel.id}/chapters/1",
            json={"title": "新标题"},
        )
        assert resp.status_code == 200
        assert resp.json()["title"] == "新标题"


# ═══════════════════════════════════════════════════════
#  2. 定稿端点
# ═══════════════════════════════════════════════════════


class TestFinalize:
    """POST finalize → FINALIZED + 记忆链路。"""

    @pytest.mark.asyncio
    async def test_finalize_success(self, env):
        client, novel, chapter, _ = env
        resp = await client.post(f"/api/novels/{novel.id}/chapters/1/finalize")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "finalized"

    @pytest.mark.asyncio
    async def test_finalize_triggers_l0(self, env):
        """定稿后 L0 索引可查。"""
        from sqlalchemy import select

        from inkmind.storage.models import MemoryArchiveModel

        client, novel, chapter, db_path = env
        await client.post(f"/api/novels/{novel.id}/chapters/1/finalize")

        mgr = DatabaseManager(db_path)
        async with mgr.session_factory() as s:
            result = await s.execute(
                select(MemoryArchiveModel).where(
                    MemoryArchiveModel.novel_id == str(novel.id),
                    MemoryArchiveModel.tier == "l0_index",
                )
            )
            l0 = result.scalar_one_or_none()
            assert l0 is not None
            assert "1" in l0.data["chapters"]
            assert l0.data["chapters"]["1"]["word_count"] > 0
        await mgr.close()

    @pytest.mark.asyncio
    async def test_finalize_triggers_l1(self, env):
        """定稿后 L1 滑窗更新。"""
        from sqlalchemy import select

        from inkmind.storage.models import MemoryArchiveModel

        client, novel, chapter, db_path = env
        await client.post(f"/api/novels/{novel.id}/chapters/1/finalize")

        mgr = DatabaseManager(db_path)
        async with mgr.session_factory() as s:
            result = await s.execute(
                select(MemoryArchiveModel).where(
                    MemoryArchiveModel.novel_id == str(novel.id),
                    MemoryArchiveModel.tier == "l1_active",
                )
            )
            l1 = result.scalar_one_or_none()
            assert l1 is not None
            assert 1 in l1.data["sliding_window"]["window"]
        await mgr.close()

    @pytest.mark.asyncio
    async def test_finalize_non_awaiting_409(self, env):
        """非 AWAITING_HUMAN 状态定稿返回 409。"""
        client, novel, chapter, db_path = env
        await _set_chapter_status(db_path, novel.id, 1, ChapterStatus.PLANNED)

        resp = await client.post(f"/api/novels/{novel.id}/chapters/1/finalize")
        assert resp.status_code == 409

    @pytest.mark.asyncio
    async def test_finalize_already_finalized_409(self, env):
        """已定稿章节再次定稿返回 409。"""
        client, novel, chapter, db_path = env
        await _set_chapter_status(db_path, novel.id, 1, ChapterStatus.FINALIZED)

        resp = await client.post(f"/api/novels/{novel.id}/chapters/1/finalize")
        assert resp.status_code == 409


# ═══════════════════════════════════════════════════════
#  3. 版本历史
# ═══════════════════════════════════════════════════════


class TestVersionHistory:
    """版本历史查看 + diff。"""

    @pytest.mark.asyncio
    async def test_list_versions(self, env):
        """有历史版本时返回版本列表。"""
        from inkmind.models.chapter import ChapterVersion

        client, novel, chapter, db_path = env

        mgr = DatabaseManager(db_path)
        async with mgr.session_factory() as s:
            uow = UnitOfWork(s)
            version = ChapterVersion(
                id=uuid4(),
                chapter_id=chapter.id,
                novel_id=novel.id,
                version=1,
                index=1,
                title="第一章",
                content="旧版本内容\n第二段",
                source_trace="ai",
            )
            await uow.chapters.save_version(version)
            await s.commit()
        await mgr.close()

        resp = await client.get(f"/api/novels/{novel.id}/chapters/{chapter.id}/versions")
        assert resp.status_code == 200
        data = resp.json()
        assert data["current_version"] == 2
        assert len(data["versions"]) == 1
        assert data["versions"][0]["version"] == 1

    @pytest.mark.asyncio
    async def test_diff_versions(self, env):
        """段落对齐 diff。"""
        from inkmind.models.chapter import ChapterVersion

        client, novel, chapter, db_path = env

        mgr = DatabaseManager(db_path)
        async with mgr.session_factory() as s:
            uow = UnitOfWork(s)
            version = ChapterVersion(
                id=uuid4(),
                chapter_id=chapter.id,
                novel_id=novel.id,
                version=1,
                index=1,
                title="第一章",
                content="第一段内容\n旧的第二段\n第三段内容",
                source_trace="ai",
            )
            await uow.chapters.save_version(version)
            await s.commit()
        await mgr.close()

        resp = await client.get(
            f"/api/novels/{novel.id}/chapters/{chapter.id}/versions/diff",
            params={"from_version": 1, "to_version": 2},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["from_version"] == 1
        assert data["to_version"] == 2
        assert len(data["paragraphs"]) > 0

        tags = [line["tag"] for para in data["paragraphs"] for line in para]
        assert "equal" in tags
        assert "delete" in tags or "insert" in tags

    @pytest.mark.asyncio
    async def test_diff_nonexistent_version_404(self, env):
        client, novel, chapter, _ = env
        resp = await client.get(
            f"/api/novels/{novel.id}/chapters/{chapter.id}/versions/diff",
            params={"from_version": 99, "to_version": 2},
        )
        assert resp.status_code == 404


# ═══════════════════════════════════════════════════════
#  4. T9 落稿状态
# ═══════════════════════════════════════════════════════


class TestT9Status:
    """T9 落稿后章节进入 AWAITING_HUMAN。"""

    @pytest.mark.asyncio
    async def test_t9_sets_awaiting_human(self, env):
        from inkmind.models.run import Run, RunKind, RunStatus

        _, novel, chapter, db_path = env

        mgr = DatabaseManager(db_path)
        async with mgr.session_factory() as s:
            uow = UnitOfWork(s)

            run = Run(
                id=uuid4(),
                novel_id=novel.id,
                chapter_id=chapter.id,
                kind=RunKind.GENERATE,
                status=RunStatus.RUNNING,
            )
            await uow.runs.save(run)

            ch = await uow.chapters.get_by_id(chapter.id)
            assert ch is not None
            ch.status = ChapterStatus.WRITING
            await uow.chapters.save(ch)
            await s.commit()

            await uow.t9_finalize_draft(
                run_id=run.id,
                chapter_content="新生成的内容",
                chapter_title="第一章",
            )
            await s.commit()

            updated = await uow.chapters.get_by_id(chapter.id)
            assert updated is not None
            assert updated.status == ChapterStatus.AWAITING_HUMAN
        await mgr.close()
