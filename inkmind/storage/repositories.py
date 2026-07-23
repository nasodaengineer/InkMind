"""Repository 层。

每个核心实体对应一个 Repository 类。Repository 在 UnitOfWork 内工作，
不直接管理事务提交。
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from datetime import datetime, timezone

from inkmind.models.agent import PipelineState
from inkmind.models.chapter import Chapter, ChapterVersion
from inkmind.models.character import Character
from inkmind.models.materials import MaterialChunk, MaterialFragment, MaterialSource
from inkmind.models.novel import Novel, OutlineSpine, Volume
from inkmind.models.run import Run
from inkmind.models.world import World
from inkmind.storage.models import (
    AppSettingsModel,
    ChapterModel,
    ChapterVersionModel,
    CharacterModel,
    CompressionTaskModel,
    MaterialChunkModel,
    MaterialFragmentModel,
    MaterialSourceModel,
    NovelModel,
    OutlineSpineModel,
    PipelineStateModel,
    ProviderStatsModel,
    RunsModel,
    VolumeModel,
    WorldModel,
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
    dict_to_outline_spine,
    dict_to_pipeline_state,
    dict_to_run,
    dict_to_volume,
    dict_to_world,
    novel_to_dict,
    novel_to_orm,
    outline_spine_to_dict,
    outline_spine_to_orm,
    pipeline_state_to_dict,
    pipeline_state_to_orm,
    run_to_dict,
    run_to_orm,
    volume_to_dict,
    volume_to_orm,
    world_to_dict,
    world_to_orm,
)


class NovelRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def save(self, novel: Novel) -> None:
        data = novel_to_orm(novel)
        result = await self._session.execute(
            select(NovelModel).where(NovelModel.uuid == str(novel.id))
        )
        existing = result.scalar_one_or_none()
        if existing:
            for k, v in data.items():
                setattr(existing, k, v)
        else:
            self._session.add(NovelModel(**data))

    async def get_by_id(self, novel_id: UUID) -> Novel | None:
        result = await self._session.execute(
            select(NovelModel).where(NovelModel.uuid == str(novel_id))
        )
        model = result.scalar_one_or_none()
        if model is None:
            return None
        return dict_to_novel(novel_to_dict(model))

    async def get_all(self) -> list[Novel]:
        result = await self._session.execute(
            select(NovelModel).order_by(NovelModel.created_at.desc())
        )
        models = result.scalars().all()
        return [dict_to_novel(novel_to_dict(m)) for m in models]

    async def delete(self, novel_id: UUID) -> bool:
        result = await self._session.execute(
            select(NovelModel).where(NovelModel.uuid == str(novel_id))
        )
        model = result.scalar_one_or_none()
        if model is None:
            return False
        await self._session.delete(model)
        return True


class ChapterRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def _ensure_volume_id(self, chapter: Chapter) -> None:
        """确保 chapter 有 volume_id（NOT NULL 约束）。"""
        if chapter.volume_id is not None:
            return
        result = await self._session.execute(
            select(VolumeModel)
            .where(VolumeModel.novel_id == str(chapter.novel_id))
            .order_by(VolumeModel.volume_index)
            .limit(1)
        )
        vol = result.scalar_one_or_none()
        if vol is None:
            from uuid import uuid4

            vol = VolumeModel(
                uuid=str(uuid4()),
                novel_id=str(chapter.novel_id),
                volume_index=1,
                title="默认卷",
                planned_size=100,
            )
            self._session.add(vol)
            await self._session.flush()
        chapter.volume_id = UUID(vol.uuid)

    async def save(self, chapter: Chapter) -> None:
        await self._ensure_volume_id(chapter)
        data = chapter_to_orm(chapter)
        result = await self._session.execute(
            select(ChapterModel).where(ChapterModel.uuid == str(chapter.id))
        )
        existing = result.scalar_one_or_none()
        if existing:
            for k, v in data.items():
                setattr(existing, k, v)
        else:
            self._session.add(ChapterModel(**data))

    async def save_version(self, version: ChapterVersion) -> None:
        data = chapter_version_to_orm(version)
        self._session.add(ChapterVersionModel(**data))

    async def get_by_id(self, chapter_id: UUID) -> Chapter | None:
        result = await self._session.execute(
            select(ChapterModel).where(ChapterModel.uuid == str(chapter_id))
        )
        model = result.scalar_one_or_none()
        if model is None:
            return None
        return dict_to_chapter(chapter_to_dict(model))

    async def get_by_novel_and_index(self, novel_id: UUID, index: int) -> Chapter | None:
        result = await self._session.execute(
            select(ChapterModel).where(
                ChapterModel.novel_id == str(novel_id),
                ChapterModel.chapter_index == index,
            )
        )
        model = result.scalar_one_or_none()
        if model is None:
            return None
        return dict_to_chapter(chapter_to_dict(model))

    async def get_chapters_by_novel(self, novel_id: UUID) -> list[Chapter]:
        result = await self._session.execute(
            select(ChapterModel)
            .where(ChapterModel.novel_id == str(novel_id))
            .order_by(ChapterModel.chapter_index)
        )
        models = result.scalars().all()
        return [dict_to_chapter(chapter_to_dict(m)) for m in models]

    async def get_versions(self, chapter_id: UUID) -> list[ChapterVersion]:
        result = await self._session.execute(
            select(ChapterVersionModel)
            .where(ChapterVersionModel.chapter_id == str(chapter_id))
            .order_by(ChapterVersionModel.version.desc())
        )
        models = result.scalars().all()
        return [dict_to_chapter_version(chapter_version_to_dict(m)) for m in models]

    async def update_status(self, novel_id: UUID, chapter_index: int, status: str) -> None:
        await self._session.execute(
            update(ChapterModel)
            .where(
                ChapterModel.novel_id == str(novel_id),
                ChapterModel.chapter_index == chapter_index,
            )
            .values(status=status)
        )

    async def get_chapters_by_volume(self, novel_id: UUID, volume_id: UUID) -> list[Chapter]:
        result = await self._session.execute(
            select(ChapterModel)
            .where(
                ChapterModel.novel_id == str(novel_id),
                ChapterModel.volume_id == str(volume_id),
            )
            .order_by(ChapterModel.chapter_index)
        )
        models = result.scalars().all()
        return [dict_to_chapter(chapter_to_dict(m)) for m in models]

    async def count_by_volume(self, volume_id: UUID) -> int:
        result = await self._session.execute(
            select(ChapterModel).where(
                ChapterModel.volume_id == str(volume_id),
                ChapterModel.is_deleted == False,
            )
        )
        return len(result.scalars().all())

    async def patch_outline(
        self,
        novel_id: UUID,
        chapter_index: int,
        fields: dict,
    ) -> Chapter | None:
        """更新章纲字段（title/summary/key_events/rhythm_marker/pov/involved）。"""
        result = await self._session.execute(
            select(ChapterModel).where(
                ChapterModel.novel_id == str(novel_id),
                ChapterModel.chapter_index == chapter_index,
            )
        )
        model = result.scalar_one_or_none()
        if model is None:
            return None
        allowed = {
            "title",
            "summary",
            "key_events",
            "rhythm_marker",
            "pov",
            "involved",
        }
        for k, v in fields.items():
            if k in allowed:
                setattr(model, k, v)
        await self._session.flush()
        await self._session.refresh(model)
        return dict_to_chapter(chapter_to_dict(model))


class CharacterRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def save(self, character: Character) -> None:
        data = character_to_orm(character)
        result = await self._session.execute(
            select(CharacterModel).where(CharacterModel.uuid == str(character.id))
        )
        existing = result.scalar_one_or_none()
        if existing:
            for k, v in data.items():
                setattr(existing, k, v)
        else:
            self._session.add(CharacterModel(**data))

    async def get_by_id(self, character_id: UUID) -> Character | None:
        result = await self._session.execute(
            select(CharacterModel).where(CharacterModel.uuid == str(character_id))
        )
        model = result.scalar_one_or_none()
        if model is None:
            return None
        return dict_to_character(character_to_dict(model))

    async def get_by_novel(self, novel_id: UUID) -> list[Character]:
        result = await self._session.execute(
            select(CharacterModel).where(CharacterModel.novel_id == str(novel_id))
        )
        models = result.scalars().all()
        return [dict_to_character(character_to_dict(m)) for m in models]


class WorldRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def save(self, world: World) -> None:
        data = world_to_orm(world)
        result = await self._session.execute(
            select(WorldModel).where(WorldModel.uuid == str(world.id))
        )
        existing = result.scalar_one_or_none()
        if existing:
            for k, v in data.items():
                setattr(existing, k, v)
        else:
            self._session.add(WorldModel(**data))

    async def get_by_id(self, world_id: UUID) -> World | None:
        result = await self._session.execute(
            select(WorldModel).where(WorldModel.uuid == str(world_id))
        )
        model = result.scalar_one_or_none()
        if model is None:
            return None
        return dict_to_world(world_to_dict(model))

    async def get_by_novel(self, novel_id: UUID) -> World | None:
        result = await self._session.execute(
            select(WorldModel).where(WorldModel.novel_id == str(novel_id))
        )
        model = result.scalar_one_or_none()
        if model is None:
            return None
        return dict_to_world(world_to_dict(model))


class PipelineStateRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def save(self, state: PipelineState) -> None:
        data = pipeline_state_to_orm(state)
        result = await self._session.execute(
            select(PipelineStateModel).where(PipelineStateModel.novel_id == str(state.novel_id))
        )
        existing = result.scalar_one_or_none()
        if existing:
            for k, v in data.items():
                setattr(existing, k, v)
        else:
            self._session.add(PipelineStateModel(**data))

    async def get_by_novel(self, novel_id: UUID) -> PipelineState | None:
        result = await self._session.execute(
            select(PipelineStateModel).where(PipelineStateModel.novel_id == str(novel_id))
        )
        model = result.scalar_one_or_none()
        if model is None:
            return None
        return dict_to_pipeline_state(pipeline_state_to_dict(model))

    async def delete(self, novel_id: UUID) -> bool:
        result = await self._session.execute(
            select(PipelineStateModel).where(PipelineStateModel.novel_id == str(novel_id))
        )
        model = result.scalar_one_or_none()
        if model is None:
            return False
        await self._session.delete(model)
        return True


class RunRepository:
    """Run 执行生命周期仓储。"""

    def __init__(self, session: AsyncSession):
        self._session = session

    async def save(self, run: Run) -> None:
        data = run_to_orm(run)
        result = await self._session.execute(select(RunsModel).where(RunsModel.uuid == str(run.id)))
        existing = result.scalar_one_or_none()
        if existing:
            for k, v in data.items():
                setattr(existing, k, v)
        else:
            self._session.add(RunsModel(**data))

    async def get_by_id(self, run_id: UUID) -> Run | None:
        result = await self._session.execute(select(RunsModel).where(RunsModel.uuid == str(run_id)))
        model = result.scalar_one_or_none()
        if model is None:
            return None
        return dict_to_run(run_to_dict(model))

    async def get_by_chapter(self, chapter_id: UUID) -> list[Run]:
        result = await self._session.execute(
            select(RunsModel)
            .where(RunsModel.chapter_id == str(chapter_id))
            .order_by(RunsModel.created_at.desc())
        )
        models = result.scalars().all()
        return [dict_to_run(run_to_dict(m)) for m in models]

    async def get_by_novel(self, novel_id: UUID) -> list[Run]:
        result = await self._session.execute(
            select(RunsModel)
            .where(RunsModel.novel_id == str(novel_id))
            .order_by(RunsModel.created_at.desc())
        )
        models = result.scalars().all()
        return [dict_to_run(run_to_dict(m)) for m in models]

    async def get_running_for_chapter(self, chapter_id: UUID) -> Run | None:
        """获取指定章节当前正在执行的 Run（如果有）。"""
        result = await self._session.execute(
            select(RunsModel).where(
                RunsModel.chapter_id == str(chapter_id),
                RunsModel.status == "running",
            )
        )
        model = result.scalar_one_or_none()
        if model is None:
            return None
        return dict_to_run(run_to_dict(model))

    async def update_status(self, run_id: UUID, status: str) -> None:
        await self._session.execute(
            update(RunsModel).where(RunsModel.uuid == str(run_id)).values(status=status)
        )

    async def set_phase(self, run_id: UUID, phase: str) -> None:
        await self._session.execute(
            update(RunsModel).where(RunsModel.uuid == str(run_id)).values(phase=phase)
        )

    async def get_all_running(self) -> list[Run]:
        """获取所有 status=running 的 Run（用于恢复）。"""
        result = await self._session.execute(select(RunsModel).where(RunsModel.status == "running"))
        models = result.scalars().all()
        return [dict_to_run(run_to_dict(m)) for m in models]


class VolumeRepository:
    """卷仓库。"""

    def __init__(self, session: AsyncSession):
        self._session = session

    async def save(self, volume: Volume) -> None:
        data = volume_to_orm(volume)
        result = await self._session.execute(
            select(VolumeModel).where(VolumeModel.uuid == str(volume.id))
        )
        existing = result.scalar_one_or_none()
        if existing:
            for k, v in data.items():
                setattr(existing, k, v)
        else:
            self._session.add(VolumeModel(**data))

    async def get_by_id(self, volume_id: UUID) -> Volume | None:
        result = await self._session.execute(
            select(VolumeModel).where(VolumeModel.uuid == str(volume_id))
        )
        model = result.scalar_one_or_none()
        if model is None:
            return None
        return dict_to_volume(volume_to_dict(model))

    async def get_by_novel_and_index(self, novel_id: UUID, volume_index: int) -> Volume | None:
        result = await self._session.execute(
            select(VolumeModel).where(
                VolumeModel.novel_id == str(novel_id),
                VolumeModel.volume_index == volume_index,
            )
        )
        model = result.scalar_one_or_none()
        if model is None:
            return None
        return dict_to_volume(volume_to_dict(model))

    async def get_by_novel(self, novel_id: UUID) -> list[Volume]:
        result = await self._session.execute(
            select(VolumeModel)
            .where(VolumeModel.novel_id == str(novel_id))
            .order_by(VolumeModel.volume_index)
        )
        models = result.scalars().all()
        return [dict_to_volume(volume_to_dict(m)) for m in models]

    async def get_next_index(self, novel_id: UUID) -> int:
        """获取下一卷序号（MAX(volume_index)+1，无卷返回 1）。"""
        result = await self._session.execute(
            select(VolumeModel)
            .where(VolumeModel.novel_id == str(novel_id))
            .order_by(VolumeModel.volume_index.desc())
            .limit(1)
        )
        last = result.scalar_one_or_none()
        return (last.volume_index + 1) if last else 1

    async def delete(self, volume_id: UUID) -> bool:
        result = await self._session.execute(
            select(VolumeModel).where(VolumeModel.uuid == str(volume_id))
        )
        model = result.scalar_one_or_none()
        if model is None:
            return False
        await self._session.delete(model)
        return True

    async def count_chapters(self, volume_id: UUID) -> int:
        """统计卷内的章节数。"""
        result = await self._session.execute(
            select(ChapterModel).where(
                ChapterModel.volume_id == str(volume_id),
                ChapterModel.is_deleted == False,
            )
        )
        return len(result.scalars().all())


class OutlineSpineRepository:
    """总纲（书脊）仓库。"""

    def __init__(self, session: AsyncSession):
        self._session = session

    async def get_by_novel(self, novel_id: UUID) -> OutlineSpine | None:
        result = await self._session.execute(
            select(OutlineSpineModel).where(OutlineSpineModel.novel_id == str(novel_id))
        )
        model = result.scalar_one_or_none()
        if model is None:
            return None
        return dict_to_outline_spine(outline_spine_to_dict(model))

    async def upsert(self, spine: OutlineSpine) -> OutlineSpine:
        """创建或更新总纲。"""
        result = await self._session.execute(
            select(OutlineSpineModel).where(OutlineSpineModel.novel_id == str(spine.novel_id))
        )
        existing = result.scalar_one_or_none()
        data = outline_spine_to_orm(spine)
        if existing:
            # 复用已有的 uuid
            data.pop("uuid", None)
            for k, v in data.items():
                setattr(existing, k, v)
            return dict_to_outline_spine(outline_spine_to_dict(existing))
        else:
            model = OutlineSpineModel(**data)
            self._session.add(model)
            return spine


# ═══════════════════════════════════════════════════════
#  MaterialSourceRepository
# ═══════════════════════════════════════════════════════


class MaterialSourceRepository:
    """素材导入源 Repository。"""

    def __init__(self, session: AsyncSession):
        self._session = session

    async def save(self, source: MaterialSource) -> None:

        result = await self._session.execute(
            select(MaterialSourceModel).where(MaterialSourceModel.uuid == str(source.id))
        )
        existing = result.scalar_one_or_none()
        data = {
            "uuid": str(source.id),
            "novel_id": str(source.novel_id),
            "raw_text": source.raw_text,
            "content_digest": source.content_digest,
            "status": source.status,
            "word_count": source.word_count,
            "is_deleted": source.is_deleted,
        }
        if existing:
            for k, v in data.items():
                setattr(existing, k, v)
        else:
            self._session.add(MaterialSourceModel(**data))

    async def get_by_id(self, source_id: UUID) -> MaterialSource | None:

        result = await self._session.execute(
            select(MaterialSourceModel).where(MaterialSourceModel.uuid == str(source_id))
        )
        model = result.scalar_one_or_none()
        if model is None:
            return None
        return MaterialSource(
            id=UUID(model.uuid),
            novel_id=UUID(model.novel_id),
            raw_text=model.raw_text,
            content_digest=model.content_digest,
            status=model.status,
            word_count=model.word_count,
            created_at=model.created_at,
            is_deleted=model.is_deleted,
        )

    async def get_by_novel(self, novel_id: UUID) -> list[MaterialSource]:

        result = await self._session.execute(
            select(MaterialSourceModel)
            .where(
                MaterialSourceModel.novel_id == str(novel_id),
                MaterialSourceModel.is_deleted == False,
            )
            .order_by(MaterialSourceModel.created_at.desc())
        )
        models = result.scalars().all()
        return [
            MaterialSource(
                id=UUID(m.uuid),
                novel_id=UUID(m.novel_id),
                raw_text=m.raw_text,
                content_digest=m.content_digest,
                status=m.status,
                word_count=m.word_count,
                created_at=m.created_at,
                is_deleted=m.is_deleted,
            )
            for m in models
        ]

    async def find_by_digest(self, novel_id: UUID, content_digest: str) -> MaterialSource | None:

        result = await self._session.execute(
            select(MaterialSourceModel).where(
                MaterialSourceModel.novel_id == str(novel_id),
                MaterialSourceModel.content_digest == content_digest,
                MaterialSourceModel.is_deleted == False,
            )
        )
        model = result.scalar_one_or_none()
        if model is None:
            return None
        return MaterialSource(
            id=UUID(model.uuid),
            novel_id=UUID(model.novel_id),
            raw_text=model.raw_text,
            content_digest=model.content_digest,
            status=model.status,
            word_count=model.word_count,
            created_at=model.created_at,
            is_deleted=model.is_deleted,
        )

    async def soft_delete(self, source_id: UUID) -> bool:

        result = await self._session.execute(
            select(MaterialSourceModel).where(MaterialSourceModel.uuid == str(source_id))
        )
        model = result.scalar_one_or_none()
        if model is None:
            return False
        model.is_deleted = True
        return True


# ═══════════════════════════════════════════════════════
#  MaterialChunkRepository
# ═══════════════════════════════════════════════════════


class MaterialChunkRepository:
    """素材拆解块 Repository。"""

    def __init__(self, session: AsyncSession):
        self._session = session

    async def save(self, chunk: MaterialChunk) -> None:

        result = await self._session.execute(
            select(MaterialChunkModel).where(MaterialChunkModel.uuid == str(chunk.id))
        )
        existing = result.scalar_one_or_none()
        data = {
            "uuid": str(chunk.id),
            "source_id": str(chunk.source_id),
            "chunk_index": chunk.chunk_index,
            "content": chunk.content,
            "content_digest": chunk.content_digest,
            "status": chunk.status,
            "retry_count": chunk.retry_count,
            "error_message": chunk.error_message,
        }
        if existing:
            for k, v in data.items():
                setattr(existing, k, v)
        else:
            self._session.add(MaterialChunkModel(**data))

    async def get_by_id(self, chunk_id: UUID) -> MaterialChunk | None:

        result = await self._session.execute(
            select(MaterialChunkModel).where(MaterialChunkModel.uuid == str(chunk_id))
        )
        model = result.scalar_one_or_none()
        if model is None:
            return None
        return MaterialChunk(
            id=UUID(model.uuid),
            source_id=UUID(model.source_id),
            chunk_index=model.chunk_index,
            content=model.content,
            content_digest=model.content_digest,
            status=model.status,
            retry_count=model.retry_count,
            error_message=model.error_message,
        )

    async def get_by_source(self, source_id: UUID) -> list[MaterialChunk]:

        result = await self._session.execute(
            select(MaterialChunkModel)
            .where(MaterialChunkModel.source_id == str(source_id))
            .order_by(MaterialChunkModel.chunk_index)
        )
        models = result.scalars().all()
        return [
            MaterialChunk(
                id=UUID(m.uuid),
                source_id=UUID(m.source_id),
                chunk_index=m.chunk_index,
                content=m.content,
                content_digest=m.content_digest,
                status=m.status,
                retry_count=m.retry_count,
                error_message=m.error_message,
            )
            for m in models
        ]

    async def get_pending_by_source(self, source_id: UUID) -> list[MaterialChunk]:

        result = await self._session.execute(
            select(MaterialChunkModel)
            .where(
                MaterialChunkModel.source_id == str(source_id),
                MaterialChunkModel.status == "pending",
            )
            .order_by(MaterialChunkModel.chunk_index)
        )
        models = result.scalars().all()
        return [
            MaterialChunk(
                id=UUID(m.uuid),
                source_id=UUID(m.source_id),
                chunk_index=m.chunk_index,
                content=m.content,
                content_digest=m.content_digest,
                status=m.status,
                retry_count=m.retry_count,
                error_message=m.error_message,
            )
            for m in models
        ]

    async def get_failed_by_source(self, source_id: UUID) -> list[MaterialChunk]:

        result = await self._session.execute(
            select(MaterialChunkModel)
            .where(
                MaterialChunkModel.source_id == str(source_id),
                MaterialChunkModel.status == "failed",
            )
            .order_by(MaterialChunkModel.chunk_index)
        )
        models = result.scalars().all()
        return [
            MaterialChunk(
                id=UUID(m.uuid),
                source_id=UUID(m.source_id),
                chunk_index=m.chunk_index,
                content=m.content,
                content_digest=m.content_digest,
                status=m.status,
                retry_count=m.retry_count,
                error_message=m.error_message,
            )
            for m in models
        ]


# ═══════════════════════════════════════════════════════
#  MaterialFragmentRepository
# ═══════════════════════════════════════════════════════


class MaterialFragmentRepository:
    """结构化碎片 Repository。"""

    def __init__(self, session: AsyncSession):
        self._session = session

    async def save(self, fragment: MaterialFragment) -> None:

        result = await self._session.execute(
            select(MaterialFragmentModel).where(MaterialFragmentModel.uuid == str(fragment.id))
        )
        existing = result.scalar_one_or_none()
        data = {
            "uuid": str(fragment.id),
            "source_id": str(fragment.source_id),
            "source_chunk_id": str(fragment.source_chunk_id),
            "title": fragment.title,
            "content": fragment.content,
            "type": fragment.type,
            "tags": fragment.tags,
            "source": fragment.source,
            "source_quote": fragment.source_quote,
            "reusability_note": fragment.reusability_note,
            "user_note": fragment.user_note,
            "user_edited": fragment.user_edited,
        }
        if existing:
            for k, v in data.items():
                setattr(existing, k, v)
        else:
            self._session.add(MaterialFragmentModel(**data))

    async def get_by_id(self, fragment_id: UUID) -> MaterialFragment | None:

        result = await self._session.execute(
            select(MaterialFragmentModel).where(MaterialFragmentModel.uuid == str(fragment_id))
        )
        model = result.scalar_one_or_none()
        if model is None:
            return None
        return MaterialFragment(
            id=UUID(model.uuid),
            source_id=UUID(model.source_id),
            source_chunk_id=UUID(model.source_chunk_id),
            title=model.title,
            content=model.content,
            type=model.type,
            tags=model.tags or [],
            source=model.source,
            source_quote=model.source_quote,
            reusability_note=model.reusability_note,
            user_note=model.user_note,
            user_edited=model.user_edited,
            created_at=model.created_at,
        )

    async def get_by_novel(
        self,
        novel_id: UUID,
        type_filter: str | None = None,
        tag_filter: str | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> list[MaterialFragment]:

        query = (
            select(MaterialFragmentModel)
            .join(MaterialSourceModel, MaterialFragmentModel.source_id == MaterialSourceModel.uuid)
            .where(
                MaterialSourceModel.novel_id == str(novel_id),
                MaterialSourceModel.is_deleted == False,
            )
        )
        if type_filter:
            query = query.where(MaterialFragmentModel.type == type_filter)
        if tag_filter:
            query = query.where(MaterialFragmentModel.tags.contains(tag_filter))
        query = query.order_by(MaterialFragmentModel.created_at.desc()).offset(offset).limit(limit)
        result = await self._session.execute(query)
        models = result.scalars().all()
        return [_material_fragment_from_orm(m) for m in models]

    async def get_by_source(self, source_id: UUID) -> list[MaterialFragment]:

        result = await self._session.execute(
            select(MaterialFragmentModel)
            .where(MaterialFragmentModel.source_id == str(source_id))
            .order_by(MaterialFragmentModel.created_at)
        )
        models = result.scalars().all()
        return [_material_fragment_from_orm(m) for m in models]

    async def get_by_chunk(self, chunk_id: UUID) -> list[MaterialFragment]:

        result = await self._session.execute(
            select(MaterialFragmentModel)
            .where(MaterialFragmentModel.source_chunk_id == str(chunk_id))
            .order_by(MaterialFragmentModel.created_at)
        )
        models = result.scalars().all()
        return [_material_fragment_from_orm(m) for m in models]

    async def delete_by_chunk_except_edited(self, chunk_id: UUID) -> int:
        """删除某 chunk 下所有非 user_edited 的片段，返回删除数。"""
        from sqlalchemy import delete

        result = await self._session.execute(
            delete(MaterialFragmentModel).where(
                MaterialFragmentModel.source_chunk_id == str(chunk_id),
                MaterialFragmentModel.user_edited == False,
            )
        )
        return result.rowcount  # type: ignore[attr-defined]

    async def delete(self, fragment_id: UUID, skip_if_edited: bool = True) -> bool:
        """删除片段。若 skip_if_edited 且 user_edited 为 True 则不删。"""
        from sqlalchemy import delete

        fragment = await self.get_by_id(fragment_id)
        if fragment is None:
            return False
        if skip_if_edited and fragment.user_edited:
            return False
        await self._session.execute(
            delete(MaterialFragmentModel).where(MaterialFragmentModel.uuid == str(fragment_id))
        )
        return True

    async def batch_save(self, fragments: list[MaterialFragment]) -> None:

        for f in fragments:
            data = {
                "uuid": str(f.id),
                "source_id": str(f.source_id),
                "source_chunk_id": str(f.source_chunk_id),
                "title": f.title,
                "content": f.content,
                "type": f.type,
                "tags": f.tags,
                "source": f.source,
                "source_quote": f.source_quote,
                "reusability_note": f.reusability_note,
                "user_note": f.user_note,
                "user_edited": f.user_edited,
            }
            self._session.add(MaterialFragmentModel(**data))


class AppSettingsRepository:
    """应用级全局设置 Repository — 单行 JSON 存储。"""

    APP_NOVEL_ID = "__app__"

    def __init__(self, session: AsyncSession):
        self._session = session

    async def get(self) -> dict | None:
        """获取 app_settings 的 JSON 数据。无记录则返回 None。"""
        result = await self._session.execute(
            select(AppSettingsModel).where(AppSettingsModel.novel_id == self.APP_NOVEL_ID)
        )
        model = result.scalar_one_or_none()
        if model is None:
            return None
        return model.settings_json

    async def upsert(self, data: dict) -> None:
        """写入 app_settings（insert or update）。"""
        result = await self._session.execute(
            select(AppSettingsModel).where(AppSettingsModel.novel_id == self.APP_NOVEL_ID)
        )
        existing = result.scalar_one_or_none()
        if existing:
            existing.settings_json = data
        else:
            self._session.add(
                AppSettingsModel(
                    novel_id=self.APP_NOVEL_ID,
                    settings_json=data,
                )
            )


# ═══════════════════════════════════════════════════════
#  StatsRepository — LLM 调用统计
# ═══════════════════════════════════════════════════════


class StatsRepository:
    """LLM 调用统计仓储。

    持久化 ProviderStats 快照，支持时间窗聚合和四维拆分。
    """

    def __init__(self, session: AsyncSession):
        self._session = session

    @staticmethod
    def _window_filter(window: str) -> list:
        """返回时间窗 WHERE 条件列表（作为 filter 参数解包使用）。"""
        now = datetime.now(timezone.utc)
        if window == "today":
            from datetime import timedelta

            start = now - timedelta(days=1)
            return [ProviderStatsModel.timestamp >= start]
        elif window == "7d":
            from datetime import timedelta

            start = now - timedelta(days=7)
            return [ProviderStatsModel.timestamp >= start]
        # "all" — 不过滤
        return []

    async def save(self, stats: ProviderStatsModel) -> None:
        """保存一条统计记录。"""
        self._session.add(stats)

    async def batch_save(self, stats_list: list[ProviderStatsModel]) -> None:
        """批量保存统计记录。"""
        for s in stats_list:
            self._session.add(s)

    async def get_overview(self, window: str = "all") -> dict:
        """按时间窗聚合总览统计。

        Returns:
            {total_calls, total_tokens, total_cost, avg_latency_ms, success_rate, degradation_rate}
        """
        filters = self._window_filter(window)
        query = select(ProviderStatsModel)
        for f in filters:
            query = query.where(f)
        result = await self._session.execute(query)
        rows = result.scalars().all()

        total_calls = len(rows)
        if total_calls == 0:
            return {
                "total_calls": 0,
                "total_tokens": 0,
                "total_cost": 0.0,
                "avg_latency_ms": 0.0,
                "success_rate": 0.0,
                "degradation_rate": 0.0,
            }

        total_tokens = sum(r.total_tokens for r in rows)
        total_cost = sum(r.estimated_cost for r in rows)
        avg_latency = sum(r.latency_ms for r in rows) / total_calls
        success_count = sum(1 for r in rows if r.success)
        degraded_count = sum(1 for r in rows if r.degraded)

        return {
            "total_calls": total_calls,
            "total_tokens": total_tokens,
            "total_cost": total_cost,
            "avg_latency_ms": round(avg_latency, 2),
            "success_rate": round(success_count / total_calls, 4),
            "degradation_rate": round(degraded_count / total_calls, 4),
        }

    async def get_breakdown(self, window: str = "all", dimension: str = "provider") -> list[dict]:
        """按维度拆分统计。

        Args:
            window: today / 7d / all
            dimension: provider / model / agent / error

        Returns:
            [{dimension_key, calls, total_tokens, total_cost, avg_latency_ms, success_rate}, ...]
        """
        filters = self._window_filter(window)
        query = select(ProviderStatsModel)
        for f in filters:
            query = query.where(f)
        result = await self._session.execute(query)
        rows = result.scalars().all()

        # 映射 dimension 到 ProviderStatsModel 字段
        dim_map = {
            "provider": "provider_name",
            "model": "model_name",
            "agent": "agent_name",
            "error": "error_type",
        }
        attr = dim_map.get(dimension, "provider_name")

        from collections import defaultdict

        groups: dict[str, dict] = defaultdict(
            lambda: {
                "calls": 0,
                "total_tokens": 0,
                "total_cost": 0.0,
                "latencies": [],
                "successes": 0,
            }
        )

        for r in rows:
            key = getattr(r, attr) or "(unknown)"
            g = groups[key]
            g["calls"] += 1
            g["total_tokens"] += r.total_tokens
            g["total_cost"] += r.estimated_cost
            g["latencies"].append(r.latency_ms)
            if r.success:
                g["successes"] += 1

        result_list = []
        for key, g in groups.items():
            avg_lat = sum(g["latencies"]) / len(g["latencies"]) if g["latencies"] else 0.0
            result_list.append(
                {
                    dimension: key,
                    "calls": g["calls"],
                    "total_tokens": g["total_tokens"],
                    "total_cost": round(g["total_cost"], 6),
                    "avg_latency_ms": round(avg_lat, 2),
                    "success_rate": round(g["successes"] / g["calls"], 4) if g["calls"] else 0.0,
                }
            )

        result_list.sort(key=lambda x: x["calls"], reverse=True)
        return result_list

    async def get_runs(self, window: str = "all") -> list[dict]:
        """获取 Run 历史。"""
        filters = self._window_filter(window)
        from sqlalchemy import select as sa_select

        _query = sa_select(RunsModel).order_by(RunsModel.created_at.desc()).limit(100)
        for f in filters:
            # RunsModel uses created_at field
            pass
        # Rebuild with proper field filter
        from datetime import timedelta

        now = datetime.now(timezone.utc)
        q = sa_select(RunsModel)
        if window == "today":
            q = q.where(RunsModel.created_at >= now - timedelta(days=1))
        elif window == "7d":
            q = q.where(RunsModel.created_at >= now - timedelta(days=7))
        q = q.order_by(RunsModel.created_at.desc()).limit(100)
        result = await self._session.execute(q)
        rows = result.scalars().all()
        return [
            {
                "id": str(r.uuid),
                "novel_id": str(r.novel_id),
                "chapter_id": str(r.chapter_id) if r.chapter_id else None,
                "kind": r.kind,
                "status": r.status,
                "phase": r.phase,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "started_at": r.started_at.isoformat() if r.started_at else None,
                "completed_at": r.completed_at.isoformat() if r.completed_at else None,
            }
            for r in rows
        ]

    async def get_compression_tasks(self) -> list[dict]:
        """获取压缩任务列表（含失败任务的高亮信息）。"""
        from sqlalchemy import select as sa_select

        result = await self._session.execute(
            sa_select(CompressionTaskModel)
            .order_by(CompressionTaskModel.created_at.desc())
            .limit(50)
        )
        rows = result.scalars().all()
        return [
            {
                "task_id": str(r.task_id),
                "novel_id": str(r.novel_id),
                "range_start": r.range_start,
                "range_end": r.range_end,
                "status": r.status,
                "error_message": r.error_message,
                "started_at": r.started_at.isoformat() if r.started_at else None,
                "completed_at": r.completed_at.isoformat() if r.completed_at else None,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]


def _material_fragment_from_orm(model) -> MaterialFragment:
    return MaterialFragment(
        id=UUID(model.uuid),
        source_id=UUID(model.source_id),
        source_chunk_id=UUID(model.source_chunk_id),
        title=model.title,
        content=model.content,
        type=model.type,
        tags=model.tags or [],
        source=model.source,
        source_quote=model.source_quote,
        reusability_note=model.reusability_note,
        user_note=model.user_note,
        user_edited=model.user_edited,
        created_at=model.created_at,
    )
