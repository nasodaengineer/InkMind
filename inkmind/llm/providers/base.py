"""Provider 抽象基类 — 统一接口 + 连接池 + 重试逻辑 + 观测埋点 + 速率限制。"""

from __future__ import annotations

import asyncio
import os
import statistics
import time  # latency tracking
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import AsyncGenerator, Callable, Dict, List, Optional

import httpx

from inkmind.llm.rate_limiter import RateLimiter
from inkmind.models.llm import ProviderConfig, RetryConfig


# ---------------------------------------------------------------------------
# 内置单价表（USD / 1K tokens）
# ---------------------------------------------------------------------------

PRICING: dict[str, dict[str, float]] = {
    # DeepSeek
    "deepseek-chat": {"input": 0.00027, "output": 0.00110},
    "deepseek-reasoner": {"input": 0.00055, "output": 0.00219},
    "deepseek-v4-pro": {"input": 0.00040, "output": 0.00160},
    "deepseek-v4-flash": {"input": 0.00015, "output": 0.00060},
    # Claude
    "claude-sonnet-4": {"input": 0.00300, "output": 0.01500},
    "claude-3-opus": {"input": 0.01500, "output": 0.07500},
    "claude-3-sonnet": {"input": 0.00300, "output": 0.01500},
    "claude-3-haiku": {"input": 0.00025, "output": 0.00125},
    "claude-sonnet-4-20250514": {"input": 0.00300, "output": 0.01500},
    # GPT
    "gpt-4o": {"input": 0.00250, "output": 0.01000},
    "gpt-4o-mini": {"input": 0.00015, "output": 0.00060},
    "gpt-4": {"input": 0.03000, "output": 0.06000},
    "gpt-3.5-turbo": {"input": 0.00100, "output": 0.00200},
    # Ollama（免费/本地，标记极低象征性成本）
    "ollama": {"input": 0.00001, "output": 0.00002},
}


def estimate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """根据内置单价表估算一次 LLM 调用的 USD 成本。

    若模型不在表中，按 deepseek-v4-flash 价格兜底。
    """
    rates = PRICING.get(model, PRICING["deepseek-v4-flash"])
    return (prompt_tokens / 1000.0) * rates["input"] + (completion_tokens / 1000.0) * rates[
        "output"
    ]


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------


class RequestCancelledError(RuntimeError):
    """调用方通过 cancel() 主动中断的请求（用于 Stats error_type 分类）。"""


@dataclass
class LLMResponse:
    """LLM 调用统一响应。"""

    content: str
    model: str
    provider: str
    finish_reason: str = "stop"
    usage: Optional[Dict[str, int]] = None


@dataclass(frozen=True)
class ProviderStats:
    """单次 LLM 调用的不可变统计快照（ADR-0010 §10-A）。

    每次 chat() / chat_stream() 调用结束产生一份，追加到
    ``BaseProvider.stats_history`` 并经 sink 汇入 ``LLMClient`` 聚合。
    """

    provider_name: str  # 实际使用的 Provider（降级后可能是备用）
    model_name: str  # 实际使用的模型
    agent_name: str = ""  # 发起调用的 Agent 角色名
    latency_ms: float = 0.0  # 端到端延迟（毫秒）
    prompt_tokens: int = 0  # 输入 Token 数
    completion_tokens: int = 0  # 输出 Token 数
    total_tokens: int = 0  # 总 Token 数
    estimated_cost: float = 0.0  # 估算费用（USD）
    success: bool = True  # 是否成功
    error_type: Optional[str] = None  # 失败时的错误类别
    degraded: bool = False  # 是否经过降级链
    retry_count: int = 0  # 重试次数
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))  # 调用时间


def aggregate_snapshots(history: List[ProviderStats]) -> dict:
    """聚合调用快照历史，返回会话级汇总统计（ADR-0010 §10-C）。

    LLMClient 与离线 ScriptedLLMClient 共用本实现。
    """
    total_calls = len(history)
    if total_calls == 0:
        return {
            "total_calls": 0,
            "total_tokens": 0,
            "total_cost": 0.0,
            "avg_latency_ms": 0.0,
            "success_rate": 0.0,
            "degradation_rate": 0.0,
            "provider_breakdown": {},
        }

    by_provider: Dict[str, List[ProviderStats]] = {}
    for s in history:
        by_provider.setdefault(s.provider_name, []).append(s)

    breakdown: Dict[str, dict] = {}
    for name, entries in by_provider.items():
        n = len(entries)
        breakdown[name] = {
            "calls": n,
            "total_tokens": sum(s.total_tokens for s in entries),
            "total_cost": sum(s.estimated_cost for s in entries),
            "avg_latency_ms": statistics.mean(s.latency_ms for s in entries),
            "success_rate": sum(1 for s in entries if s.success) / n,
        }

    return {
        "total_calls": total_calls,
        "total_tokens": sum(s.total_tokens for s in history),
        "total_cost": sum(s.estimated_cost for s in history),
        "avg_latency_ms": statistics.mean(s.latency_ms for s in history),
        "success_rate": sum(1 for s in history if s.success) / total_calls,
        "degradation_rate": sum(1 for s in history if s.degraded) / total_calls,
        "provider_breakdown": breakdown,
    }


