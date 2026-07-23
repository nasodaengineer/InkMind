"""测试：批示修订（五区序列化预览 + revise run + 回稿重定位）（Issue #41）。

覆盖验收标准：
1. 编排器左勾选右预览：五区序列化 + 指纹实时计算，与服务端权威渲染一致
2. revise run 单次直达 Writer、不过 Editor，回稿停 AWAITING_HUMAN
3. 回稿批注三档分流：≥0.9 自动落位 / 0.5–0.9 待确认 / <0.5 未定位区
4. 回稿绝不自动 resolve；确认/重锚经合并端点、apply-relocation 批量回写经 HTTP 缝可验
5. 序列化范围正确（open/relocated_fuzzy/orphaned 且 intent≠note），按文中位置排序
6. 交错手动改 + 批示再修后一键 finalize 走通
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

import inkmind.storage.database as db_module
from inkmind.agents.prompts import serialize_annotations
from inkmind.api.app import create_app
from inkmind.models.agent import (
    AnnotationRef,
    ChapterOutline,
    QuoteContext,
    RevisionRequestPayload,
)
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
from inkmind.models.agent import ChapterStatus
from inkmind.storage.database import DatabaseManager
from inkmind.storage.repositories import AnnotationRepository
from inkmind.storage.unit_of_work import UnitOfWork

pytestmark = pytest.mark.asyncio


# ═══════════════════════════════════════════════════════
#  Fixtures
# ═══════════════════════════════════════════════════════


@pytest_asyncio.fixture
async def env(tmp_path):
    """HTTP 测试环境：文件 DB + 种子小说 + AWAITING_HUMAN 章节 + 批注。"""
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
        content="夜色渐深，林远站在山巅。风吹过他的衣袍，他望着远方的灯火。",
        status=ChapterStatus.AWAITING_HUMAN,
        version=2,
        volume_id=uuid4(),
    )

    now = datetime.now(timezone.utc)
    # 批注 1: instruction + anchored (open)
    thread_instruction = CommentThread(
        id=uuid4(),
        chapter_id=chapter.id,
        novel_id=novel.id,
        intent=CommentIntent.instruction,
        status=ThreadStatus.open,
        anchor=AnchorFingerprint(
            exact="林远站在山巅",
            prefix="夜色渐深，",
            suffix="。风吹过",
            pos_hint_start=5,
            pos_hint_end=11,
        ),
        comments=[Comment(id=uuid4(), author="user", body="把山巅改为悬崖", created_at=now)],
        created_at=now,
        updated_at=now,
    )
    # 批注 2: question + anchored (open)
    thread_question = CommentThread(
        id=uuid4(),
        chapter_id=chapter.id,
        novel_id=novel.id,
        intent=CommentIntent.question,
        status=ThreadStatus.open,
        anchor=AnchorFingerprint(
            exact="远方的灯火",
            prefix="他望着",
            suffix="。",
            pos_hint_start=20,
            pos_hint_end=25,
        ),
        comments=[Comment(id=uuid4(), author="user", body="灯火是谁家的？", created_at=now)],
        created_at=now,
        updated_at=now,
    )
    # 批注 3: note (should be excluded from serialization)
    thread_note = CommentThread(
        id=uuid4(),
        chapter_id=chapter.id,
        novel_id=novel.id,
        intent=CommentIntent.note,
        status=ThreadStatus.open,
        anchor=AnchorFingerprint(exact="夜色渐深"),
        comments=[Comment(id=uuid4(), author="user", body="备忘：时间线", created_at=now)],
        created_at=now,
        updated_at=now,
    )
    # 批注 4: reference + no anchor (章节总评)
    thread_general = CommentThread(
        id=uuid4(),
        chapter_id=chapter.id,
        novel_id=novel.id,
        intent=CommentIntent.reference,
        status=ThreadStatus.open,
        anchor=None,
        comments=[Comment(id=uuid4(), author="user", body="整体节奏偏慢", created_at=now)],
        created_at=now,
        updated_at=now,
    )
    # 批注 5: orphaned instruction
    thread_orphaned = CommentThread(
        id=uuid4(),
        chapter_id=chapter.id,
        novel_id=novel.id,
        intent=CommentIntent.instruction,
        status=ThreadStatus.orphaned,
        anchor=AnchorFingerprint(exact="已删除的文本"),
        comments=[Comment(id=uuid4(), author="user", body="这段需要重写", created_at=now)],
        created_at=now,
        updated_at=now,
    )

    async with manager.session_factory() as s:
        uow = UnitOfWork(s)
        await uow.novels.save(novel)
        await uow.chapters.save(chapter)
        repo = AnnotationRepository(s)
        await repo.save_thread(thread_instruction)
        await repo.save_thread(thread_question)
        await repo.save_thread(thread_note)
        await repo.save_thread(thread_general)
        await repo.save_thread(thread_orphaned)
        await s.commit()

    await manager.close()
    db_module._engine = None
    db_module._session_factory = None

    app = create_app(db_path=db_path)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield {
            "client": client,
            "novel": novel,
            "chapter": chapter,
            "db_path": db_path,
            "thread_instruction": thread_instruction,
            "thread_question": thread_question,
            "thread_note": thread_note,
            "thread_general": thread_general,
            "thread_orphaned": thread_orphaned,
        }

    db_module._engine = None
    db_module._session_factory = None


# ═══════════════════════════════════════════════════════
#  1. 五区序列化单元测试
# ═══════════════════════════════════════════════════════


class TestSerializeAnnotations:
    """五区序列化范围与排序。"""

    def _make_ref(
        self,
        intent: str = "instruction",
        status: str = "open",
        quote: str = "引文",
        prefix: str = "",
        suffix: str = "",
        comments: list[str] | None = None,
    ) -> AnnotationRef:
        return AnnotationRef(
            thread_id=uuid4(),
            intent=intent,
            status=status,
            anchored_quote=quote,
            quote_context=QuoteContext(prefix=prefix, suffix=suffix),
            comments=comments or ["测试批注"],
        )

    def test_empty_returns_empty(self):
        assert serialize_annotations([]) == ""

    def test_note_excluded(self):
        """intent=note 不序列化。"""
        ref = self._make_ref(intent="note")
        assert serialize_annotations([ref]) == ""

    def test_resolved_excluded(self):
        """status=resolved 不序列化。"""
        ref = self._make_ref(status="resolved")
        assert serialize_annotations([ref]) == ""

    def test_pending_relocate_excluded(self):
        """status=pending_relocate 不序列化。"""
        ref = self._make_ref(status="pending_relocate")
        assert serialize_annotations([ref]) == ""

    def test_open_included(self):
        ref = self._make_ref(status="open")
        result = serialize_annotations([ref])
        assert "测试批注" in result

    def test_relocated_fuzzy_included(self):
        ref = self._make_ref(status="relocated_fuzzy")
        result = serialize_annotations([ref])
        assert "测试批注" in result

    def test_orphaned_included(self):
        ref = self._make_ref(status="orphaned")
        result = serialize_annotations([ref])
        assert "原文已被改写" in result

    def test_five_zones_order(self):
        """五区顺序：章节总评→修订指令→保留要求→读者疑问→已失效批注。"""
        content = "AAAA BBBB CCCC"
        refs = [
            self._make_ref(intent="question", quote="CCCC", comments=["疑问"]),
            self._make_ref(intent="instruction", quote="AAAA", comments=["指令"]),
            self._make_ref(intent="reference", quote="BBBB", comments=["保留"]),
            self._make_ref(intent="instruction", quote="", comments=["总评"]),
            self._make_ref(intent="instruction", status="orphaned", quote="XX", comments=["失效"]),
        ]
        result = serialize_annotations(refs, content)
        pos_general = result.find("【章节总评】")
        pos_instruction = result.find("【修订指令】")
        pos_reference = result.find("【保留要求】")
        pos_question = result.find("【读者疑问】")
        pos_orphaned = result.find("【已失效批注】")
        assert pos_general < pos_instruction < pos_reference < pos_question < pos_orphaned

    def test_anchored_sorted_by_position(self):
        """锚定条目按文中位置排序。"""
        content = "第一段文本 第二段文本 第三段文本"
        refs = [
            self._make_ref(intent="instruction", quote="第三段文本", comments=["第三"]),
            self._make_ref(intent="instruction", quote="第一段文本", comments=["第一"]),
        ]
        result = serialize_annotations(refs, content)
        assert result.find("第一") < result.find("第三")

    def test_anchor_format(self):
        """锚定条目格式：锚定引文：「exact」（前：…prefix / 后：suffix…）。"""
        ref = self._make_ref(quote="精确引文", prefix="前文", suffix="后文", comments=["批注内容"])
        result = serialize_annotations([ref])
        assert "锚定引文：「精确引文」（前：…前文 / 后：后文…）" in result
        assert "批注内容" in result


# ═══════════════════════════════════════════════════════
#  2. RevisionRequestPayload 协议校验
# ═══════════════════════════════════════════════════════


class TestRevisionRequestPayload:
    """issues 与 annotations 至少其一非空。"""

    def _outline(self) -> ChapterOutline:
        return ChapterOutline(index=1, title="第一章", summary="摘要" * 10, key_events=["事件"])

    def test_issues_only_ok(self):
        p = RevisionRequestPayload(
            novel_id=uuid4(),
            chapter_index=1,
            previous_content="内容",
            issues=["问题1"],
            iteration=1,
            chapter_outline=self._outline(),
        )
        assert len(p.issues) == 1

    def test_annotations_only_ok(self):
        ref = AnnotationRef(
            thread_id=uuid4(), intent="instruction", status="open", comments=["批注"]
        )
        p = RevisionRequestPayload(
            novel_id=uuid4(),
            chapter_index=1,
            previous_content="内容",
            annotations=[ref],
            iteration=1,
            chapter_outline=self._outline(),
        )
        assert len(p.annotations) == 1

    def test_both_empty_raises(self):
        with pytest.raises(ValueError, match="至少其一非空"):
            RevisionRequestPayload(
                novel_id=uuid4(),
                chapter_index=1,
                previous_content="内容",
                issues=[],
                annotations=[],
                iteration=1,
                chapter_outline=self._outline(),
            )


# ═══════════════════════════════════════════════════════
#  3. 预览端点（HTTP 缝）
# ═══════════════════════════════════════════════════════


class TestPreviewEndpoint:
    """POST /preview 五区序列化预览。"""

    async def test_preview_returns_serialized(self, env):
        client = env["client"]
        novel = env["novel"]
        chapter = env["chapter"]
        t_instr = env["thread_instruction"]
        t_question = env["thread_question"]

        resp = await client.post(
            f"/api/novels/{novel.id}/chapters/{chapter.id}/annotations/preview",
            json={"thread_ids": [str(t_instr.id), str(t_question.id)]},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["thread_count"] == 2
        assert "【修订指令】" in data["serialized"]
        assert "【读者疑问】" in data["serialized"]
        assert "把山巅改为悬崖" in data["serialized"]

    async def test_preview_excludes_note(self, env):
        """note 类批注不出现在预览中。"""
        client = env["client"]
        novel = env["novel"]
        chapter = env["chapter"]
        t_note = env["thread_note"]

        resp = await client.post(
            f"/api/novels/{novel.id}/chapters/{chapter.id}/annotations/preview",
            json={"thread_ids": [str(t_note.id)]},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["serialized"] == ""

    async def test_preview_orphaned_zone(self, env):
        """orphaned 批注出现在已失效批注区。"""
        client = env["client"]
        novel = env["novel"]
        chapter = env["chapter"]
        t_orphaned = env["thread_orphaned"]

        resp = await client.post(
            f"/api/novels/{novel.id}/chapters/{chapter.id}/annotations/preview",
            json={"thread_ids": [str(t_orphaned.id)]},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "【已失效批注】" in data["serialized"]
        assert "原文已被改写" in data["serialized"]


# ═══════════════════════════════════════════════════════
#  4. apply-relocation 三档分流（HTTP 缝）
# ═══════════════════════════════════════════════════════


class TestApplyRelocation:
    """POST /apply-relocation 三档分流。"""

    async def _set_pending(self, db_path, thread_id):
        mgr = DatabaseManager(db_path)
        async with mgr.session_factory() as s:
            repo = AnnotationRepository(s)
            await repo.update_thread_status(thread_id, ThreadStatus.pending_relocate)
            await s.commit()
        await mgr.close()

    async def test_high_score_auto_open(self, env):
        """score ≥0.9 → open（自动落位）。"""
        client = env["client"]
        novel = env["novel"]
        chapter = env["chapter"]
        db_path = env["db_path"]
        t_instr = env["thread_instruction"]

        await self._set_pending(db_path, t_instr.id)

        resp = await client.post(
            f"/api/novels/{novel.id}/chapters/{chapter.id}/annotations/apply-relocation",
            json={
                "items": [
                    {
                        "thread_id": str(t_instr.id),
                        "anchor": {"exact": "新引文", "prefix": "", "suffix": ""},
                        "score": 0.95,
                    }
                ]
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["relocated"] == 1

        # 验证状态
        mgr = DatabaseManager(db_path)
        async with mgr.session_factory() as s:
            repo = AnnotationRepository(s)
            thread = await repo.get_thread(t_instr.id)
            assert thread.status == ThreadStatus.open
        await mgr.close()

    async def test_mid_score_fuzzy(self, env):
        """0.5–0.9 → relocated_fuzzy（待确认）。"""
        client = env["client"]
        novel = env["novel"]
        chapter = env["chapter"]
        db_path = env["db_path"]
        t_instr = env["thread_instruction"]

        await self._set_pending(db_path, t_instr.id)

        resp = await client.post(
            f"/api/novels/{novel.id}/chapters/{chapter.id}/annotations/apply-relocation",
            json={
                "items": [
                    {
                        "thread_id": str(t_instr.id),
                        "anchor": {"exact": "模糊引文", "prefix": "", "suffix": ""},
                        "score": 0.7,
                    }
                ]
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["fuzzy"] == 1

        mgr = DatabaseManager(db_path)
        async with mgr.session_factory() as s:
            repo = AnnotationRepository(s)
            thread = await repo.get_thread(t_instr.id)
            assert thread.status == ThreadStatus.relocated_fuzzy
        await mgr.close()

    async def test_low_score_orphaned(self, env):
        """<0.5 → orphaned（未定位区）。"""
        client = env["client"]
        novel = env["novel"]
        chapter = env["chapter"]
        db_path = env["db_path"]
        t_instr = env["thread_instruction"]

        await self._set_pending(db_path, t_instr.id)

        resp = await client.post(
            f"/api/novels/{novel.id}/chapters/{chapter.id}/annotations/apply-relocation",
            json={
                "items": [
                    {
                        "thread_id": str(t_instr.id),
                        "anchor": {"exact": "无关引文", "prefix": "", "suffix": ""},
                        "score": 0.3,
                    }
                ]
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["orphaned"] == 1

        mgr = DatabaseManager(db_path)
        async with mgr.session_factory() as s:
            repo = AnnotationRepository(s)
            thread = await repo.get_thread(t_instr.id)
            assert thread.status == ThreadStatus.orphaned
        await mgr.close()

    async def test_never_auto_resolve(self, env):
        """回稿绝不自动 resolve。"""
        client = env["client"]
        novel = env["novel"]
        chapter = env["chapter"]
        db_path = env["db_path"]
        t_instr = env["thread_instruction"]

        await self._set_pending(db_path, t_instr.id)

        # 即使 score=0 也不会 resolve
        resp = await client.post(
            f"/api/novels/{novel.id}/chapters/{chapter.id}/annotations/apply-relocation",
            json={
                "items": [
                    {
                        "thread_id": str(t_instr.id),
                        "anchor": {"exact": "x", "prefix": "", "suffix": ""},
                        "score": 0.0,
                    }
                ]
            },
        )
        assert resp.status_code == 200

        mgr = DatabaseManager(db_path)
        async with mgr.session_factory() as s:
            repo = AnnotationRepository(s)
            thread = await repo.get_thread(t_instr.id)
            assert thread.status != ThreadStatus.resolved
        await mgr.close()


# ═══════════════════════════════════════════════════════
#  5. confirm-relocate 合并端点（HTTP 缝）
# ═══════════════════════════════════════════════════════


class TestConfirmRelocate:
    """POST /confirm-relocate 合并端点。"""

    async def _set_fuzzy(self, db_path, thread_id):
        mgr = DatabaseManager(db_path)
        async with mgr.session_factory() as s:
            repo = AnnotationRepository(s)
            # open → pending_relocate → relocated_fuzzy
            await repo.update_thread_status(thread_id, ThreadStatus.pending_relocate)
            await repo.update_thread_status(thread_id, ThreadStatus.relocated_fuzzy)
            await s.commit()
        await mgr.close()

    async def test_confirm_fuzzy_to_open(self, env):
        """确认 relocated_fuzzy → open。"""
        client = env["client"]
        novel = env["novel"]
        chapter = env["chapter"]
        db_path = env["db_path"]
        t_instr = env["thread_instruction"]

        await self._set_fuzzy(db_path, t_instr.id)

        resp = await client.post(
            f"/api/novels/{novel.id}/chapters/{chapter.id}/annotations/confirm-relocate",
            json={"items": [{"thread_id": str(t_instr.id), "action": "confirm"}]},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["confirmed"] == 1

        mgr = DatabaseManager(db_path)
        async with mgr.session_factory() as s:
            repo = AnnotationRepository(s)
            thread = await repo.get_thread(t_instr.id)
            assert thread.status == ThreadStatus.open
        await mgr.close()

    async def test_manual_reanchor(self, env):
        """手动重锚：提供新 anchor + confirm。"""
        client = env["client"]
        novel = env["novel"]
        chapter = env["chapter"]
        db_path = env["db_path"]
        t_orphaned = env["thread_orphaned"]

        resp = await client.post(
            f"/api/novels/{novel.id}/chapters/{chapter.id}/annotations/confirm-relocate",
            json={
                "items": [
                    {
                        "thread_id": str(t_orphaned.id),
                        "action": "confirm",
                        "anchor": {"exact": "新锚定文本", "prefix": "前", "suffix": "后"},
                    }
                ]
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["reanchored"] == 1

        mgr = DatabaseManager(db_path)
        async with mgr.session_factory() as s:
            repo = AnnotationRepository(s)
            thread = await repo.get_thread(t_orphaned.id)
            assert thread.status == ThreadStatus.open
            assert thread.anchor.exact == "新锚定文本"
        await mgr.close()

    async def test_reject_fuzzy(self, env):
        """拒绝 relocated_fuzzy → resolved。"""
        client = env["client"]
        novel = env["novel"]
        chapter = env["chapter"]
        db_path = env["db_path"]
        t_instr = env["thread_instruction"]

        await self._set_fuzzy(db_path, t_instr.id)

        resp = await client.post(
            f"/api/novels/{novel.id}/chapters/{chapter.id}/annotations/confirm-relocate",
            json={"items": [{"thread_id": str(t_instr.id), "action": "reject"}]},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["rejected"] == 1

        mgr = DatabaseManager(db_path)
        async with mgr.session_factory() as s:
            repo = AnnotationRepository(s)
            thread = await repo.get_thread(t_instr.id)
            assert thread.status == ThreadStatus.resolved
        await mgr.close()


# ═══════════════════════════════════════════════════════
#  6. 交错手动改 + 批示再修 + 一键 finalize
# ═══════════════════════════════════════════════════════


class TestInterleavedEditAndFinalize:
    """交错手动改 + 批示再修后一键 finalize 走通。"""

    async def test_manual_edit_then_finalize(self, env):
        """手动改内容 → 定稿。"""
        client = env["client"]
        novel = env["novel"]

        # 手动改
        resp = await client.patch(
            f"/api/novels/{novel.id}/chapters/1",
            json={"content": "手动修改后的内容"},
        )
        assert resp.status_code == 200

        # 定稿
        resp = await client.post(f"/api/novels/{novel.id}/chapters/1/finalize")
        assert resp.status_code == 200
        assert resp.json()["status"] == "finalized"

    async def test_finalize_after_annotation_flow(self, env):
        """批注流程后仍可定稿。"""
        client = env["client"]
        novel = env["novel"]
        chapter = env["chapter"]
        t_instr = env["thread_instruction"]

        # 预览（模拟编排器勾选）
        resp = await client.post(
            f"/api/novels/{novel.id}/chapters/{chapter.id}/annotations/preview",
            json={"thread_ids": [str(t_instr.id)]},
        )
        assert resp.status_code == 200

        # 手动改内容（模拟人工门内编辑）
        resp = await client.patch(
            f"/api/novels/{novel.id}/chapters/1",
            json={"content": "批示修订后的内容"},
        )
        assert resp.status_code == 200

        # 一键定稿
        resp = await client.post(f"/api/novels/{novel.id}/chapters/1/finalize")
        assert resp.status_code == 200
        assert resp.json()["status"] == "finalized"


# ═══════════════════════════════════════════════════════
#  7. pending_relocate 状态转换
# ═══════════════════════════════════════════════════════


class TestPendingRelocateTransitions:
    """pending_relocate 支撑崩溃续跑重定位。"""

    def test_pending_relocate_to_open(self):
        assert can_transition(ThreadStatus.pending_relocate, ThreadStatus.open)

    def test_pending_relocate_to_fuzzy(self):
        assert can_transition(ThreadStatus.pending_relocate, ThreadStatus.relocated_fuzzy)

    def test_pending_relocate_to_orphaned(self):
        assert can_transition(ThreadStatus.pending_relocate, ThreadStatus.orphaned)

    def test_pending_relocate_to_resolved_illegal(self):
        assert not can_transition(ThreadStatus.pending_relocate, ThreadStatus.resolved)
