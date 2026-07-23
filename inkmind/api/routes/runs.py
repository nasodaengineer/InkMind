"""Run API 路由 — SSE 流、CRUD、取消、AI 规划。

提供 Run 执行生命周期的 HTTP 接口：
- POST   /api/novels/{novel_id}/runs           启动 run
- GET    /api/novels/{novel_id}/runs/{run_id}/stream  SSE 流
- GET    /api/novels/{novel_id}/runs            列 runs
- GET    /api/novels/{novel_id}/runs/{run_id}   单 run
- POST   /api/novels/{novel_id}/runs/{run_id}/cancel  取消

Issue #42: AI 大纲规划 — 支持 planner 参数。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from inkmind.execution.runner import PlanParams, RunLoop
from inkmind.llm.client import build_llm_client
from inkmind.models.agent import PlanLevel
from inkmind.models.run import RunKind, RunStatus
from inkmind.api.deps import get_db as get_session
from inkmind.storage.unit_of_work import UnitOfWork

router = APIRouter(tags=["runs"])


# ── Schema ────────────────────────────────────────────


class StartRunRequest(BaseModel):
    kind: str = Field(description="generate / revise / finalize / plan")
    chapter_id: str | None = Field(default=None, description="关联章节 UUID")

    # Issue #42: AI 大纲规划参数
    level: str | None = Field(
        default=None, description="规划级别: spine / volume / chapter / split_volumes"
    )
    prompt: str | None = Field(default=None, description="可选的提示文本，指导 LLM 生成方向")
    volume_count: int | None = Field(
        default=None, ge=2, le=20, description="拆卷数量（仅 split_volumes）"
    )
    confirm_overwrite: bool = Field(default=False, description="确认覆盖非空内容")
    volume_id: str | None = Field(default=None, description="关联的卷 UUID（仅 volume/chapter）")
    chapter_count: int | None = Field(
        default=None, ge=5, le=50, description="待规划的章节数（仅 chapter）"
    )


class RunResponse(BaseModel):
    id: str
    novel_id: str
    chapter_id: str | None = None
    kind: str
    status: str
    phase: str
    partial_content: str
    llm_stats: dict[str, Any] = Field(default_factory=dict)
    started_at: str | None = None
    completed_at: str | None = None
    created_at: str
    updated_at: str


class RunListResponse(BaseModel):
    runs: list[RunResponse]


# ── Helper ────────────────────────────────────────────


def _run_to_response(run) -> RunResponse:
    """将 Run 领域模型转换为 API 响应。"""
    return RunResponse(
        id=str(run.id),
        novel_id=str(run.novel_id),
        chapter_id=str(run.chapter_id) if run.chapter_id else None,
        kind=run.kind.value if hasattr(run.kind, "value") else str(run.kind),
        status=run.status.value if hasattr(run.status, "value") else str(run.status),
        phase=run.phase,
        partial_content=run.partial_content,
        llm_stats=run.llm_stats or {},
        started_at=run.started_at.isoformat() if run.started_at else None,
        completed_at=run.completed_at.isoformat() if run.completed_at else None,
        created_at=run.created_at.isoformat() if run.created_at else "",
        updated_at=run.updated_at.isoformat() if run.updated_at else "",
    )


async def _get_uow(
    session: AsyncSession = Depends(get_session),
) -> UnitOfWork:
    """依赖注入：获取 UnitOfWork。"""
    return UnitOfWork(session)


# ── 启动 Run ──────────────────────────────────────────


@router.post(
    "/novels/{novel_id}/runs",
    status_code=201,
    response_model=RunResponse,
)
async def start_run(
    novel_id: str,
    body: StartRunRequest,
    uow: UnitOfWork = Depends(_get_uow),
):
    """启动一段 Run 执行生命周期。

    同章重复启动返回 409。

    Issue #42: kind=plan 时支持 planner 参数（level/prompt/volume_count/confirm_overwrite/volume_id/chapter_count）。
    前置校验：
      - spine_required: spine/volume/split_volumes/chapter 均需总纲存在
      - zero_volume_409: split_volumes 时零卷返回 409
      - confirm_overwrite: spine/volume 非空时需确认覆盖
    """
    novel_uuid = UUID(novel_id)
    chapter_uuid = UUID(body.chapter_id) if body.chapter_id else None

    assert uow.spines is not None
    assert uow.volumes is not None
    assert uow.runs is not None

    try:
        kind = RunKind(body.kind)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"无效的 RunKind: {body.kind}，可选: generate/revise/finalize/plan",
        )

    # ── 前置校验（仅 kind=plan） ──────────────────────
    plan_params = PlanParams()
    if kind == RunKind.PLAN:
        # 解析 level
        if body.level:
            try:
                plan_params.level = PlanLevel(body.level)
            except ValueError:
                raise HTTPException(
                    status_code=400,
                    detail=f"无效的规划级别: {body.level}，可选: spine/volume/chapter/split_volumes",
                )

        plan_params.prompt = body.prompt
        plan_params.confirm_overwrite = body.confirm_overwrite
        if body.volume_count is not None:
            plan_params.volume_count = body.volume_count
        if body.chapter_count is not None:
            plan_params.chapter_count = body.chapter_count
        if body.volume_id:
            plan_params.volume_id = UUID(body.volume_id)

        # 校验 1: spine_required — 除 spine 外其他 level 均需总纲存在
        if plan_params.level != PlanLevel.SPINE:
            existing_spine = await uow.spines.get_by_novel(novel_uuid)
            if existing_spine is None:
                raise HTTPException(
                    status_code=400,
                    detail="spine_required: 总纲不存在，请先起草总纲",
                )

        # 校验 2: zero_volume_409 — 零卷时不可拆卷（冷启动）
        if plan_params.level == PlanLevel.SPLIT_VOLUMES:
            existing_volumes = await uow.volumes.get_by_novel(novel_uuid)
            if len(existing_volumes) > 0:
                raise HTTPException(
                    status_code=409,
                    detail="zero_volume_409: 已有卷存在，拆卷仅支持零卷冷启动。"
                    "如需新增卷请使用「添加卷」手动操作。",
                )

        # 校验 3: volume_id required for volume/chapter
        if plan_params.level in (PlanLevel.VOLUME, PlanLevel.CHAPTER):
            if plan_params.volume_id is None:
                raise HTTPException(
                    status_code=400,
                    detail=f"{plan_params.level.value} 需要 volume_id",
                )

        # 校验 4: confirm_overwrite — spine/volume 非空时需确认
        if not plan_params.confirm_overwrite:
            if plan_params.level == PlanLevel.SPINE:
                existing_spine = await uow.spines.get_by_novel(novel_uuid)
                if existing_spine is not None:
                    has_content = any(
                        [
                            existing_spine.main_line,
                            existing_spine.core_conflict,
                            existing_spine.ending,
                            existing_spine.selling_points,
                            existing_spine.world_background,
                            existing_spine.golden_finger,
                        ]
                    )
                    if has_content:
                        raise HTTPException(
                            status_code=400,
                            detail="confirm_overwrite: 总纲非空，需确认覆盖（confirm_overwrite=true）",
                        )

            if plan_params.level == PlanLevel.VOLUME and plan_params.volume_id:
                existing_vol = await uow.volumes.get_by_id(plan_params.volume_id)
                if existing_vol is not None:
                    has_content = any(
                        [
                            existing_vol.stage_goal,
                            existing_vol.main_line,
                            existing_vol.side_line,
                            existing_vol.volume_cliffhanger,
                        ]
                    )
                    if has_content:
                        raise HTTPException(
                            status_code=400,
                            detail="confirm_overwrite: 卷纲非空，需确认覆盖（confirm_overwrite=true）",
                        )

    try:
        run_id = await uow.t8_run_start(
            novel_id=novel_uuid,
            kind=kind,
            chapter_id=chapter_uuid,
        )
    except ValueError as e:
        if "已有正在执行的 Run" in str(e):
            raise HTTPException(status_code=409, detail=str(e))
        raise HTTPException(status_code=400, detail=str(e))

    run = await uow.runs.get_by_id(run_id)
    if run is None:
        raise HTTPException(status_code=500, detail="创建 Run 失败")

    # Issue #42: 持久化 plan_params 到 run 的 llm_stats 透传
    if kind == RunKind.PLAN:
        run.llm_stats = run.llm_stats or {}
        run.llm_stats["plan_params"] = {
            "level": plan_params.level.value,
            "prompt": plan_params.prompt,
            "volume_count": plan_params.volume_count,
            "confirm_overwrite": plan_params.confirm_overwrite,
            "volume_id": str(plan_params.volume_id) if plan_params.volume_id else None,
            "chapter_count": plan_params.chapter_count,
        }
        await uow.runs.save(run)
        await uow.commit()

    return _run_to_response(run)


# ── SSE 流 ────────────────────────────────────────────


@router.get(
    "/novels/{novel_id}/runs/{run_id}/stream",
    response_class=StreamingResponse,
)
async def stream_run(
    novel_id: str,
    run_id: str,
    chapter_id: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
):
    """SSE 流式获取 Run 执行进度。

    事件类型:
      - phase:    阶段变更 {phase: "writing|reviewing|revising|complete"}
      - token:    流式 token 分片
      - verdict:  Editor 评审结论 {verdict: "approve|needs_revision"}
      - done:     执行完成 {status: "completed|failed|cancelled|awaiting_human"}
      - error:    执行错误 {message: "..."}

    重连行为:
      如果 Run 已存在且未 running，返回当前快照后结束。
    """
    run_uuid = UUID(run_id)
    novel_uuid = UUID(novel_id)
    chapter_uuid = UUID(chapter_id) if chapter_id else None

    # 检查 Run 状态（UnitOfWork 仅支持 sync context manager）
    with UnitOfWork(session) as uow:
        assert uow.runs is not None
        run = await uow.runs.get_by_id(run_uuid)

    if run is None:
        raise HTTPException(status_code=404, detail="Run 不存在")

    # Issue #42: 读取 plan_params（存于 run 的 llm_stats 中作为透传）
    plan_params = None
    if run.llm_stats and "plan_params" in run.llm_stats:
        pp = run.llm_stats["plan_params"]
        plan_params = PlanParams(
            level=PlanLevel(pp.get("level", "chapter")) if pp.get("level") else PlanLevel.CHAPTER,
            prompt=pp.get("prompt"),
            volume_count=pp.get("volume_count", 5),
            confirm_overwrite=pp.get("confirm_overwrite", False),
            volume_id=UUID(pp["volume_id"]) if pp.get("volume_id") else None,
            chapter_count=pp.get("chapter_count", 10),
        )

    # 非 running 状态 — 返回快照后结束
    if run.status != RunStatus.RUNNING:
        return _snapshot_sse_response(run)

    # running 状态 — 启动 RunLoop 并流式推送
    return _streaming_run_response(session, run_uuid, novel_uuid, chapter_uuid, plan_params)


def _snapshot_sse_response(run) -> StreamingResponse:
    """为已完成/已中断的 Run 返回快照 SSE。"""
    events = []
    events.append(f"event: phase\ndata: {json.dumps({'phase': run.phase})}\n\n")
    events.append(f"event: done\ndata: {json.dumps({'status': run.status.value})}\n\n")

    async def generate():
        for event in events:
            yield event

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _streaming_run_response(
    session: AsyncSession,
    run_uuid: UUID,
    novel_uuid: UUID,
    chapter_uuid: UUID | None,
    plan_params: PlanParams | None = None,
) -> StreamingResponse:
    """创建流式 SSE 响应，在后台 run loop 中执行。"""

    async def event_generator():
        # 为 SSE 构建独立 UoW + LLMClient
        # UnitOfWork 仅支持 sync context manager，用 with 而非 async with
        llm = build_llm_client()

        with UnitOfWork(session) as uow:
            # 获取 chapter 和 outline
            chapter = None
            if chapter_uuid:
                chapter = await uow.chapters.get_by_id(chapter_uuid)

            # 获取 Run 信息
            run = await uow.runs.get_by_id(run_uuid)
            if run is None:
                yield f"event: error\ndata: {json.dumps({'message': 'Run 不存在'})}\n\n"
                return

            # 创建 RunLoop（issue #42: 传递 plan_params）
            loop = RunLoop.create(uow, llm, run_uuid, chapter=chapter, plan_params=plan_params)
            queue: asyncio.Queue = asyncio.Queue()

            def event_listener(event: str, data: Any) -> None:
                queue.put_nowait((event, data))

            loop.events.subscribe(event_listener)

            # 启动执行协程（后台任务）
            async def run_exec():
                try:
                    await loop.start_run(run.kind, novel_uuid, chapter_uuid)
                except Exception as e:
                    queue.put_nowait(("error", {"message": str(e)}))
                finally:
                    queue.put_nowait(("done", {"status": "completed"}))

            exec_task = asyncio.create_task(run_exec())

            try:
                # 推送当前阶段快照
                if run.phase:
                    yield f"event: phase\ndata: {json.dumps({'phase': run.phase})}\n\n"

                # 循环消费队列事件
                while True:
                    try:
                        event, data = await asyncio.wait_for(queue.get(), timeout=30.0)
                    except asyncio.TimeoutError:
                        # 心跳保活
                        yield ": heartbeat\n\n"
                        continue

                    if event == "token":
                        yield f"data: {json.dumps(data)}\n\n"
                    elif event == "phase":
                        yield f"event: phase\ndata: {json.dumps(data)}\n\n"
                    elif event == "verdict":
                        yield f"event: verdict\ndata: {json.dumps(data)}\n\n"
                    elif event == "result":
                        yield f"event: result\ndata: {json.dumps(data)}\n\n"
                    elif event == "error":
                        yield f"event: error\ndata: {json.dumps(data)}\n\n"
                        break
                    elif event == "done":
                        yield f"event: done\ndata: {json.dumps(data)}\n\n"
                        break

            finally:
                exec_task.cancel()
                try:
                    await exec_task
                except asyncio.CancelledError:
                    pass
                await llm.shutdown()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ── 列 Runs ────────────────────────────────────────────


@router.get(
    "/novels/{novel_id}/runs",
    response_model=RunListResponse,
)
async def list_runs(
    novel_id: str,
    uow: UnitOfWork = Depends(_get_uow),
):
    """获取小说的所有 Run 记录。"""
    assert uow.runs is not None

    novel_uuid = UUID(novel_id)
    runs = await uow.runs.get_by_novel(novel_uuid)
    return RunListResponse(runs=[_run_to_response(r) for r in runs])


# ── 单 Run ────────────────────────────────────────────


@router.get(
    "/novels/{novel_id}/runs/{run_id}",
    response_model=RunResponse,
)
async def get_run(
    novel_id: str,
    run_id: str,
    uow: UnitOfWork = Depends(_get_uow),
):
    """获取单条 Run 记录。"""
    assert uow.runs is not None

    run_uuid = UUID(run_id)
    run = await uow.runs.get_by_id(run_uuid)
    if run is None:
        raise HTTPException(status_code=404, detail="Run 不存在")
    return _run_to_response(run)


# ── 取消 Run ────────────────────────────────────────────


@router.post(
    "/novels/{novel_id}/runs/{run_id}/cancel",
    response_model=RunResponse,
)
async def cancel_run(
    novel_id: str,
    run_id: str,
    uow: UnitOfWork = Depends(_get_uow),
):
    """取消一个正在执行的 Run。

    设置取消标记并持久化终态。
    """
    assert uow.runs is not None

    run_uuid = UUID(run_id)
    run = await uow.runs.get_by_id(run_uuid)
    if run is None:
        raise HTTPException(status_code=404, detail="Run 不存在")

    if run.status != RunStatus.RUNNING:
        raise HTTPException(
            status_code=409,
            detail=f"Run 状态为 {run.status.value}，无法取消",
        )

    run.cancel()
    await uow.runs.save(run)
    await uow.commit()

    return _run_to_response(run)