def _classify_error(exc: Optional[BaseException]) -> str:
    """将调用异常归类为 ADR-0010 的错误类别。"""
    if isinstance(exc, RequestCancelledError):
        return "cancelled"
    if isinstance(exc, httpx.TimeoutException):
        return "timeout"
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status == 429:
            return "rate_limit"
        if status in (401, 403):
            return "auth"
        return "unknown"
    return "unknown"


@dataclass
class ProviderStatsAccumulator:
    """Provider 运行时统计（可变累计器）。

    包含调用计数、延迟追踪、Token 用量和成本估算。
    与单次调用快照 ProviderStats 互补：本类提供
    ``LLMClient.get_stats()`` 的 per-Provider 汇总视图。
    """

    # 调用计数
    total_calls: int = 0
    successful_calls: int = 0
    failed_calls: int = 0
    fallback_used: int = 0

    # 延迟追踪（秒）
    min_latency: float = 0.0
    max_latency: float = 0.0
    avg_latency: float = 0.0

    # Token 用量
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_tokens: int = 0

    # 成本估算（USD）
    estimated_cost: float = 0.0

    # 内部字段（不公开）
    _latency_samples: int = 0

    def record_success(self, elapsed: float, usage: Optional[Dict[str, int]] = None) -> None:
        """记录一次成功调用的延迟和 Token 用量。"""
        if self._latency_samples == 0:
            self.min_latency = elapsed
            self.max_latency = elapsed
            self.avg_latency = elapsed
        else:
            self.min_latency = min(self.min_latency, elapsed)
            self.max_latency = max(self.max_latency, elapsed)
            self.avg_latency = (self.avg_latency * self._latency_samples + elapsed) / (
                self._latency_samples + 1
            )
        self._latency_samples += 1

        if usage:
            pt = usage.get("prompt_tokens", 0) or 0
            ct = usage.get("completion_tokens", 0) or 0
            tt = usage.get("total_tokens")
            if tt is None:
                tt = pt + ct  # 未提供 total_tokens 时按 pt+ct 计算
            self.total_prompt_tokens += pt
            self.total_completion_tokens += ct
            self.total_tokens += tt


# ---------------------------------------------------------------------------
# HTTP 客户端池（全局复用，以 provider_key 为索引）
# ---------------------------------------------------------------------------

_http_client_pool: Dict[str, httpx.AsyncClient] = {}
_pool_lock = asyncio.Lock()


def _provider_key(cfg: ProviderConfig) -> str:
    """生成 Provider 唯一索引键。"""
    return f"{cfg.protocol.value}::{cfg.base_url}"


async def _get_or_create_http_client(cfg: ProviderConfig) -> httpx.AsyncClient:
    """获取或创建全局复用的 httpx 客户端。"""
    key = _provider_key(cfg)
    async with _pool_lock:
        client = _http_client_pool.get(key)
        if client is not None and not client.is_closed:
            return client

        client = httpx.AsyncClient(
            base_url=cfg.base_url,
            # 无超时可中断：LLM 长文生成耗时长且不可预估，不设超时，
            # 取消统一走 BaseProvider.cancel()
            timeout=None,
            limits=httpx.Limits(
                max_keepalive_connections=cfg.max_keepalive,
                max_connections=cfg.max_concurrent * 2,
                keepalive_expiry=60,
            ),
        )
        _http_client_pool[key] = client
        return client


async def cleanup_http_clients() -> None:
    """清理所有全局 HTTP 客户端（应用关闭时调用）。"""
    async with _pool_lock:
        for key, client in list(_http_client_pool.items()):
            if not client.is_closed:
                await client.aclose()
            del _http_client_pool[key]


# ---------------------------------------------------------------------------
# 抽象基类
# ---------------------------------------------------------------------------


