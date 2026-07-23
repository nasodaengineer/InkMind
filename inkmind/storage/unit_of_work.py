"""事务边界管理（Unit of Work）。

定义 InkMind 的 5 个关键事务边界（T1-T5）。确保每个跨实体的
写操作都是「全有或全无」的。

事务边界（摘自 ADR-0005）:
  T1: Writer 完成章节  — 写入 Draft content + 更新 ChapterStatus
  T2: Planner 完成规划 — 批量插入 ChapterOutline + 更新 PipelineState
  T3: Editor 完成评审  — 写入 VerdictPayload + 更新 ChapterStatus
  T4: MemoryKeeper 完成压缩 — 写入 CompressedMemory + 更新 L2Archive + 标记任务 COMPLETED
  T5: 滑窗更新          — 更新 SlidingWindowState + 更新 L1 Snapshot
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from inkmind.storage.concurrency import FileLock

from inkmind.models.agent import ChapterStatus, PipelineState
from inkmind.models.chapter import Chapter, ChapterVersion
from inkmind.storage.idempotency import (
    IdempotencyGuard,
)
from inkmind.storage.repositories import (
    ChapterRepository,
    CharacterRepository,
    NovelRepository,
    PipelineStateRepository,
    WorldRepository,
)
from inkmind.storage.digest import compute_content_digest
from inkmind.errors import StaleVersionError


class UnitOfWork:
    """事务工作单元。

    每个方法对应一个事务边界。所有操作在同一个 AsyncSession 上执行，
    由调用方负责 commit / rollback。

    并发安全：session 模式传入 db_path 或以 db_path 字符串构造时，
    均启用文件级写锁（ADR-0011 §11-C，commit 在锁保护下序列化）；
    两种构造的锁文件统一为 {db_path}.lock（§11-B），互斥才成立。
    """

    def __init__(
        self,
        session_or_db_path: AsyncSession | str | None = None,
        timeout: float = 5.0,
        db_path: str | None = None,
    ):
        if isinstance(session_or_db_path, str):
            # 字符串路径 → 文件锁模式（用于测试或 CLI 简易调用，无 session/repos）
            db_path = session_or_db_path
            session_or_db_path = None

        self._session = session_or_db_path
        self._lock = (
            FileLock.from_path(db_path, timeout=timeout) if db_path else None
        )
        self.novels = NovelRepository(self._session) if self._session else None
        self.chapters = ChapterRepository(self._session) if self._session else None
        self.characters = CharacterRepository(self._session) if self._session else None
        self.worlds = WorldRepository(self._session) if self._session else None
        self.pipelines = PipelineStateRepository(self._session) if self._session else None
        self.idempotency = IdempotencyGuard(self._session) if self._session else None
        self._lock_held = False

    @property
    def session(self) -> AsyncSession:
        """只读访问内部 session（ADR-0009：外部不得持有私有 ``_session``）。

        仅供只读查询与仓库未覆盖的旧接口（如 JSONSnapshot）使用；
        写操作一律经 T1-T5 事务边界方法 + commit()。
        """
        if self._session is None:
            raise RuntimeError("UnitOfWork 未绑定 session（字符串锁模式不支持）")
        return self._session

    def __enter__(self) -> UnitOfWork:
        """获取文件级写锁（同步上下文管理器入口）。"""
        if self._lock:
            if not self._lock.acquire():
                raise RuntimeError("数据库写锁超时，请稍后重试")
            self._lock_held = True
        return self

    def __exit__(self, *args) -> None:
        """释放文件级写锁。"""
        if self._lock and self._lock_held:
            self._lock.release()
            self._lock_held = False

    # ═══════════════════════════════════════════════════
    #  T1: Writer 完成章节
    # ═══════════════════════════════════════════════════

    async def t1_writer_complete_chapter(
        self,
        chapter: Chapter,
        previous_version: ChapterVersion | None = None,
    ) -> tuple[bool, str]:
        """T1: Writer 完成章节。

        Args:
            chapter: 最新版本的 Chapter
            previous_version: 上一版本的 ChapterVersion（有则存为历史）

        Returns:
            (是重复, digest)
        """
        # 1. 幂等检查（基于 content digest）
        digest = compute_content_digest(chapter.content)
        if await self.idempotency.is_duplicate(digest):
            return True, digest

        # 2. 保存历史版本
        if previous_version is not None:
            await self.chapters.save_version(previous_version)

        # 3. 写入新章节内容并更新状态
        chapter.status = ChapterStatus.DRAFT_READY
        await self.chapters.save(chapter)

        # 4. 标记幂等
        await self.idempotency.mark_processed(digest, chapter.id)

        return False, digest

    # ═══════════════════════════════════════════════════
    #  T2: Planner 完成规划
    # ═══════════════════════════════════════════════════

    async def t2_planner_complete_planning(
        self,
        chapters: list[Chapter],
        pipeline_state: PipelineState,
    ) -> None:
        """T2: Planner 完成批量规划。

        Args:
            chapters: 新规划的章节列表
            pipeline_state: 更新后的流水线状态

        Raises:
            ValueError: 如果章节和流水线的 novel_id 不一致
        """
        # 验证 novel_id 一致性
        for ch in chapters:
            if ch.novel_id != pipeline_state.novel_id:
                raise ValueError(
                    f"Chapter novel_id {ch.novel_id} != PipelineState novel_id {pipeline_state.novel_id}"
                )

        # 1. 批量插入 Chapter
        for ch in chapters:
            ch.status = ChapterStatus.PLANNED
            await self.chapters.save(ch)

        # 2. 更新 PipelineState
        await self.pipelines.save(pipeline_state)

    # ═══════════════════════════════════════════════════
    #  T3: Editor 完成评审
    # ═══════════════════════════════════════════════════

    async def t3_editor_complete_review(
        self,
        novel_id: UUID,
        chapter_index: int,
        is_approved: bool,
        is_baseline: bool = False,
    ) -> None:
        """T3: Editor 完成评审。

        Args:
            novel_id: 小说 ID
            chapter_index: 章节序号
            is_approved: 是否批准
            is_baseline: 是否标记为基线版本
        """
        new_status = (
            ChapterStatus.APPROVED
            if is_approved
            else ChapterStatus.REVISING
        )

        # 更新章节状态
        await self.chapters.update_status(
            novel_id, chapter_index, new_status.value
        )

        # 如果批准且标记基线
        if is_approved and is_baseline:
            chapter = await self.chapters.get_by_novel_and_index(
                novel_id, chapter_index
            )
            if chapter is not None:
                chapter.is_baseline = True
                await self.chapters.save(chapter)

    # ═══════════════════════════════════════════════════
    #  T4: MemoryKeeper 完成压缩
    # ═══════════════════════════════════════════════════

    async def t4_memory_keeper_complete_compression(
        self,
        novel_id: UUID,
        compressed_data: dict,
        task_id: UUID,
        task_update: dict,
    ) -> None:
        """T4: MemoryKeeper 完成压缩。

        Args:
            novel_id: 小说 ID
            compressed_data: L2 archive 更新数据（MongoDB-style json）
            task_id: 压缩任务 ID
            task_update: 任务状态更新（{status, completed_at, ...}）
        """
        from sqlalchemy import update as sa_update

        from inkmind.storage.models import (
            CompressionTaskModel,
            MemoryArchiveModel,
        )

        # 1. 更新/写入 L2Archive
        existing = await self._session.execute(
            sa_update(MemoryArchiveModel)
            .where(
                MemoryArchiveModel.novel_id == str(novel_id),
                MemoryArchiveModel.tier == "l2_compressed",
            )
            .values(data=compressed_data)
        )
        if existing.rowcount == 0:
            self._session.add(
                MemoryArchiveModel(
                    novel_id=str(novel_id),
                    tier="l2_compressed",
                    data=compressed_data,
                )
            )

        # 2. 标记 CompressionTask 为 COMPLETED
        await self._session.execute(
            sa_update(CompressionTaskModel)
            .where(CompressionTaskModel.task_id == str(task_id))
            .values(**task_update)
        )

    # ═══════════════════════════════════════════════════
    #  T5: 滑窗更新
    # ═══════════════════════════════════════════════════

    async def t5_window_shift(
        self,
        novel_id: UUID,
        sliding_window_state: dict,
        l1_snapshot: dict,
    ) -> None:
        """T5: 滑窗更新。

        更新 L1 SlidingWindowState 和 L1 ActiveContext 快照。

        Args:
            novel_id: 小说 ID
            sliding_window_state: 新的滑窗状态 dict
            l1_snapshot: L1 ActiveContext 快照 dict
        """
        from sqlalchemy import update as sa_update

        from inkmind.storage.models import MemoryArchiveModel

        # 1. 更新 L1 滑窗状态
        existing = await self._session.execute(
            sa_update(MemoryArchiveModel)
            .where(
                MemoryArchiveModel.novel_id == str(novel_id),
                MemoryArchiveModel.tier == "l1_active",
            )
            .values(
                data={
                    "sliding_window": sliding_window_state,
                    "snapshot": l1_snapshot,
                }
            )
        )
        if existing.rowcount == 0:
            self._session.add(
                MemoryArchiveModel(
                    novel_id=str(novel_id),
                    tier="l1_active",
                    data={
                        "sliding_window": sliding_window_state,
                        "snapshot": l1_snapshot,
                    },
                )
            )

    # ═══════════════════════════════════════════════════
    #  T12: 手动编辑落稿（人工门）
    # ═══════════════════════════════════════════════════

    async def t12_manual_edit(
        self,
        chapter_id: UUID,
        new_content: str,
        base_digest: str,
        source_trace: str = "manual",
    ) -> Chapter:
        """T12: 手动编辑落稿。

        三方原子：归档旧版 + 写章 + fingerprint_updates。
        base_digest 校验在事务内，冲突即抛 StaleVersionError（409）。
        不走 T1 content-digest 全局去重——改回旧文不吞。

        Args:
            chapter_id: 章节 UUID
            new_content: 新正文
            base_digest: 客户端编辑前拿到的 content_digest
            source_trace: 来源标记，默认 "manual"

        Returns:
            更新后的 Chapter

        Raises:
            StaleVersionError: base_digest 与服务端不一致
            ValueError: 章节不存在
        """
        chapter = await self.chapters.get_by_id(chapter_id)
        if chapter is None:
            raise ValueError(f"章节不存在: {chapter_id}")

        # 1. base_digest 乐观锁校验（事务内）
        current_digest = compute_content_digest(chapter.content)
        if base_digest != current_digest:
            raise StaleVersionError(expected=base_digest, actual=current_digest)

        # 2. 归档旧版
        old_version = ChapterVersion(
            chapter_id=chapter.id,
            novel_id=chapter.novel_id,
            version=chapter.version,
            index=chapter.index,
            title=chapter.title,
            content=chapter.content,
            summary=chapter.summary,
            key_events=chapter.key_events,
            source_trace=chapter.source_trace,
            is_baseline=chapter.is_baseline,
            content_digest=current_digest,
        )
        await self.chapters.save_version(old_version)

        # 3. 写入新内容 + fingerprint_updates
        new_digest = compute_content_digest(new_content)
        chapter.content = new_content
        chapter.version += 1
        chapter.source_trace = source_trace
        chapter.content_digest = new_digest
        await self.chapters.save(chapter)

        return chapter

    async def patch_chapter(
        self,
        chapter_id: UUID,
        *,
        content: str | None = None,
        base_digest: str | None = None,
        title: str | None = None,
        summary: str | None = None,
        key_events: list[str] | None = None,
    ) -> Chapter:
        """PATCH 一端两用：含 content → T12；否则大纲字段单行写。

        Args:
            chapter_id: 章节 UUID
            content: 新正文（有则走 T12）
            base_digest: 乐观锁摘要（content 有值时必传）
            title: 新标题（可选）
            summary: 新摘要（可选）
            key_events: 新关键事件（可选）

        Returns:
            更新后的 Chapter
        """
        if content is not None:
            if base_digest is None:
                raise ValueError("手动编辑必须提供 base_digest")
            return await self.t12_manual_edit(
                chapter_id, content, base_digest
            )

        chapter = await self.chapters.get_by_id(chapter_id)
        if chapter is None:
            raise ValueError(f"章节不存在: {chapter_id}")

        if title is not None:
            chapter.title = title
        if summary is not None:
            chapter.summary = summary
        if key_events is not None:
            chapter.key_events = key_events
        await self.chapters.save(chapter)
        return chapter

    # ═══════════════════════════════════════════════════
    #  通用
    # ═══════════════════════════════════════════════════

    async def create_compression_task(
        self,
        task_id: UUID,
        novel_id: UUID,
        range_start: int,
        range_end: int,
    ) -> None:
        """创建压缩任务（T4 前置），初始状态 running。

        ADR-0009：外部不得直接操作 ORM Session，任务创建收拢到 UoW。
        """
        from inkmind.storage.models import CompressionTaskModel

        self._session.add(
            CompressionTaskModel(
                task_id=str(task_id),
                novel_id=str(novel_id),
                range_start=range_start,
                range_end=range_end,
                status="running",
            )
        )
        await self._session.flush()

    async def commit(self) -> None:
        """提交当前事务（ADR-0009：外部不直接触碰 session）。

        ADR-0011 §11-C：持有文件锁配置时，提交在锁保护下序列化；
        锁已被 ``with uow`` 持有（_lock_held）时不重复获取，避免自锁死。
        """
        if self._lock is None or self._lock_held:
            await self._session.commit()
            return
        acquired = await self._lock.aacquire()
        if not acquired:
            raise RuntimeError("数据库写锁超时，请稍后重试")
        try:
            await self._session.commit()
        finally:
            self._lock.release()

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        """启动一个事务。

        如果事务内抛出异常，自动回滚；否则由外部提交。
        """
        try:
            yield
        except Exception:
            await self._session.rollback()
            raise
