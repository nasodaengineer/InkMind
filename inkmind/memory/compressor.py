"""MemoryKeeper 核心压缩管线。

提供四级记忆架构的核心算法：
1. 滑窗管理（滚动 / 伏笔驱动扩展 / 角色状态卡更新）
2. 压缩触发决策（固定粒度 / 动态调整 / 伏笔阈值）
3. 记忆快照组装（L1 + L2 + L3 打包）
4. 压缩任务生命周期管理（异步队列）

纯逻辑层，不依赖 LLM Provider，通过回调/Protocol 接入外部 LLM。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol
from uuid import UUID, uuid4

from inkmind.models.memory import (
    ActiveContext,
    CharacterStateCard,
    CompressedEvent,
    CompressedMemory,
    CompressionGranularity,
    CompressionMeta,
    CompressionResult,
    CompressionTask,
    CompressionTaskStatus,
    CompressStrategy,
    L0Index,
    L2Archive,
    L3Archive,
    MemoryNotificationPayload,
    MemorySnapshot,
    SlidingWindowState,
    TimeRange,
)


# ──────────────────────────────────────────────
#  LLM 压缩回调协议
# ──────────────────────────────────────────────


class LLMCompressor(Protocol):
    """LLM 压缩回调解约。MemoryKeeperCore 不直接调用 LLM，
    由外部 Provider 注入此回调。
    """

    async def __call__(
        self,
        chapters: list[dict],
        strategy: CompressStrategy,
    ) -> tuple[str, list[CompressedEvent]]:
        """给定一批章节原文及其元数据，返回压缩结果。

        Args:
            chapters: 每章内容，格式为 [{index, title, content, key_events, characters, location}]
            strategy: 压缩策略配置

        Returns:
            (summary, events) 一段总摘要 + 结构化事件清单
        """
        ...


# ──────────────────────────────────────────────
#  MemoryKeeper 核心
# ──────────────────────────────────────────────


class MemoryKeeperCore:
    """MemoryKeeper 核心逻辑。无状态设计，所有数据通过参数传入传出。

    职责边界：
    - 滑窗管理（不是存储）
    - 压缩决策（不是执行）
    - 快照组装（不是传输）
    - 任务跟踪（不是调度）
    """

    def __init__(
        self,
        novel_id: UUID,
        strategy: CompressStrategy | None = None,
        llm_compressor: LLMCompressor | None = None,
    ):
        self.novel_id = novel_id
        self.strategy = strategy or CompressStrategy()
        self._llm_compressor = llm_compressor

        # 运行时状态
        self._sliding_window: SlidingWindowState | None = None
        self._pending_tasks: dict[UUID, CompressionTask] = {}
        self._l0_index: L0Index = L0Index(novel_id=novel_id)
        self._l2_archive: L2Archive = L2Archive(novel_id=novel_id)
        self._l3_archive: L3Archive = L3Archive(novel_id=novel_id)

    # ══════════════════════════════════════════
    #  对外接口
    # ══════════════════════════════════════════

    # ── 1. 接收新章定稿 ──

    def on_chapter_finalized(
        self,
        chapter_index: int,
        chapter_title: str,
        content: str,
        key_events: list[str],
        character_events: list[dict],
        location_changes: list[str],
    ) -> MemoryNotificationPayload:
        """每章定稿后调用。更新 L0/L1，判断是否需要触发 L2 压缩。

        返回事件通知（异步任务已创建 / 无需压缩 / 滑窗已滚动）。
        """
        # 1. L0 索引更新
        self._index_chapter(chapter_index, content)

        # 2. L1 滑窗滚动 + 状态卡更新
        expansion_note = self._roll_window(
            chapter_index, chapter_title, character_events, location_changes
        )

        # 3. 检查是否需要触发压缩
        compression_task = self._check_compression_trigger(chapter_index)

        # 4. 组装通知
        # 注意：MemoryNotification 在 memory.py 中定义，此处直接使用字符串
        if compression_task:
            notification = MemoryNotificationPayload(
                novel_id=self.novel_id,
                notification_type="compression_started",  # type: ignore[arg-type]
                message=(
                    f"第 {chapter_index} 章定稿，触发了 L2 压缩任务"
                    f"（{compression_task.range.start_chapter}-"
                    f"{compression_task.range.end_chapter}）"
                ),
            )
        else:
            notification = MemoryNotificationPayload(
                novel_id=self.novel_id,
                notification_type="l1_window_shifted",  # type: ignore[arg-type]
                message=f"第 {chapter_index} 章定稿，L1 滑窗已滚动"
                + (f"（{expansion_note}）" if expansion_note else ""),
            )

        return notification

    # ── 2. 执行压缩（同步内部方法，异步外部调用） ──

    async def execute_compression(self, task_id: UUID) -> CompressionResult:
        """执行指定的压缩任务。

        调用 LLMCompressor 回调完成实际压缩。
        外部应该将这个方法放入异步队列执行。
        """
        task = self._pending_tasks.get(task_id)
        if not task:
            return CompressionResult(task_id=task_id, success=False, error="任务不存在")

        if self._llm_compressor is None:
            task.status = CompressionTaskStatus.FAILED
            task.error_message = "未注入 LLMCompressor 回调"
            return CompressionResult(
                task_id=task_id,
                success=False,
                error="LLMCompressor not configured",
            )

        task.status = CompressionTaskStatus.RUNNING
        task.started_at = datetime.now(timezone.utc)

        try:
            # 收集待压缩的章节数据（此处从 L0/L1 源组装chapters数据）
            chapters_data = self._collect_chapters_for_range(task.range)

            summary, events = await self._llm_compressor(
                chapters=chapters_data,
                strategy=self.strategy,
            )

            compressed = CompressedMemory(
                novel_id=self.novel_id,
                range=task.range,
                meta=CompressionMeta(
                    chapter_count=task.range.end_chapter - task.range.start_chapter + 1,
                ),
                summary=summary,
                events=events,
            )

            # 归档
            self._l2_archive.memories.append(compressed)

            task.status = CompressionTaskStatus.COMPLETED
            task.completed_at = datetime.now(timezone.utc)

            return CompressionResult(task_id=task_id, success=True, compressed=compressed)

        except Exception as exc:
            task.status = CompressionTaskStatus.FAILED
            task.error_message = str(exc)
            return CompressionResult(
                task_id=task_id,
                success=False,
                error=str(exc),
            )

    # ── 3. 构建记忆快照（给 Writer/Planner） ──

    def build_memory_snapshot(self, target_chapter: int) -> MemorySnapshot:
        """为指定章节构建完整的上下文快照。

        Writer 写第 N 章前调用此方法获取全部需要的上下文。
        """
        # L1 活跃上下文
        active = self._build_active_context(target_chapter)

        # L2 最近压缩记忆（按时间逆序，最多 3 条）
        recent = list(reversed(self._l2_archive.memories[-3:]))

        # 伏笔提示
        foreshadowing_notes = []
        if self._sliding_window:
            for m in self._sliding_window.pending_foreshadowing:
                note = (
                    f"[伏笔] 第{m.planted_chapter}章: {m.description}"
                )
                if m.expected_payoff_chapter:
                    note += f"（预期第{m.expected_payoff_chapter}章回收）"
                foreshadowing_notes.append(note)

        return MemorySnapshot(
            novel_id=self.novel_id,
            active_context=active,
            recent_compressed=recent,
            permanent_archive=self._l3_archive,
            foreshadowing_notes=foreshadowing_notes,
            pending_compression_tasks=len(self._pending_tasks),
        )

    # ── 4. L3 长期知识管理 ──

    def update_long_term_entry(
        self,
        entry_id: UUID | None = None,
        title: str | None = None,
        content: str = "",
        entry_type: str = "note",
        tags: list[str] | None = None,
    ) -> UUID:
        """更新或创建长期知识条目。

        如果提供了 entry_id，则更新；否则创建新条目。
        """
        from inkmind.models.memory import LongTermEntry, LongTermEntryType

        try:
            etype = LongTermEntryType(entry_type)
        except ValueError:
            etype = LongTermEntryType.NOTE

        if entry_id and entry_id in self._l3_archive.entries:
            entry = self._l3_archive.entries[entry_id]
            entry.content = content
            entry.version += 1
            entry.updated_at = datetime.now(timezone.utc)
            if title:
                entry.title = title
            if tags:
                entry.tags = tags
            return entry_id
        else:
            entry = LongTermEntry(
                entry_id=entry_id or uuid4(),
                entry_type=etype,
                title=title or "未命名",
                content=content,
                tags=tags or [],
            )
            self._l3_archive.entries[entry.entry_id] = entry
            self._l3_archive.last_updated_at = datetime.now(timezone.utc)
            return entry.entry_id

    # ══════════════════════════════════════════
    #  内部方法
    # ══════════════════════════════════════════

    # ── L0 索引 ──

    def _index_chapter(self, chapter_index: int, content: str) -> None:
        """将章节内容加入 L0 索引。"""
        from inkmind.models.memory import IndexEntry

        paragraphs = content.strip().split("\n\n")
        for i, para in enumerate(paragraphs, 1):
            if para.strip():
                entry = IndexEntry(
                    chapter_index=chapter_index,
                    paragraph_index=i,
                    content_hash=str(hash(para.strip())),
                )
                self._l0_index.entries.append(entry)

        self._l0_index.total_chapters_indexed += 1
        self._l0_index.last_indexed_at = datetime.now(timezone.utc)

    # ── L1 滑窗管理 ──

    def _roll_window(
        self,
        chapter_index: int,
        chapter_title: str,
        character_events: list[dict],
        location_changes: list[str],
    ) -> str | None:
        """滚动滑窗。返回扩展原因（如有）。

        流程：
        1. 更新当前章节索引
        2. 更新角色状态卡
        3. 检查未回收伏笔，动态扩展滑窗
        4. 清理超范围的旧章节
        """
        if self._sliding_window is None:
            self._sliding_window = SlidingWindowState(
                novel_id=self.novel_id,
                current_chapter_index=chapter_index,
                recent_chapters=[chapter_index],
            )
            self._sliding_window.current_expanded_size = (
                self._sliding_window.default_window_size
            )

        sw = self._sliding_window
        prev_chapter = sw.current_chapter_index
        sw.current_chapter_index = chapter_index

        # 追加到 recent_chapters
        if chapter_index not in sw.recent_chapters:
            sw.recent_chapters.append(chapter_index)
            sw.recent_chapters.sort()

        # 更新角色状态卡
        for ce in character_events:
            cid = ce.get("character_id")
            if cid is None:
                continue
            if isinstance(cid, str):
                # 尝试将 str 转 UUID，不能转的就保持原样
                try:
                    cid_uuid = UUID(cid)
                except ValueError:
                    # 非 UUID 的临时标识，跳转
                    continue
            else:
                cid_uuid = cid

            card = sw.character_states.get(cid_uuid)
            if card:
                card.recent_action = ce.get("event", "")
                if "location" in ce:
                    card.current_location = ce["location"]
            else:
                sw.character_states[cid_uuid] = CharacterStateCard(
                    character_id=cid_uuid,
                    name=ce.get("name", str(cid_uuid)[:8]),
                    current_location=ce.get("location"),
                    recent_action=ce.get("event"),
                )

        # 检查伏笔驱动滑窗扩展
        expand_reason = self._check_window_expansion(chapter_index)
        if expand_reason:
            sw.expand_reason = expand_reason
            # 动态扩展：按需扩大滑窗
            sw.current_expanded_size = max(
                sw.default_window_size,
                len(sw.recent_chapters),
            )

        # 清理超出滑窗的旧章节
        max_size = sw.current_expanded_size
        if len(sw.recent_chapters) > max_size:
            sw.recent_chapters = sw.recent_chapters[-max_size:]

        return expand_reason

    def _check_window_expansion(self, chapter_index: int) -> str | None:
        """检查是否需要因伏笔而扩展滑窗。

        如果当前滑窗内的 pending_foreshadowing 中有伏笔的
        expected_payoff_chapter 超过了滑窗边界，就需要扩展。
        """
        if not self._sliding_window:
            return None

        sw = self._sliding_window
        if not sw.recent_chapters:
            return None

        # 滑窗覆盖范围
        window_start = min(sw.recent_chapters)
        window_end = max(sw.recent_chapters)
        current_size = window_end - window_start + 1

        # 检查伏笔
        for marker in sw.pending_foreshadowing:
            if (
                marker.expected_payoff_chapter
                and marker.expected_payoff_chapter > window_end
            ):
                extra = marker.expected_payoff_chapter - window_end
                if extra > 0:
                    return (
                        f"伏笔「{marker.description}」预期在第"
                        f"{marker.expected_payoff_chapter}章回收，"
                        f"滑窗扩大 {extra} 章"
                    )

        return None

    # ── L2 压缩决策 ──

    def _check_compression_trigger(
        self, chapter_index: int
    ) -> CompressionTask | None:
        """检查是否需要触发 L2 压缩。

        触发条件（任一满足）：
        1. 固定粒度：自上次压缩后章节数 >= default_granularity
        2. 动态调整：连续 N 章的关键事件数超过阈值
        3. 伏笔阈值：未回收伏笔数超过 max_pending_foreshadowing
        """
        # 确定自上次压缩以来的章节范围
        last_compressed = 0
        if self._l2_archive.memories:
            last_compressed = self._l2_archive.memories[-1].range.end_chapter

        accumulated = chapter_index - last_compressed

        # 条件 1：固定粒度
        if accumulated >= self.strategy.default_granularity:
            task = self._create_compression_task(
                last_compressed + 1,
                chapter_index,
                CompressionGranularity.FIXED,
                f"达到 {self.strategy.default_granularity} 章上限",
            )
            return task

        # 条件 2：伏笔阈值
        if self._sliding_window:
            pending_count = len(self._sliding_window.pending_foreshadowing)
            if pending_count >= self.strategy.max_pending_foreshadowing:
                task = self._create_compression_task(
                    last_compressed + 1,
                    chapter_index,
                    CompressionGranularity.DYNAMIC,
                    f"未回收伏笔数（{pending_count}）超过阈值"
                    f"（{self.strategy.max_pending_foreshadowing}）",
                )
                return task

        # 条件 3：里程碑章节触发（动态调整）
        if (
            self.strategy.enable_dynamic_granularity
            and accumulated >= self.strategy.min_event_count_for_milestone
        ):
            # 简化版：如果累积章节数超过粒度的一半且最近一章是关键事件章
            if accumulated >= self.strategy.default_granularity // 2:
                # 实际应用中会更精细地检查 key_events 数量
                # 这里留作扩展点
                pass

        return None

    def _create_compression_task(
        self,
        start_chapter: int,
        end_chapter: int,
        granularity: CompressionGranularity,
        reason: str,
    ) -> CompressionTask:
        """创建压缩任务并注册到待处理队列。"""
        task = CompressionTask(
            novel_id=self.novel_id,
            range=TimeRange(start_chapter=start_chapter, end_chapter=end_chapter),
        )
        self._pending_tasks[task.task_id] = task
        return task

    def _collect_chapters_for_range(self, r: TimeRange) -> list[dict]:
        """收集指定章节范围的数据用于压缩。

        注意：完整实现需要从存储层读取章节原文。
        当前返回章节索引级别的摘要——实际使用时 L0 层会提供原文。
        """
        chapters = []
        for entry in self._l0_index.entries:
            if r.start_chapter <= entry.chapter_index <= r.end_chapter:
                chapters.append({
                    "index": entry.chapter_index,
                    "content_hash": entry.content_hash,
                    "keywords": entry.keywords,
                })

        # 去重
        seen = set()
        unique = []
        for c in chapters:
            if c["index"] not in seen:
                seen.add(c["index"])
                unique.append(c)

        return unique

    # ── L1 活跃上下文构建 ──

    def _build_active_context(self, target_chapter: int) -> ActiveContext:
        """为指定章节构建 L1 活跃上下文。"""
        sw = self._sliding_window or SlidingWindowState(
            novel_id=self.novel_id,
            current_chapter_index=target_chapter,
            recent_chapters=[target_chapter],
        )

        foreshadowing_notes = []
        for m in sw.pending_foreshadowing:
            if m.expected_payoff_chapter is None:
                foreshadowing_notes.append(m.description)

        return ActiveContext(
            novel_id=self.novel_id,
            current_chapter_index=target_chapter,
            sliding_window=sw,
            state_cards=list(sw.character_states.values()),
            foreshadowing_notes=foreshadowing_notes,
        )
