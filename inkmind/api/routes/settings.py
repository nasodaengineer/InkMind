"""设置管理端点 — LLM 配置 CRUD + Provider 模型查询。

遵循 Issue #47 规格：
- API Key 仅返回 {name: "已设置"}，不出现在 DB 与响应体
- 保存后下一 run 生效（T11 只写入，不重建 LLMClient）
- 表不存在/无记录时代码默认兜底
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from inkmind.api.deps import get_db
from inkmind.models.llm import LLMConfig
from inkmind.storage.repositories import AppSettingsRepository
from inkmind.storage.unit_of_work import UnitOfWork

router = APIRouter(prefix="/api/settings", tags=["settings"])


# ── 请求/响应模型 ──


class ProviderItem(BaseModel):
    """Provider 配置项（API Key 仅暴露有无标记）。"""

    name: str
    protocol: str
    base_url: str
    api_key: str | None  # "已设置" 或 null
    models: list[str]
    max_concurrent: int
    max_keepalive: int
    max_calls_per_minute: int


class ModelBindingItem(BaseModel):
    """Agent → 模型绑定项。"""

    agent_role: str
    primary_model: str
    fallback_models: list[str]


class RetryConfigItem(BaseModel):
    """重试配置（只读展示）。"""

    max_retries: int
    base_delay_s: float
    non_retryable_statuses: list[int]


class SettingsResponse(BaseModel):
    """完整设置响应。"""

    providers: dict[str, ProviderItem]
    model_router: dict  # {bindings: [...]}
    retry: RetryConfigItem
    default_model: str


class SettingsUpdateRequest(BaseModel):
    """保存设置请求体。"""

    providers: dict
    model_router: dict
    retry: dict
    default_model: str


# ── 内部辅助 ──


def _is_api_key_set(api_key_env: str) -> str | None:
    """检查环境变量中是否有对应 API Key。"""
    import os

    return "已设置" if api_key_env and os.environ.get(api_key_env) else None


def _build_settings_response(config: LLMConfig) -> SettingsResponse:
    """从 LLMConfig 构建 API 响应（过滤 API Key）。"""
    providers: dict[str, ProviderItem] = {}
    for name, pc in config.providers.items():
        providers[name] = ProviderItem(
            name=pc.name,
            protocol=pc.protocol.value,
            base_url=pc.base_url,
            api_key=_is_api_key_set(pc.api_key_env),
            models=list(pc.models),
            max_concurrent=pc.max_concurrent,
            max_keepalive=pc.max_keepalive,
            max_calls_per_minute=pc.max_calls_per_minute,
        )
    return SettingsResponse(
        providers=providers,
        model_router={
            "bindings": [
                {
                    "agent_role": b.agent_role,
                    "primary_model": b.primary_model,
                    "fallback_models": list(b.fallback_models),
                }
                for b in config.router.bindings
            ]
        },
        retry=RetryConfigItem(
            max_retries=config.retry.max_retries,
            base_delay_s=config.retry.base_delay_s,
            non_retryable_statuses=list(config.retry.non_retryable_statuses),
        ),
        default_model=config.default_model,
    )


def _collect_all_models(data: dict) -> list[str]:
    """从 settings dict 中收集所有 provider 的模型名列表。"""
    models: set[str] = set()
    for pname, pc in data.get("providers", {}).items():
        for m in pc.get("models", []):
            models.add(m)
    return sorted(models)


def _validate_bindings(data: dict) -> None:
    """校验 model_router.bindings 中模型名是否在 providers 的模型列表中。

    Raises:
        HTTPException(422): 绑定引用了不存在的模型
    """
    all_models = _collect_all_models(data)
    if not all_models:
        return  # 无 provider 配置时不校验（清空状态允许）

    bindings = data.get("model_router", {}).get("bindings", [])
    for b in bindings:
        model = b.get("primary_model", "")
        if model and model not in all_models:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"模型「{model}」未在任何 Provider 的 models 列表中（agent_role: {b.get('agent_role')}）",
            )
        for fb in b.get("fallback_models", []):
            if fb and fb not in all_models:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"降级模型「{fb}」未在任何 Provider 的 models 列表中（agent_role: {b.get('agent_role')}）",
                )


def _strip_api_keys(data: dict) -> dict:
    """从 data 中移除 api_key 字段（仅用于存储，不持久化 key 值）。"""
    cleaned = dict(data)
    providers = {}
    for name, pc in cleaned.get("providers", {}).items():
        p = dict(pc)
        p.pop("api_key", None)
        providers[name] = p
    cleaned["providers"] = providers
    return cleaned


# ── 端点 ──


@router.get("")
async def get_settings(
    session: AsyncSession = Depends(get_db),
) -> SettingsResponse:
    """获取当前 LLM 配置（合并 app_settings + 代码默认）。"""
    repo = AppSettingsRepository(session)
    saved = await repo.get()
    if saved is None:
        # 无 DB 记录 → 返回代码默认
        config = LLMConfig()
    else:
        config = LLMConfig.load_or_default(saved)
    return _build_settings_response(config)


@router.put("")
async def save_settings(
    body: SettingsUpdateRequest,
    session: AsyncSession = Depends(get_db),
) -> SettingsResponse:
    """保存 LLM 配置（T11）。

    校验:
        - 模型名白名单: binding 模型名必须在 providers 的模型列表中
        - API Key 不持久化
    """
    data = body.model_dump()

    # 1. 白名单校验
    _validate_bindings(data)

    # 2. 移除 API Key（不持久化）
    cleaned = _strip_api_keys(data)

    # 3. T11 写入
    uow = UnitOfWork(session, db_path=None)
    await uow.t11_settings_save(cleaned)
    await uow.commit()

    # 4. 返回保存后的配置
    config = LLMConfig.load_or_default(cleaned)
    return _build_settings_response(config)


@router.get("/provider-models")
async def get_provider_models(
    session: AsyncSession = Depends(get_db),
) -> dict:
    """返回当前配置中所有 Provider 的模型列表（供前端白名单校验引用）。

    无配置时返回代码默认值中的模型列表。
    """
    repo = AppSettingsRepository(session)
    saved = await repo.get()
    config = LLMConfig.load_or_default(saved)

    result: dict[str, list[str]] = {}
    for name, pc in config.providers.items():
        result[name] = list(pc.models)
    return {
        "providers": result,
        "all_models": _collect_all_models(
            {"providers": {n: {"models": p.models} for n, p in config.providers.items()}}
        ),
    }
