"""Agent 流水线协议单元测试。"""

from uuid import uuid4

from inkmind.models.agent import (
    AgentPacket,
    AgentType,
    BatchPlanPayload,
    ChapterOutline,
    ChapterStatus,
    DraftPayload,
    MemorizedPayload,
    MemorizeRequestPayload,
    PacketType,
    PipelineState,
    PlanRequestPayload,
    ReviewRequestPayload,
    RevisionRequestPayload,
    Verdict,
    VerdictPayload,
    WriteRequestPayload,
)


def _sample_outline(index: int, title: str) -> ChapterOutline:
    return ChapterOutline(
        index=index,
        title=title,
        summary=(
            f"这是一段足够长的章节摘要内容，用于描述第{index}章的主要情节发展方向。"
            f"需要至少二十个字才能满足Pydantic的最小长度验证要求。"
        ),
        key_events=["事件A", "事件B"],
    )


def test_plan_request() -> None:
    nid = uuid4()
    p = AgentPacket(
        source=AgentType.DESIGNER,
        target=AgentType.PLANNER,
        novel_id=nid,
        packet_type=PacketType.PLAN_REQUEST,
        payload=PlanRequestPayload(
            novel_id=nid,
            chapter_count=5,
            world_id=uuid4(),
            context_summary="主角刚穿越到异世界",
        ),
    )
    assert p.packet_type == PacketType.PLAN_REQUEST
    assert isinstance(p.payload, PlanRequestPayload)
    assert p.payload.chapter_count == 5


def test_batch_plan() -> None:
    nid = uuid4()
    p = AgentPacket(
        source=AgentType.PLANNER,
        target=AgentType.WRITER,
        novel_id=nid,
        packet_type=PacketType.BATCH_PLAN,
        payload=BatchPlanPayload(
            outlines=[
                _sample_outline(1, "开局"),
                _sample_outline(2, "测试"),
            ]
        ),
    )
    assert len(p.payload.outlines) == 2
    assert p.payload.outlines[0].title == "开局"


def test_write_request() -> None:
    nid = uuid4()
    p = AgentPacket(
        source=AgentType.PLANNER,
        target=AgentType.WRITER,
        novel_id=nid,
        packet_type=PacketType.WRITE_REQUEST,
        payload=WriteRequestPayload(
            novel_id=nid,
            chapter_outline=_sample_outline(1, "开局"),
            context_summary="前文无",
        ),
    )
    assert p.payload.word_count_min == 1000
    assert p.payload.word_count_max == 3000


def test_draft() -> None:
    nid = uuid4()
    p = AgentPacket(
        source=AgentType.WRITER,
        target=AgentType.EDITOR,
        novel_id=nid,
        packet_type=PacketType.DRAFT,
        payload=DraftPayload(
            chapter_index=1,
            content="张三睁开眼睛，发现自己躺在陌生的森林里。" * 20,
            paragraph_count=5,
        ),
    )
    assert len(p.payload.content) > 100
    assert p.payload.chapter_index == 1


def test_review_approve() -> None:
    nid = uuid4()
    p = AgentPacket(
        source=AgentType.EDITOR,
        target=AgentType.MEMORY_KEEPER,
        novel_id=nid,
        packet_type=PacketType.VERDICT,
        payload=VerdictPayload(verdict=Verdict.APPROVE),
    )
    assert p.payload.verdict == Verdict.APPROVE
    assert p.payload.issues == []


def test_review_needs_revision() -> None:
    nid = uuid4()
    p = AgentPacket(
        source=AgentType.EDITOR,
        target=AgentType.WRITER,
        novel_id=nid,
        packet_type=PacketType.VERDICT,
        payload=VerdictPayload(
            verdict=Verdict.NEEDS_REVISION,
            issues=["主角情绪描写不足", "穿越过程过于突兀"],
        ),
    )
    assert p.payload.verdict == Verdict.NEEDS_REVISION
    assert len(p.payload.issues) == 2


def test_revision_request() -> None:
    nid = uuid4()
    p = AgentPacket(
        source=AgentType.EDITOR,
        target=AgentType.WRITER,
        novel_id=nid,
        packet_type=PacketType.REVISION_REQUEST,
        payload=RevisionRequestPayload(
            novel_id=nid,
            chapter_index=1,
            previous_content="旧版草稿内容。" * 10,
            issues=["情绪描写不足"],
            iteration=1,
            chapter_outline=_sample_outline(1, "开局"),
        ),
    )
    assert p.payload.iteration == 1


def test_memorize_flow() -> None:
    nid = uuid4()
    req = AgentPacket(
        source=AgentType.WRITER,
        target=AgentType.MEMORY_KEEPER,
        novel_id=nid,
        packet_type=PacketType.MEMORIZE_REQUEST,
        payload=MemorizeRequestPayload(
            novel_id=nid,
            chapter_index=1,
            chapter_title="开局",
            chapter_summary="主角穿越",
            key_events=["穿越"],
        ),
    )
    assert req.payload.chapter_index == 1

    resp = AgentPacket(
        source=AgentType.MEMORY_KEEPER,
        target=AgentType.WRITER,
        novel_id=nid,
        packet_type=PacketType.MEMORIZED,
        payload=MemorizedPayload(
            novel_id=nid,
            chapter_index=1,
            digest="abc123",
        ),
    )
    assert resp.payload.success is True
    assert resp.payload.digest == "abc123"


def test_pipeline_state() -> None:
    nid = uuid4()
    state = PipelineState(
        novel_id=nid,
        total_chapters=10,
        chapters={
            1: ChapterStatus.DRAFT_READY,
            2: ChapterStatus.PLANNED,
            3: ChapterStatus.FINALIZED,
        },
        current_chapter_index=1,
    )
    assert state.chapters[1] == ChapterStatus.DRAFT_READY
    assert state.chapters[3] == ChapterStatus.FINALIZED
    assert state.max_iterations == 3


def test_type_narrowing_at_runtime() -> None:
    """验证通过 packet_type 做运行时类型窄化。"""
    nid = uuid4()

    draft_packet = AgentPacket(
        source=AgentType.WRITER,
        target=AgentType.EDITOR,
        novel_id=nid,
        packet_type=PacketType.DRAFT,
        payload=DraftPayload(
            chapter_index=1,
            content="正文内容。" * 30,
            paragraph_count=5,
        ),
    )

    verdict_packet = AgentPacket(
        source=AgentType.EDITOR,
        target=AgentType.WRITER,
        novel_id=nid,
        packet_type=PacketType.VERDICT,
        payload=VerdictPayload(verdict=Verdict.NEEDS_REVISION, issues=["需要修改"]),
    )

    # 运行时类型窄化
    if draft_packet.packet_type == PacketType.DRAFT:
        assert isinstance(draft_packet.payload, DraftPayload)

    if verdict_packet.packet_type == PacketType.VERDICT:
        assert isinstance(verdict_packet.payload, VerdictPayload)
        assert verdict_packet.payload.verdict == Verdict.NEEDS_REVISION
