"""素材管理 API 端点。

前缀 /api/novels/{novel_id}/materials
"""

from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from inkmind.api.deps import get_db
from inkmind.storage.search import FTSManager
from inkmind.models.materials import (
    FRAGMENT_TYPES,
    MaterialChunk,
    MaterialFragment,
    MaterialSource,
)
from inkmind.storage.repositories import (
    MaterialChunkRepository,
    MaterialFragmentRepository,
    MaterialSourceRepository,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/novels/{novel_id}/materials",
    tags=["materials"],
)


# ═══════════════════════════════════════════════════════
#  请求 / 响应模型
# ═══════════════════════════════════════════════════════


class SourceResponse(BaseModel):
    id: str
    novel_id: str
    raw_text: str
    content_digest: str
    status: str
    word_count: int
    created_at: str
    chunk_count: int = 0
    fragment_count: int = 0


class ChunkResponse(BaseModel):
    id: str
    source_id: str
    chunk_index: int
    content: str
    content_digest: str
    status: str
    retry_count: int
    error_message: str | None
    fragment_count: int = 0


class FragmentResponse(BaseModel):
    id: str
    source_id: str
    source_chunk_id: str
    title: str
    content: str
    type: str
    tags: list[str]
    source: str
    source_quote: str | None
    reusability_note: str
    user_note: str
    user_edited: bool
    created_at: str


class ImportRequest(BaseModel):
    raw_text: str = Field(min_length=1)


class ImportResponse(BaseModel):
    source: SourceResponse
    is_duplicate: bool = False


class UpdateFragmentRequest(BaseModel):
    title: str | None = None
    content: str | None = None
    type: str | None = None
    tags: list[str] | None = None
    source_quote: str | None = None
    reusability_note: str | None = None
    user_note: str | None = None
    clear_edited: bool = False


class CreateFragmentRequest(BaseModel):
    title: str = Field(max_length=20)
    content: str = Field(max_length=2000)
    type: str = "misc"
    tags: list[str] = Field(default_factory=list)
    source_quote: str | None = None
    reusability_note: str = ""
    user_note: str = ""


class SourceDetailResponse(BaseModel):
    source: SourceResponse
    chunks: list[ChunkResponse]
    fragments: list[FragmentResponse]


# ═══════════════════════════════════════════════════════
#  辅助函数
# ═══════════════════════════════════════════════════════


def _source_to_response(
    s: MaterialSource,
    chunk_count: int = 0,
    fragment_count: int = 0,
) -> SourceResponse:
    return SourceResponse(
        id=str(s.id),
        novel_id=str(s.novel_id),
        raw_text=s.raw_text,
        content_digest=s.content_digest,
        status=s.status,
        word_count=s.word_count,
        created_at=s.created_at.isoformat(),
        chunk_count=chunk_count,
        fragment_count=fragment_count,
    )


def _chunk_to_response(
    c: MaterialChunk,
    fragment_count: int = 0,
) -> ChunkResponse:
    return ChunkResponse(
        id=str(c.id),
        source_id=str(c.source_id),
        chunk_index=c.chunk_index,
        content=c.content,
        content_digest=c.content_digest,
        status=c.status,
        retry_count=c.retry_count,
        error_message=c.error_message,
        fragment_count=fragment_count,
    )


def _fragment_to_response(f: MaterialFragment) -> FragmentResponse:
    return FragmentResponse(
        id=str(f.id),
        source_id=str(f.source_id),
        source_chunk_id=str(f.source_chunk_id),
        title=f.title,
        content=f.content,
        type=f.type,
        tags=f.tags,
        source=f.source,
        source_quote=f.source_quote,
        reusability_note=f.reusability_note,
        user_note=f.user_note,
        user_edited=f.user_edited,
        created_at=f.created_at.isoformat(),
    )


def _get_repos(session: AsyncSession) -> tuple:
    return (
        MaterialSourceRepository(session),
        MaterialChunkRepository(session),
        MaterialFragmentRepository(session),
    )


# ═══════════════════════════════════════════════════════
#  端点
# ═══════════════════════════════════════════════════════


@router.post("/sources", status_code=status.HTTP_201_CREATED)
async def import_source(
    novel_id: UUID,
    body: ImportRequest,
    session: AsyncSession = Depends(get_db),
) -> ImportResponse:
    """导入素材原文（T6 事务）。"""
    from inkmind.storage.unit_of_work import UnitOfWork

    uow = UnitOfWork(session)
    source = await uow.t6_import_material(novel_id, body.raw_text)
    await session.commit()

    # 加载计数
    chunks = await uow.material_chunks.get_by_source(source.id)
    fragments = await uow.material_fragments.get_by_source(source.id)

    return ImportResponse(
        source=_source_to_response(
            source,
            chunk_count=len(chunks),
            fragment_count=len(fragments),
        ),
        is_duplicate=source.status == "pending" and len(chunks) == 0,
    )


