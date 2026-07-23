"""FastAPI 应用工厂。"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from inkmind.api.routes import annotations


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    db_path = getattr(app.state, "db_path", ".inkmind/data.db")
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    yield


def create_app(db_path: str | None = None) -> FastAPI:
    app = FastAPI(
        title="InkMind",
        description="InkMind — AI 小说协作写作系统",
        version="0.1.0",
        lifespan=lifespan,
    )

    if db_path:
        app.state.db_path = db_path

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(annotations.router)

    return app
