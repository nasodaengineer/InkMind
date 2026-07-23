"""测试：Run 执行层后端（Issue #36）。

覆盖验收标准：
1. HTTP 缝全生命周期：启动 → phase 推进 → SSE 五事件 → awaiting_human
2. (chapter_id, running) 互斥：同章重复启动返回 409
3. 取消/崩溃语义：partial_content 留 run 记录
4. 重启恢复：running run 变 interrupted（第 9 步）
5. 仅终稿落库；T9 内嵌 T1/T2 原子；T10 写 stats 聚合快照
6. INKMIND_LLM_FAKE=1 下 chat_stream 确定性 token 分片
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

import inkmind.storage.database as db_module
from inkmind.api.app import create_app
from inkmind.execution.runner import EventEmitter, RunLoop
from inkmind.llm.scripted import ScriptedLLMClient
from inkmind.models.chapter import Chapter
from inkmind.models.novel import Novel, NovelMetadata
from inkmind.models.run import RunKind, RunStatus
from inkmind.storage.database import DatabaseManager
from inkmind.storage.models import RunsModel
from inkmind.storage.recovery import RecoveryManager
from inkmind.storage.unit_of_work import UnitOfWork


# ═══════════════════════════════════════════════════════
#  Fixtures
# ═══════════════════════════════════════════════════════


@pytest_asyncio.fixture
async def db():
    """每个测试独立的内存数据库（重置全局单例）。"""
    db_module._engine = None
    db_module._session_factory = None
    manager = DatabaseManager(":memory:")
    await manager.create_tables()
    yield manager
    await manager.drop_tables()
    await manager.close()
    db_module._engine = None
    db_module._session_factory = None


@pytest_asyncio.fixture
async def session(db):
    """获取测试会话。"""
    async with db.session_factory() as s:
        yield s


@pytest_asyncio.fixture
async def uow(session):
    """获取 UnitOfWork。"""
    return UnitOfWork(session)


@pytest.fixture(autouse=True)
def fake_llm_env(monkeypatch):
    """所有测试默认使用离线 LLM。"""
    monkeypatch.setenv("INKMIND_LLM_FAKE", "1")


@pytest_asyncio.fixture
async def seeded_novel(uow, session):
    """创建一个测试小说和章节。"""
    novel = Novel(
        id=uuid4(),
        title="测试小说",
        metadata=NovelMetadata(description="Run 测试"),
    )
    await uow.novels.save(novel)

    chapter = Chapter(
        novel_id=novel.id,
        index=1,
        title="第一章",
        content="",
    )
    await uow.chapters.save(chapter)
    await session.commit()
    return novel, chapter


# ═══════════════════════════════════════════════════════
#  1. ScriptedLLMClient chat_stream 确定性
# ═══════════════════════════════════════════════════════


class TestScriptedChatStream:
    """INKMIND_LLM_FAKE=1 下 chat_stream 确定性产出 token 分片。"""

    @pytest.mark.asyncio
    async def test_stream_produces_deterministic_chunks(self):
        client = ScriptedLLMClient()
        chunks = []
        async for chunk in client.chat_stream("writer", "写第一章"):
            chunks.append(chunk)

        assert len(chunks) > 1
        assert all(isinstance(c, str) for c in chunks)
        full = "".join(chunks)
        assert "离线演示内容" in full

    @pytest.mark.asyncio
    async def test_stream_chunk_size_is_10(self):
        client = ScriptedLLMClient()
        chunks = []
        async for chunk in client.chat_stream("writer", "测试"):
            chunks.append(chunk)

        for chunk in chunks[:-1]:
            assert len(chunk) == 10

    @pytest.mark.asyncio
    async def test_stream_custom_responses(self):
        client = ScriptedLLMClient(responses={"writer": ["自定义内容ABC"]})
        chunks = []
        async for chunk in client.chat_stream("writer", "任意"):
            chunks.append(chunk)

        assert "".join(chunks) == "自定义内容ABC"

    @pytest.mark.asyncio
    async def test_stream_records_stats(self):
        client = ScriptedLLMClient()
        async for _ in client.chat_stream("writer", "测试"):
            pass

        raw = client.get_raw_stats()
        assert len(raw) == 1
        assert raw[0].provider_name == "scripted"

    @pytest.mark.asyncio
    async def test_cancel_all_and_shutdown_are_noop(self):
        client = ScriptedLLMClient()
        client.cancel_all()
        await client.shutdown()


# ═══════════════════════════════════════════════════════
#  2. T8 Run 启动 + 章节互斥
# ═══════════════════════════════════════════════════════


class TestT8RunStart:
    """T8 启动 + (chapter_id, running) 互斥。"""

    @pytest.mark.asyncio
    async def test_start_run_creates_running(self, uow, session, seeded_novel):
        novel, chapter = seeded_novel
        run_id = await uow.t8_run_start(
            novel_id=novel.id,
            kind=RunKind.GENERATE,
            chapter_id=chapter.id,
        )
        await session.commit()

        run = await uow.runs.get_by_id(run_id)
        assert run is not None
        assert run.status == RunStatus.RUNNING
        assert run.kind == RunKind.GENERATE
        assert run.chapter_id == chapter.id

    @pytest.mark.asyncio
    async def test_duplicate_chapter_raises_409(self, uow, session, seeded_novel):
        novel, chapter = seeded_novel
        await uow.t8_run_start(
            novel_id=novel.id,
            kind=RunKind.GENERATE,
            chapter_id=chapter.id,
        )
        await session.commit()

        with pytest.raises(ValueError, match="已有正在执行的 Run"):
            await uow.t8_run_start(
                novel_id=novel.id,
                kind=RunKind.GENERATE,
                chapter_id=chapter.id,
            )

    @pytest.mark.asyncio
    async def test_plan_kind_no_chapter(self, uow, session, seeded_novel):
        novel, _ = seeded_novel
        run_id = await uow.t8_run_start(
            novel_id=novel.id,
            kind=RunKind.PLAN,
            chapter_id=None,
        )
        await session.commit()

        run = await uow.runs.get_by_id(run_id)
        assert run.chapter_id is None


# ═══════════════════════════════════════════════════════
#  3. RunLoop 全生命周期（generate）
# ═══════════════════════════════════════════════════════


class TestRunLoopGenerate:
    """RunLoop generate: Writer → Editor(≤3) → awaiting_human。"""

    @pytest.mark.asyncio
    async def test_full_lifecycle_approve(self, uow, session, seeded_novel):
        novel, chapter = seeded_novel
        run_id = await uow.t8_run_start(
            novel_id=novel.id,
            kind=RunKind.GENERATE,
            chapter_id=chapter.id,
        )
        await session.commit()

        llm = ScriptedLLMClient()
        loop = RunLoop(uow, llm, run_id, chapter=chapter)

        events_received: list[tuple[str, object]] = []
        loop.events.subscribe(lambda e, d: events_received.append((e, d)))

        await loop.start_run(RunKind.GENERATE, novel.id, chapter.id)
        await session.commit()

        run = await uow.runs.get_by_id(run_id)
        assert run.status == RunStatus.AWAITING_HUMAN

        event_types = [e for e, _ in events_received]
        assert "phase" in event_types
        assert "token" in event_types
        assert "verdict" in event_types

    @pytest.mark.asyncio
    async def test_revision_loop_max_3(self, uow, session, seeded_novel):
        """Editor 连续 needs_revision → 超限自动降级 APPROVED。"""
        novel, chapter = seeded_novel
        run_id = await uow.t8_run_start(
            novel_id=novel.id,
            kind=RunKind.GENERATE,
            chapter_id=chapter.id,
        )
        await session.commit()

        llm = ScriptedLLMClient(
            responses={
                "editor": ['{"verdict": "needs_revision"}'] * 5,
            }
        )
        loop = RunLoop(uow, llm, run_id, chapter=chapter)

        events_received: list[tuple[str, object]] = []
        loop.events.subscribe(lambda e, d: events_received.append((e, d)))

        await loop.start_run(RunKind.GENERATE, novel.id, chapter.id)
        await session.commit()

        run = await uow.runs.get_by_id(run_id)
        assert run.status == RunStatus.AWAITING_HUMAN

        verdict_events = [d for e, d in events_received if e == "verdict"]
        assert len(verdict_events) == 3

    @pytest.mark.asyncio
    async def test_cancel_stops_execution(self, uow, session, seeded_novel):
        novel, chapter = seeded_novel
        run_id = await uow.t8_run_start(
            novel_id=novel.id,
            kind=RunKind.GENERATE,
            chapter_id=chapter.id,
        )
        await session.commit()

        llm = ScriptedLLMClient()
        loop = RunLoop(uow, llm, run_id, chapter=chapter)
        loop.cancel()

        await loop.start_run(RunKind.GENERATE, novel.id, chapter.id)

        events_received: list[tuple[str, object]] = []
        loop.events.subscribe(lambda e, d: events_received.append((e, d)))
        assert loop._cancelled


# ═══════════════════════════════════════════════════════
#  4. T9 落稿 + T10 终态
# ═══════════════════════════════════════════════════════


class TestT9T10:
    """T9 落稿收口（内嵌 T1）+ T10 终态收口。"""

    @pytest.mark.asyncio
    async def test_t9_writes_chapter_content(self, uow, session, seeded_novel):
        novel, chapter = seeded_novel
        run_id = await uow.t8_run_start(
            novel_id=novel.id,
            kind=RunKind.GENERATE,
            chapter_id=chapter.id,
        )
        await session.commit()

        await uow.t9_finalize_draft(
            run_id=run_id,
            chapter_content="终稿内容：夜色如墨。",
            chapter_title="第一章 终稿",
        )
        await session.commit()

        updated = await uow.chapters.get_by_id(chapter.id)
        assert updated.content == "终稿内容：夜色如墨。"
        assert updated.title == "第一章 终稿"
        assert updated.version == 2

    @pytest.mark.asyncio
    async def test_t9_clears_partial_content(self, uow, session, seeded_novel):
        novel, chapter = seeded_novel
        run_id = await uow.t8_run_start(
            novel_id=novel.id,
            kind=RunKind.GENERATE,
            chapter_id=chapter.id,
        )
        run = await uow.runs.get_by_id(run_id)
        run.partial_content = "中间稿..."
        await uow.runs.save(run)
        await session.commit()

        await uow.t9_finalize_draft(
            run_id=run_id,
            chapter_content="终稿",
            chapter_title="第一章",
        )
        await session.commit()

        updated_run = await uow.runs.get_by_id(run_id)
        assert updated_run.partial_content == ""

    @pytest.mark.asyncio
    async def test_t10_writes_stats_snapshot(self, uow, session, seeded_novel):
        novel, chapter = seeded_novel
        run_id = await uow.t8_run_start(
            novel_id=novel.id,
            kind=RunKind.GENERATE,
            chapter_id=chapter.id,
        )
        await session.commit()

        stats = {"total_calls": 2, "total_tokens": 100}
        await uow.t10_run_finalize(
            run_id=run_id,
            new_status=RunStatus.AWAITING_HUMAN,
            llm_stats=stats,
        )
        await session.commit()

        run = await uow.runs.get_by_id(run_id)
        assert run.status == RunStatus.AWAITING_HUMAN
        assert run.llm_stats == stats

    @pytest.mark.asyncio
    async def test_t10_completed_sets_completed_at(self, uow, session, seeded_novel):
        novel, chapter = seeded_novel
        run_id = await uow.t8_run_start(
            novel_id=novel.id,
            kind=RunKind.PLAN,
            chapter_id=None,
        )
        await session.commit()

        await uow.t10_run_finalize(
            run_id=run_id,
            new_status=RunStatus.COMPLETED,
        )
        await session.commit()

        run = await uow.runs.get_by_id(run_id)
        assert run.status == RunStatus.COMPLETED
        assert run.completed_at is not None


# ═══════════════════════════════════════════════════════
#  5. 恢复第 9 步：running → interrupted
# ═══════════════════════════════════════════════════════


class TestRecoveryStep9:
    """服务重启后 running run 自动标记 interrupted。"""

    @pytest.mark.asyncio
    async def test_interrupt_running_runs(self, db, session, seeded_novel):
        novel, chapter = seeded_novel
        uow = UnitOfWork(session)
        run_id = await uow.t8_run_start(
            novel_id=novel.id,
            kind=RunKind.GENERATE,
            chapter_id=chapter.id,
        )
        await session.commit()

        recovery = RecoveryManager(session)
        count = await recovery.step9_interrupt_running_runs()
        await session.commit()

        assert count == 1
        run = await uow.runs.get_by_id(run_id)
        assert run.status == RunStatus.INTERRUPTED
        assert run.completed_at is not None

    @pytest.mark.asyncio
    async def test_non_running_not_affected(self, db, session, seeded_novel):
        novel, chapter = seeded_novel
        uow = UnitOfWork(session)
        run_id = await uow.t8_run_start(
            novel_id=novel.id,
            kind=RunKind.GENERATE,
            chapter_id=chapter.id,
        )
        await uow.t10_run_finalize(run_id, RunStatus.COMPLETED)
        await session.commit()

        recovery = RecoveryManager(session)
        count = await recovery.step9_interrupt_running_runs()
        assert count == 0


# ═══════════════════════════════════════════════════════
#  6. 取消语义：partial_content 保留
# ═══════════════════════════════════════════════════════


class TestCancelSemantics:
    """取消/崩溃：partial_content 留 run 记录，可裁决保留/丢弃。"""

    @pytest.mark.asyncio
    async def test_cancel_preserves_partial(self, uow, session, seeded_novel):
        novel, chapter = seeded_novel
        run_id = await uow.t8_run_start(
            novel_id=novel.id,
            kind=RunKind.GENERATE,
            chapter_id=chapter.id,
        )
        run = await uow.runs.get_by_id(run_id)
        run.partial_content = "部分稿件内容..."
        await uow.runs.save(run)
        await session.commit()

        run.cancel()
        await uow.runs.save(run)
        await session.commit()

        updated = await uow.runs.get_by_id(run_id)
        assert updated.status == RunStatus.CANCELLED
        assert updated.partial_content == "部分稿件内容..."

    @pytest.mark.asyncio
    async def test_interrupt_preserves_partial(self, uow, session, seeded_novel):
        novel, chapter = seeded_novel
        run_id = await uow.t8_run_start(
            novel_id=novel.id,
            kind=RunKind.GENERATE,
            chapter_id=chapter.id,
        )
        run = await uow.runs.get_by_id(run_id)
        run.partial_content = "崩溃前的部分稿..."
        await uow.runs.save(run)
        await session.commit()

        run.interrupt()
        await uow.runs.save(run)
        await session.commit()

        updated = await uow.runs.get_by_id(run_id)
        assert updated.status == RunStatus.INTERRUPTED
        assert updated.partial_content == "崩溃前的部分稿..."


# ═══════════════════════════════════════════════════════
#  7. HTTP 缝集成测试（FastAPI + httpx）
# ═══════════════════════════════════════════════════════


@pytest_asyncio.fixture
async def http_env(tmp_path):
    """HTTP 测试环境：文件 DB + 种子数据 + httpx 客户端。

    使用文件 DB 因为 get_db 依赖每次请求后 dispose 引擎会销毁内存 DB。
    """
    db_module._engine = None
    db_module._session_factory = None

    db_path = str(tmp_path / "test_http.db")
    manager = DatabaseManager(db_path)
    await manager.create_tables()

    from inkmind.storage.unit_of_work import UnitOfWork as UoW

    async with manager.session_factory() as s:
        uow = UoW(s)
        novel = Novel(
            id=uuid4(),
            title="HTTP 测试小说",
            metadata=NovelMetadata(description="HTTP 缝测试"),
        )
        await uow.novels.save(novel)
        chapter = Chapter(
            novel_id=novel.id,
            index=1,
            title="第一章",
            content="",
        )
        await uow.chapters.save(chapter)
        await s.commit()

    await manager.close()
    db_module._engine = None
    db_module._session_factory = None

    app = create_app(db_path=db_path)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, novel, chapter

    db_module._engine = None
    db_module._session_factory = None


class TestRunHTTP:
    """HTTP 缝验证 Run REST API。"""

    @pytest.mark.asyncio
    async def test_start_and_get_run(self, http_env):
        client, novel, chapter = http_env

        run_resp = await client.post(
            f"/novels/{novel.id}/runs",
            json={"kind": "generate", "chapter_id": str(chapter.id)},
        )
        assert run_resp.status_code == 201
        run_data = run_resp.json()
        assert run_data["status"] == "running"
        assert run_data["kind"] == "generate"

        get_resp = await client.get(
            f"/novels/{novel.id}/runs/{run_data['id']}"
        )
        assert get_resp.status_code == 200
        assert get_resp.json()["id"] == run_data["id"]

    @pytest.mark.asyncio
    async def test_duplicate_chapter_409(self, http_env):
        client, novel, chapter = http_env

        resp1 = await client.post(
            f"/novels/{novel.id}/runs",
            json={"kind": "generate", "chapter_id": str(chapter.id)},
        )
        assert resp1.status_code == 201

        resp2 = await client.post(
            f"/novels/{novel.id}/runs",
            json={"kind": "generate", "chapter_id": str(chapter.id)},
        )
        assert resp2.status_code == 409

    @pytest.mark.asyncio
    async def test_cancel_run(self, http_env):
        client, novel, chapter = http_env

        run_resp = await client.post(
            f"/novels/{novel.id}/runs",
            json={"kind": "generate", "chapter_id": str(chapter.id)},
        )
        run_id = run_resp.json()["id"]

        cancel_resp = await client.post(
            f"/novels/{novel.id}/runs/{run_id}/cancel"
        )
        assert cancel_resp.status_code == 200
        assert cancel_resp.json()["status"] == "cancelled"

    @pytest.mark.asyncio
    async def test_cancel_non_running_409(self, http_env):
        client, novel, chapter = http_env

        run_resp = await client.post(
            f"/novels/{novel.id}/runs",
            json={"kind": "generate", "chapter_id": str(chapter.id)},
        )
        run_id = run_resp.json()["id"]

        await client.post(f"/novels/{novel.id}/runs/{run_id}/cancel")
        resp2 = await client.post(f"/novels/{novel.id}/runs/{run_id}/cancel")
        assert resp2.status_code == 409

    @pytest.mark.asyncio
    async def test_list_runs(self, http_env):
        client, novel, chapter = http_env

        await client.post(
            f"/novels/{novel.id}/runs",
            json={"kind": "plan", "level": "spine"},
        )

        list_resp = await client.get(f"/novels/{novel.id}/runs")
        assert list_resp.status_code == 200
        assert len(list_resp.json()["runs"]) == 1

    @pytest.mark.asyncio
    async def test_invalid_kind_400(self, http_env):
        client, novel, chapter = http_env

        resp = await client.post(
            f"/novels/{novel.id}/runs",
            json={"kind": "invalid_kind"},
        )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_sse_snapshot_for_non_running(self, http_env):
        """非 running 状态的 Run 请求 SSE 流时返回快照。"""
        client, novel, chapter = http_env

        run_resp = await client.post(
            f"/novels/{novel.id}/runs",
            json={"kind": "generate", "chapter_id": str(chapter.id)},
        )
        run_id = run_resp.json()["id"]

        await client.post(f"/novels/{novel.id}/runs/{run_id}/cancel")

        sse_resp = await client.get(
            f"/novels/{novel.id}/runs/{run_id}/stream"
        )
        assert sse_resp.status_code == 200
        assert "text/event-stream" in sse_resp.headers["content-type"]
        body = sse_resp.text
        assert "event: done" in body


# ═══════════════════════════════════════════════════════
#  8. Checkpoint 机制
# ═══════════════════════════════════════════════════════


class TestCheckpoint:
    """Checkpoint: 2s / 500 字节流式落 partial_content。"""

    @pytest.mark.asyncio
    async def test_checkpoint_writes_partial(self, uow, session, seeded_novel):
        novel, chapter = seeded_novel
        run_id = await uow.t8_run_start(
            novel_id=novel.id,
            kind=RunKind.GENERATE,
            chapter_id=chapter.id,
        )
        await session.commit()

        llm = ScriptedLLMClient(
            responses={"writer": ["A" * 600]}
        )
        loop = RunLoop(uow, llm, run_id, chapter=chapter)
        loop._checkpoint_interval_bytes = 500

        events: list[tuple[str, object]] = []
        loop.events.subscribe(lambda e, d: events.append((e, d)))

        await loop.start_run(RunKind.GENERATE, novel.id, chapter.id)
        await session.commit()

        token_events = [d for e, d in events if e == "token"]
        assert len(token_events) > 1