@router.get("/sources")
async def list_sources(
    novel_id: UUID,
    session: AsyncSession = Depends(get_db),
) -> list[SourceResponse]:
    """获取来源台账列表。"""
    repo_s, repo_ch, repo_f = _get_repos(session)
    sources = await repo_s.get_by_novel(novel_id)
    result = []
    for s in sources:
        chunks = await repo_ch.get_by_source(s.id)
        fragments = await repo_f.get_by_source(s.id)
        result.append(
            _source_to_response(
                s,
                chunk_count=len(chunks),
                fragment_count=len(fragments),
            )
        )
    return result


@router.get("/sources/{source_id}")
async def get_source_detail(
    novel_id: UUID,
    source_id: UUID,
    session: AsyncSession = Depends(get_db),
) -> SourceDetailResponse:
    """获取单来源详情（含块 + 片段）。"""
    repo_s, repo_ch, repo_f = _get_repos(session)
    source = await repo_s.get_by_id(source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="source not found")

    chunks = await repo_ch.get_by_source(source_id)
    fragments = await repo_f.get_by_source(source_id)

    return SourceDetailResponse(
        source=_source_to_response(
            source,
            chunk_count=len(chunks),
            fragment_count=len(fragments),
        ),
        chunks=[_chunk_to_response(c) for c in chunks],
        fragments=[_fragment_to_response(f) for f in fragments],
    )


@router.delete("/sources/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
async def soft_delete_source(
    novel_id: UUID,
    source_id: UUID,
    session: AsyncSession = Depends(get_db),
) -> None:
    """软删素材来源。"""
    repo_s, _, _ = _get_repos(session)
    deleted = await repo_s.soft_delete(source_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="source not found")
    await session.commit()


@router.post("/sources/{source_id}/decompose")
async def start_decompose(
    novel_id: UUID,
    source_id: UUID,
    session: AsyncSession = Depends(get_db),
) -> dict:
    """启动拆解（只对 pending chunks）。

    注意：生产环境应使用 Celery/ARQ 等任务队列。
    当前实现直接同步拆解所有 pending chunks（适用于小型素材）。
    """
    from inkmind.llm.client import LLMClient
    from inkmind.materials.decomposer import MaterialDecomposer
    from inkmind.storage.unit_of_work import UnitOfWork

    repo_s, repo_ch, repo_f = _get_repos(session)
    source = await repo_s.get_by_id(source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="source not found")

    pending_chunks = await repo_ch.get_pending_by_source(source_id)
    if not pending_chunks:
        return {"status": "no_pending_chunks", "decomposed": 0, "total": 0}

    # 标记 source 为 processing
    source.status = "processing"
    await repo_s.save(source)
    await session.commit()

    # 逐个拆解 pending chunks
    llm_client = LLMClient()
    decomposer = MaterialDecomposer(llm_client)

    decomposed_count = 0
    failed_count = 0
    uow = UnitOfWork(session)

    for chunk in pending_chunks:
        try:
            fragments = await decomposer.decompose_chunk(chunk, source)
            await uow.t7_submit_decompose(
                chunk_id=chunk.id,
                fragments=fragments,
                chunk_status="done",
            )
            await session.commit()
            decomposed_count += 1
        except Exception as e:
            logger.error("Decompose chunk %s failed: %s", chunk.id, e)
            await uow.t7_submit_decompose(
                chunk_id=chunk.id,
                fragments=[],
                chunk_status="failed",
                error_message=str(e),
            )
            await session.commit()
            failed_count += 1

    # 更新 source 状态
    source.status = "done" if failed_count == 0 else "failed"
    await repo_s.save(source)
    await session.commit()

    # 拆解完成后重建 FTS 索引
    try:
        from inkmind.storage.search import FTSManager

        fts_mgr = FTSManager(session)
        await fts_mgr.rebuild()
    except Exception:
        pass

    return {
        "status": "done",
        "source_id": str(source_id),
        "decomposed": decomposed_count,
        "failed": failed_count,
        "total": len(pending_chunks),
    }


@router.get("/sources/{source_id}/decompose/progress")
async def get_decompose_progress(
    novel_id: UUID,
    source_id: UUID,
    session: AsyncSession = Depends(get_db),
) -> dict:
    """获取拆解进度。"""
    repo_s, repo_ch, repo_f = _get_repos(session)
    source = await repo_s.get_by_id(source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="source not found")

    chunks = await repo_ch.get_by_source(source_id)
    total = len(chunks)
    done = sum(1 for c in chunks if c.status == "done")
    failed = sum(1 for c in chunks if c.status == "failed")
    low_quality = sum(1 for c in chunks if c.status == "low_quality")
    pending = sum(1 for c in chunks if c.status == "pending")

    return {
        "source_id": str(source_id),
        "status": source.status,
        "total": total,
        "done": done,
        "failed": failed,
        "low_quality": low_quality,
        "pending": pending,
        "chunks": [
            {
                "id": str(c.id),
                "index": c.chunk_index,
                "status": c.status,
                "error_message": c.error_message,
                "retry_count": c.retry_count,
            }
            for c in chunks
        ],
    }


@router.get("/fragments")
async def list_fragments(
    novel_id: UUID,
    type_filter: str | None = Query(None, alias="type"),
    tag_filter: str | None = Query(None, alias="tag"),
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    session: AsyncSession = Depends(get_db),
) -> list[FragmentResponse]:
    """获取片段列表（按 novel，可筛选 type/tags）。"""
    _, _, repo_f = _get_repos(session)
    fragments = await repo_f.get_by_novel(
        novel_id,
        type_filter=type_filter,
        tag_filter=tag_filter,
        offset=offset,
        limit=limit,
    )
    return [_fragment_to_response(f) for f in fragments]


@router.patch("/fragments/{fragment_id}")
async def update_fragment(
    novel_id: UUID,
    fragment_id: UUID,
    body: UpdateFragmentRequest,
    session: AsyncSession = Depends(get_db),
) -> FragmentResponse:
    """编辑片段（触发 user_edited=true）。"""
    _, _, repo_f = _get_repos(session)
    fragment = await repo_f.get_by_id(fragment_id)
    if fragment is None:
        raise HTTPException(status_code=404, detail="fragment not found")

    if body.title is not None:
        fragment.title = body.title[:20]
    if body.content is not None:
        fragment.content = body.content[:2000]
    if body.type is not None:
        if body.type not in FRAGMENT_TYPES:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid type: {body.type}. Must be one of {FRAGMENT_TYPES}",
            )
        fragment.type = body.type
    if body.tags is not None:
        fragment.tags = body.tags
    if body.source_quote is not None:
        fragment.source_quote = body.source_quote[:50] if body.source_quote else None
    if body.reusability_note is not None:
        fragment.reusability_note = body.reusability_note
    if body.user_note is not None:
        fragment.user_note = body.user_note

    if body.clear_edited:
        fragment.user_edited = False
    else:
        fragment.user_edited = True

    await repo_f.save(fragment)
    await session.commit()

    # 同步 FTS 索引
    mgr = FTSManager(session)
    await mgr.ensure_table()
    # 获取 ORM 行 id（用于 FTS rowid）
    from inkmind.storage.models import MaterialFragmentModel
    from sqlalchemy import select as sql_select

    result = await session.execute(
        sql_select(MaterialFragmentModel.id).where(MaterialFragmentModel.uuid == str(fragment.id))
    )
    row_id = result.scalar_one_or_none()
    if row_id:
        await mgr.sync_fragment(row_id, fragment.title, fragment.content)

    return _fragment_to_response(fragment)


