"""Agent 协作流水线 — 真实 LLM 生成（工单 06）。

结构：
  - PlannerAgent / WriterAgent / EditorAgent / MemoryKeeperAgent：各自持有
    LLM 客户端，通过 ``chat(role, prompt, system_prompt)`` 接口调用
    （LLMClient 与 ScriptedLLMClient 均满足该接口，后者用于离线测试）
  - CollaborationPipeline.run_one_round：1 轮完整协作

设计要点：
  - LLM 调用（慢）全部发生在数据库事务之外；事务内只做持久化
  - 持久化沿用 T1/T2/T3/T4/T5 事务边界（ADR-0005），含 digest 幂等；
    不直接触碰 ORM Session（ADR-0009），任务创建与提交走 UoW 公开方法
  - Planner 批量规划 5 章大纲（T2 持久化为 PLANNED 占位章节），Writer
    按本章大纲写作；定稿复用 PLANNED 行 id，避免唯一约束冲突
  - Editor 真实评审驱动修订循环；达到 max_iterations 后放行定稿
    （流水线不停摆，生成质量调优留给后续迭代）
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Protocol
from uuid import UUID, uuid4

from inkmind.agents.prompts import (
    EDITOR_SYSTEM_PROMPT,
    MEMORY_KEEPER_SYSTEM_PROMPT,
    PLANNER_SYSTEM_PROMPT,
    WRITER_SYSTEM_PROMPT,
    build_editor_prompt,
    build_memory_prompt,
    build_planner_prompt,
    build_revision_prompt,
    build_writer_prompt,
)
from inkmind.llm.providers.base import LLMResponse
from inkmind.models.agent import ChapterStatus, PipelineState, Verdict, VerdictPayload
from inkmind.models.chapter import Chapter
from inkmind.storage.unit_of_work import UnitOfWork


# ──────────────────────────────────────────────
#  LLM 客户端接口（LLMClient / ScriptedLLMClient 均满足）
# ──────────────────────────────────────────────


class ChatClient(Protocol):
    async def chat(
        self,
        agent_role: str,
        prompt: str,
        system_prompt: Optional[str] = None,
        **kwargs,
    ) -> LLMResponse: ...


# ──────────────────────────────────────────────
#  数据类型
# ──────────────────────────────────────────────


@dataclass(frozen=True)
class ChapterContext:
    """Writer/Editor 共享的章节上下文。"""

    novel_title: str
    novel_description: str
    chapter_index: int
    chapter_title: str
    previous_summaries: list[str] = field(default_factory=list)
    outline: str = ""  # Planner 产出的本章大纲（无大纲时为空）


@dataclass(frozen=True)
class PlannedChapter:
    """Planner 产出的单章大纲。"""

    index: int
    title: str
    outline: str


@dataclass
class PipelineResult:
    """一轮协作的结果摘要（供 CLI 输出）。"""

    chapter_index: int
    chapter_title: str
    chapter_id: UUID
    content: str
    content_length: int
    iterations: int
    verdict: Verdict
    issues: list[str]
    summary: str
    key_events: list[str]
    max_iterations_hit: bool
    planned_count: int  # 本轮新规划的章节数（0 表示复用已有大纲）


# ──────────────────────────────────────────────
#  JSON 响应宽松解析
# ──────────────────────────────────────────────


def _extract_json_object(text: str) -> Optional[dict]:
    """从 LLM 响应中提取 JSON 对象（容忍 markdown 围栏与前后杂文本）。"""
    stripped = text.strip()

    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", stripped, re.DOTALL)
    if fence:
        try:
            data = json.loads(fence.group(1))
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass

    start = stripped.find("{")
    end = stripped.rfind("}")
    if start != -1 and end > start:
        try:
            data = json.loads(stripped[start : end + 1])
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass
    return None


def _extract_json_array(text: str) -> Optional[list]:
    """从 LLM 响应中提取 JSON 数组（容忍 markdown 围栏与前后杂文本）。"""
    stripped = text.strip()

    fence = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", stripped, re.DOTALL)
    if fence:
        try:
            data = json.loads(fence.group(1))
            if isinstance(data, list):
                return data
        except json.JSONDecodeError:
            pass

    start = stripped.find("[")
    end = stripped.rfind("]")
    if start != -1 and end > start:
        try:
            data = json.loads(stripped[start : end + 1])
            if isinstance(data, list):
                return data
        except json.JSONDecodeError:
            pass
    return None


# ──────────────────────────────────────────────
#  Agents
# ──────────────────────────────────────────────


class PlannerAgent:
    """Planner：批量规划后续章节大纲，保持宏观情节连贯。"""

    ROLE = "planner"

    def __init__(self, llm: ChatClient) -> None:
        self._llm = llm

    async def plan(
        self,
        novel_title: str,
        novel_description: str,
        previous_summaries: list[str],
        start_index: int,
        count: int,
    ) -> list[PlannedChapter]:
        """规划 start_index 起 count 章的大纲。解析失败返回空列表（不阻塞流水线）。"""
        resp = await self._llm.chat(
            self.ROLE,
            build_planner_prompt(
                novel_title,
                novel_description,
                previous_summaries,
                start_index,
                count,
            ),
            system_prompt=PLANNER_SYSTEM_PROMPT,
        )
        return self._parse_chapters(resp.content)

    @staticmethod
    def _parse_chapters(text: str) -> list[PlannedChapter]:
        raw_items: Optional[list] = None
        obj = _extract_json_object(text)
        if obj and isinstance(obj.get("chapters"), list):
            raw_items = obj["chapters"]
        if raw_items is None:
            # 无 chapters 包装（或对象实为数组内元素）→ 尝试裸数组
            raw_items = _extract_json_array(text)
        if not raw_items:
            return []

        planned: list[PlannedChapter] = []
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            try:
                index = int(item["index"])
            except (KeyError, TypeError, ValueError):
                continue
            title = str(item.get("title") or f"第 {index} 章")[:100]
            outline = str(item.get("outline") or "")
            planned.append(PlannedChapter(index=index, title=title, outline=outline))
        return planned


class WriterAgent:
    """Writer：生成初稿与修订稿。"""

    ROLE = "writer"

    def __init__(self, llm: ChatClient) -> None:
        self._llm = llm

    async def write(self, ctx: ChapterContext) -> tuple[str, str]:
        """根据章节上下文生成初稿正文。返回 (正文, 实际使用的模型)。"""
        resp = await self._llm.chat(
            self.ROLE,
            build_writer_prompt(ctx),
            system_prompt=WRITER_SYSTEM_PROMPT,
        )
        return resp.content.strip(), resp.model

    async def revise(
        self,
        ctx: ChapterContext,
        previous_content: str,
        issues: list[str],
        iteration: int,
    ) -> tuple[str, str]:
        """根据 Editor 的问题清单修订正文。返回 (正文, 实际使用的模型)。"""
        resp = await self._llm.chat(
            self.ROLE,
            build_revision_prompt(ctx, previous_content, issues, iteration),
            system_prompt=WRITER_SYSTEM_PROMPT,
        )
        return resp.content.strip(), resp.model


class EditorAgent:
    """Editor：评审草稿，产出二值 Verdict（approve / needs_revision）。"""

    ROLE = "editor"

    def __init__(self, llm: ChatClient) -> None:
        self._llm = llm

    async def review(self, ctx: ChapterContext, content: str, iteration: int) -> VerdictPayload:
        """评审章节内容。解析失败时兜底 approve（不阻塞流水线）。"""
        resp = await self._llm.chat(
            self.ROLE,
            build_editor_prompt(ctx, content, iteration),
            system_prompt=EDITOR_SYSTEM_PROMPT,
        )
        return self._parse_verdict(resp.content)

    @staticmethod
    def _parse_verdict(text: str) -> VerdictPayload:
        data = _extract_json_object(text)
        if data and data.get("verdict") in ("approve", "needs_revision"):
            verdict = Verdict(data["verdict"])
            issues = [str(i) for i in data.get("issues", [])]
            if verdict == Verdict.APPROVE:
                issues = []
            return VerdictPayload(verdict=verdict, issues=issues)

        # 宽松兜底：关键词判定，仍无法判定则 approve
        if "needs_revision" in text:
            return VerdictPayload(
                verdict=Verdict.NEEDS_REVISION,
                issues=[text.strip()[:100]],
            )
        return VerdictPayload(verdict=Verdict.APPROVE, issues=[])


class MemoryKeeperAgent:
    """MemoryKeeper：为定稿章节生成摘要与关键事件。"""

    ROLE = "memory-keeper"

    def __init__(self, llm: ChatClient) -> None:
        self._llm = llm

    async def summarize(
        self, chapter_index: int, chapter_title: str, content: str
    ) -> tuple[str, list[str]]:
        """返回 (章节摘要, 关键事件列表)。解析失败时取原文截断为摘要。"""
        resp = await self._llm.chat(
            self.ROLE,
            build_memory_prompt(chapter_index, chapter_title, content),
            system_prompt=MEMORY_KEEPER_SYSTEM_PROMPT,
        )
        data = _extract_json_object(resp.content)
        if data and data.get("summary"):
            events = [str(e) for e in data.get("key_events", [])]
            return str(data["summary"]), events
        return resp.content.strip()[:100], []


# ──────────────────────────────────────────────
#  协作流水线编排
# ──────────────────────────────────────────────


class CollaborationPipeline:
    """1 轮完整 Agent 协作：Planner → Writer → Editor(修订循环) → MemoryKeeper → WindowShift。"""

    #: Planner 每次批量规划的章节数（无大纲时触发）
    PLAN_BATCH = 5
    #: L1 滑窗大小（CONTEXT.md：默认窗口 5 章）
    WINDOW_SIZE = 5

    def __init__(self, llm: ChatClient, max_iterations: int = 3) -> None:
        self.planner = PlannerAgent(llm)
        self.writer = WriterAgent(llm)
        self.editor = EditorAgent(llm)
        self.memory_keeper = MemoryKeeperAgent(llm)
        self._max_iterations = max_iterations

    async def run_one_round(
        self,
        uow: UnitOfWork,
        novel_id: UUID,
        title: str = "",
    ) -> PipelineResult:
        """执行 1 轮协作，产出 1 个真实 LLM 生成的定稿章节。

        Raises:
            ValueError: 小说不存在
            RuntimeError: 所有 LLM 候选模型均失败
        """

        # ── Phase 1: 读取上下文（事务外） ──
        pipeline_state = await uow.pipelines.get_by_novel(novel_id)
        if pipeline_state is None:
            next_index = 1
        else:
            next_index = (pipeline_state.current_chapter_index or 0) + 1

        novel = await uow.novels.get_by_id(novel_id)
        if novel is None:
            raise ValueError(f"小说 {novel_id} 不存在，请先执行 inkmind init")

        existing = await uow.chapters.get_chapters_by_novel(novel_id)
        existing_indices = {c.index for c in existing}
        finalized = [c for c in existing if c.status == ChapterStatus.FINALIZED]
        previous_summaries = [
            f"第{c.index}章「{c.title}」：{c.summary}"
            for c in finalized[-self.WINDOW_SIZE :]
            if c.summary
        ]

        # ── Phase 1.5: Planner 大纲（LLM，事务外；持久化走 T2） ──
        planned_ch = await uow.chapters.get_by_novel_and_index(novel_id, next_index)
        chapter_id: UUID = uuid4()
        outline = ""
        planned_count = 0

        if planned_ch is not None and planned_ch.status == ChapterStatus.PLANNED:
            # 复用已存储的大纲（T2 曾写入的 PLANNED 占位章节）
            chapter_id = planned_ch.id
            outline = planned_ch.summary
            if not title:
                title = planned_ch.title
        else:
            # 无大纲 → Planner 批量规划未来 PLAN_BATCH 章
            planned_list = await self.planner.plan(
                novel.title,
                novel.metadata.description,
                previous_summaries,
                start_index=next_index,
                count=self.PLAN_BATCH,
            )
            new_plans = [p for p in planned_list if p.index not in existing_indices]
            if new_plans:
                if pipeline_state is None:
                    pipeline_state = PipelineState(
                        novel_id=novel_id,
                        total_chapters=0,
                        chapters={},
                        current_chapter_index=None,
                        iteration=0,
                        max_iterations=self._max_iterations,
                    )
                planned_chapters = []
                for p in new_plans:
                    planned_chapters.append(
                        Chapter(
                            id=uuid4(),
                            novel_id=novel_id,
                            index=p.index,
                            title=p.title,
                            content="",
                            status=ChapterStatus.PLANNED,
                            summary=p.outline,
                            source_trace="llm:planner",
                        )
                    )
                    pipeline_state.chapters[p.index] = ChapterStatus.PLANNED
                pipeline_state.total_chapters = max(
                    pipeline_state.total_chapters, new_plans[-1].index
                )
                async with uow.transaction():
                    # T2: Planner 完成规划（批量 PLANNED 占位 + PipelineState）
                    await uow.t2_planner_complete_planning(planned_chapters, pipeline_state)
                    await uow.commit()
                planned_count = len(new_plans)

            current_plan = next((p for p in planned_list if p.index == next_index), None)
            if current_plan is not None:
                outline = current_plan.outline
                if not title:
                    title = current_plan.title
                stored = await uow.chapters.get_by_novel_and_index(novel_id, next_index)
                if stored is not None and stored.status == ChapterStatus.PLANNED:
                    chapter_id = stored.id

        ch_title = title or f"第 {next_index} 章"
        ctx = ChapterContext(
            novel_title=novel.title,
            novel_description=novel.metadata.description,
            chapter_index=next_index,
            chapter_title=ch_title,
            previous_summaries=previous_summaries,
            outline=outline,
        )

        # ── Phase 2: Writer 初稿 + Editor 修订循环（LLM，事务外） ──
        content, writer_model = await self.writer.write(ctx)
        iteration = 0
        max_iterations_hit = False
        while True:
            verdict = await self.editor.review(ctx, content, iteration)
            if verdict.verdict == Verdict.APPROVE:
                break
            if iteration >= self._max_iterations:
                max_iterations_hit = True
                break
            iteration += 1
            content, writer_model = await self.writer.revise(
                ctx, content, verdict.issues, iteration
            )

        # ── Phase 3: MemoryKeeper 摘要（LLM，事务外） ──
        summary, key_events = await self.memory_keeper.summarize(next_index, ch_title, content)

        # ── Phase 4: 事务持久化（T1 → T3 → T4 → T5 → 定稿） ──
        chapter = Chapter(
            id=chapter_id,
            novel_id=novel_id,
            index=next_index,
            title=ch_title,
            content=content,
            status=ChapterStatus.DRAFT_READY,
            summary=summary,
            key_events=key_events,
            source_trace=f"llm:{writer_model}",
            version=1,
            is_baseline=False,
        )

        async with uow.transaction():
            # T1: Writer 完成章节（digest 幂等）
            is_dup, _ = await uow.t1_writer_complete_chapter(chapter)
            if is_dup:
                # 新章节序号但内容与历史完全重复（生成异常，非重试）：
                # 绕过 digest 直接保存，保证流水线状态一致
                await uow.chapters.save(chapter)

            if pipeline_state is None:
                pipeline_state = PipelineState(
                    novel_id=novel_id,
                    total_chapters=0,
                    chapters={},
                    current_chapter_index=None,
                    iteration=0,
                    max_iterations=self._max_iterations,
                )
            pipeline_state.current_chapter_index = next_index
            pipeline_state.total_chapters = max(pipeline_state.total_chapters, next_index)
            pipeline_state.chapters[next_index] = ChapterStatus.DRAFT_READY
            pipeline_state.iteration = iteration
            await uow.pipelines.save(pipeline_state)

            # T3: Editor 完成评审（循环结束时 verdict 必为 approve 或兜底放行）
            await uow.t3_editor_complete_review(
                novel_id, next_index, is_approved=True, is_baseline=True
            )
            pipeline_state.chapters[next_index] = ChapterStatus.APPROVED
            await uow.pipelines.save(pipeline_state)

            # T4: MemoryKeeper 完成压缩（真实摘要 + 关键事件）
            task_id = uuid4()
            await uow.create_compression_task(
                task_id, novel_id, range_start=next_index, range_end=next_index
            )
            compressed_data = {
                "summaries": [{"chapter": next_index, "summary": summary}],
                "events": [{"chapter": next_index, "key_events": key_events}],
            }
            await uow.t4_memory_keeper_complete_compression(
                novel_id,
                compressed_data,
                task_id,
                {"status": "completed", "completed_at": datetime.now(timezone.utc)},
            )

            # T5: 滑窗更新（最近 WINDOW_SIZE 章真实摘要）
            all_chapters = await uow.chapters.get_chapters_by_novel(novel_id)
            written = [c for c in all_chapters if c.content]
            window_start = max(1, next_index - self.WINDOW_SIZE + 1)
            sliding_window_state = {
                "current_start": window_start,
                "current_end": next_index,
                "window_size": self.WINDOW_SIZE,
                "active_chapters": [c.index for c in written if c.index >= window_start],
            }
            l1_snapshot = {
                "last_n_chapters": [
                    {"index": c.index, "summary": c.summary or c.title}
                    for c in written
                    if c.index >= window_start
                ],
            }
            await uow.t5_window_shift(novel_id, sliding_window_state, l1_snapshot)

            # 定稿
            await uow.chapters.update_status(novel_id, next_index, ChapterStatus.FINALIZED.value)
            pipeline_state.chapters[next_index] = ChapterStatus.FINALIZED
            await uow.pipelines.save(pipeline_state)

            # 小说元数据
            novel.metadata.chapter_count = next_index
            novel.metadata.status = "writing"
            novel.metadata.word_count = (novel.metadata.word_count or 0) + len(content)
            await uow.novels.save(novel)

            await uow.commit()

        return PipelineResult(
            chapter_index=next_index,
            chapter_title=ch_title,
            chapter_id=chapter.id,
            content=content,
            content_length=len(content),
            iterations=iteration,
            verdict=verdict.verdict,
            issues=verdict.issues,
            summary=summary,
            key_events=key_events,
            max_iterations_hit=max_iterations_hit,
            planned_count=planned_count,
        )