class BaseProvider(ABC):
    """Provider 抽象基类。

    职责：
      1. 维护 per-Provider 信号量（连接池并发控制）
      2. 统一重试逻辑（固定间隔，无超时可中断）
      3. 提供 `chat()` / `chat_stream()` / `cancel()` 抽象方法
      4. HTTP 客户端自动复用
    """

    def __init__(self, config: ProviderConfig, retry: Optional[RetryConfig] = None) -> None:
        self.config = config
        self.retry = retry or RetryConfig()
        self._semaphore = asyncio.Semaphore(config.max_concurrent)
        self._cancel_event = asyncio.Event()
        self._client: Optional[httpx.AsyncClient] = None
        self.stats = ProviderStatsAccumulator()
        # ADR-0010 §10-A：每次调用一份不可变快照
        self.stats_history: List[ProviderStats] = []
        # 快照 sink（LLMClient 注入，record_stats 回调）
        self._stats_sink: Optional[Callable[[ProviderStats], None]] = None
        # ADR-0010 §10-E：per-Provider 速率限制（0 = 不限制）
        self._rate_limiter: Optional[RateLimiter] = (
            RateLimiter(config.max_calls_per_minute, window_seconds=60.0)
            if config.max_calls_per_minute > 0
            else None
        )

    # ── 子类必须实现的抽象方法 ──────────────────────────────

    @abstractmethod
    def _build_headers(self) -> Dict[str, str]:
        """构造认证头。每个 Provider 的认证机制不同。"""
        ...

    @abstractmethod
    async def _do_chat(
        self,
        client: httpx.AsyncClient,
        messages: List[Dict[str, str]],
        model: str,
        **kwargs,
    ) -> LLMResponse:
        """实际发送非流式聊天请求。"""
        ...

    @abstractmethod
    def _do_chat_stream(
        self,
        client: httpx.AsyncClient,
        messages: List[Dict[str, str]],
        model: str,
        **kwargs,
    ) -> AsyncGenerator[str, None]:
        """实际发送流式聊天请求。"""
        ...

    @abstractmethod
    def _build_messages(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
    ) -> List[Dict[str, str]]:
        """将 prompt 构造为 provider 要求的消息格式。"""
        ...

    # ── 公共方法 ──────────────────────────────────────────

    async def chat(
        self,
        prompt: str,
        model: Optional[str] = None,
        system_prompt: Optional[str] = None,
        *,
        degraded: bool = False,
        agent_name: str = "",
        **kwargs,
    ) -> LLMResponse:
        """统一非流式 chat 接口。

        Args:
            degraded: 本调用是否经由降级链（由 ModelRouter 标记），
                      记入 Stats 快照（ADR-0010 §10-A）。
            agent_name: 发起调用的 Agent 角色名。
        """
        effective_model = model or (self.config.models[0] if self.config.models else "unknown")
        messages = self._build_messages(prompt, system_prompt)
        client = await self._get_client()
        call_start = time.monotonic()

        def _snap(**kw) -> ProviderStats:
            return self._record_call(
                model=effective_model,
                start=call_start,
                degraded=degraded,
                agent_name=agent_name,
                **kw,
            )

        async with self._semaphore:
            last_error: Optional[Exception] = None
            for attempt in range(self.retry.max_retries + 1):
                self.stats.total_calls += 1
                start = time.monotonic()

                # 可中断检查
                if self._cancel_event.is_set():
                    error = RequestCancelledError("Request cancelled by caller")
                    _snap(success=False, error=error, retry_count=attempt)
                    raise error

                try:
                    if attempt > 0:
                        await asyncio.sleep(self.retry.base_delay_s)
                    if self._rate_limiter is not None:
                        await self._rate_limiter.acquire()

                    response = await self._do_chat(client, messages, effective_model, **kwargs)

                    # 记录成功调用的延迟和用量
                    elapsed = time.monotonic() - start
                    self.stats.record_success(elapsed, response.usage)
                    self.stats.successful_calls += 1
                    _snap(success=True, usage=response.usage, retry_count=attempt)
                    return response

                except httpx.HTTPStatusError as e:
                    self.stats.failed_calls += 1
                    status = e.response.status_code
                    if (
                        status in self.retry.non_retryable_statuses
                        or attempt == self.retry.max_retries
                    ):
                        _snap(success=False, error=e, retry_count=attempt)
                        raise
                    last_error = e

                except (httpx.ConnectError, httpx.RemoteProtocolError, httpx.TimeoutException) as e:
                    self.stats.failed_calls += 1
                    if attempt == self.retry.max_retries:
                        _snap(success=False, error=e, retry_count=attempt)
                        raise
                    last_error = e

            raise RuntimeError("All retries exhausted") from last_error

    async def chat_stream(
        self,
        prompt: str,
        model: Optional[str] = None,
        system_prompt: Optional[str] = None,
        *,
        degraded: bool = False,
        agent_name: str = "",
        **kwargs,
    ) -> AsyncGenerator[str, None]:
        """统一流式 chat 接口。"""
        effective_model = model or (self.config.models[0] if self.config.models else "unknown")
        messages = self._build_messages(prompt, system_prompt)
        client = await self._get_client()
        call_start = time.monotonic()

        def _snap(**kw) -> ProviderStats:
            return self._record_call(
                model=effective_model,
                start=call_start,
                degraded=degraded,
                agent_name=agent_name,
                **kw,
            )

        async with self._semaphore:
            for attempt in range(self.retry.max_retries + 1):
                if self._cancel_event.is_set():
                    error = RequestCancelledError("Request cancelled by caller")
                    _snap(success=False, error=error, retry_count=attempt)
                    raise error
                try:
                    if attempt > 0:
                        await asyncio.sleep(self.retry.base_delay_s)
                    if self._rate_limiter is not None:
                        await self._rate_limiter.acquire()
                    self.stats.total_calls += 1
                    start = time.monotonic()
                    async for chunk in self._do_chat_stream(
                        client, messages, effective_model, **kwargs
                    ):
                        yield chunk
                    elapsed = time.monotonic() - start
                    self.stats.record_success(elapsed)
                    self.stats.successful_calls += 1
                    _snap(success=True, retry_count=attempt)
                    return
                except httpx.HTTPStatusError as e:
                    self.stats.failed_calls += 1
                    status = e.response.status_code
                    if (
                        status in self.retry.non_retryable_statuses
                        or attempt == self.retry.max_retries
                    ):
                        _snap(success=False, error=e, retry_count=attempt)
                        raise
                except (httpx.ConnectError, httpx.RemoteProtocolError, httpx.TimeoutException) as e:
                    self.stats.failed_calls += 1
                    if attempt == self.retry.max_retries:
                        _snap(success=False, error=e, retry_count=attempt)
                        raise

    def cancel(self) -> None:
        """中断当前正在执行的请求。"""
        self._cancel_event.set()

    def reset_cancel(self) -> None:
        """重置中断信号，允许新请求。"""
        self._cancel_event.clear()

    # ── 内部方法 ──────────────────────────────────────────

    def _record_call(
        self,
        *,
        model: str,
        start: float,
        success: bool,
        usage: Optional[Dict[str, int]] = None,
        error: Optional[BaseException] = None,
        retry_count: int = 0,
        degraded: bool = False,
        agent_name: str = "",
    ) -> ProviderStats:
        """埋点（ADR-0010 §10-B）：生成不可变快照，追加历史并转发 sink。

        作为基类 mixin 方法统一实现，不侵入各 Provider 的业务逻辑。
        """
        prompt_tokens = completion_tokens = total_tokens = 0
        if usage:
            prompt_tokens = usage.get("prompt_tokens", 0) or 0
            completion_tokens = usage.get("completion_tokens", 0) or 0
            total_tokens = usage.get("total_tokens") or (prompt_tokens + completion_tokens)
        estimated = estimate_cost(model, prompt_tokens, completion_tokens)
        snapshot = ProviderStats(
            provider_name=self.config.name,
            model_name=model,
            agent_name=agent_name,
            latency_ms=(time.monotonic() - start) * 1000.0,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            estimated_cost=estimated,
            success=success,
            error_type=None if success else _classify_error(error),
            degraded=degraded,
            retry_count=retry_count,
            timestamp=datetime.now(timezone.utc),
        )
        self.stats_history.append(snapshot)
        if self._stats_sink is not None:
            self._stats_sink(snapshot)
        return snapshot

    def _resolve_api_key(self) -> str:
        """从环境变量解析 API Key。"""
        if not self.config.api_key_env:
            return ""
        key = os.environ.get(self.config.api_key_env)
        if not key:
            raise RuntimeError(
                f"Environment variable '{self.config.api_key_env}' is not set "
                f"for provider '{self.config.name}'"
            )
        return key

    async def _get_client(self) -> httpx.AsyncClient:
        """获取全局复用的 HTTP 客户端。"""
        if self._client is None or self._client.is_closed:
            self._client = await _get_or_create_http_client(self.config)
        return self._client


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------


def extract_api_key_from_env(cfg: ProviderConfig) -> str:
    """从环境变量提取 API Key（无则返回空字符串）。"""
    if not cfg.api_key_env:
        return ""
    return os.environ.get(cfg.api_key_env, "")