@router.delete("/fragments/{fragment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_fragment(
    novel_id: UUID,
    fragment_id: UUID,
    session: AsyncSession = Depends(get_db),
) -> None:
    """删除片段（跳过 user_edited）。"""
    _, _, repo_f = _get_repos(session)
    deleted = await repo_f.delete(fragment_id, skip_if_edited=True)
    if not deleted:
        # 可能 user_edited 保护或不存在
        fragment = await repo_f.get_by_id(fragment_id)
        if fragment is None:
            raise HTTPException(status_code=404, detail="fragment not found")
        raise HTTPException(
            status_code=409,
            detail="Cannot delete user_edited fragment",
        )
    await session.commit()

    # 同步 FTS 索引（删除）
    mgr = FTSManager(session)
    await mgr.ensure_table()
    # 已删除无法获取 row id，重建兜底
    try:
        await mgr.rebuild()
    except Exception:
        pass


@router.post("/fragments", status_code=status.HTTP_201_CREATED)
async def create_fragment(
    novel_id: UUID,
    body: CreateFragmentRequest,
    session: AsyncSession = Depends(get_db),
) -> FragmentResponse:
    """手工新建片段（归 pseudo-source 桶）。"""

    # 使用一个伪 source_id 来标识手工片段
    # 实际上查找或创建一个特殊标记的 source
    from inkmind.storage.digest import compute_content_digest

    repo_s, _, repo_f = _get_repos(session)

    # 查找或创建 pseudo-source
    pseudo_digest = f"pseudo-{novel_id}"
    pseudo = await repo_s.find_by_digest(novel_id, pseudo_digest)
    if pseudo is None:
        pseudo = MaterialSource(
            novel_id=novel_id,
            raw_text="",
            content_digest=pseudo_digest,
            status="done",
            word_count=0,
        )
        await repo_s.save(pseudo)

    # 创建伪 chunk
    from inkmind.models.materials import MaterialChunk

    pseudo_chunk = MaterialChunk(
        source_id=pseudo.id,
        chunk_index=0,
        content=body.content,
        content_digest=compute_content_digest(body.content),
        status="done",
    )
    await MaterialChunkRepository(session).save(pseudo_chunk)

    fragment = MaterialFragment(
        source_id=pseudo.id,
        source_chunk_id=pseudo_chunk.id,
        title=body.title,
        content=body.content,
        type=body.type if body.type in FRAGMENT_TYPES else "misc",
        tags=body.tags,
        source="手工创建",
        source_quote=body.source_quote,
        reusability_note=body.reusability_note,
        user_note=body.user_note,
        user_edited=True,
    )
    await repo_f.save(fragment)
    await session.commit()

    # 同步 FTS 索引
    mgr = FTSManager(session)
    await mgr.ensure_table()
    from inkmind.storage.models import MaterialFragmentModel
    from sqlalchemy import select as sql_select

    result = await session.execute(
        sql_select(MaterialFragmentModel.id).where(MaterialFragmentModel.uuid == str(fragment.id))
    )
    row_id = result.scalar_one_or_none()
    if row_id:
        await mgr.sync_fragment(row_id, fragment.title, fragment.content)

    return _fragment_to_response(fragment)


