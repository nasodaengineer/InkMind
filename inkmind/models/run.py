"""Run 执行模型。

Run 是一轮 AI 执行的生命周期单元：启动 → 流式生成 → 评审 → 终态。
每个 Run 对应一次用户发起的操作（生成、修订、完稿、规划）。
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class RunKind(str, Enum):
    """Run 的类型。"""

    GENERATE = "generate"
    """Writer 生成新章节。"""
    REVISE = "revise"
    """Writer 修订已有章节。"""
    FINALIZE = "finalize"
    """终稿确认。"""
    PLAN = "plan"
    """Planner 批量规划大纲。"""


class RunStatus(str, Enum):
    """Run 的生命周期状态。"""

    RUNNING = "running"
    """执行中（含流式生成）。"""
    AWAITING_HUMAN = "awaiting_human"
    """等待人工确认/干预。"""
    COMPLETED = "completed"
    """正常完成。"""
    FAILED = "failed"
    """执行失败。"""
    CANCELLED = "cancelled"
    """用户取消。"""
    INTERRUPTED = "interrupted"
    """系统崩溃/重启导致中断。"""


class Run(BaseModel):
    """Run 领域模型。

    一次 AI 执行的生命周期。
    kind=plan 时 chapter_id 为 None。
    """

    id: UUID = Field(default_factory=uuid4)
    novel_id: UUID
    chapter_id: UUID | None = Field(
        default=None, description="kind=plan 时 None"
    )
    kind: RunKind
    status: RunStatus = RunStatus.RUNNING

    # ── 遥测 ──
    phase: str = Field(
        default="",
        description="当前阶段描述: writing / reviewing / revising / complete",
    )

    # ── 流式中间稿 ──
    partial_content: str = Field(
        default="", description="流式中间稿，checkpoint 时写入"
    )

    # ── 统计与覆盖 ──
    llm_stats: dict[str, Any] = Field(
        default_factory=dict, description="聚合 LLM 调用统计快照"
    )
    overwritten_values: dict[str, Any] | None = Field(
        default=None, description="AI 起草被覆盖的旧值（人工编辑时）"
    )

    # ── 时间戳 ──
    started_at: datetime | None = Field(
        default=None, description="实际开始执行时间"
    )
    completed_at: datetime | None = Field(
        default=None, description="实际完成/终止时间"
    )
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def set_phase(self, phase: str) -> None:
        """更新阶段并刷新 updated_at。"""
        self.phase = phase
        self.updated_at = datetime.now(timezone.utc)

    def complete(self, stats: dict[str, Any] | None = None) -> None:
        """标记为完成。"""
        self.status = RunStatus.COMPLETED
        self.completed_at = datetime.now(timezone.utc)
        self.updated_at = self.completed_at
        if stats:
            self.llm_stats = stats
        self.partial_content = ""

    def fail(self, stats: dict[str, Any] | None = None) -> None:
        """标记为失败。"""
        self.status = RunStatus.FAILED
        self.completed_at = datetime.now(timezone.utc)
        self.updated_at = self.completed_at
        if stats:
            self.llm_stats = stats

    def cancel(self) -> None:
        """标记为取消。"""
        self.status = RunStatus.CANCELLED
        self.completed_at = datetime.now(timezone.utc)
        self.updated_at = self.completed_at

    def interrupt(self) -> None:
        """标记为系统中断。"""
        self.status = RunStatus.INTERRUPTED
        self.completed_at = datetime.now(timezone.utc)
        self.updated_at = self.completed_at

    def mark_awaiting_human(self) -> None:
        """标记为等待人工。"""
        self.status = RunStatus.AWAITING_HUMAN
        self.updated_at = datetime.now(timezone.utc)
