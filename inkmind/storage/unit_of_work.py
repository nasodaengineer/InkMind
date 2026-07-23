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
from datetime import datetime, timezone
from typing import TYPE_CHECKING, AsyncIterator
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from inkmind.storage.concurrency import FileLock

from inkmind.models.agent import ChapterStatus, PipelineState
from inkmind.models.chapter import Chapter, ChapterVersion
from inkmind.models.novel import OutlineSpine
from inkmind.models.run import Run, RunKind, RunStatus

if TYPE_CHECKING:
    from inkmind.models.materials import MaterialFragment, MaterialSource
    from inkmind.models.novel import Volume

from inkmind.storage.idempotency import (
    IdempotencyGuard,
)
from inkmind.llm.providers.base import ProviderStats
from inkmind.storage.models import ProviderStatsModel
from inkmind.storage.repositories import (
    AppSettingsRepository,
    ChapterRepository,
    CharacterRepository,
    MaterialChunkRepository,
    MaterialFragmentRepository,
    MaterialSourceRepository,
    NovelRepository,
    OutlineSpineRepository,
    PipelineStateRepository,
    RunRepository,
    StatsRepository,
    VolumeRepository,
    WorldRepository,
)
from inkmind.storage.digest import compute_content_digest


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
        self._lock = FileLock.from_path(db_path, timeout=timeout) if db_path else None
        self.novels = NovelRepository(self._session) if self._session else None
        self.chapters = ChapterRepository(self._session) if self._session else None
        self.characters = CharacterRepository(self._session) if self._session else None
        self.worlds = WorldRepository(self._session) if self._session else None
        self.pipelines = PipelineStateRepository(self._session) if self._session else None
        self.runs = RunRepository(self._session) if self._session else None
        self.volumes = VolumeRepository(self._session) if self._session else None
        self.spines = OutlineSpineRepository(self._session) if self._session else None
        self.material_sources = MaterialSourceRepository(self._session) if self._session else None
        self.material_chunks = MaterialChunkRepository(self._session) if self._session else None
        self.material_fragments = (
            MaterialFragmentRepository(self._session) if self._session else None
        )
        self.app_settings = AppSettingsRepository(self._session) if self._session else None
        self.idempotency = IdempotencyGuard(self._session) if self._session else None
        self.stats = StatsRepository(self._session) if self._session else None
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
        assert self.idempotency is not None
        assert self.chapters is not None

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
        assert self.chapters is not None
        assert self.pipelines is not None

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
    #  T2a: Planner 保存总纲（覆盖保护）
    # ═══════════════════════════════════════════════════

    async def t2_planner_save_spine(
        self,
        novel_id: UUID,
        spine: OutlineSpine,
        confirm_overwrite: bool = False,
    ) -> tuple[bool, OutlineSpine]:
        """T2a: Planner 保存总纲 — 覆盖保护。

        如果已有非空总纲且未确认覆盖，返回 (False, 现有总纲)。

        Args:
            novel_id: 小说 ID
            spine: 新总纲
            confirm_overwrite: 确认覆盖非空内容

        Returns:
            (是否已写入, 写入/现有的总纲)

        Raises:
            ValueError: 非空总纲未确认覆盖时抛出
        """
        assert self.spines is not None

        existing = await self.spines.get_by_novel(novel_id)

        if existing is not None:
            # 检查是否有非空字段
            has_content = any(
                [
                    existing.main_line,
                    existing.core_conflict,
                    existing.ending,
                    existing.selling_points,
                    existing.world_background,
                    existing.golden_finger,
                ]
            )
            if has_content and not confirm_overwrite:
                raise ValueError(
                    "总纲非空，需确认覆盖（confirm_overwrite=True）。"
                    "如确认覆盖，请重新提交并设置 confirm_overwrite=true。"
                )

        # 写入新总纲
        result = await self.spines.upsert(spine)
        return True, result

    # ═══════════════════════════════════════════════════
    #  T2b: Planner 批量创建卷（拆卷）
    # ═══════════════════════════════════════════════════

    async def t2_planner_batch_create_volumes(
        self,
        novel_id: UUID,
        volumes_data: list[dict],
        start_index: int = 1,
    ) -> list[Volume]:
        """T2b: Planner 批量创建卷。

        Args:
            novel_id: 小说 ID
            volumes_data: LLM 返回的卷数据列表（title/stage_goal/main_line/side_line/volume_cliffhanger/planned_size）
            start_index: 起始卷序号（默认 1）

        Returns:
            创建的 Volume 列表
        """
        from inkmind.models.novel import Volume

        assert self.volumes is not None

        created: list[Volume] = []
        for i, vd in enumerate(volumes_data):
            vol = Volume(
                novel_id=novel_id,
                volume_index=start_index + i,
                title=vd.get("title", f"第 {start_index + i} 卷"),
                stage_goal=vd.get("stage_goal", ""),
                main_line=vd.get("main_line", ""),
                side_line=vd.get("side_line", ""),
                volume_cliffhanger=vd.get("volume_cliffhanger", ""),
                planned_size=vd.get("planned_size", 10),
            )
            await self.volumes.save(vol)
            created.append(vol)

        return created

    # ═══════════════════════════════════════════════════
    #  T2c: Planner 规划章（覆盖保护 — 已定稿/开工锁定）
    # ═══════════════════════════════════════════════════

    async def t2_planner_plan_chapters(
        self,
        novel_id: UUID,
        chapters_data: list[dict],
        volume_id: UUID | None = None,
    ) -> list[Chapter]:
        """T2c: Planner 批量创建/覆盖章节大纲。

        仅 PLANNED 状态的章节可覆盖；已定稿（approved/finalized）或
        开工（writing/draft_ready/reviewing/revising）的章节锁定不覆盖。

        Args:
            novel_id: 小说 ID
            chapters_data: LLM 返回的章节数据列表（含 chapter_index/title/summary/key_events/rhythm_marker/pov/involved）
            volume_id: 所属卷 ID

        Returns:
            实际写入的 Chapter 列表
        """
        assert self.chapters is not None

        chapters_created: list[Chapter] = []

        for ch_data in chapters_data:
            idx = ch_data.get("chapter_index")
            if idx is None:
                continue

            # 检查是否存在已有章节
            existing = await self.chapters.get_by_novel_and_index(novel_id, idx)

            if existing is not None:
                # 锁定检查：已定稿/开工的章不可覆盖
                locked_statuses = [
                    ChapterStatus.APPROVED,
                    ChapterStatus.FINALIZED,
                    ChapterStatus.WRITING,
                    ChapterStatus.DRAFT_READY,
                    ChapterStatus.REVIEWING,
                    ChapterStatus.REVISING,
                ]
                if existing.status in locked_statuses:
                    # 跳过不覆盖
                    continue

                # 仅 PLANNED 状态可覆盖 — 更新字段
                existing.title = ch_data.get("title", existing.title)
                existing.summary = ch_data.get("summary", existing.summary)
                existing.key_events = ch_data.get("key_events", existing.key_events)
                existing.rhythm_marker = ch_data.get("rhythm_marker", existing.rhythm_marker)
                existing.pov = ch_data.get("pov", existing.pov)
                existing.involved = ch_data.get("involved", existing.involved)
                if volume_id is not None:
                    existing.volume_id = volume_id
                existing.status = ChapterStatus.PLANNED
                await self.chapters.save(existing)
                chapters_created.append(existing)
            else:
                # 新建章节
                ch = Chapter(
                    novel_id=novel_id,
                    index=idx,
                    title=ch_data.get("title", f"第 {idx} 章"),
                    summary=ch_data.get("summary", ""),
                    key_events=ch_data.get("key_events", []),
                    status=ChapterStatus.PLANNED,
                    rhythm_marker=ch_data.get("rhythm_marker"),
                    pov=ch_data.get("pov", ""),
                    involved=ch_data.get("involved", []),
                    volume_id=volume_id,
                )
                await self.chapters.save(ch)
                chapters_created.append(ch)

        return chapters_created

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
        assert self.chapters is not None

        new_status = ChapterStatus.APPROVED if is_approved else ChapterStatus.REVISING

        # 更新章节状态
        await self.chapters.update_status(novel_id, chapter_index, new_status.value)

        # 如果批准且标记基线
        if is_approved and is_baseline:
            chapter = await self.chapters.get_by_novel_and_index(novel_id, chapter_index)
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

        assert self._session is not None

        # 1. 更新/写入 L2Archive
        existing = await self._session.execute(
            sa_update(MemoryArchiveModel)
            .where(
                MemoryArchiveModel.novel_id == str(novel_id),
                MemoryArchiveModel.tier == "l2_compressed",
            )
            .values(data=compressed_data)
        )
        if existing.rowcount == 0:  # type: ignore[attr-defined]
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

        assert self._session is not None

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
        if existing.rowcount == 0:  # type: ignore[attr-defined]
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
    #  T6: 素材导入
    # ═══════════════════════════════════════════════════

    async def t6_import_material(
        self,
        novel_id: UUID,
        raw_text: str,
        max_total_words: int = 100_000,
        chunk_max_words: int = 8000,
    ) -> MaterialSource:
        """T6: 导入素材原文。

        1. 计算 content_digest (SHA-256)
        2. 幂等检查: (novel_id, content_digest) UNIQUE → 重复返回已有 source
        3. 8000 字/块段落边界吸附
        4. 10 万字上限校验
        5. 创建 MaterialSource + MaterialChunk 记录（状态 pending）

        Returns:
            新建（或幂等匹配）的 MaterialSource
        """

        from inkmind.models.materials import MaterialChunk, MaterialSource
        from inkmind.storage.digest import compute_content_digest

        assert self.material_sources is not None
        assert self.material_chunks is not None

        # 1. 计算 digest
        digest = compute_content_digest(raw_text)

        # 2. 幂等检查
        existing = await self.material_sources.find_by_digest(novel_id, digest)
        if existing is not None:
            return existing

        # 3. 段落吸附切块
        paragraphs = raw_text.split("\n")
        chunks: list[str] = []
        current_chunk: list[str] = []
        current_len = 0

        for para in paragraphs:
            para_len = len(para)
            # 如果当前段落本身超过 chunk_max_words，强制单独成块
            if para_len > chunk_max_words and not current_chunk:
                chunks.append(para)
                continue
            # 如果加上当前段落后超过限制，先保存当前块再开始新块
            if current_len + para_len > chunk_max_words and current_chunk:
                chunks.append("\n".join(current_chunk))
                current_chunk = [para]
                current_len = para_len
            else:
                current_chunk.append(para)
                current_len += para_len
        if current_chunk:
            chunks.append("\n".join(current_chunk))

        # 4. 字数上限校验
        total_words = sum(len(c) for c in chunks)
        if total_words > max_total_words:
            raise ValueError(f"素材字数 {total_words} 超过上限 {max_total_words}")

        # 5. 创建 source + chunks
        source = MaterialSource(
            novel_id=novel_id,
            raw_text=raw_text,
            content_digest=digest,
            status="pending",
            word_count=total_words,
        )
        await self.material_sources.save(source)

        for i, chunk_text in enumerate(chunks):
            chunk = MaterialChunk(
                source_id=source.id,
                chunk_index=i,
                content=chunk_text,
                content_digest=compute_content_digest(chunk_text),
                status="pending",
            )
            await self.material_chunks.save(chunk)

        return source

    # ═══════════════════════════════════════════════════
    #  T7: 拆解提交
    # ═══════════════════════════════════════════════════

    async def t7_submit_decompose(
        self,
        chunk_id: UUID,
        fragments: list[MaterialFragment],
        chunk_status: str = "done",
        error_message: str | None = None,
        chunk_retry_count: int | None = None,
    ) -> None:
        """T7: 提交拆解结果。

        1. 清除该 chunk 旧的非 user_edited 片段
        2. 批量插入新 fragments
        3. 标记 chunk done/failed/low_quality

        Args:
            chunk_id: 拆解块 ID
            fragments: LLM 产出片段列表
            chunk_status: chunk 新状态（done/failed/low_quality）
            error_message: 错误消息（失败时）
            chunk_retry_count: 更新重试次数（None 则不变）
        """
        assert self.material_fragments is not None
        assert self.material_chunks is not None

        # 1. 清除旧的非 user_edited 片段
        await self.material_fragments.delete_by_chunk_except_edited(chunk_id)

        # 2. 批量插入新片段
        if fragments:
            await self.material_fragments.batch_save(fragments)

        # 3. 更新 chunk 状态
        chunk = await self.material_chunks.get_by_id(chunk_id)
        if chunk is not None:
            chunk.status = chunk_status
            if error_message is not None:
                chunk.error_message = error_message
            if chunk_retry_count is not None:
                chunk.retry_count = chunk_retry_count
            await self.material_chunks.save(chunk)

    # ═══════════════════════════════════════════════════
    #  T11: 保存设置
    # ═══════════════════════════════════════════════════

    async def t11_settings_save(self, settings_json: dict) -> None:
        """T11: 保存 app_settings 配置。

        Args:
            settings_json: 完整 LLMConfig 序列化 dict
        """
        assert self.app_settings is not None

        await self.app_settings.upsert(settings_json)

    # ═══════════════════════════════════════════════════
    #  T12: 持久化 Stats
    # ═══════════════════════════════════════════════════

    async def t12_persist_stats(self, stats_list: list[ProviderStats]) -> None:
        """T12: 持久化 ProviderStats 快照到数据库。

        Args:
            stats_list: 来自 LLMClient.get_raw_stats() 的 ProviderStats 列表。
        """
        assert self._session is not None

        for s in stats_list:
            model = ProviderStatsModel(
                provider_name=s.provider_name,
                model_name=s.model_name,
                agent_name=s.agent_name,
                latency_ms=s.latency_ms,
                prompt_tokens=s.prompt_tokens,
                completion_tokens=s.completion_tokens,
                total_tokens=s.total_tokens,
                estimated_cost=s.estimated_cost,
                success=s.success,
                error_type=s.error_type,
                degraded=s.degraded,
                retry_count=s.retry_count,
                timestamp=s.timestamp,
            )
            self._session.add(model)

    # ═══════════════════════════════════════════════════
    #  T8: Run 启动
    # ═══════════════════════════════════════════════════

    async def t8_run_start(
        self,
        novel_id: UUID,
        kind: RunKind,
        chapter_id: UUID | None = None,
    ) -> UUID:
        """T8: 启动一段 Run 执行生命周期。

        Args:
            novel_id: 小说 ID
            kind: Run 类型（generate/revise/finalize/plan）
            chapter_id: 关联章节 ID（kind=plan 时 None）

        Returns:
            run_id: 新创建的 Run UUID

        Raises:
            ValueError: 同章有 running 状态的 run 时 409
        """
        assert self.runs is not None

        # 校验同章无 running run
        if chapter_id is not None:
            existing = await self.runs.get_running_for_chapter(chapter_id)
            if existing is not None:
                raise ValueError(f"章节 {chapter_id} 已有正在执行的 Run ({existing.id})")

        now = datetime.now(timezone.utc)
        run = Run(
            novel_id=novel_id,
            chapter_id=chapter_id,
            kind=kind,
            status=RunStatus.RUNNING,
            phase="",
            partial_content="",
            started_at=now,
        )
        await self.runs.save(run)
        return run.id

    # ═══════════════════════════════════════════════════
    #  T9: 落稿收口
    # ═══════════════════════════════════════════════════

    async def t9_finalize_draft(
        self,
        run_id: UUID,
        chapter_content: str,
        chapter_title: str,
    ) -> UUID:
        """T9: 落稿收口 — 内嵌 T1 写 Chapter + 归档旧版本。

        Args:
            run_id: Run UUID
            chapter_content: 最终定稿内容
            chapter_title: 章节标题

        Returns:
            chapter_id: 写入的 Chapter UUID
        """
        assert self.runs is not None
        assert self.chapters is not None

        run = await self.runs.get_by_id(run_id)
        if run is None:
            raise ValueError(f"Run {run_id} 不存在")
        if run.chapter_id is None:
            raise ValueError(f"Run {run_id} 无关联章节")

        chapter = await self.chapters.get_by_id(run.chapter_id)
        if chapter is None:
            raise ValueError(f"章节 {run.chapter_id} 不存在")

        # 归档旧版本（如果有）
        previous_version = (
            ChapterVersion(
                chapter_id=chapter.id,
                novel_id=chapter.novel_id,
                version=chapter.version,
                index=chapter.index,
                title=chapter.title,
                content=chapter.content,
                summary=chapter.summary,
            )
            if chapter.content
            else None
        )

        # 更新章节内容
        chapter.content = chapter_content
        chapter.title = chapter_title
        chapter.status = ChapterStatus.AWAITING_HUMAN
        chapter.version += 1

        # 内嵌 T1 写 Chapter
        await self.chapters.save(chapter)
        if previous_version:
            await self.chapters.save_version(previous_version)

        # 清空 partial_content
        run.partial_content = ""
        await self.runs.save(run)

        return chapter.id

    # ═══════════════════════════════════════════════════
    #  T10: Run 终态收口
    # ═══════════════════════════════════════════════════

    async def t10_run_finalize(
        self,
        run_id: UUID,
        new_status: RunStatus,
        llm_stats: dict | None = None,
    ) -> None:
        """T10: Run 终态收口 — 写 stats 快照、标记 completed_at。

        Args:
            run_id: Run UUID
            new_status: 终态（awaiting_human/completed/failed/cancelled/interrupted）
            llm_stats: 聚合 LLM 统计快照
        """
        assert self.runs is not None

        run = await self.runs.get_by_id(run_id)
        if run is None:
            raise ValueError(f"Run {run_id} 不存在")

        if new_status in (
            RunStatus.COMPLETED,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
            RunStatus.INTERRUPTED,
        ):
            run.completed_at = datetime.now(timezone.utc)

        if new_status == RunStatus.AWAITING_HUMAN:
            run.mark_awaiting_human()
        else:
            run.status = new_status

        run.updated_at = datetime.now(timezone.utc)
        if llm_stats:
            run.llm_stats = llm_stats

        await self.runs.save(run)

    # ═══════════════════════════════════════════════════
    #  T13: 人工定稿
    # ═══════════════════════════════════════════════════

    async def t13_human_finalize(
        self,
        novel_id: UUID,
        chapter_index: int,
    ) -> None:
        """T13: 人工定稿 — AWAITING_HUMAN → FINALIZED + 记忆链路触发。

        记忆链路：
        1. L0 索引：将定稿内容按段落写入 L0 全文索引
        2. L1 滑窗：更新活跃上下文窗口
        3. L2 压缩：若滑窗外章节数 ≥ 阈值则创建压缩任务
        """
        from inkmind.storage.models import MemoryArchiveModel

        assert self.chapters is not None
        assert self._session is not None

        chapter = await self.chapters.get_by_novel_and_index(novel_id, chapter_index)
        if chapter is None:
            raise ValueError(f"章节 {novel_id}:{chapter_index} 不存在")

        chapter.status = ChapterStatus.FINALIZED
        await self.chapters.save(chapter)

        # ── L0 索引更新 ──
        paragraphs = [p.strip() for p in chapter.content.split("\n") if p.strip()]
        l0_entry = {
            "chapter_index": chapter_index,
            "title": chapter.title,
            "paragraphs": paragraphs,
            "word_count": len(chapter.content),
        }

        from sqlalchemy import select as sa_select

        existing_l0 = await self._session.execute(
            sa_select(MemoryArchiveModel).where(
                MemoryArchiveModel.novel_id == str(novel_id),
                MemoryArchiveModel.tier == "l0_index",
            )
        )
        l0_model = existing_l0.scalar_one_or_none()
        if l0_model is not None:
            data = dict(l0_model.data) if l0_model.data else {}
            chapters_index = data.get("chapters", {})
            chapters_index[str(chapter_index)] = l0_entry
            data["chapters"] = chapters_index
            l0_model.data = data
        else:
            self._session.add(
                MemoryArchiveModel(
                    novel_id=str(novel_id),
                    tier="l0_index",
                    data={"chapters": {str(chapter_index): l0_entry}},
                )
            )

        # ── L1 滑窗更新 ──
        existing_l1 = await self._session.execute(
            sa_select(MemoryArchiveModel).where(
                MemoryArchiveModel.novel_id == str(novel_id),
                MemoryArchiveModel.tier == "l1_active",
            )
        )
        l1_model = existing_l1.scalar_one_or_none()
        window_size = 5
        if l1_model is not None:
            data = dict(l1_model.data) if l1_model.data else {}
            window = data.get("sliding_window", {}).get("window", [])
            if chapter_index not in window:
                window.append(chapter_index)
            if len(window) > window_size:
                window = window[-window_size:]
            data.setdefault("sliding_window", {})["window"] = window
            data["sliding_window"]["latest_finalized"] = chapter_index
            l1_model.data = data
        else:
            self._session.add(
                MemoryArchiveModel(
                    novel_id=str(novel_id),
                    tier="l1_active",
                    data={
                        "sliding_window": {
                            "window": [chapter_index],
                            "latest_finalized": chapter_index,
                        },
                        "snapshot": {},
                    },
                )
            )

        # ── L2 压缩任务（滑窗外章节 ≥ 10 时触发） ──
        compression_threshold = 10
        if l1_model is not None:
            data = dict(l1_model.data) if l1_model.data else {}
            window = data.get("sliding_window", {}).get("window", [])
        else:
            window = [chapter_index]

        if chapter_index > window_size and (chapter_index - window[0]) >= compression_threshold:
            from uuid import uuid4

            from inkmind.storage.models import CompressionTaskModel

            range_start = window[0] if window else 1
            range_end = chapter_index - window_size
            if range_end >= range_start:
                self._session.add(
                    CompressionTaskModel(
                        task_id=str(uuid4()),
                        novel_id=str(novel_id),
                        range_start=range_start,
                        range_end=range_end,
                        status="pending",
                    )
                )

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

        assert self._session is not None

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
            assert self._session is not None
            await self._session.commit()
            return
        acquired = await self._lock.aacquire()
        if not acquired:
            raise RuntimeError("数据库写锁超时，请稍后重试")
        try:
            assert self._session is not None
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
            assert self._session is not None
            await self._session.rollback()
            raise
