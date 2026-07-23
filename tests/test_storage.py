"""测试：事务式持久化层。

覆盖范围：
1. DatabaseManager — 创建/删除表，会话生命周期
2. ORM 模型 — 10 张表的 CRUD
3. Serializers — 所有 Pydantic ↔ ORM 的双向转换
4. Idempotency — digest 计算、幂等检查、标记
5. Repositories — 5 个 Repository 的 CRUD
6. UnitOfWork — T1-T5 事务边界
7. Snapshot — JSON dump/restore
8. Recovery — 故障恢复状态重建
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select

from inkmind.models.agent import ChapterStatus, PipelineState
from inkmind.models.chapter import Chapter, ChapterVersion
from inkmind.models.character import Character, CharacterTimelineEntry
from inkmind.models.novel import Novel, NovelMetadata
from inkmind.models.world import (
    Faction,
    Location,
    MagicSystem,
    PowerAbility,
    PowerSystem,
    TimelineMarker,
    World,
)
from inkmind.storage.database import DatabaseManager
from inkmind.storage.idempotency import (
    IdempotencyGuard,
)
from inkmind.storage.models import (
    AgentQueueModel,
    ChapterModel,
    ChapterVersionModel,
    CharacterModel,
    CompressionTaskModel,
    MemoryArchiveModel,
    NovelModel,
    PipelineStateModel,
    ProcessedDigestModel,
    WorldModel,
)
from inkmind.storage.recovery import RecoveryManager
from inkmind.storage.repositories import (
    ChapterRepository,
    CharacterRepository,
    NovelRepository,
    PipelineStateRepository,
    WorldRepository,
)
from inkmind.storage.serializers import (
    chapter_to_dict,
    chapter_to_orm,
    chapter_version_to_dict,
    chapter_version_to_orm,
    character_to_dict,
    character_to_orm,
    dict_to_chapter,
    dict_to_chapter_version,
    dict_to_character,
    dict_to_novel,
    dict_to_pipeline_state,
    dict_to_world,
    novel_to_dict,
    novel_to_orm,
    pipeline_state_to_dict,
    pipeline_state_to_orm,
    world_to_dict,
    world_to_orm,
)
from inkmind.storage.snapshot import JSONSnapshot
from inkmind.storage.digest import compute_content_digest
from inkmind.storage.unit_of_work import UnitOfWork

# ═══════════════════════════════════════════════════════
#  Fixtures
# ═══════════════════════════════════════════════════════

TEST_DB_PATH = ":memory:"


@pytest_asyncio.fixture
async def db():
    """内存 SQLite 测试数据库。"""
    manager = DatabaseManager(TEST_DB_PATH)
    await manager.create_tables()
    yield manager
    # 清理表，不影响下个测试
    await manager.drop_tables()
    await manager.close()


@pytest_asyncio.fixture
async def session(db):
    """获取测试会话。"""
    async with db.session_factory() as s:
        yield s


@pytest.fixture
def sample_novel():
    return Novel(
        id=uuid4(),
        title="测试小说",
        metadata=NovelMetadata(
            description="一本测试用小说",
            word_count=1500,
            chapter_count=3,
            status="draft",
        ),
    )


@pytest.fixture
def sample_chapter(sample_novel):
    return Chapter(
        id=uuid4(),
        novel_id=sample_novel.id,
        index=1,
        title="第一章",
        content="这是第一章的正文内容。",
        status=ChapterStatus.PLANNED,
        summary="第一章摘要",
        key_events=["事件A", "事件B"],
        source_trace="test-model",
        version=1,
        is_baseline=True,
    )


@pytest.fixture
def sample_character(sample_novel):
    return Character(
        id=uuid4(),
        novel_id=sample_novel.id,
        name="张三",
        aliases=["张", "三"],
        role="protagonist",
        personality_tags=["勇敢", "善良"],
        behavior_rules="对朋友忠诚",
        appearance="高大威猛",
        background="来自小镇",
        relationships="李四的朋友",
        current_state="充满斗志",
        knowledge=["剑术"],
        voice_examples="说话直爽",
        timeline=[
            CharacterTimelineEntry(
                chapter_index=1,
                key_events=["初登场"],
                current_state="兴奋",
            )
        ],
    )


@pytest.fixture
def sample_world(sample_novel):
    return World(
        id=uuid4(),
        novel_id=sample_novel.id,
        title="奇幻大陆",
        genre_tags=["西幻", "冒险"],
        setting="中世纪魔法世界",
        rules=["魔法需要吟唱"],
        factions=[
            Faction(
                id=uuid4(),
                name="光明教会",
                description="正义的宗教组织",
                leader="教皇",
                members=["骑士A", "牧师B"],
                goals=["消灭黑暗"],
                relationships="与黑暗势力对立",
            )
        ],
        timeline_markers=[
            TimelineMarker(
                label="魔法觉醒",
                description="大陆魔力复苏",
                chapter_index=1,
                is_pivotal=True,
            )
        ],
        power_system=PowerSystem(
            name="斗气",
            description="物理战斗体系",
            abilities=[
                PowerAbility(
                    name="斗气外放",
                    description="释放体内斗气攻击",
                    tier="一阶",
                    limitations=["消耗体力"],
                )
            ],
            rules=["斗气可修炼"],
            limitations=["不能飞行"],
        ),
        magic_system=MagicSystem(
            name="元素魔法",
            description="操控元素之力",
            schools=["火系", "水系"],
            spells=[
                PowerAbility(
                    name="火球术",
                    description="发射火球",
                    tier="初级",
                    limitations=["需要法杖"],
                )
            ],
            rules=["魔力耗尽会昏倒"],
            limitations=["不能死而复生"],
            mana_source="自然元素",
        ),
        location_tree=[
            Location(
                id=uuid4(),
                name="大陆",
                type="continent",
                description="主大陆",
            ),
            Location(
                id=uuid4(),
                name="王城",
                type="city",
                parent_id=None,
                description="首都",
                notable_features=["王宫"],
            ),
        ],
    )


@pytest.fixture
def sample_pipeline(sample_novel):
    return PipelineState(
        novel_id=sample_novel.id,
        total_chapters=3,
        chapters={
            1: ChapterStatus.PLANNED,
            2: ChapterStatus.PLANNED,
            3: ChapterStatus.PLANNED,
        },
        current_chapter_index=1,
        iteration=0,
        max_iterations=3,
    )


# ═══════════════════════════════════════════════════════
#  1. DatabaseManager
# ═══════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_database_create_tables(db):
    """创建表后应存在所有表。"""
    async with db.session_factory() as s:
        result = await s.execute(
            select(NovelModel).limit(1)
        )
        # 表存在即可查询，无行
        assert result is not None


@pytest.mark.asyncio
async def test_database_session_commit_rollback(db):
    """会话应正确提交。"""
    async with db.session_factory() as s:
        novel = NovelModel(
            uuid=str(uuid4()),
            title="提交测试",
        )
        s.add(novel)
        await s.commit()

    # 在另一个会话中验证
    async with db.session_factory() as s:
        result = await s.execute(
            select(NovelModel).where(
                NovelModel.title == "提交测试"
            )
        )
        assert result.scalar_one_or_none() is not None


@pytest.mark.asyncio
async def test_database_session_rollback_on_error(db):
    """异常时自动回滚。"""
    async with db.session_factory() as s:
        novel = NovelModel(
            uuid=str(uuid4()),
            title="回滚测试",
        )
        s.add(novel)
        # 不提交，直接关闭

    # 数据不应持久化
    async with db.session_factory() as s:
        result = await s.execute(
            select(NovelModel).where(
                NovelModel.title == "回滚测试"
            )
        )
        assert result.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_database_close(db):
    """close 应成功。"""
    await db.close()


# ═══════════════════════════════════════════════════════
#  2. ORM 模型 CRUD
# ═══════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_orm_novel_crud(session, sample_novel):
    """NovelModel CRUD。"""
    orm_data = novel_to_orm(sample_novel)
    model = NovelModel(**orm_data)
    session.add(model)
    await session.commit()

    # Read
    result = await session.execute(
        select(NovelModel).where(NovelModel.uuid == str(sample_novel.id))
    )
    loaded = result.scalar_one_or_none()
    assert loaded is not None
    assert loaded.title == "测试小说"

    # Update
    loaded.title = "新标题"
    await session.commit()

    result = await session.execute(
        select(NovelModel).where(NovelModel.uuid == str(sample_novel.id))
    )
    loaded2 = result.scalar_one_or_none()
    assert loaded2.title == "新标题"

    # Delete
    await session.delete(loaded2)
    await session.commit()

    result = await session.execute(
        select(NovelModel).where(NovelModel.uuid == str(sample_novel.id))
    )
    loaded3 = result.scalar_one_or_none()
    assert loaded3 is None


@pytest.mark.asyncio
async def test_orm_chapter_crud(
    session, sample_novel, sample_chapter
):
    """ChapterModel CRUD。"""
    # 先插入 novel
    session.add(NovelModel(**novel_to_orm(sample_novel)))
    await session.commit()

    orm_data = chapter_to_orm(sample_chapter)
    model = ChapterModel(**orm_data)
    session.add(model)
    await session.commit()

    result = await session.execute(
        select(ChapterModel).where(ChapterModel.uuid == str(sample_chapter.id))
    )
    loaded = result.scalar_one_or_none()
    assert loaded is not None
    assert loaded.title == "第一章"
    assert loaded.content == "这是第一章的正文内容。"
    assert loaded.chapter_index == 1


@pytest.mark.asyncio
async def test_orm_character_crud(
    session, sample_novel, sample_character
):
    """CharacterModel CRUD。"""
    session.add(NovelModel(**novel_to_orm(sample_novel)))
    await session.commit()

    orm_data = character_to_orm(sample_character)
    model = CharacterModel(**orm_data)
    session.add(model)
    await session.commit()

    result = await session.execute(
        select(CharacterModel).where(CharacterModel.uuid == str(sample_character.id))
    )
    loaded = result.scalar_one_or_none()
    assert loaded is not None
    assert loaded.name == "张三"
    assert loaded.role == "protagonist"
    assert loaded.aliases == ["张", "三"]


@pytest.mark.asyncio
async def test_orm_world_crud(session, sample_novel, sample_world):
    """WorldModel CRUD。"""
    session.add(NovelModel(**novel_to_orm(sample_novel)))
    await session.commit()

    orm_data = world_to_orm(sample_world)
    model = WorldModel(**orm_data)
    session.add(model)
    await session.commit()

    result = await session.execute(
        select(WorldModel).where(WorldModel.uuid == str(sample_world.id))
    )
    loaded = result.scalar_one_or_none()
    assert loaded is not None
    assert loaded.title == "奇幻大陆"
    assert loaded.setting == "中世纪魔法世界"

    # 检查结构化字段
    data = world_to_dict(loaded)
    assert data["power_system"]["name"] == "斗气"
    assert data["magic_system"]["name"] == "元素魔法"
    assert len(data["factions"]) == 1
    assert data["factions"][0]["name"] == "光明教会"


@pytest.mark.asyncio
async def test_orm_pipeline_crud(
    session, sample_novel, sample_pipeline
):
    """PipelineStateModel CRUD。"""
    session.add(NovelModel(**novel_to_orm(sample_novel)))
    await session.commit()

    orm_data = pipeline_state_to_orm(sample_pipeline)
    model = PipelineStateModel(**orm_data)
    session.add(model)
    await session.commit()

    result = await session.execute(
        select(PipelineStateModel).where(PipelineStateModel.novel_id == str(sample_pipeline.novel_id))
    )
    loaded = result.scalar_one_or_none()
    assert loaded is not None
    assert loaded.total_chapters == 3


@pytest.mark.asyncio
async def test_orm_agent_queue(session, sample_novel):
    """AgentQueueModel CRUD。"""
    session.add(NovelModel(**novel_to_orm(sample_novel)))
    await session.commit()

    queue = AgentQueueModel(
        packet_id=str(uuid4()),
        digest=hashlib.sha256(b"test").hexdigest(),
        packet_type="DRAFT",
        source="writer",
        target="editor",
        novel_id=str(sample_novel.id),
        payload={"content": "test"},
        status="pending",
    )
    session.add(queue)
    await session.commit()

    loaded = await session.get(AgentQueueModel, queue.id)
    assert loaded is not None
    assert loaded.packet_type == "DRAFT"


@pytest.mark.asyncio
async def test_orm_compression_task(session, sample_novel):
    """CompressionTaskModel CRUD。"""
    session.add(NovelModel(**novel_to_orm(sample_novel)))
    await session.commit()

    task = CompressionTaskModel(
        task_id=str(uuid4()),
        novel_id=str(sample_novel.id),
        range_start=1,
        range_end=10,
        status="pending",
    )
    session.add(task)
    await session.commit()

    loaded = await session.get(CompressionTaskModel, task.id)
    assert loaded is not None
    assert loaded.range_start == 1
    assert loaded.range_end == 10


@pytest.mark.asyncio
async def test_orm_memory_archive(session, sample_novel):
    """MemoryArchiveModel CRUD。"""
    session.add(NovelModel(**novel_to_orm(sample_novel)))
    await session.commit()

    archive = MemoryArchiveModel(
        novel_id=str(sample_novel.id),
        tier="l0_index",
        data={"entries": []},
    )
    session.add(archive)
    await session.commit()

    loaded = await session.get(MemoryArchiveModel, archive.id)
    assert loaded is not None
    assert loaded.tier == "l0_index"


@pytest.mark.asyncio
async def test_orm_processed_digest(session):
    """ProcessedDigestModel CRUD。"""
    digest = hashlib.sha256(b"test_data").hexdigest()
    entry = ProcessedDigestModel(
        digest=digest,
        packet_id=str(uuid4()),
    )
    session.add(entry)
    await session.commit()

    loaded = await session.get(ProcessedDigestModel, digest)
    assert loaded is not None
    assert loaded.packet_id is not None


# ═══════════════════════════════════════════════════════
#  3. Serializers
# ═══════════════════════════════════════════════════════


class TestSerializers:
    def test_novel_roundtrip(self, sample_novel):
        """Novel → ORM dict → Novel。"""
        orm_data = novel_to_orm(sample_novel)
        loaded = dict_to_novel(novel_to_dict(NovelModel(**orm_data)))
        assert loaded.id == sample_novel.id
        assert loaded.title == sample_novel.title
        assert loaded.metadata.description == sample_novel.metadata.description

    def test_chapter_roundtrip(self, sample_chapter):
        """Chapter → ORM dict → Chapter。"""
        orm_data = chapter_to_orm(sample_chapter)
        model = ChapterModel(**orm_data)
        dict_data = chapter_to_dict(model)
        loaded = dict_to_chapter(dict_data)
        assert loaded.id == sample_chapter.id
        assert loaded.title == "第一章"
        assert loaded.key_events == ["事件A", "事件B"]

    def test_chapter_version_roundtrip(self, sample_chapter):
        """ChapterVersion → ORM dict → ChapterVersion。"""
        ver = ChapterVersion(
            id=uuid4(),
            chapter_id=sample_chapter.id,
            novel_id=sample_chapter.novel_id,
            version=1,
            index=1,
            title="第一章",
            content="正文 v1",
            summary="摘要",
            key_events=["事件A"],
            source_trace="model",
            is_baseline=True,
            content_digest="abc123",
        )
        orm_data = chapter_version_to_orm(ver)
        model = ChapterVersionModel(**orm_data)
        dict_data = chapter_version_to_dict(model)
        loaded = dict_to_chapter_version(dict_data)
        assert loaded.id == ver.id
        assert loaded.version == 1
        assert loaded.is_baseline is True

    def test_character_roundtrip(self, sample_character):
        """Character → ORM dict → Character。"""
        orm_data = character_to_orm(sample_character)
        model = CharacterModel(**orm_data)
        dict_data = character_to_dict(model)
        loaded = dict_to_character(dict_data)
        assert loaded.name == "张三"
        assert loaded.role == "protagonist"
        assert len(loaded.timeline) == 1
        assert loaded.timeline[0].chapter_index == 1

    def test_world_roundtrip(self, sample_world):
        """World → ORM dict → World。"""
        orm_data = world_to_orm(sample_world)
        model = WorldModel(**orm_data)
        dict_data = world_to_dict(model)
        loaded = dict_to_world(dict_data)
        assert loaded.title == "奇幻大陆"
        assert loaded.power_system is not None
        assert loaded.power_system.name == "斗气"
        assert loaded.magic_system is not None
        assert loaded.magic_system.name == "元素魔法"
        assert len(loaded.factions) == 1
        assert loaded.factions[0].name == "光明教会"
        assert len(loaded.location_tree) == 2

    def test_pipeline_roundtrip(self, sample_pipeline):
        """PipelineState → ORM dict → PipelineState。"""
        orm_data = pipeline_state_to_orm(sample_pipeline)
        model = PipelineStateModel(**orm_data)
        dict_data = pipeline_state_to_dict(model)
        loaded = dict_to_pipeline_state(dict_data)
        assert loaded.total_chapters == 3
        assert loaded.chapters[1] == ChapterStatus.PLANNED

    def test_pipeline_without_current(self, sample_novel):
        """current_chapter_index 为 None 时正确反序列化。"""
        state = PipelineState(
            novel_id=sample_novel.id,
            total_chapters=0,
            chapters={},
            current_chapter_index=None,
        )
        orm_data = pipeline_state_to_orm(state)
        model = PipelineStateModel(**orm_data)
        dict_data = pipeline_state_to_dict(model)
        loaded = dict_to_pipeline_state(dict_data)
        assert loaded.current_chapter_index is None

    def test_empty_world(self, sample_novel):
        """空世界观。"""
        world = World(
            id=uuid4(),
            novel_id=sample_novel.id,
            title="空世界",
        )
        orm_data = world_to_orm(world)
        model = WorldModel(**orm_data)
        dict_data = world_to_dict(model)
        loaded = dict_to_world(dict_data)
        assert loaded.power_system is None
        assert loaded.magic_system is None
        assert loaded.factions == []
        assert loaded.location_tree == []


# ═══════════════════════════════════════════════════════
#  4. Idempotency
# ═══════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_idempotency_not_duplicate(session):
    """新 digest 不应被标记为重复。"""
    guard = IdempotencyGuard(session)
    dup = await guard.is_duplicate(
        hashlib.sha256(b"new_data").hexdigest()
    )
    assert dup is False


@pytest.mark.asyncio
async def test_idempotency_mark_and_check(session):
    """标记后应为重复。"""
    guard = IdempotencyGuard(session)
    digest = hashlib.sha256(b"mark_test").hexdigest()
    packet_id = uuid4()

    await guard.mark_processed(digest, packet_id)
    await session.commit()

    dup = await guard.is_duplicate(digest)
    assert dup is True


@pytest.mark.asyncio
async def test_idempotency_multiple_digests(session):
    """多个 digest 独立。"""
    guard = IdempotencyGuard(session)
    d1 = hashlib.sha256(b"data1").hexdigest()
    d2 = hashlib.sha256(b"data2").hexdigest()

    await guard.mark_processed(d1, uuid4())
    await session.commit()

    assert await guard.is_duplicate(d1) is True
    assert await guard.is_duplicate(d2) is False


@pytest.mark.asyncio
async def test_idempotency_same_digest_twice(session):
    """同一 digest 标记两次不应报错。"""
    guard = IdempotencyGuard(session)
    digest = hashlib.sha256(b"twice").hexdigest()
    packet_id = uuid4()

    await guard.mark_processed(digest, packet_id)
    await session.commit()

    # 第二次标记（理论上主键会冲突，但 SQLite INSERT or REPLACE 解决）
    await guard.mark_processed(digest, packet_id)
    await session.commit()

    assert await guard.is_duplicate(digest) is True


# ═══════════════════════════════════════════════════════
#  5. Repositories
# ═══════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_novel_repository(
    session, sample_novel, sample_chapter
):
    """NovelRepository 基本 CRUD。"""
    repo = NovelRepository(session)

    # Create
    await repo.save(sample_novel)
    await session.commit()

    # Read
    loaded = await repo.get_by_id(sample_novel.id)
    assert loaded is not None
    assert loaded.title == "测试小说"

    # Update
    loaded.title = "更新标题"
    await repo.save(loaded)
    await session.commit()

    loaded2 = await repo.get_by_id(sample_novel.id)
    assert loaded2.title == "更新标题"


@pytest.mark.asyncio
async def test_novel_repository_get_all(
    session, db
):
    """get_all 返回所有小说。"""
    repo = NovelRepository(session)
    n1 = Novel(title="小说A", id=uuid4())
    n2 = Novel(title="小说B", id=uuid4())
    await repo.save(n1)
    await repo.save(n2)
    await session.commit()

    all_novels = await repo.get_all()
    assert len(all_novels) >= 2
    titles = [n.title for n in all_novels]
    assert "小说A" in titles
    assert "小说B" in titles


@pytest.mark.asyncio
async def test_chapter_repository(
    session, sample_novel, sample_chapter
):
    """ChapterRepository CRUD。"""
    # 先插入 novel
    await session.execute(
        NovelModel.__table__.insert().values(
            uuid=str(sample_novel.id),
            title=sample_novel.title,
        )
    )
    await session.commit()

    repo = ChapterRepository(session)

    # Create
    await repo.save(sample_chapter)
    await session.commit()

    # Read by id
    loaded = await repo.get_by_id(sample_chapter.id)
    assert loaded is not None
    assert loaded.title == "第一章"

    # Read by novel + index
    loaded2 = await repo.get_by_novel_and_index(
        sample_novel.id, 1
    )
    assert loaded2 is not None
    assert loaded2.title == "第一章"

    # Update status
    await repo.update_status(
        sample_novel.id, 1, "draft_ready"
    )
    await session.commit()

    loaded3 = await repo.get_by_id(sample_chapter.id)
    assert loaded3.status == ChapterStatus.DRAFT_READY


@pytest.mark.asyncio
async def test_chapter_repository_get_chapters_by_novel(
    session, sample_novel
):
    """get_chapters_by_novel 按 index 排序。"""
    await session.execute(
        NovelModel.__table__.insert().values(
            uuid=str(sample_novel.id),
            title=sample_novel.title,
        )
    )
    await session.commit()

    repo = ChapterRepository(session)
    ch1 = Chapter(
        id=uuid4(),
        novel_id=sample_novel.id,
        index=2,
        title="第二章",
    )
    ch2 = Chapter(
        id=uuid4(),
        novel_id=sample_novel.id,
        index=1,
        title="第一章",
    )
    await repo.save(ch1)
    await repo.save(ch2)
    await session.commit()

    chapters = await repo.get_chapters_by_novel(sample_novel.id)
    assert len(chapters) == 2
    assert chapters[0].title == "第一章"  # index=1 first
    assert chapters[1].title == "第二章"  # index=2 second


@pytest.mark.asyncio
async def test_character_repository(
    session, sample_novel, sample_character
):
    """CharacterRepository CRUD。"""
    await session.execute(
        NovelModel.__table__.insert().values(
            uuid=str(sample_novel.id),
            title=sample_novel.title,
        )
    )
    await session.commit()

    repo = CharacterRepository(session)

    await repo.save(sample_character)
    await session.commit()

    loaded = await repo.get_by_id(sample_character.id)
    assert loaded is not None
    assert loaded.name == "张三"

    characters = await repo.get_by_novel(sample_novel.id)
    assert len(characters) == 1


@pytest.mark.asyncio
async def test_world_repository(
    session, sample_novel, sample_world
):
    """WorldRepository CRUD。"""
    await session.execute(
        NovelModel.__table__.insert().values(
            uuid=str(sample_novel.id),
            title=sample_novel.title,
        )
    )
    await session.commit()

    repo = WorldRepository(session)

    await repo.save(sample_world)
    await session.commit()

    loaded = await repo.get_by_novel(sample_novel.id)
    assert loaded is not None
    assert loaded.title == "奇幻大陆"


@pytest.mark.asyncio
async def test_pipeline_repository(
    session, sample_novel, sample_pipeline
):
    """PipelineStateRepository CRUD。"""
    await session.execute(
        NovelModel.__table__.insert().values(
            uuid=str(sample_novel.id),
            title=sample_novel.title,
        )
    )
    await session.commit()

    repo = PipelineStateRepository(session)

    await repo.save(sample_pipeline)
    await session.commit()

    loaded = await repo.get_by_novel(sample_novel.id)
    assert loaded is not None
    assert loaded.total_chapters == 3
    assert loaded.chapters[1] == ChapterStatus.PLANNED


# ═══════════════════════════════════════════════════════
#  6. UnitOfWork
# ═══════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_t1_writer_complete_chapter(
    session, sample_novel, sample_chapter
):
    """T1: Writer 完成章节。"""
    await session.execute(
        NovelModel.__table__.insert().values(
            uuid=str(sample_novel.id),
            title=sample_novel.title,
        )
    )
    await session.commit()

    uow = UnitOfWork(session)

    is_dup, _ = await uow.t1_writer_complete_chapter(
        sample_chapter
    )
    await session.commit()
    assert is_dup is False

    # 章节状态应为 DRAFT_READY
    result = await session.execute(
        select(ChapterModel).where(ChapterModel.uuid == str(sample_chapter.id))
    )
    loaded = result.scalar_one_or_none()
    assert loaded.status == "draft_ready"


@pytest.mark.asyncio
async def test_t1_writer_twice_is_dup(
    session, sample_novel, sample_chapter
):
    """T1: 相同内容第二次提交应为重复。"""
    await session.execute(
        NovelModel.__table__.insert().values(
            uuid=str(sample_novel.id),
            title=sample_novel.title,
        )
    )
    await session.commit()

    uow = UnitOfWork(session)

    # 第一次提交
    is_dup1, digest = await uow.t1_writer_complete_chapter(
        sample_chapter
    )
    await session.commit()
    assert is_dup1 is False

    # 第二次提交（相同内容）
    is_dup2, _ = await uow.t1_writer_complete_chapter(
        sample_chapter
    )
    await session.commit()
    assert is_dup2 is True


@pytest.mark.asyncio
async def test_t2_planner_complete_planning(
    session, sample_novel
):
    """T2: Planner 完成批量规划。"""
    await session.execute(
        NovelModel.__table__.insert().values(
            uuid=str(sample_novel.id),
            title=sample_novel.title,
        )
    )
    await session.commit()

    uow = UnitOfWork(session)
    chapters = [
        Chapter(
            id=uuid4(),
            novel_id=sample_novel.id,
            index=i,
            title=f"第{i}章",
        )
        for i in range(1, 4)
    ]
    pipeline = PipelineState(
        novel_id=sample_novel.id,
        total_chapters=3,
        chapters={i: ChapterStatus.PLANNED for i in range(1, 4)},
        current_chapter_index=1,
    )

    await uow.t2_planner_complete_planning(chapters, pipeline)
    await session.commit()

    # 验证章节已创建
    for ch in chapters:
        result = await session.execute(
            select(ChapterModel).where(ChapterModel.uuid == str(ch.id))
        )
        loaded = result.scalar_one_or_none()
        assert loaded is not None
        assert loaded.status == "planned"

    # 验证 pipeline
    result = await session.execute(
        select(PipelineStateModel).where(PipelineStateModel.novel_id == str(sample_novel.id))
    )
    pipeline_loaded = result.scalar_one_or_none()
    assert pipeline_loaded.total_chapters == 3


@pytest.mark.asyncio
async def test_t3_editor_approve(
    session, sample_novel, sample_chapter
):
    """T3: Editor 批准章节。"""
    await session.execute(
        NovelModel.__table__.insert().values(
            uuid=str(sample_novel.id),
            title=sample_novel.title,
        )
    )
    await session.commit()

    # 先保存章节
    repo = ChapterRepository(session)
    await repo.save(sample_chapter)
    await session.commit()

    uow = UnitOfWork(session)
    await uow.t3_editor_complete_review(
        novel_id=sample_novel.id,
        chapter_index=1,
        is_approved=True,
        is_baseline=True,
    )
    await session.commit()

    loaded = await repo.get_by_novel_and_index(
        sample_novel.id, 1
    )
    assert loaded.status == ChapterStatus.APPROVED
    assert loaded.is_baseline is True


@pytest.mark.asyncio
async def test_t3_editor_reject(
    session, sample_novel, sample_chapter
):
    """T3: Editor 驳回章节。"""
    await session.execute(
        NovelModel.__table__.insert().values(
            uuid=str(sample_novel.id),
            title=sample_novel.title,
        )
    )
    await session.commit()

    repo = ChapterRepository(session)
    await repo.save(sample_chapter)
    await session.commit()

    uow = UnitOfWork(session)
    await uow.t3_editor_complete_review(
        novel_id=sample_novel.id,
        chapter_index=1,
        is_approved=False,
    )
    await session.commit()

    loaded = await repo.get_by_novel_and_index(
        sample_novel.id, 1
    )
    assert loaded.status == ChapterStatus.REVISING


@pytest.mark.asyncio
async def test_t4_memory_compression(
    session, sample_novel
):
    """T4: MemoryKeeper 完成压缩。"""
    await session.execute(
        NovelModel.__table__.insert().values(
            uuid=str(sample_novel.id),
            title=sample_novel.title,
        )
    )
    await session.commit()

    # 先创建压缩任务
    task_id = uuid4()
    session.add(
        CompressionTaskModel(
            task_id=str(task_id),
            novel_id=str(sample_novel.id),
            range_start=1,
            range_end=10,
            status="running",
        )
    )
    await session.commit()

    uow = UnitOfWork(session)

    await uow.t4_memory_keeper_complete_compression(
        novel_id=sample_novel.id,
        compressed_data={
            "entries": [
                {
                    "range": {"start_chapter": 1, "end_chapter": 10},
                    "summary": "总摘要",
                    "events": [{"chapter": 1, "event": "事件"}],
                }
            ]
        },
        task_id=task_id,
        task_update={
            "status": "completed",
            "completed_at": datetime.now(timezone.utc),
        },
    )
    await session.commit()

    # 验证 L2 archive 存在
    from sqlalchemy import select as sa_select

    result = await session.execute(
        sa_select(MemoryArchiveModel).where(
            MemoryArchiveModel.novel_id == str(sample_novel.id),
            MemoryArchiveModel.tier == "l2_compressed",
        )
    )
    archive = result.scalar_one_or_none()
    assert archive is not None

    # 验证任务已标记完成
    result = await session.execute(
        sa_select(CompressionTaskModel).where(
            CompressionTaskModel.task_id == str(task_id)
        )
    )
    task = result.scalar_one_or_none()
    assert task.status == "completed"


@pytest.mark.asyncio
async def test_create_compression_task_and_commit(session, sample_novel):
    """UoW.create_compression_task + commit：T4 前置任务创建的封装（ADR-0009）。"""
    await session.execute(
        NovelModel.__table__.insert().values(
            uuid=str(sample_novel.id),
            title=sample_novel.title,
        )
    )
    await session.commit()

    uow = UnitOfWork(session)
    task_id = uuid4()
    await uow.create_compression_task(
        task_id=task_id,
        novel_id=sample_novel.id,
        range_start=1,
        range_end=1,
    )
    await uow.commit()

    from sqlalchemy import select as sa_select

    result = await session.execute(
        sa_select(CompressionTaskModel).where(
            CompressionTaskModel.task_id == str(task_id)
        )
    )
    task = result.scalar_one_or_none()
    assert task is not None
    assert task.status == "running"
    assert task.range_start == 1
    assert task.range_end == 1


@pytest.mark.asyncio
async def test_t5_window_shift(session, sample_novel):
    """T5: 滑窗更新。"""
    await session.execute(
        NovelModel.__table__.insert().values(
            uuid=str(sample_novel.id),
            title=sample_novel.title,
        )
    )
    await session.commit()

    uow = UnitOfWork(session)
    await uow.t5_window_shift(
        novel_id=sample_novel.id,
        sliding_window_state={
            "start_chapter": 1,
            "end_chapter": 5,
            "total_active": 5,
        },
        l1_snapshot={
            "chapters": [1, 2, 3, 4, 5],
            "key_events": ["事件A"],
        },
    )
    await session.commit()

    from sqlalchemy import select as sa_select

    result = await session.execute(
        sa_select(MemoryArchiveModel).where(
            MemoryArchiveModel.novel_id == str(sample_novel.id),
            MemoryArchiveModel.tier == "l1_active",
        )
    )
    archive = result.scalar_one_or_none()
    assert archive is not None
    assert archive.data["sliding_window"]["start_chapter"] == 1


@pytest.mark.asyncio
async def test_uow_transaction_rollback(session, sample_novel):
    """事务内异常应回滚。"""
    uow = UnitOfWork(session)
    await uow.novels.save(sample_novel)

    # 模拟异常
    try:
        async with uow.transaction():
            raise ValueError("模拟错误")
    except ValueError:
        pass

    # 验证 novel 没有被持久化
    result = await session.execute(
        select(NovelModel).where(NovelModel.uuid == str(sample_novel.id))
    )
    loaded = result.scalar_one_or_none()
    assert loaded is None


# ═══════════════════════════════════════════════════════
#  7. Snapshot
# ═══════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_snapshot_dump_restore(
    session, sample_novel, sample_chapter
):
    """JSON dump 和 restore 的完整生命周期。"""
    # 先写入数据
    session.add(NovelModel(**novel_to_orm(sample_novel)))
    session.add(ChapterModel(**chapter_to_orm(sample_chapter)))
    await session.commit()

    snapshot = JSONSnapshot(session)

    with tempfile.NamedTemporaryFile(
        suffix=".json", delete=False, mode="w", encoding="utf-8"
    ) as f:
        output_path = f.name

    try:
        # Dump
        exported = await snapshot.dump(
            sample_novel.id, output_path
        )
        assert Path(output_path).exists()

        # 验证导出内容
        raw = json.loads(
            Path(output_path).read_text(encoding="utf-8")
        )
        assert raw["novel_id"] == str(sample_novel.id)
        assert raw["novel"]["title"] == "测试小说"
        assert len(raw["chapters"]) == 1
        assert raw["chapters"][0]["title"] == "第一章"

        # Restore（先清理）
        await session.execute(
            NovelModel.__table__.delete().where(
                NovelModel.uuid == str(sample_novel.id)
            )
        )
        await session.commit()

        restored_id = await snapshot.restore(output_path)
        assert restored_id == sample_novel.id

    finally:
        os.unlink(output_path)


# ═══════════════════════════════════════════════════════
#  8. Recovery
# ═══════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_recovery_basic(
    session, sample_novel, sample_pipeline
):
    """Recovery: 基本恢复流程。"""
    # 写入数据
    session.add(NovelModel(**novel_to_orm(sample_novel)))
    session.add(
        PipelineStateModel(**pipeline_state_to_orm(sample_pipeline))
    )
    session.add(
        MemoryArchiveModel(
            novel_id=str(sample_novel.id),
            tier="l2_compressed",
            data={
                "entries": [
                    {
                        "range": {"start_chapter": 1, "end_chapter": 5},
                        "summary": "总摘要",
                        "events": [{"chapter": 1, "event": "事件"}],
                    }
                ]
            },
        )
    )
    session.add(
        MemoryArchiveModel(
            novel_id=str(sample_novel.id),
            tier="l1_active",
            data={
                "sliding_window": {
                    "novel_id": str(sample_novel.id),
                    "current_chapter_index": 1,
                    "default_window_size": 5,
                    "current_expanded_size": 5,
                    "recent_chapters": [1, 2, 3],
                },
                "snapshot": {},
            },
        )
    )
    session.add(
        CompressionTaskModel(
            task_id=str(uuid4()),
            novel_id=str(sample_novel.id),
            range_start=6,
            range_end=15,
            status="running",
        )
    )
    session.add(
        CompressionTaskModel(
            task_id=str(uuid4()),
            novel_id=str(sample_novel.id),
            range_start=1,
            range_end=5,
            status="completed",
        )
    )
    await session.commit()

    recovery = RecoveryManager(session)
    state = await recovery.recover(sample_novel.id)

    assert state.novel_id == sample_novel.id
    assert state.l2_archive is not None
    assert state.sliding_window is not None
    assert state.sliding_window.current_chapter_index == 1
    assert state.pipeline_state is not None
    assert state.pipeline_state.total_chapters == 3

    # 只有 PENDING/RUNNING 任务被恢复
    assert len(state.pending_tasks) == 1
    assert state.has_pending_work is True

    # 验证 RUNNING 任务被重置为 PENDING
    from sqlalchemy import select as sa_select

    result = await session.execute(
        sa_select(CompressionTaskModel).where(
            CompressionTaskModel.novel_id == str(sample_novel.id),
            CompressionTaskModel.status == "running",
        )
    )
    running = result.scalars().all()
    assert len(running) == 0

    result = await session.execute(
        sa_select(CompressionTaskModel).where(
            CompressionTaskModel.novel_id == str(sample_novel.id),
            CompressionTaskModel.status == "pending",
        )
    )
    pending = result.scalars().all()
    assert len(pending) == 1  # 只有之前 running 的


@pytest.mark.asyncio
async def test_recovery_empty(session):
    """Recovery: 不存在的 novel 返回空状态。"""
    recovery = RecoveryManager(session)
    state = await recovery.recover(uuid4())
    assert state.l0_index is None
    assert state.l2_archive is None
    assert state.pending_tasks == []
    assert state.has_pending_work is False


# ═══════════════════════════════════════════════════════
#  9. Compute Digest
# ═══════════════════════════════════════════════════════


def test_compute_content_digest():
    d1 = compute_content_digest("hello world")
    d2 = compute_content_digest("hello world")
    d3 = compute_content_digest("hello world!")

    assert d1 == d2
    assert d1 != d3
    assert len(d1) == 64  # SHA256 hex