@router.post("/sources/{source_id}/rerun-failed")
async def rerun_failed_chunks(
    novel_id: UUID,
    source_id: UUID,
    session: AsyncSession = Depends(get_db),
) -> dict:
    """重跑失败块。"""
    repo_s, repo_ch, _ = _get_repos(session)
    source = await repo_s.get_by_id(source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="source not found")

    failed_chunks = await repo_ch.get_failed_by_source(source_id)
    now_pending = 0
    for c in failed_chunks:
        c.status = "pending"
        c.error_message = None
        c.retry_count = 0
        await repo_ch.save(c)
        now_pending += 1

    source.status = "processing"
    await repo_s.save(source)
    await session.commit()

    return {
        "status": "accepted",
        "rerun_count": now_pending,
        "message": f"{now_pending} 个失败块已重置为待处理",
    }


# ═══════════════════════════════════════════════════════
#  Issue #44: 素材搜索与标签自动补全
# ═══════════════════════════════════════════════════════


class SearchResultItem(BaseModel):
    """搜索结果项。"""

    id: str
    title: str
    content: str
    type: str
    tags: list[str]
    source: str
    source_quote: str | None = None
    reusability_note: str = ""
    user_note: str = ""
    user_edited: bool = False
    created_at: str
    source_id: str
    source_chunk_id: str


class SearchResponse(BaseModel):
    items: list[SearchResultItem]
    total: int
    page: int
    per_page: int


class TagSuggestion(BaseModel):
    tag: str
    count: int


@router.get("/search")
async def search_fragments(
    novel_id: UUID,
    q: str | None = Query(None, description="搜索关键词（空格分词 AND）"),
    type_filter: str | None = Query(None, alias="type", description="片段类型筛选"),
    tag_filter: str | None = Query(None, alias="tag", description="标签筛选"),
    page: int = Query(1, ge=1, description="页码"),
    per_page: int = Query(20, ge=1, le=100, description="每页条数"),
    session: AsyncSession = Depends(get_db),
) -> SearchResponse:
    """全文搜索素材片段。

    支持：
    - 关键词空格分词 AND 搜索
    - < 3 字符自动降级 LIKE
    - 类型多选 chip + 标签 chip 组合筛选
    - 分页
    """
    mgr = FTSManager(session)
    await mgr.ensure_table()
    result = await mgr.search(
        novel_id=novel_id,
        query=q,
        type_filter=type_filter,
        tag_filter=tag_filter,
        page=page,
        per_page=per_page,
    )
    return SearchResponse(
        items=[SearchResultItem(**item) for item in result["items"]],
        total=result["total"],
        page=result.get("page", page),
        per_page=result.get("per_page", per_page),
    )


@router.get("/tags")
async def get_tag_suggestions(
    novel_id: UUID,
    prefix: str | None = Query(None, description="输入前缀"),
    session: AsyncSession = Depends(get_db),
) -> list[TagSuggestion]:
    """标签自动补全。

    返回 Top 50 复用 + 3-6 条前缀建议。
    """
    mgr = FTSManager(session)
    await mgr.ensure_table()
    results = await mgr.tag_autocomplete(novel_id=novel_id, prefix=prefix)
    return [TagSuggestion(**r) for r in results]


@router.post("/rebuild-fts")
async def rebuild_fts(
    novel_id: UUID,
    session: AsyncSession = Depends(get_db),
) -> dict:
    """全量重建 FTS 索引（兜底用）。"""
    mgr = FTSManager(session)
    result = await mgr.rebuild()
    return result
