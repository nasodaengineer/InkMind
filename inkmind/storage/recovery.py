"""故障恢复入口。

InkMind 启动时调用 RecoveryManager.recover() 从数据库重建
MemoryKeeperCore 的运行时状态。

恢复步骤（ADR-0005 ⑤-F）:
  1. 连接存储 → 按 novel_id 加载
  2. 加载 L0Index（若有）
  3. 加载 L2Archive（所有 CompressedMemory）
  4. 加载 L3Archive（所有 LongTermEntry）
  5. 加载 SlidingWindowState（L1）
  6. 加载所有 PENDING/RUNNING 的 CompressionTask → 重新推入异步队列
  7. 加载 PipelineState（所有章节状态映射）
  8. 恢复完成
  9. 检测所有 status=running 的 Run → 标记 interrupted
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select, update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from inkmind.models.agent import PipelineState
from inkmind.models.memory import (
    CompressionTask,
    CompressionTaskStatus,
    L0Index,
    L2Archive,
    L3Archive,
    SlidingWindowState,
    TimeRange,
)
from inkmind.storage.models import (
    CompressionTaskModel,
    MemoryArchiveModel,
    PipelineStateModel,
)
from inkmind.storage.serializers import (
    dict_to_pipeline_state,
    pipeline_state_to_dict,
)


class RecoveredMemoryState:
    """故障恢复后的 MemoryKeeper 运行时状态。"""

    def __init__(self):
        self.novel_id: UUID | None = None
        self.l0_index: L0Index | None = None
        self.l2_archive: L2Archive | None = None
        self.l3_archive: L3Archive | None = None
        self.sliding_window: SlidingWindowState | None = None
        self.pending_tasks: list[CompressionTask] = []
        self.pipeline_state: PipelineState | None = None

    @property
    def has_pending_work(self) -> bool:
        """是否有待恢复的异步任务。"""
        return len(self.pending_tasks) > 0


class RecoveryManager:
    """故障恢复管理器。"""

    def __init__(self, session: AsyncSession):
        self._session = session

    async def recover(self, novel_id: UUID) -> RecoveredMemoryState:
        """从存储重建 MemoryKeeper 运行时状态。

        Args:
            novel_id: 要恢复的小说 ID

        Returns:
            恢复后的运行时状态
        """
        state = RecoveredMemoryState()
        state.novel_id = novel_id
        novel_id_str = str(novel_id)

        # Step 1-4: 加载记忆归档
        archives = await self._load_archives(novel_id_str)
        for archive in archives:
            if archive.tier == "l0_index":
                state.l0_index = L0Index(novel_id=UUID(archive.novel_id), **archive.data)
            elif archive.tier == "l2_compressed":
                state.l2_archive = L2Archive(novel_id=UUID(archive.novel_id), **archive.data)
            elif archive.tier == "l3_permanent":
                state.l3_archive = L3Archive(novel_id=UUID(archive.novel_id), **archive.data)

        # Step 5: 加载 L1 滑窗状态
        for archive in archives:
            if archive.tier == "l1_active":
                win_data = archive.data.get("sliding_window")
                if win_data:
                    try:
                        state.sliding_window = SlidingWindowState(**win_data)
                    except Exception:
                        pass

        # Step 6: 加载待处理压缩任务
        state.pending_tasks = await self._load_pending_tasks(novel_id_str)

        # Step 7: 加载 PipelineState
        state.pipeline_state = await self._load_pipeline(novel_id_str)

        # Step 6.5: 重置 PENDING/RUNNING 任务为 PENDING（重启后重新调度）
        await self._reset_pending_tasks(novel_id_str)

        # Step 9: 中断所有 running 的 Run
        interrupted_count = await self.step9_interrupt_running_runs()
        if interrupted_count > 0:
            print(f"[Recovery] Step 9: {interrupted_count} 个 Run 已标记为 interrupted")

        return state

    async def _load_archives(self, novel_id: str) -> list[MemoryArchiveModel]:
        result = await self._session.execute(
            select(MemoryArchiveModel).where(MemoryArchiveModel.novel_id == novel_id)
        )
        return list(result.scalars().all())

    async def _load_pending_tasks(self, novel_id: str) -> list[CompressionTask]:
        result = await self._session.execute(
            select(CompressionTaskModel).where(
                CompressionTaskModel.novel_id == novel_id,
                CompressionTaskModel.status.in_(["pending", "running"]),
            )
        )
        models = result.scalars().all()
        tasks = []
        for m in models:
            tasks.append(
                CompressionTask(
                    task_id=UUID(m.task_id),
                    novel_id=UUID(m.novel_id),
                    range=TimeRange(
                        start_chapter=m.range_start,
                        end_chapter=m.range_end,
                    ),
                    status=CompressionTaskStatus.PENDING,
                    started_at=m.started_at,
                    completed_at=m.completed_at,
                    error_message=m.error_message,
                )
            )
        return tasks

    async def _reset_pending_tasks(self, novel_id: str) -> None:
        """重启后，将 PENDING/RUNNING 任务重置为 PENDING。"""
        await self._session.execute(
            sa_update(CompressionTaskModel)
            .where(
                CompressionTaskModel.novel_id == novel_id,
                CompressionTaskModel.status.in_(["pending", "running"]),
            )
            .values(status="pending", started_at=None)
        )

    async def _load_pipeline(self, novel_id: str) -> PipelineState | None:
        result = await self._session.execute(
            select(PipelineStateModel).where(PipelineStateModel.novel_id == novel_id)
        )
        model = result.scalar_one_or_none()
        if model is None:
            return None
        return dict_to_pipeline_state(pipeline_state_to_dict(model))

    # ═══════════════════════════════════════════════════════
    #  Step 9: 中断所有 running 的 Run
    # ═══════════════════════════════════════════════════════

    async def step9_interrupt_running_runs(self) -> int:
        """检测所有 status=running 的 Run，标记为 interrupted。

        Returns:
            被中断的 Run 数量
        """
        from datetime import datetime, timezone

        from inkmind.storage.models import RunsModel

        result = await self._session.execute(select(RunsModel).where(RunsModel.status == "running"))
        running_runs = list(result.scalars().all())

        now = datetime.now(timezone.utc)
        for run in running_runs:
            run.status = "interrupted"
            run.completed_at = now

        return len(running_runs)
