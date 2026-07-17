"""Agent 协作流水线测试 — 真实 LLM 接入（工单 06）。

覆盖：
  - ScriptedLLMClient：离线确定性 LLM 缝（INKMIND_LLM_FAKE=1）
  - WriterAgent / EditorAgent / MemoryKeeperAgent：prompt 构造 + 响应解析
  - CollaborationPipeline.run_one_round：真实 LLM 内容 → T1/T3/T4/T5 事务持久化
  - 修订循环（needs_revision → revise）与 max_iterations 兜底
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from uuid import UUID, uuid4

pytestmark = pytest.mark.asyncio

from sqlalchemy.ext.asyncio import AsyncSession

from inkmind.agents.collaboration import (
    ChapterContext,
    CollaborationPipeline,
    EditorAgent,
    MemoryKeeperAgent,
    PlannerAgent,
    WriterAgent,
)
from inkmind.llm.client import build_llm_client
from inkmind.llm.scripted import ScriptedLLMClient
from inkmind.models.agent import ChapterStatus, Verdict
from inkmind.models.novel import Novel, NovelMetadata
from inkmind.storage import UnitOfWork
from inkmind.storage.database import get_manager
from inkmind.storage.models import MemoryArchiveModel
from sqlalchemy import select


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


def _make_novel(novel_id: UUID) -> Novel:
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


async def _seed_novel(uow: UnitOfWork, novel_id: UUID) -> None:
    async with uow.transaction():
        await uow.novels.save(_make_novel(novel_id))
        await uow.commit()


def _ctx(**overrides) -> ChapterContext:
    base = dict(
        novel_title="星辰之海",
        novel_description="一个关于星空与命运的故事",
        chapter_index=1,
        chapter_title="启程",
        previous_summaries=[],
    )
    base.update(overrides)
    return ChapterContext(**base)


# ═══════════════════════════════════════════════════════════════
#  1. ScriptedLLMClient — 离线确定性缝
# ═══════════════════════════════════════════════════════════════


class TestScriptedLLMClient:
    async def test_default_responses_by_role(self):
        llm = ScriptedLLMClient()
        writer_resp = await llm.chat("writer", "写第一章")
        editor_resp = await llm.chat("editor", "评审")
        memory_resp = await llm.chat("memory-keeper", "总结")

        assert len(writer_resp.content) > 100  # 像章节正文
        assert '"approve"' in editor_resp.content
        assert '"summary"' in memory_resp.content

    async def test_default_writer_content_unique_per_call(self):
        """默认 writer 响应每次调用内容不同（避免 digest 幂等误判重复）。"""
        llm = ScriptedLLMClient()
        r1 = await llm.chat("writer", "写第一章")
        r2 = await llm.chat("writer", "写第二章")
        assert r1.content != r2.content

    async def test_custom_responses_consumed_in_order(self):
        llm = ScriptedLLMClient(
            responses={"editor": ['{"verdict": "needs_revision", "issues": ["节奏慢"]}',
                                  '{"verdict": "approve", "issues": []}']}
        )
        r1 = await llm.chat("editor", "评审1")
        r2 = await llm.chat("editor", "评审2")
        r3 = await llm.chat("editor", "评审3")  # 队列耗尽 → 重复最后一个
        assert "needs_revision" in r1.content
        assert '"approve"' in r2.content
        assert '"approve"' in r3.content

    async def test_calls_recorded(self):
        llm = ScriptedLLMClient()
        await llm.chat("writer", "测试 prompt", system_prompt="系统提示")
        assert len(llm.calls) == 1
        assert llm.calls[0]["agent_role"] == "writer"
        assert llm.calls[0]["prompt"] == "测试 prompt"
        assert llm.calls[0]["system_prompt"] == "系统提示"


# ═══════════════════════════════════════════════════════════════
#  2. WriterAgent — 真实内容生成
# ═══════════════════════════════════════════════════════════════


class TestWriterAgent:
    async def test_write_returns_llm_content(self):
        llm = ScriptedLLMClient(responses={"writer": ["林星辰仰望夜空，繁星如雨。"]})
        agent = WriterAgent(llm)
        content, model = await agent.write(_ctx())
        assert content == "林星辰仰望夜空，繁星如雨。"
        assert model == "scripted-fake"  # 返回值携带模型，不用可变状态

    async def test_write_prompt_contains_context(self):
        llm = ScriptedLLMClient()
        agent = WriterAgent(llm)
        await agent.write(
            _ctx(
                chapter_index=3,
                chapter_title="星陨",
                previous_summaries=["第一章：主角启程", "第二章：遭遇星兽"],
            )
        )
        prompt = llm.calls[0]["prompt"]
        assert "星辰之海" in prompt          # 小说标题
        assert "星陨" in prompt              # 本章标题
        assert "第 3 章" in prompt or "第3章" in prompt
        assert "第一章：主角启程" in prompt   # 前文摘要
        assert "遭遇星兽" in prompt

    async def test_revise_prompt_contains_issues_and_previous(self):
        llm = ScriptedLLMClient(responses={"writer": ["修订后的正文。"]})
        agent = WriterAgent(llm)
        content, _ = await agent.revise(
            _ctx(),
            previous_content="旧版正文内容。",
            issues=["情绪描写不足", "节奏拖沓"],
            iteration=1,
        )
        assert content == "修订后的正文。"
        prompt = llm.calls[0]["prompt"]
        assert "旧版正文内容。" in prompt
        assert "情绪描写不足" in prompt
        assert "节奏拖沓" in prompt

    async def test_write_prompt_contains_outline(self):
        """Writer prompt 注入 Planner 产出的本章大纲。"""
        llm = ScriptedLLMClient()
        agent = WriterAgent(llm)
        await agent.write(_ctx(outline="主角在港口登上星舰，遭遇伏击"))
        prompt = llm.calls[0]["prompt"]
        assert "主角在港口登上星舰，遭遇伏击" in prompt

    async def test_write_uses_writer_role(self):
        llm = ScriptedLLMClient()
        agent = WriterAgent(llm)
        await agent.write(_ctx())
        assert llm.calls[0]["agent_role"] == "writer"


# ═══════════════════════════════════════════════════════════════
#  2.5 PlannerAgent — 批量大纲规划
# ═══════════════════════════════════════════════════════════════


class TestPlannerAgent:
    async def test_plan_parses_chapters_json(self):
        llm = ScriptedLLMClient(
            responses={
                "planner": [
                    '{"chapters": ['
                    '{"index": 1, "title": "启程", "outline": "主角离开村庄"},'
                    '{"index": 2, "title": "星途", "outline": "登上星舰"}'
                    "]}"
                ]
            }
        )
        agent = PlannerAgent(llm)
        planned = await agent.plan(
            novel_title="星辰之海",
            novel_description="星空故事",
            previous_summaries=[],
            start_index=1,
            count=5,
        )
        assert len(planned) == 2
        assert planned[0].index == 1
        assert planned[0].title == "启程"
        assert planned[0].outline == "主角离开村庄"
        assert planned[1].index == 2

    async def test_plan_bare_json_array(self):
        """容忍直接输出 JSON 数组（无 chapters 包装）。"""
        llm = ScriptedLLMClient(
            responses={"planner": ['[{"index": 3, "title": "遇险", "outline": "遭遇星兽"}]']}
        )
        agent = PlannerAgent(llm)
        planned = await agent.plan("t", "", [], start_index=3, count=5)
        assert len(planned) == 1
        assert planned[0].index == 3
        assert planned[0].outline == "遭遇星兽"

    async def test_plan_malformed_returns_empty(self):
        """解析失败返回空列表（不阻塞流水线）。"""
        llm = ScriptedLLMClient(responses={"planner": ["我完全没法规划"]})
        agent = PlannerAgent(llm)
        planned = await agent.plan("t", "", [], start_index=1, count=5)
        assert planned == []

    async def test_plan_prompt_contains_context(self):
        llm = ScriptedLLMClient()
        agent = PlannerAgent(llm)
        await agent.plan(
            novel_title="星辰之海",
            novel_description="星空故事",
            previous_summaries=["第一章：启程"],
            start_index=2,
            count=5,
        )
        call = llm.calls[0]
        assert call["agent_role"] == "planner"
        assert "星辰之海" in call["prompt"]
        assert "第一章：启程" in call["prompt"]
        assert "2" in call["prompt"]  # 起始章节序号


# ═══════════════════════════════════════════════════════════════
#  3. EditorAgent — 真实评审 + Verdict 解析
# ═══════════════════════════════════════════════════════════════


class TestEditorAgent:
    async def test_approve_plain_json(self):
        llm = ScriptedLLMClient(
            responses={"editor": ['{"verdict": "approve", "issues": []}']}
        )
        agent = EditorAgent(llm)
        verdict = await agent.review(_ctx(), content="正文", iteration=0)
        assert verdict.verdict == Verdict.APPROVE
        assert verdict.issues == []

    async def test_needs_revision_in_markdown_fence(self):
        llm = ScriptedLLMClient(
            responses={
                "editor": [
                    '评审结果如下：\n```json\n{"verdict": "needs_revision", '
                    '"issues": ["节奏慢", "对话生硬"]}\n```'
                ]
            }
        )
        agent = EditorAgent(llm)
        verdict = await agent.review(_ctx(), content="正文", iteration=0)
        assert verdict.verdict == Verdict.NEEDS_REVISION
        assert verdict.issues == ["节奏慢", "对话生硬"]

    async def test_malformed_json_falls_back_to_approve(self):
        """无法解析的响应不阻塞流水线 — 兜底为 approve。"""
        llm = ScriptedLLMClient(responses={"editor": ["我觉得写得挺好的没问题"]})
        agent = EditorAgent(llm)
        verdict = await agent.review(_ctx(), content="正文", iteration=0)
        assert verdict.verdict == Verdict.APPROVE

    async def test_keyword_fallback_needs_revision(self):
        llm = ScriptedLLMClient(
            responses={"editor": ["结论：needs_revision。主角动机完全站不住脚。"]}
        )
        agent = EditorAgent(llm)
        verdict = await agent.review(_ctx(), content="正文", iteration=0)
        assert verdict.verdict == Verdict.NEEDS_REVISION
        assert len(verdict.issues) >= 1

    async def test_review_prompt_contains_content(self):
        llm = ScriptedLLMClient()
        agent = EditorAgent(llm)
        await agent.review(_ctx(chapter_title="星陨"), content="被评审的正文内容", iteration=2)
        prompt = llm.calls[0]["prompt"]
        assert "被评审的正文内容" in prompt
        assert "星陨" in prompt
        assert llm.calls[0]["agent_role"] == "editor"


# ═══════════════════════════════════════════════════════════════
#  4. MemoryKeeperAgent — 真实摘要
# ═══════════════════════════════════════════════════════════════


class TestMemoryKeeperAgent:
    async def test_summarize_json(self):
        llm = ScriptedLLMClient(
            responses={
                "memory-keeper": [
                    '{"summary": "主角启程前往星辉城", "key_events": ["启程", "告别村长"]}'
                ]
            }
        )
        agent = MemoryKeeperAgent(llm)
        summary, events = await agent.summarize(
            chapter_index=1, chapter_title="启程", content="正文内容"
        )
        assert summary == "主角启程前往星辉城"
        assert events == ["启程", "告别村长"]

    async def test_summarize_malformed_fallback(self):
        """畸形响应兜底：摘要取原始文本截断，事件为空。"""
        llm = ScriptedLLMClient(responses={"memory-keeper": ["这不是 JSON 的响应"]})
        agent = MemoryKeeperAgent(llm)
        summary, events = await agent.summarize(
            chapter_index=1, chapter_title="启程", content="正文内容"
        )
        assert summary  # 非空
        assert events == []

    async def test_summarize_prompt_contains_content(self):
        llm = ScriptedLLMClient()
        agent = MemoryKeeperAgent(llm)
        await agent.summarize(chapter_index=1, chapter_title="启程", content="需要总结的正文")
        assert "需要总结的正文" in llm.calls[0]["prompt"]
        assert llm.calls[0]["agent_role"] == "memory-keeper"


# ═══════════════════════════════════════════════════════════════
#  5. CollaborationPipeline — 一轮完整协作（集成）
# ═══════════════════════════════════════════════════════════════


def _approve_llm(writer_content: str, memory_json: str | None = None) -> ScriptedLLMClient:
    return ScriptedLLMClient(
        responses={
            "writer": [writer_content],
            "editor": ['{"verdict": "approve", "issues": []}'],
            "memory-keeper": [
                memory_json
                or '{"summary": "主角踏上旅程", "key_events": ["启程"]}'
            ],
        }
    )


class TestPipelineOneRound:
    async def test_real_content_persisted(self, uow: UnitOfWork, novel_id: UUID):
        """核心验收：章节内容来自 LLM 生成，而非占位符。"""
        await _seed_novel(uow, novel_id)
        llm = _approve_llm("夜空中最亮的星，指引着迷途的旅人。" * 10)

        pipeline = CollaborationPipeline(llm)
        result = await pipeline.run_one_round(uow, novel_id, title="启程")

        assert result.chapter_index == 1
        assert result.content_length > 100

        async with uow.transaction():
            ch = await uow.chapters.get_by_novel_and_index(novel_id, 1)
            assert ch is not None
            assert ch.content == "夜空中最亮的星，指引着迷途的旅人。" * 10
            assert "占位" not in ch.content  # 不再是占位符文本
            assert ch.status == ChapterStatus.FINALIZED
            assert ch.is_baseline is True

    async def test_summary_and_events_from_llm(self, uow: UnitOfWork, novel_id: UUID):
        await _seed_novel(uow, novel_id)
        llm = _approve_llm("正文。" * 100)

        pipeline = CollaborationPipeline(llm)
        result = await pipeline.run_one_round(uow, novel_id, title="启程")

        assert result.summary == "主角踏上旅程"
        assert result.key_events == ["启程"]

        async with uow.transaction():
            ch = await uow.chapters.get_by_novel_and_index(novel_id, 1)
            assert ch.summary == "主角踏上旅程"
            assert ch.key_events == ["启程"]

    async def test_pipeline_state_advanced(self, uow: UnitOfWork, novel_id: UUID):
        await _seed_novel(uow, novel_id)
        llm = _approve_llm("正文。" * 100)

        pipeline = CollaborationPipeline(llm)
        await pipeline.run_one_round(uow, novel_id, title="启程")

        async with uow.transaction():
            state = await uow.pipelines.get_by_novel(novel_id)
            assert state is not None
            assert state.current_chapter_index == 1
            assert state.chapters[1] == ChapterStatus.FINALIZED

    async def test_memory_archives_written(self, uow: UnitOfWork, novel_id: UUID):
        await _seed_novel(uow, novel_id)
        llm = _approve_llm("正文。" * 100)

        pipeline = CollaborationPipeline(llm)
        await pipeline.run_one_round(uow, novel_id, title="启程")

        async with uow.transaction():
            # L2 压缩记忆包含真实摘要
            result = await uow._session.execute(
                select(MemoryArchiveModel).where(
                    MemoryArchiveModel.novel_id == str(novel_id),
                    MemoryArchiveModel.tier == "l2_compressed",
                )
            )
            l2 = result.scalar_one_or_none()
            assert l2 is not None
            assert l2.data["summaries"][0]["summary"] == "主角踏上旅程"
            assert l2.data["events"][0]["key_events"] == ["启程"]

            # L1 滑窗快照包含真实摘要
            result = await uow._session.execute(
                select(MemoryArchiveModel).where(
                    MemoryArchiveModel.novel_id == str(novel_id),
                    MemoryArchiveModel.tier == "l1_active",
                )
            )
            l1 = result.scalar_one_or_none()
            assert l1 is not None
            assert l1.data["sliding_window"]["current_end"] == 1
            assert l1.data["sliding_window"]["window_size"] == 5  # CONTEXT.md 默认窗口

    async def test_novel_metadata_updated(self, uow: UnitOfWork, novel_id: UUID):
        await _seed_novel(uow, novel_id)
        content = "正文。" * 100
        llm = _approve_llm(content)

        pipeline = CollaborationPipeline(llm)
        await pipeline.run_one_round(uow, novel_id, title="启程")

        async with uow.transaction():
            novel = await uow.novels.get_by_id(novel_id)
            assert novel.metadata.chapter_count == 1
            assert novel.metadata.word_count == len(content)
            assert novel.metadata.status == "writing"

    async def test_source_trace_records_model(self, uow: UnitOfWork, novel_id: UUID):
        await _seed_novel(uow, novel_id)
        llm = _approve_llm("正文。" * 100)

        pipeline = CollaborationPipeline(llm)
        await pipeline.run_one_round(uow, novel_id, title="启程")

        async with uow.transaction():
            ch = await uow.chapters.get_by_novel_and_index(novel_id, 1)
            assert ch.source_trace  # 非空：记录真实来源

    async def test_missing_novel_raises(self, uow: UnitOfWork, novel_id: UUID):
        llm = _approve_llm("正文。" * 100)
        pipeline = CollaborationPipeline(llm)
        with pytest.raises(ValueError, match="不存在"):
            await pipeline.run_one_round(uow, novel_id, title="启程")


class TestPipelinePlanner:
    async def test_first_round_plans_and_persists_outlines(
        self, uow: UnitOfWork, novel_id: UUID
    ):
        """第 1 轮：无大纲 → Planner 规划多章 → T2 持久化 PLANNED 占位章节。"""
        await _seed_novel(uow, novel_id)
        llm = _approve_llm("正文。" * 100)
        pipeline = CollaborationPipeline(llm)
        await pipeline.run_one_round(uow, novel_id)

        assert len([c for c in llm.calls if c["agent_role"] == "planner"]) == 1

        async with uow.transaction():
            ch1 = await uow.chapters.get_by_novel_and_index(novel_id, 1)
            assert ch1.status == ChapterStatus.FINALIZED
            # 后续章节已规划为 PLANNED 占位（含大纲文本）
            planned = await uow.chapters.get_by_novel_and_index(novel_id, 2)
            assert planned is not None
            assert planned.status == ChapterStatus.PLANNED
            assert planned.summary  # 大纲文本存于 summary
            assert planned.content == ""  # 占位章节无正文

    async def test_writer_prompt_contains_planner_outline(
        self, uow: UnitOfWork, novel_id: UUID
    ):
        """Writer 根据 Planner 大纲写作（scripted 大纲含可识别标记）。"""
        await _seed_novel(uow, novel_id)
        llm = _approve_llm("正文。" * 100)
        pipeline = CollaborationPipeline(llm)
        await pipeline.run_one_round(uow, novel_id)

        writer_calls = [c for c in llm.calls if c["agent_role"] == "writer"]
        assert "第1章大纲内容" in writer_calls[0]["prompt"]

    async def test_second_round_reuses_stored_outline(
        self, uow: UnitOfWork, novel_id: UUID
    ):
        """第 2 轮：第 2 章已有 PLANNED 大纲 → 不再调用 Planner，直接复用。"""
        await _seed_novel(uow, novel_id)
        llm = _approve_llm("正文。" * 100)
        pipeline = CollaborationPipeline(llm)
        await pipeline.run_one_round(uow, novel_id)
        n_planner_r1 = len([c for c in llm.calls if c["agent_role"] == "planner"])

        await pipeline.run_one_round(uow, novel_id)
        n_planner_r2 = len([c for c in llm.calls if c["agent_role"] == "planner"])
        assert n_planner_r2 == n_planner_r1  # 未新增 Planner 调用

        # 第 2 轮 writer prompt 含第 2 章大纲，且不含未来章节的大纲
        writer_calls = [c for c in llm.calls if c["agent_role"] == "writer"]
        r2_prompt = writer_calls[-1]["prompt"]
        assert "第2章大纲内容" in r2_prompt
        assert "第3章大纲内容" not in r2_prompt

    async def test_finalized_chapter_reuses_planned_row(
        self, uow: UnitOfWork, novel_id: UUID
    ):
        """定稿复用 PLANNED 行（同 id 更新，不新增行触发唯一约束冲突）。"""
        await _seed_novel(uow, novel_id)
        llm = _approve_llm("正文。" * 100)
        pipeline = CollaborationPipeline(llm)
        await pipeline.run_one_round(uow, novel_id)
        r2 = await pipeline.run_one_round(uow, novel_id)

        async with uow.transaction():
            chapters = await uow.chapters.get_chapters_by_novel(novel_id)
            ch2s = [c for c in chapters if c.index == 2]
            assert len(ch2s) == 1
            assert ch2s[0].status == ChapterStatus.FINALIZED
            assert ch2s[0].id == r2.chapter_id

    async def test_planner_failure_does_not_block(
        self, uow: UnitOfWork, novel_id: UUID
    ):
        """Planner 解析失败 → 无大纲兜底，流水线继续定稿。"""
        await _seed_novel(uow, novel_id)
        llm = ScriptedLLMClient(
            responses={
                "planner": ["无法规划的垃圾输出"],
                "writer": ["正文。" * 100],
                "editor": ['{"verdict": "approve", "issues": []}'],
                "memory-keeper": ['{"summary": "摘要", "key_events": []}'],
            }
        )
        pipeline = CollaborationPipeline(llm)
        result = await pipeline.run_one_round(uow, novel_id, title="启程")
        assert result.chapter_index == 1

        async with uow.transaction():
            ch = await uow.chapters.get_by_novel_and_index(novel_id, 1)
            assert ch.status == ChapterStatus.FINALIZED


class TestPipelineMultiRound:
    async def test_second_round_uses_previous_summary(
        self, uow: UnitOfWork, novel_id: UUID
    ):
        """第 2 轮的 Writer prompt 注入第 1 章的真实摘要。"""
        await _seed_novel(uow, novel_id)
        llm = ScriptedLLMClient(
            responses={
                "writer": ["第一章正文。" * 50, "第二章正文。" * 50],
                "editor": ['{"verdict": "approve", "issues": []}'],
                "memory-keeper": [
                    '{"summary": "第一章摘要：启程", "key_events": ["启程"]}',
                    '{"summary": "第二章摘要：遇险", "key_events": ["遇险"]}',
                ],
            }
        )
        pipeline = CollaborationPipeline(llm)
        r1 = await pipeline.run_one_round(uow, novel_id, title="启程")
        r2 = await pipeline.run_one_round(uow, novel_id, title="遇险")

        assert r1.chapter_index == 1
        assert r2.chapter_index == 2

        # 第二次 writer 调用的 prompt 包含第一章摘要
        writer_calls = [c for c in llm.calls if c["agent_role"] == "writer"]
        assert len(writer_calls) == 2
        assert "第一章摘要：启程" in writer_calls[1]["prompt"]

        async with uow.transaction():
            ch2 = await uow.chapters.get_by_novel_and_index(novel_id, 2)
            assert ch2.content == "第二章正文。" * 50
            assert ch2.status == ChapterStatus.FINALIZED


class TestPipelineRevisionLoop:
    async def test_revision_then_approve(self, uow: UnitOfWork, novel_id: UUID):
        """needs_revision → Writer 修订 → approve，最终保存修订版内容。"""
        await _seed_novel(uow, novel_id)
        llm = ScriptedLLMClient(
            responses={
                "writer": ["初稿内容。" * 50, "修订稿内容。" * 50],
                "editor": [
                    '{"verdict": "needs_revision", "issues": ["情绪不足"]}',
                    '{"verdict": "approve", "issues": []}',
                ],
                "memory-keeper": ['{"summary": "摘要", "key_events": []}'],
            }
        )
        pipeline = CollaborationPipeline(llm)
        result = await pipeline.run_one_round(uow, novel_id, title="启程")

        assert result.iterations == 1
        assert result.verdict == Verdict.APPROVE

        # Writer 被调用 2 次（初稿 + 修订），Editor 2 次
        assert len([c for c in llm.calls if c["agent_role"] == "writer"]) == 2
        assert len([c for c in llm.calls if c["agent_role"] == "editor"]) == 2

        # 修订 prompt 包含 Editor 的 issues
        writer_calls = [c for c in llm.calls if c["agent_role"] == "writer"]
        revise_prompt = writer_calls[1]["prompt"]
        assert "情绪不足" in revise_prompt

        async with uow.transaction():
            ch = await uow.chapters.get_by_novel_and_index(novel_id, 1)
            assert ch.content == "修订稿内容。" * 50  # 保存的是修订版
            assert ch.status == ChapterStatus.FINALIZED

    async def test_max_iterations_fallback(self, uow: UnitOfWork, novel_id: UUID):
        """持续 needs_revision → 达到 max_iterations 后放行定稿并标记。"""
        await _seed_novel(uow, novel_id)
        llm = ScriptedLLMClient(
            responses={
                "writer": ["内容v1。" * 50, "内容v2。" * 50, "内容v3。" * 50],
                "editor": ['{"verdict": "needs_revision", "issues": ["不够好"]}'],
                "memory-keeper": ['{"summary": "摘要", "key_events": []}'],
            }
        )
        pipeline = CollaborationPipeline(llm, max_iterations=2)
        result = await pipeline.run_one_round(uow, novel_id, title="启程")

        assert result.max_iterations_hit is True
        assert result.iterations == 2
        # 1 次初稿 + 2 次修订 = 3 次 writer 调用
        assert len([c for c in llm.calls if c["agent_role"] == "writer"]) == 3

        # 流水线不停摆：章节仍定稿
        async with uow.transaction():
            ch = await uow.chapters.get_by_novel_and_index(novel_id, 1)
            assert ch is not None
            assert ch.status == ChapterStatus.FINALIZED


# ═══════════════════════════════════════════════════════════════
#  6. build_llm_client — 环境缝
# ═══════════════════════════════════════════════════════════════


class TestBuildLLMClient:
    async def test_fake_env_returns_scripted(self, monkeypatch):
        monkeypatch.setenv("INKMIND_LLM_FAKE", "1")
        client = build_llm_client()
        assert isinstance(client, ScriptedLLMClient)

    async def test_default_returns_real_client(self, monkeypatch):
        from inkmind.llm.client import LLMClient

        monkeypatch.delenv("INKMIND_LLM_FAKE", raising=False)
        client = build_llm_client()
        assert isinstance(client, LLMClient)
