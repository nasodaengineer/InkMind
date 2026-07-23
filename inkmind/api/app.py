"""FastAPI 应用工厂。"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from inkmind.api.routes import (
    health,
    novels,
    chapters,
    volumes,
    spine,
    runs,
    materials,
    settings,
    stats,
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """应用生命周期。

    启动时：确保数据目录存在。
    """
    db_path = getattr(app.state, "db_path", ".inkmind/data.db")
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    yield


def create_app(db_path: str | None = None) -> FastAPI:
    """创建并返回 FastAPI 应用实例。

    Args:
        db_path: SQLite 数据库路径。None 则从环境变量或默认值读取。
    """
    app = FastAPI(
        title="InkMind",
        description="InkMind — AI 小说协作写作系统",
        version="0.1.0",
        lifespan=lifespan,
    )

    # ── 状态 ──
    if db_path:
        app.state.db_path = db_path

    # ── CORS（开发时 vite proxy 可覆盖，生产同域不需要但无害）──
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── API 路由 ──
    app.include_router(health.router)
    app.include_router(novels.router)
    app.include_router(chapters.router)
    app.include_router(volumes.router)
    app.include_router(spine.router)
    app.include_router(runs.router)
    app.include_router(materials.router)
    app.include_router(settings.router)
    app.include_router(stats.router)

    # ── 静态文件与 SPA fallback（生产模式）──
    dist = Path(__file__).resolve().parent.parent.parent / "web" / "dist"
    if dist.is_dir():
        # 静态资源（带 hash 的文件名）
        app.mount("/assets/", StaticFiles(directory=str(dist / "assets")), name="assets")
        # SPA fallback: index.html + 任意非 /api 路径回退
        from fastapi.responses import HTMLResponse

        index_html = (dist / "index.html").read_text(encoding="utf-8")

        @app.get("/{full_path:path}", include_in_schema=False)
        async def spa_fallback(full_path: str):
            if full_path.startswith("api/"):
                from fastapi.responses import JSONResponse

                return JSONResponse(
                    {"error": {"code": 404, "message": "not found"}}, status_code=404
                )
            return HTMLResponse(index_html)
    else:
        # 无 dist 时 /api 以外的路径返回 404 提示
        from fastapi.responses import JSONResponse

        @app.get("/{full_path:path}", include_in_schema=False)
        async def no_dist_fallback(full_path: str):
            return JSONResponse(
                {"error": {"code": 404, "message": "前端尚未构建，请运行 cd web && pnpm build"}},
                status_code=404,
            )

    return app
