"""FTS5 全文搜索引擎 — 素材片段搜索。

管理 fragments_fts FTS5 虚拟表，提供全文搜索、标签过滤、
自动补全和索引同步功能。

架构：
  - fragments_fts 表只索引 fragments 的 title + content
  - 使用 trigram tokenizer 支持中英文混合查询
  - < 3 字符查询自动降级为 LIKE
  - 标签过滤通过 json_each 实现
  - 应用层同步 + rebuild 兜底
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# FTS5 虚拟表 DDL（trigram tokenizer 支持 CJK 子串匹配）
FTS5_TABLE_SQL = """
CREATE VIRTUAL TABLE IF NOT EXISTS fragments_fts USING fts5(
    title,
    content,
    content=material_fragments,
    content_rowid=id,
    tokenize='trigram'
)
"""

# 从 content 表全量重建 FTS 索引
FTS5_REBUILD_SQL = "INSERT INTO fragments_fts(fragments_fts, rank) VALUES('rebuild', 0)"

# 增删改查
FTS5_INSERT = "INSERT INTO fragments_fts(rowid, title, content) VALUES(:rowid, :title, :content)"
FTS5_DELETE = "DELETE FROM fragments_fts WHERE rowid = :rowid"

# LIKE 降级查询（用于 < 3 字符或 FTS5 不可用时的降级）
LIKE_SEARCH_SQL = """
SELECT f.id, f.title, f.content, f.type, f.tags, f.source,
       f.source_quote, f.reusability_note, f.user_note, f.user_edited,
       f.created_at, f.source_id, f.source_chunk_id
FROM material_fragments f
JOIN material_sources s ON f.source_id = s.uuid
WHERE s.novel_id = :novel_id AND s.is_deleted = 0
  AND (f.title LIKE :qpat OR f.content LIKE :qpat)
  AND (:type_filter IS NULL OR f.type = :type_filter)
  AND (:tag_filter IS NULL OR EXISTS (
      SELECT 1 FROM json_each(f.tags) AS j WHERE j.value = :tag_filter
  ))
ORDER BY f.created_at DESC
LIMIT :limit OFFSET :offset
"""

# FTS5 搜索查询
FTS5_SEARCH_SQL = """
SELECT f.id, f.title, f.content, f.type, f.tags, f.source,
       f.source_quote, f.reusability_note, f.user_note, f.user_edited,
       f.created_at, f.source_id, f.source_chunk_id
FROM fragments_fts
JOIN material_fragments f ON fragments_fts.rowid = f.id
JOIN material_sources s ON f.source_id = s.uuid
WHERE fragments_fts MATCH :query
  AND s.novel_id = :novel_id AND s.is_deleted = 0
  AND (:type_filter IS NULL OR f.type = :type_filter)
  AND (:tag_filter IS NULL OR EXISTS (
      SELECT 1 FROM json_each(f.tags) AS j WHERE j.value = :tag_filter
  ))
ORDER BY rank
LIMIT :limit OFFSET :offset
"""

# 标签自动补全（前缀过滤）
TAG_AUTOCOMPLETE_SQL = """
SELECT j.value AS tag, COUNT(*) AS cnt
FROM material_fragments f
JOIN material_sources s ON f.source_id = s.uuid
CROSS JOIN json_each(f.tags) AS j
WHERE s.novel_id = :novel_id AND s.is_deleted = 0
  AND (:prefix IS NULL OR j.value LIKE :prefix_pat)
