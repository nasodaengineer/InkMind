"""RunLoop — Run 生命周期执行驱动机。

编排 Writer/Editor 流水线步骤，管理流式 checkpoint、取消信号、终态持久化。
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Callable, Optional
from uuid import UUID

from inkmind.llm.client import LLMClient, build_llm_client
from inkmind.models.agent import ChapterStatus, ChapterOutline, Verdict
from inkmind.models.chapter import Chapter
from inkmind.models.llm import LLMConfig
from inkmind.models.run import RunKind, RunStatus
from inkmind.storage.unit_of_work import UnitOfWork


class EventEmitter:
    """事件发射器 — 向 SSE 等下游通知 phase/token/verdict/done/error。"""

    def __init__(self) -> None:
        self._listeners: list[Callable[[str, Any], None]] = []

    def subscribe(self, listener: Callable[[str, Any], None]) -> None:
        self._listeners.append(listener)

    def emit(self, event: str, data: Any) -> None:
        for listener in self._listeners:
            listener(event, data)


class RunLoop:
    """Run 生命周期执行驱动机。

    用法:
        loop = RunLoop(uow, llm_client, run_id)
        await loop.start_generate()
    """

    def __init__(
        self,
        uow: UnitOfWork,
        llm_client: LLMClient,
        run_id: UUID,
        chapter: Chapter | None = None,
        outline: ChapterOutline | None = None,
    ):
        self._uow = uow
        self._llm = llm_client
        self._run_id = run_id
        self._chapter = chapter
        self._outline = outline

        # 内部状态
        self._cancelled = False
        self._phase = ""
        self._partial_content = ""
        self._last_checkpoint = time.monotonic()
        self._content_since_checkpoint = 0

        # 事件发射
        self.events = EventEmitter()

        # Checkpoint 配置
        self._checkpoint_interval_s = 2.0
        self._checkpoint_interval_bytes = 500

    # ── 主入口 ──────────────────────────────────────────

    async def start_run(
        self,
        kind: RunKind,
        novel_id: UUID,
        chapter_id: UUID | None = None,
    ) -> None:
        """按 kind 路由到对应的执行流程。"""
        if self._check_cancelled():
            return

        # 获取 Run 记录
        run = await self._uow.runs.get_by_id(self._run_id)
        if run is None:
            raise ValueError(f"Run {self._run_id} 不存在")

        run.started_at = datetime.now(timezone.utc)
        await self._uow.runs.save(run)

        try:
            if kind == RunKind.GENERATE:
                await self._run_generate(novel_id, chapter_id)
            elif kind == RunKind.REVISE:
                await self._run_revise(novel_id, chapter_id)
            elif kind == RunKind.FINALIZE:
                await self._run_finalize(novel_id, chapter_id)
            elif kind == RunKind.PLAN:
                await self._run_plan(novel_id)
            else:
                raise ValueError(f"未知 RunKind: {kind}")
        except Exception as e:
            if not self._cancelled:
                await self._fail(str(e))

    # ── Generate ────────────────────────────────────────

    async def _run_generate(
        self, novel_id: UUID, chapter_id: UUID | None
    ) -> None:
        """Writer → Editor(≤3次修订) → awaiting_human"""
        self._emit_phase("writing")

        # 1. Writer 生成初稿
        draft_content = await self._stream_write(novel_id)
        if self._cancelled:
            return

        # 2. 评审循环
        iteration = 0
        max_iterations = 3
        while iteration < max_iterations:
            if self._check_cancelled():
                return

            self._emit_phase("reviewing")

            # Editor 评审
            verdict = await self._call_editor(novel_id, draft_content)
            if self._cancelled:
                return

            if verdict == Verdict.APPROVE:
                self._emit_phase("complete")
                self._partial_content = draft_content
                await self._do_checkpoint()

                # T9 + T10: 落稿 & 等待人工确认
                await self._finalize_draft(novel_id, draft_content)
                # 等待人工确认 — 终态交给人工处理
                self._emit_phase("awaiting_human")
                return

            # needs_revision
            iteration += 1
            if iteration >= max_iterations:
                # 超限自动降级
                self._emit_phase("complete")
                self._partial_content = draft_content
                await self._do_checkpoint()
                await self._finalize_draft(novel_id, draft_content)
                self._emit_phase("awaiting_human")
                return

            self._emit_phase("revising")
            draft_content = await self._stream_revise(
                novel_id, draft_content
            )
            if self._cancelled:
                return

    # ── Revise ──────────────────────────────────────────

    async def _run_revise(
        self, novel_id: UUID, chapter_id: UUID | None
    ) -> None:
        """Writer 修订 → Editor → awaiting_human"""
        if self._chapter is None:
            raise ValueError("revise 需要传入 chapter")

        self._emit_phase("revising")

        # 用现有内容作为基线，让 Writer 重写
        revised = await self._stream_revise(
            novel_id, self._chapter.content
        )
        if self._cancelled:
            return

        self._emit_phase("reviewing")
        verdict = await self._call_editor(novel_id, revised)
        if self._cancelled:
            return

        if verdict == Verdict.APPROVE:
            self._emit_phase("complete")
            self._partial_content = revised
            await self._do_checkpoint()
            await self._finalize_draft(novel_id, revised)
            self._emit_phase("awaiting_human")
        else:
            # 即使未通过也让人工介入
            self._emit_phase("awaiting_human")

    # ── Finalize ────────────────────────────────────────

    async def _run_finalize(
        self, novel_id: UUID, chapter_id: UUID | None
    ) -> None:
        """直接落稿（无 AI 生成）。"""
        if self._chapter is None:
            raise ValueError("finalize 需要传入 chapter")

        self._emit_phase("complete")
        self._partial_content = self._chapter.content
        await self._do_checkpoint()
        await self._finalize_draft(novel_id, self._chapter.content)
        self._emit_phase("awaiting_human")

    # ── Plan ────────────────────────────────────────────

    async def _run_plan(self, novel_id: UUID) -> None:
        """Planner 生成批量大纲。"""
        self._emit_phase("planning")

        # Planner 调用
        plan_prompt = (
            f"请为小说 {novel_id} 规划接下来的章节。"
            f"请以 JSON 格式返回章节大纲列表。"
        )
        response = await self._llm.chat("planner", plan_prompt)
        if self._cancelled:
            return

        self._emit_phase("complete")
        await self._complete(response.content)

    # ── Writer 流式写作 ─────────────────────────────────

    async def _stream_write(self, novel_id: UUID) -> str:
        """流式调用 Writer，积累完整内容后返回。"""
        prompt = self._build_write_prompt(novel_id)
        return await self._accumulate_stream(
            "writer", prompt, "writing"
        )

    async def _stream_revise(
        self, novel_id: UUID, existing_content: str
    ) -> str:
        """流式调用 Writer 修订。"""
        prompt = self._build_revise_prompt(novel_id, existing_content)
        return await self._accumulate_stream(
            "writer", prompt, "revising"
        )

    async def _accumulate_stream(
        self, agent_role: str, prompt: str, phase: str
    ) -> str:
        """从 chat_stream 累积内容，同时做 checkpoint。"""
        chunks: list[str] = []
        async for chunk in self._llm.chat_stream(agent_role, prompt):
            if self._cancelled:
                return "".join(chunks)

            chunks.append(chunk)
            self._partial_content += chunk
            self._content_since_checkpoint += len(chunk)

            # 发射 token 事件
            self.events.emit("token", chunk)

            # Checkpoint: 每 2s 或 500 字节
            now = time.monotonic()
            if (
                now - self._last_checkpoint >= self._checkpoint_interval_s
                or self._content_since_checkpoint
                >= self._checkpoint_interval_bytes
            ):
                await self._do_checkpoint()
                self._last_checkpoint = now
                self._content_since_checkpoint = 0

        # 最终 checkpoint
        await self._do_checkpoint()
        return "".join(chunks)

    # ── Editor 评审 ─────────────────────────────────────

    async def _call_editor(
        self, novel_id: UUID, content: str
    ) -> Verdict:
        """调用 Editor 评审，返回结论。"""
        review_prompt = (
            f"请评审以下章节内容。"
            f"以 JSON 格式返回 {{\"verdict\": \"approve\" 或 \"needs_revision\"}}。\n\n"
            f"{content[:3000]}"
        )
        response = await self._llm.chat("editor", review_prompt)

        try:
            result = json.loads(response.content)
            verdict_str = result.get("verdict", "approve")
            verdict = Verdict.APPROVE if verdict_str == "approve" else Verdict.NEEDS_REVISION
            self.events.emit("verdict", {"verdict": verdict.value})
            return verdict
        except (json.JSONDecodeError, KeyError):
            # 解析失败默认为 approve
            self.events.emit("verdict", {"verdict": "approve"})
            return Verdict.APPROVE

    # ── Checkpoint ──────────────────────────────────────

    async def _do_checkpoint(self) -> None:
        """将 partial_content 写入数据库。"""
        run = await self._uow.runs.get_by_id(self._run_id)
        if run is None:
            return
        run.partial_content = self._partial_content
        await self._uow.runs.save(run)

    # ── 终态处理 ────────────────────────────────────────

    async def _finalize_draft(
        self, novel_id: UUID, content: str
    ) -> None:
        """T9 + T10: 落稿 + 等待人工确认。"""
        chapter_title = (
            self._chapter.title if self._chapter else "未命名章节"
        )
        await self._uow.t9_finalize_draft(
            run_id=self._run_id,
            chapter_content=content,
            chapter_title=chapter_title,
        )

        # 聚合 stats
        stats = self._llm.aggregate_stats()

        # 持久化原始 ProviderStats 快照
        raw_stats = self._llm.get_raw_stats()
        await self._uow.t12_persist_stats(raw_stats)

        await self._uow.t10_run_finalize(
            run_id=self._run_id,
            new_status=RunStatus.AWAITING_HUMAN,
            llm_stats=stats,
        )

    async def _complete(self, content: str) -> None:
        """T10: 正常完成。"""
        stats = self._llm.aggregate_stats()

        # 持久化原始 ProviderStats 快照
        raw_stats = self._llm.get_raw_stats()
        await self._uow.t12_persist_stats(raw_stats)

        await self._uow.t10_run_finalize(
            run_id=self._run_id,
            new_status=RunStatus.COMPLETED,
            llm_stats=stats,
        )

    async def _fail(self, error_msg: str) -> None:
        """T10: 标记失败。"""
        stats = self._llm.aggregate_stats()

        # 持久化原始 ProviderStats 快照
        raw_stats = self._llm.get_raw_stats()
        await self._uow.t12_persist_stats(raw_stats)

        await self._uow.t10_run_finalize(
            run_id=self._run_id,
            new_status=RunStatus.FAILED,
            llm_stats=stats,
        )
        self.events.emit("error", {"message": error_msg})

    # ── 取消 ──────────────────────────────────────────

    def cancel(self) -> None:
        """设置取消标记，RunLoop 协程检查后退出。"""
        self._cancelled = True
        self._llm.cancel_all()

    def _check_cancelled(self) -> bool:
        if self._cancelled:
            self.events.emit("done", {"status": "cancelled"})
            return True
        return False

    # ── 工具方法 ──────────────────────────────────────

    def _emit_phase(self, phase: str) -> None:
        """发射 phase 事件并更新 uow 中的 phase。"""
        self._phase = phase
        self.events.emit("phase", {"phase": phase})

    def _build_write_prompt(self, novel_id: UUID) -> str:
        title = self._chapter.title if self._chapter else "新章节"
        outline_text = ""
        if self._outline:
            outline_text = (
                f"本章大纲: {self._outline.summary}\n"
                f"关键事件: {', '.join(self._outline.key_events)}"
            )
        return (
            f"请写作小说章节「{title}」。\n"
            f"{outline_text}\n\n"
            f"请开始写作正文。"
        )

    def _build_revise_prompt(
        self, novel_id: UUID, existing_content: str
    ) -> str:
        return (
            f"请修订以下章节内容。\n"
            f"现有内容:\n{existing_content[:3000]}\n\n"
            f"请输出修订后的完整版本。"
        )

    # ── 工厂 ──────────────────────────────────────────

    @classmethod
    def create(
        cls,
        uow: UnitOfWork,
        llm_client: LLMClient,
        run_id: UUID,
        chapter: Chapter | None = None,
        outline: ChapterOutline | None = None,
    ) -> RunLoop:
        """创建 RunLoop 实例。"""
        return cls(uow, llm_client, run_id, chapter, outline)
