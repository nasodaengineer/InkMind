"""观测统计面板 API 端点。

提供三档时间窗（today/7d/all）的 LLM 调用统计总览、
四维拆分（provider/model/agent/error）、Run 历史与压缩任务查询。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from inkmind.api.deps import get_db
from inkmind.storage.repositories import StatsRepository

router = APIRouter(prefix="/api/stats", tags=["stats"])


@router.get("/overview")
async def get_stats_overview(
    window: str = Query("all", pattern="^(today|7d|all)$"),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """获取统计总览（三档时间窗）。

    Returns:
        {total_calls, total_tokens, total_cost, avg_latency_ms, success_rate, degradation_rate}
    """
    repo = StatsRepository(session)
    return await repo.get_overview(window)


@router.get("/breakdown")
async def get_stats_breakdown(
    window: str = Query("all", pattern="^(today|7d|all)$"),
    dimension: str = Query("provider", pattern="^(provider|model|agent|error)$"),
    session: AsyncSession = Depends(get_db),
) -> list[dict]:
    """获取四维拆分统计。

    Args:
        window: 时间窗 today / 7d / all
        dimension: 拆分维度 provider / model / agent / error

    Returns:
        [{dimension, calls, total_tokens, total_cost, avg_latency_ms, success_rate}, ...]
    """
    repo = StatsRepository(session)
    return await repo.get_breakdown(window, dimension)


@router.get("/runs")
async def get_stats_runs(
    window: str = Query("all", pattern="^(today|7d|all)$"),
    session: AsyncSession = Depends(get_db),
) -> list[dict]:
    """获取 Run 历史列表。

    Returns:
        [{id, novel_id, chapter_id, kind, status, phase, created_at, started_at, completed_at}, ...]
    """
    repo = StatsRepository(session)
    return await repo.get_runs(window)


@router.get("/compression-tasks")
async def get_compression_tasks(
    session: AsyncSession = Depends(get_db),
) -> list[dict]:
    """获取压缩任务列表（含失败任务的高亮信息）。

    Returns:
        [{task_id, novel_id, range_start, range_end, status, error_message, ...}, ...]
    """
    repo = StatsRepository(session)
    return await repo.get_compression_tasks()