GROUP BY j.value
ORDER BY cnt DESC, j.value ASC
LIMIT 50
"""

# 全部标签（用于高频复用候选）
TOP_TAGS_SQL = """
SELECT j.value AS tag, COUNT(*) AS cnt
FROM material_fragments f
JOIN material_sources s ON f.source_id = s.uuid
CROSS JOIN json_each(f.tags) AS j
WHERE s.novel_id = :novel_id AND s.is_deleted = 0
GROUP BY j.value
ORDER BY cnt DESC, j.value ASC
LIMIT 50
"""


class FTSManager:
    """FTS5 全文搜索引擎管理器。

    使用方式：
        mgr = FTSManager(session)
        await mgr.ensure_table()
        await mgr.sync_fragment(fragment_id, title, content)
        results = await mgr.search(novel_id, q, type_filter, tag_filter)
    """

    def __init__(self, session: AsyncSession):
        self._session = session
        self._fts_available: bool | None = None

    async def ensure_table(self) -> bool:
        """确保 FTS5 虚拟表存在。返回 True 表示可用。"""
        try:
            await self._session.execute(text(FTS5_TABLE_SQL))
            self._fts_available = True
            return True
        except Exception as e:
            logger.warning("FTS5 表创建失败，将降级为 LIKE 搜索: %s", e)
            self._fts_available = False
            return False

    async def sync_fragment(self, fragment_id: int, title: str, content: str) -> None:
        """索引或更新单个片段到 FTS 表。"""
        if self._fts_available is False:
            return
        if self._fts_available is None:
            await self.ensure_table()
        if not self._fts_available:
            return
        try:
            await self._session.execute(
                text(FTS5_INSERT),
                {"rowid": fragment_id, "title": title, "content": content},
            )
        except Exception as e:
            logger.warning("FTS sync fragment %s 失败: %s", fragment_id, e)

    async def remove_fragment(self, fragment_id: int) -> None:
        """从 FTS 表删除片段。"""
        if self._fts_available is False:
            return
        if self._fts_available is None:
            await self.ensure_table()
        if not self._fts_available:
            return
        try:
            await self._session.execute(
                text(FTS5_DELETE),
                {"rowid": fragment_id},
            )
        except Exception as e:
            logger.warning("FTS remove fragment %s 失败: %s", fragment_id, e)

    async def rebuild(self) -> dict[str, Any]:
        """全量重建 FTS 索引。返回 {status, count}。"""
        if self._fts_available is False:
            # 标记为 None 以重试 ensure_table
            self._fts_available = None
        await self.ensure_table()
        if not self._fts_available:
            return {"status": "fts_not_available", "count": 0}

        try:
            # 先清空
            await self._session.execute(text("DELETE FROM fragments_fts"))
            # 重建
            await self._session.execute(text(FTS5_REBUILD_SQL))
            # 计数
            result = await self._session.execute(text("SELECT COUNT(*) FROM fragments_fts"))
            count = result.scalar() or 0
            logger.info("FTS 索引重建完成，共 %s 条", count)
            return {"status": "ok", "count": count}
        except Exception as e:
            logger.error("FTS 索引重建失败: %s", e)
            return {"status": "error", "error": str(e), "count": 0}

    async def search(
        self,
        novel_id: UUID,
        query: str | None = None,
        type_filter: str | None = None,
        tag_filter: str | None = None,
        page: int = 1,
        per_page: int = 20,
    ) -> dict[str, Any]:
        """全文搜索素材片段。

        Args:
            novel_id: 小说 UUID
            query: 搜索关键词（空格分词 AND）
            type_filter: 片段类型筛选
            tag_filter: 标签筛选
            page: 页码（从 1 开始）
            per_page: 每页条数（最大 100）

        Returns:
            {"items": [...], "total": int, "page": int, "per_page": int}
        """
        offset = (page - 1) * per_page
        params: dict[str, Any] = {
            "novel_id": str(novel_id),
            "type_filter": type_filter,
            "tag_filter": tag_filter,
            "limit": per_page,
            "offset": offset,
        }

        # 无关键词时：按最新排序 + 筛选
        if not query or not query.strip():
            return await self._list_all(params)

        query = query.strip()

        # < 3 字符查询：降级 LIKE
        if len(query) < 3:
            return await self._like_search(params, query)

        # 尝试 FTS5 搜索
        if self._fts_available is False:
            return await self._like_search(params, query)
        if self._fts_available is None:
            await self.ensure_table()

        if self._fts_available:
            fts_result = await self._fts_search(params, query)
            if fts_result is not None:
                return fts_result

        # FTS5 失败降级 LIKE
        return await self._like_search(params, query)

    async def _list_all(self, params: dict) -> dict[str, Any]:
        """无关键词：按最新排序返回。"""
        sql = """
        SELECT f.id, f.title, f.content, f.type, f.tags, f.source,
               f.source_quote, f.reusability_note, f.user_note, f.user_edited,
               f.created_at, f.source_id, f.source_chunk_id
        FROM material_fragments f
        JOIN material_sources s ON f.source_id = s.uuid
        WHERE s.novel_id = :novel_id AND s.is_deleted = 0
          AND (:type_filter IS NULL OR f.type = :type_filter)
          AND (:tag_filter IS NULL OR EXISTS (
              SELECT 1 FROM json_each(f.tags) AS j WHERE j.value = :tag_filter
          ))
        ORDER BY f.created_at DESC
        LIMIT :limit OFFSET :offset
        """
        rows = await self._session.execute(text(sql), params)
        items = [self._row_to_dict(r) for r in rows.fetchall()]

        count_sql = """
        SELECT COUNT(*)
        FROM material_fragments f
        JOIN material_sources s ON f.source_id = s.uuid
        WHERE s.novel_id = :novel_id AND s.is_deleted = 0
          AND (:type_filter IS NULL OR f.type = :type_filter)
          AND (:tag_filter IS NULL OR EXISTS (
              SELECT 1 FROM json_each(f.tags) AS j WHERE j.value = :tag_filter
          ))
        """
        count_row = await self._session.execute(text(count_sql), params)
        total = count_row.scalar() or 0

        return {
            "items": items,
            "total": total,
            "page": page_from_params(params),
            "per_page": params["limit"],
        }

    async def _like_search(self, params: dict, query: str) -> dict[str, Any]:
        """LIKE 降级搜索。"""
        qpat = f"%{query}%"
        params_with_q = {**params, "qpat": qpat}
        rows = await self._session.execute(text(LIKE_SEARCH_SQL), params_with_q)
        items = [self._row_to_dict(r) for r in rows.fetchall()]

        count_sql = """
        SELECT COUNT(*)
        FROM material_fragments f
        JOIN material_sources s ON f.source_id = s.uuid
        WHERE s.novel_id = :novel_id AND s.is_deleted = 0
          AND (f.title LIKE :qpat OR f.content LIKE :qpat)
          AND (:type_filter IS NULL OR f.type = :type_filter)
          AND (:tag_filter IS NULL OR EXISTS (
              SELECT 1 FROM json_each(f.tags) AS j WHERE j.value = :tag_filter
          ))
        """
        count_row = await self._session.execute(text(count_sql), params_with_q)
        total = count_row.scalar() or 0

        page = (params["offset"] // params["limit"]) + 1 if params["limit"] > 0 else 1
        return {"items": items, "total": total, "page": page, "per_page": params["limit"]}

    async def _fts_search(self, params: dict, query: str) -> dict[str, Any] | None:
        """FTS5 搜索，失败返回 None。"""
        # 空格分词 AND 转换为 FTS5 查询语法
        terms = query.split()
        fts_query = " AND ".join(f'"{t}"' for t in terms)

        fts_params = {**params, "query": fts_query}
        try:
            rows = await self._session.execute(text(FTS5_SEARCH_SQL), fts_params)
            items = [self._row_to_dict(r) for r in rows.fetchall()]

            # FTS5 计数
            count_sql = """
            SELECT COUNT(*)
            FROM fragments_fts
            JOIN material_fragments f ON fragments_fts.rowid = f.id
            JOIN material_sources s ON f.source_id = s.uuid
            WHERE fragments_fts MATCH :query
              AND s.novel_id = :novel_id AND s.is_deleted = 0
              AND (:type_filter IS NULL OR f.type = :type_filter)
              AND (:tag_filter IS NULL OR EXISTS (
                  SELECT 1 FROM json_each(f.tags) AS j WHERE j.value = :tag_filter
              ))
            """
            count_row = await self._session.execute(text(count_sql), fts_params)
            total = count_row.scalar() or 0

            page = (params["offset"] // params["limit"]) + 1 if params["limit"] > 0 else 1
            return {"items": items, "total": total, "page": page, "per_page": params["limit"]}
        except Exception as e:
            logger.warning("FTS5 搜索失败，降级 LIKE: %s", e)
            return None

    async def tag_autocomplete(
        self,
        novel_id: UUID,
        prefix: str | None = None,
    ) -> list[dict[str, Any]]:
        """标签自动补全。

        返回 Top 50 复用 + 如果 prefix 有值额外返回 3-6 条建议。

        Args:
            novel_id: 小说 UUID
            prefix: 输入前缀（可选）

        Returns:
            [{"tag": str, "count": int}, ...]
        """
        if prefix and prefix.strip():
            pat = f"%{prefix.strip()}%"
            params = {
                "novel_id": str(novel_id),
                "prefix": prefix.strip(),
                "prefix_pat": pat,
            }
            rows = await self._session.execute(text(TAG_AUTOCOMPLETE_SQL), params)
            filtered = [{"tag": r[0], "count": r[1]} for r in rows.fetchall()]

            # 如果没有匹配的前缀结果，返回 Top 50
            if not filtered:
                return await self._top_tags(novel_id)

            # 如果匹配结果太少（< 3），补充 Top 高频到 6 条
            if len(filtered) < 3:
                top = await self._top_tags(novel_id)
                existing = {t["tag"] for t in filtered}
                for t in top:
                    if t["tag"] not in existing:
                        filtered.append(t)
                        if len(filtered) >= 6:
                            break

            return filtered[:50]

        return await self._top_tags(novel_id)

    async def _top_tags(self, novel_id: UUID) -> list[dict[str, Any]]:
        """Top 50 高频标签。"""
        params = {"novel_id": str(novel_id)}
        rows = await self._session.execute(text(TOP_TAGS_SQL), params)
        return [{"tag": r[0], "count": r[1]} for r in rows.fetchall()]

    def _row_to_dict(self, row) -> dict:
        """将查询行转换为响应字典。"""
        return {
            "id": str(row[0]),
            "title": row[1],
            "content": row[2],
            "type": row[3],
            "tags": row[4] or [],
            "source": row[5],
            "source_quote": row[6],
            "reusability_note": row[7] or "",
            "user_note": row[8] or "",
            "user_edited": row[9],
            "created_at": row[10].isoformat() if hasattr(row[10], "isoformat") else str(row[10]),
            "source_id": str(row[11]),
            "source_chunk_id": str(row[12]),
        }


def page_from_params(params: dict) -> int:
    """从 params offset/limit 计算页码。"""
    limit = int(params.get("limit", 20))
    offset = int(params.get("offset", 0))
    if limit and limit > 0:
        return (offset // limit) + 1
    return 1
