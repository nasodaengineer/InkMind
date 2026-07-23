"""Pydantic ↔ ORM 序列化。

负责将领域模型（Novel / Chapter / Character / World / LLMConfig 等）
序列化为 ORM 模型字典，以及从 ORM 模型重建 Pydantic 实例。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from uuid import UUID, uuid4

if TYPE_CHECKING:
    from inkmind.storage.models import CommentThreadModel, RunsModel

from inkmind.models.agent import (
    ChapterStatus,
    PipelineState,
)
from inkmind.models.annotation import Comment, CommentThread
from inkmind.models.chapter import Chapter, ChapterVersion
from inkmind.models.character import Character, CharacterTimelineEntry
from inkmind.models.novel import Novel, NovelMetadata, OutlineSpine, Volume
from inkmind.models.run import Run, RunKind, RunStatus
from inkmind.models.world import (
    Faction,
    Location,
    MagicSystem,
    PowerAbility,
    PowerSystem,
    TimelineMarker,
    World,
)
from inkmind.storage.models import (
    ChapterModel,
    ChapterVersionModel,
    CharacterModel,
    NovelModel,
    OutlineSpineModel,
    PipelineStateModel,
    VolumeModel,
    WorldModel,
)

# ═══════════════════════════════════════════════════════
#  Helper: UUID ↔ str
# ═══════════════════════════════════════════════════════


def _to_str(u: UUID | None) -> str | None:
    return str(u) if u is not None else None


def _to_uuid(s: str | None) -> UUID | None:
    return UUID(s) if s else None


# ═══════════════════════════════════════════════════════
#  Novel
# ═══════════════════════════════════════════════════════


def novel_to_dict(model: NovelModel) -> dict:
    return {
        "id": UUID(model.uuid),
        "title": model.title,
        "metadata": {
            "description": model.description,
            "word_count": model.word_count,
            "chapter_count": model.chapter_count,
            "status": model.status,
        },
        "created_at": model.created_at or datetime.now(timezone.utc),
        "updated_at": model.updated_at or datetime.now(timezone.utc),
    }


def dict_to_novel(data: dict) -> Novel:
    meta = data.get("metadata", {})
    raw_id = data.get("id", data.get("uuid"))
    return Novel(
        id=raw_id if isinstance(raw_id, UUID) else UUID(data["id"]),
        title=data["title"],
        metadata=NovelMetadata(
            description=meta.get("description", ""),
            word_count=meta.get("word_count", 0),
            chapter_count=meta.get("chapter_count", 0),
            status=meta.get("status", "draft"),
        ),
        created_at=data.get("created_at") or datetime.now(timezone.utc),
        updated_at=data.get("updated_at") or datetime.now(timezone.utc),
    )


def novel_to_orm(novel: Novel) -> dict:
    return {
        "uuid": str(novel.id),
        "title": novel.title,
        "description": novel.metadata.description,
        "word_count": novel.metadata.word_count,
        "chapter_count": novel.metadata.chapter_count,
        "status": novel.metadata.status,
    }


# ═══════════════════════════════════════════════════════
#  Chapter
# ═══════════════════════════════════════════════════════


def chapter_to_dict(model: ChapterModel) -> dict:
    return {
        "id": UUID(model.uuid),
        "novel_id": UUID(model.novel_id),
        "chapter_index": model.chapter_index,
        "title": model.title,
        "content": model.content,
        "status": ChapterStatus(model.status),
        "summary": model.summary,
        "key_events": model.key_events or [],
        "source_trace": model.source_trace,
        "outline_id": _to_uuid(model.outline_id),
        "volume_id": _to_uuid(model.volume_id) if model.volume_id else None,
        "rhythm_marker": model.rhythm_marker,
        "pov": model.pov or "",
        "involved": model.involved or [],
        "version": model.version,
        "is_baseline": model.is_baseline,
        "content_digest": model.content_digest or "",
        "created_at": model.created_at or datetime.now(timezone.utc),
        "updated_at": model.updated_at or datetime.now(timezone.utc),
    }


def dict_to_chapter(data: dict) -> Chapter:
    return Chapter(
        id=data["id"] if isinstance(data.get("id"), UUID) else UUID(data["id"]),
        novel_id=data["novel_id"]
        if isinstance(data.get("novel_id"), UUID)
        else UUID(data["novel_id"]),
        index=data.get("chapter_index", 0),
        title=data["title"],
        content=data.get("content", ""),
        status=ChapterStatus(data.get("status", "planned")),
        summary=data.get("summary", ""),
        key_events=data.get("key_events", []),
        source_trace=data.get("source_trace", ""),
        outline_id=(
            data["outline_id"]
            if isinstance(data.get("outline_id"), UUID)
            else UUID(data["outline_id"])
            if data.get("outline_id")
            else None
        ),
        volume_id=(
            data["volume_id"]
            if isinstance(data.get("volume_id"), UUID)
            else UUID(data["volume_id"])
            if data.get("volume_id")
            else None
        ),
        rhythm_marker=data.get("rhythm_marker"),
        pov=data.get("pov", ""),
        involved=data.get("involved", []),
        version=data.get("version", 1),
        is_baseline=data.get("is_baseline", False),
        content_digest=data.get("content_digest", ""),
        created_at=data.get("created_at") or datetime.now(timezone.utc),
        updated_at=data.get("updated_at") or datetime.now(timezone.utc),
    )


def chapter_to_orm(chapter: Chapter) -> dict:
    return {
        "uuid": str(chapter.id),
        "novel_id": str(chapter.novel_id),
        "chapter_index": chapter.index,
        "title": chapter.title,
        "content": chapter.content,
        "status": chapter.status.value,
        "summary": chapter.summary,
        "key_events": chapter.key_events,
        "source_trace": chapter.source_trace,
        "outline_id": _to_str(chapter.outline_id) if chapter.outline_id else None,
        "volume_id": _to_str(chapter.volume_id),
        "rhythm_marker": chapter.rhythm_marker,
        "pov": chapter.pov,
        "involved": chapter.involved,
        "version": chapter.version,
        "is_baseline": chapter.is_baseline,
        "content_digest": chapter.content_digest,
    }


# ═══════════════════════════════════════════════════════
#  ChapterVersion
# ═══════════════════════════════════════════════════════


def chapter_version_to_dict(model: ChapterVersionModel) -> dict:
    return {
        "id": UUID(model.uuid),
        "chapter_id": UUID(model.chapter_id),
        "novel_id": UUID(model.novel_id),
        "version": model.version,
        "chapter_index": model.chapter_index,
        "title": model.title,
        "content": model.content,
        "summary": model.summary,
        "key_events": model.key_events or [],
        "source_trace": model.source_trace,
        "is_baseline": model.is_baseline,
        "content_digest": model.content_digest,
        "created_at": model.created_at or datetime.now(timezone.utc),
    }


def dict_to_chapter_version(data: dict) -> ChapterVersion:
    return ChapterVersion(
        id=data["id"] if isinstance(data.get("id"), UUID) else UUID(data["id"]),
        chapter_id=data["chapter_id"]
        if isinstance(data.get("chapter_id"), UUID)
        else UUID(data["chapter_id"]),
        novel_id=data["novel_id"]
        if isinstance(data.get("novel_id"), UUID)
        else UUID(data["novel_id"]),
        version=data["version"],
        index=data.get("chapter_index", 0),
        title=data["title"],
        content=data["content"],
        summary=data.get("summary", ""),
        key_events=data.get("key_events", []),
        source_trace=data.get("source_trace", ""),
        is_baseline=data.get("is_baseline", False),
        content_digest=data.get("content_digest", ""),
        created_at=data.get("created_at") or datetime.now(timezone.utc),
    )


def chapter_version_to_orm(ver: ChapterVersion) -> dict:
    return {
        "uuid": str(ver.id),
        "chapter_id": str(ver.chapter_id),
        "novel_id": str(ver.novel_id),
        "version": ver.version,
        "chapter_index": ver.index,
        "title": ver.title,
        "content": ver.content,
        "summary": ver.summary,
        "key_events": ver.key_events,
        "source_trace": ver.source_trace,
        "is_baseline": ver.is_baseline,
        "content_digest": ver.content_digest,
    }


# ═══════════════════════════════════════════════════════
#  Character
# ═══════════════════════════════════════════════════════


def character_to_dict(model: CharacterModel) -> dict:
    return {
        "id": UUID(model.uuid),
        "novel_id": UUID(model.novel_id),
        "name": model.name,
        "aliases": model.aliases or [],
        "role": model.role,
        "personality_tags": model.personality_tags or [],
        "behavior_rules": model.behavior_rules,
        "appearance": model.appearance,
        "background": model.background,
        "relationships": model.relationships,
        "arc_notes": model.arc_notes,
        "current_state": model.current_state,
        "knowledge": model.knowledge or [],
        "voice_examples": model.voice_examples,
        "timeline": model.timeline or [],
        "created_at": model.created_at or datetime.now(timezone.utc),
        "updated_at": model.updated_at or datetime.now(timezone.utc),
    }


def dict_to_character(data: dict) -> Character:
    timeline = [
        CharacterTimelineEntry(**t) if isinstance(t, dict) else t
        for t in (data.get("timeline") or [])
    ]
    return Character(
        id=data["id"] if isinstance(data.get("id"), UUID) else UUID(data["id"]),
        novel_id=data["novel_id"]
        if isinstance(data.get("novel_id"), UUID)
        else UUID(data["novel_id"]),
        name=data["name"],
        aliases=data.get("aliases", []),
        role=data.get("role", "supporting"),
        personality_tags=data.get("personality_tags", []),
        behavior_rules=data.get("behavior_rules", ""),
        appearance=data.get("appearance", ""),
        background=data.get("background", ""),
        relationships=data.get("relationships", ""),
        arc_notes=data.get("arc_notes", ""),
        current_state=data.get("current_state", ""),
        knowledge=data.get("knowledge", []),
        voice_examples=data.get("voice_examples", ""),
        timeline=timeline,
        created_at=data.get("created_at") or datetime.now(timezone.utc),
        updated_at=data.get("updated_at") or datetime.now(timezone.utc),
    )


def character_to_orm(character: Character) -> dict:
    timeline_dicts = []
    for entry in character.timeline:
        if isinstance(entry, CharacterTimelineEntry):
            timeline_dicts.append(entry.model_dump())
        else:
            timeline_dicts.append(entry)
    return {
        "uuid": str(character.id),
        "novel_id": str(character.novel_id),
        "name": character.name,
        "aliases": character.aliases,
        "role": character.role,
        "personality_tags": character.personality_tags,
        "behavior_rules": character.behavior_rules,
        "appearance": character.appearance,
        "background": character.background,
        "relationships": character.relationships,
        "arc_notes": character.arc_notes,
        "current_state": character.current_state,
        "knowledge": character.knowledge,
        "voice_examples": character.voice_examples,
        "timeline": timeline_dicts,
    }


# ═══════════════════════════════════════════════════════
#  World
# ═══════════════════════════════════════════════════════


def world_to_dict(model: WorldModel) -> dict:
    return {
        "id": UUID(model.uuid),
        "novel_id": UUID(model.novel_id),
        "title": model.title,
        "genre_tags": model.genre_tags or [],
        "setting": model.setting,
        "rules": model.rules or [],
        "factions": model.factions or [],
        "timeline_markers": model.timeline_markers or [],
        "power_system": model.power_system,
        "magic_system": model.magic_system,
        "location_tree": model.location_tree or [],
        "created_at": model.created_at or datetime.now(timezone.utc),
        "updated_at": model.updated_at or datetime.now(timezone.utc),
    }


def dict_to_world(data: dict) -> World:
    def _parse_faction(f: dict) -> Faction:
        return Faction(**f)

    def _parse_location(loc: dict) -> Location:
        return Location(**loc)

    def _parse_marker(m: dict) -> TimelineMarker:
        return TimelineMarker(**m)

    def _parse_power(p: dict | None) -> PowerSystem | None:
        if p is None:
            return None
        abilities = [PowerAbility(**a) for a in p.get("abilities", [])]
        return PowerSystem(
            name=p.get("name", ""),
            description=p.get("description", ""),
            abilities=abilities,
            rules=p.get("rules", []),
            limitations=p.get("limitations", []),
        )

    def _parse_magic(m: dict | None) -> MagicSystem | None:
        if m is None:
            return None
        spells = [PowerAbility(**s) for s in m.get("spells", [])]
        return MagicSystem(
            name=m.get("name", ""),
            description=m.get("description", ""),
            schools=m.get("schools", []),
            spells=spells,
            rules=m.get("rules", []),
            limitations=m.get("limitations", []),
            mana_source=m.get("mana_source"),
        )

    return World(
        id=data["id"] if isinstance(data.get("id"), UUID) else UUID(data["id"]),
        novel_id=data["novel_id"]
        if isinstance(data.get("novel_id"), UUID)
        else UUID(data["novel_id"]),
        title=data.get("title", ""),
        genre_tags=data.get("genre_tags", []),
        setting=data.get("setting", ""),
        rules=data.get("rules", []),
        factions=[_parse_faction(f) for f in (data.get("factions") or [])],
        timeline_markers=[_parse_marker(m) for m in (data.get("timeline_markers") or [])],
        power_system=_parse_power(data.get("power_system")),
        magic_system=_parse_magic(data.get("magic_system")),
        location_tree=[_parse_location(loc) for loc in (data.get("location_tree") or [])],
        created_at=data.get("created_at") or datetime.now(timezone.utc),
        updated_at=data.get("updated_at") or datetime.now(timezone.utc),
    )


def world_to_orm(world: World) -> dict:
    return {
        "uuid": str(world.id),
        "novel_id": str(world.novel_id),
        "title": world.title,
        "genre_tags": world.genre_tags,
        "setting": world.setting,
        "rules": world.rules,
        "factions": [f.model_dump(mode="json") for f in world.factions],
        "timeline_markers": [t.model_dump(mode="json") for t in world.timeline_markers],
        "power_system": world.power_system.model_dump(mode="json") if world.power_system else None,
        "magic_system": world.magic_system.model_dump(mode="json") if world.magic_system else None,
        "location_tree": [loc.model_dump(mode="json") for loc in world.location_tree],
    }


# ═══════════════════════════════════════════════════════
#  PipelineState
# ═══════════════════════════════════════════════════════


def pipeline_state_to_dict(model: PipelineStateModel) -> dict:
    chapters = {int(k): ChapterStatus(v) for k, v in (model.chapters_state or {}).items()}
    return {
        "novel_id": UUID(model.novel_id),
        "total_chapters": model.total_chapters,
        "chapters": chapters,
        "current_chapter_index": model.current_chapter_index,
        "iteration": model.iteration,
        "max_iterations": model.max_iterations,
    }


def dict_to_pipeline_state(data: dict) -> PipelineState:
    chapters = {}
    for k, v in (data.get("chapters", {}) or {}).items():
        if isinstance(v, str):
            chapters[int(k)] = ChapterStatus(v)
        else:
            chapters[int(k)] = v
    return PipelineState(
        novel_id=data["novel_id"]
        if isinstance(data.get("novel_id"), UUID)
        else UUID(data["novel_id"]),
        total_chapters=data.get("total_chapters", 0),
        chapters=chapters,
        current_chapter_index=data.get("current_chapter_index"),
        iteration=data.get("iteration", 0),
        max_iterations=data.get("max_iterations", 3),
    )


def pipeline_state_to_orm(state: PipelineState) -> dict:
    return {
        "novel_id": str(state.novel_id),
        "total_chapters": state.total_chapters,
        "chapters_state": {str(k): v.value for k, v in state.chapters.items()},
        "current_chapter_index": state.current_chapter_index,
        "iteration": state.iteration,
        "max_iterations": state.max_iterations,
    }


# ═══════════════════════════════════════════════════════
#  Run
# ═══════════════════════════════════════════════════════


def run_to_dict(model: RunsModel) -> dict:
    return {
        "id": UUID(model.uuid),
        "novel_id": UUID(model.novel_id),
        "chapter_id": _to_uuid(model.chapter_id),
        "kind": RunKind(model.kind),
        "status": RunStatus(model.status),
        "phase": model.phase,
        "partial_content": model.partial_content,
        "llm_stats": model.llm_stats or {},
        "overwritten_values": model.overwritten_values,
        "started_at": model.started_at,
        "completed_at": model.completed_at,
        "created_at": model.created_at or datetime.now(timezone.utc),
        "updated_at": model.updated_at or datetime.now(timezone.utc),
    }


def dict_to_run(data: dict) -> Run:
    return Run(
        id=data["id"] if isinstance(data.get("id"), UUID) else UUID(data["id"]),
        novel_id=data["novel_id"]
        if isinstance(data.get("novel_id"), UUID)
        else UUID(data["novel_id"]),
        chapter_id=(
            data["chapter_id"]
            if isinstance(data.get("chapter_id"), UUID)
            else UUID(data["chapter_id"])
            if data.get("chapter_id")
            else None
        ),
        kind=RunKind(data["kind"]) if isinstance(data.get("kind"), str) else data["kind"],
        status=RunStatus(data["status"]) if isinstance(data.get("status"), str) else data["status"],
        phase=data.get("phase", ""),
        partial_content=data.get("partial_content", ""),
        llm_stats=data.get("llm_stats", {}),
        overwritten_values=data.get("overwritten_values"),
        started_at=data.get("started_at"),
        completed_at=data.get("completed_at"),
        created_at=data.get("created_at") or datetime.now(timezone.utc),
        updated_at=data.get("updated_at") or datetime.now(timezone.utc),
    )


def run_to_orm(run: Run) -> dict:
    return {
        "uuid": str(run.id),
        "novel_id": str(run.novel_id),
        "chapter_id": str(run.chapter_id) if run.chapter_id else None,
        "kind": run.kind.value,
        "status": run.status.value,
        "phase": run.phase,
        "partial_content": run.partial_content,
        "llm_stats": run.llm_stats,
        "overwritten_values": run.overwritten_values,
        "started_at": run.started_at,
        "completed_at": run.completed_at,
    }


# ═══════════════════════════════════════════════════════
#  Volume
# ═══════════════════════════════════════════════════════


def volume_to_dict(model: VolumeModel) -> dict:
    return {
        "id": UUID(model.uuid),
        "novel_id": UUID(model.novel_id),
        "volume_index": model.volume_index,
        "title": model.title,
        "stage_goal": model.stage_goal,
        "main_line": model.main_line,
        "side_line": model.side_line,
        "volume_cliffhanger": model.volume_cliffhanger,
        "planned_size": model.planned_size,
        "created_at": model.created_at or datetime.now(timezone.utc),
        "updated_at": model.updated_at or datetime.now(timezone.utc),
    }


def dict_to_volume(data: dict) -> Volume:
    return Volume(
        id=data["id"] if isinstance(data.get("id"), UUID) else UUID(data["id"]),
        novel_id=data["novel_id"]
        if isinstance(data.get("novel_id"), UUID)
        else UUID(data["novel_id"]),
        volume_index=data["volume_index"],
        title=data["title"],
        stage_goal=data.get("stage_goal", ""),
        main_line=data.get("main_line", ""),
        side_line=data.get("side_line", ""),
        volume_cliffhanger=data.get("volume_cliffhanger", ""),
        planned_size=data.get("planned_size", 10),
        created_at=data.get("created_at") or datetime.now(timezone.utc),
        updated_at=data.get("updated_at") or datetime.now(timezone.utc),
    )


def volume_to_orm(volume: Volume) -> dict:
    return {
        "uuid": str(volume.id),
        "novel_id": str(volume.novel_id),
        "volume_index": volume.volume_index,
        "title": volume.title,
        "stage_goal": volume.stage_goal,
        "main_line": volume.main_line,
        "side_line": volume.side_line,
        "volume_cliffhanger": volume.volume_cliffhanger,
        "planned_size": volume.planned_size,
    }


# ═══════════════════════════════════════════════════════
#  OutlineSpine
# ═══════════════════════════════════════════════════════


def outline_spine_to_dict(model: OutlineSpineModel) -> dict:
    return {
        "novel_id": UUID(model.novel_id),
        "main_line": model.main_line,
        "core_conflict": model.core_conflict,
        "ending": model.ending,
        "selling_points": model.selling_points,
        "world_background": model.world_background,
        "golden_finger": model.golden_finger,
        "created_at": model.created_at or datetime.now(timezone.utc),
        "updated_at": model.updated_at or datetime.now(timezone.utc),
    }


def dict_to_outline_spine(data: dict) -> OutlineSpine:
    return OutlineSpine(
        novel_id=data["novel_id"]
        if isinstance(data.get("novel_id"), UUID)
        else UUID(data["novel_id"]),
        main_line=data.get("main_line", ""),
        core_conflict=data.get("core_conflict", ""),
        ending=data.get("ending", ""),
        selling_points=data.get("selling_points", ""),
        world_background=data.get("world_background", ""),
        golden_finger=data.get("golden_finger", ""),
        created_at=data.get("created_at") or datetime.now(timezone.utc),
        updated_at=data.get("updated_at") or datetime.now(timezone.utc),
    )


def outline_spine_to_orm(spine: OutlineSpine) -> dict:
    return {
        "uuid": str(uuid4()),
        "novel_id": str(spine.novel_id),
        "main_line": spine.main_line,
        "core_conflict": spine.core_conflict,
        "ending": spine.ending,
        "selling_points": spine.selling_points,
        "world_background": spine.world_background,
        "golden_finger": spine.golden_finger,
    }


# ═══════════════════════════════════════════════════════
#  CommentThread / Comment（批注）
# ═══════════════════════════════════════════════════════


def comment_thread_to_dict(model: CommentThreadModel) -> dict:
    comments = []
    for c in model.comments or []:
        comments.append(
            {
                "id": UUID(c.uuid),
                "author": c.author,
                "body": c.body,
                "created_at": c.created_at or datetime.now(timezone.utc),
            }
        )
    return {
        "id": UUID(model.uuid),
        "novel_id": UUID(model.novel_id),
        "chapter_id": UUID(model.chapter_id),
        "intent": model.intent,
        "status": model.status,
        "anchor": model.anchor,
        "comments": comments,
        "created_at": model.created_at or datetime.now(timezone.utc),
        "updated_at": model.updated_at or datetime.now(timezone.utc),
        "resolved_at": model.resolved_at,
    }


def dict_to_comment_thread(data: dict) -> CommentThread:
    from inkmind.models.annotation import (
        AnchorFingerprint,
        CommentIntent,
        ThreadStatus,
    )

    anchor = None
    if data.get("anchor"):
        anchor = AnchorFingerprint(**data["anchor"])

    comments = [
        Comment(
            id=c["id"] if isinstance(c.get("id"), UUID) else UUID(c["id"]),
            author=c.get("author", "user"),
            body=c["body"],
            created_at=c.get("created_at"),
        )
        for c in (data.get("comments") or [])
    ]

    return CommentThread(
        id=data["id"] if isinstance(data.get("id"), UUID) else UUID(data["id"]),
        novel_id=data["novel_id"]
        if isinstance(data.get("novel_id"), UUID)
        else UUID(data["novel_id"]),
        chapter_id=data["chapter_id"]
        if isinstance(data.get("chapter_id"), UUID)
        else UUID(data["chapter_id"]),
        intent=CommentIntent(data.get("intent", "note")),
        status=ThreadStatus(data.get("status", "open")),
        anchor=anchor,
        comments=comments,
        created_at=data.get("created_at"),
        updated_at=data.get("updated_at"),
        resolved_at=data.get("resolved_at"),
    )


def comment_thread_to_orm(thread: CommentThread) -> dict:
    return {
        "uuid": str(thread.id),
        "novel_id": str(thread.novel_id),
        "chapter_id": str(thread.chapter_id),
        "intent": thread.intent.value,
        "status": thread.status.value,
        "anchor": thread.anchor.model_dump(mode="json") if thread.anchor else None,
        "resolved_at": thread.resolved_at,
    }


def comment_to_orm(comment: Comment, thread_id: str) -> dict:
    return {
        "uuid": str(comment.id),
        "thread_id": thread_id,
        "author": comment.author,
        "body": comment.body,
    }
