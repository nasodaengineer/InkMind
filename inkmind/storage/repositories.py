"""Repository 层。

每个核心实体对应一个 Repository 类。Repository 在 UnitOfWork 内工作，
不直接管理事务提交。
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from inkmind.models.agent import PipelineState
from inkmind.models.chapter import Chapter, ChapterVersion
from inkmind.models.character import Character
from inkmind.models.novel import Novel
from inkmind.models.world import World
from inkmind.storage.models import (
    AppSettingsModel,
    ChapterModel,
    ChapterVersionModel,
    CharacterModel,
    NovelModel,
    PipelineStateModel,
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
    dict_to_pipeline_state,
    dict_to_world,
    novel_to_dict,
    novel_to_orm,
    pipeline_state_to_dict,
    pipeline_state_to_orm,
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

    async def save(self, chapter: Chapter) -> None:
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

    async def get_by_novel_and_index(
        self, novel_id: UUID, index: int
    ) -> Chapter | None:
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

    async def get_chapters_by_novel(
        self, novel_id: UUID
    ) -> list[Chapter]:
        result = await self._session.execute(
            select(ChapterModel)
            .where(ChapterModel.novel_id == str(novel_id))
                .order_by(ChapterModel.chapter_index)
        )
        models = result.scalars().all()
        return [dict_to_chapter(chapter_to_dict(m)) for m in models]

    async def get_versions(
        self, chapter_id: UUID
    ) -> list[ChapterVersion]:
        result = await self._session.execute(
            select(ChapterVersionModel)
            .where(ChapterVersionModel.chapter_id == str(chapter_id))
            .order_by(ChapterVersionModel.version.desc())
        )
        models = result.scalars().all()
        return [dict_to_chapter_version(chapter_version_to_dict(m)) for m in models]

    async def update_status(
        self, novel_id: UUID, chapter_index: int, status: str
    ) -> None:
        await self._session.execute(
            update(ChapterModel)
            .where(
                ChapterModel.novel_id == str(novel_id),
                ChapterModel.chapter_index == chapter_index,
            )
            .values(status=status)
        )


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
            select(CharacterModel).where(
                CharacterModel.novel_id == str(novel_id)
            )
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
            select(WorldModel).where(
                WorldModel.novel_id == str(novel_id)
            )
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

    async def get_by_novel(
        self, novel_id: UUID
    ) -> PipelineState | None:
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


class AppSettingsRepository:
    """应用级全局设置 Repository — 单行 JSON 存储。"""

    APP_NOVEL_ID = "__app__"

    def __init__(self, session: AsyncSession):
        self._session = session

    async def get(self) -> dict | None:
        """获取 app_settings 的 JSON 数据。无记录则返回 None。"""
        result = await self._session.execute(
            select(AppSettingsModel).where(
                AppSettingsModel.novel_id == self.APP_NOVEL_ID
            )
        )
        model = result.scalar_one_or_none()
        if model is None:
            return None
        return model.settings_json

    async def upsert(self, data: dict) -> None:
        """写入 app_settings（insert or update）。"""
        result = await self._session.execute(
            select(AppSettingsModel).where(
                AppSettingsModel.novel_id == self.APP_NOVEL_ID
            )
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
