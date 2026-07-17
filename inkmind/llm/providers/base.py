"""Provider 抽象基类 — 统一接口 + 连接池 + 重试逻辑。"""

from __future__ import annotations

import asyncio
import os
import time  # latency tracking
from abc import ABC, abstractmethod
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import AsyncGenerator, Dict, List, Optional, Tuple

import httpx

from inkmind.models.llm import ProviderConfig, RetryConfig


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------

@dataclass
class LLMResponse:
    """LLM 调用统一响应。"""
    content: str
    model: str
    provider: str
    finish_reason: str = "stop"
    usage: Optional[Dict[str, int]] = None


@dataclass
class ProviderStats:
    """Provider 运行时统计。
    
    包含调用计数、延迟追踪、Token 用量和成本估算。
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
            self.avg_latency = (
                (self.avg_latency * self._latency_samples + elapsed)
                / (self._latency_samples + 1)
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
        self.stats = ProviderStats()

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
    async def _do_chat_stream(
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
        **kwargs,
    ) -> LLMResponse:
        """统一非流式 chat 接口。"""
        effective_model = model or (self.config.models[0] if self.config.models else "unknown")
        messages = self._build_messages(prompt, system_prompt)
        client = await self._get_client()

        async with self._semaphore:
            last_error: Optional[Exception] = None
            for attempt in range(self.retry.max_retries + 1):
                self.stats.total_calls += 1
                start = time.monotonic()

                # 可中断检查
                if self._cancel_event.is_set():
                    raise RuntimeError("Request cancelled by caller")

                try:
                    if attempt > 0:
                        await asyncio.sleep(self.retry.base_delay_s)

                    response = await self._do_chat(client, messages, effective_model, **kwargs)
                    
                    # 记录成功调用的延迟和用量
                    elapsed = time.monotonic() - start
                    self.stats.record_success(elapsed, response.usage)
                    self.stats.successful_calls += 1
                    return response

                except httpx.HTTPStatusError as e:
                    self.stats.failed_calls += 1
                    status = e.response.status_code
                    if status in self.retry.non_retryable_statuses or attempt == self.retry.max_retries:
                        raise
                    last_error = e

                except (httpx.ConnectError, httpx.RemoteProtocolError, httpx.TimeoutException) as e:
                    self.stats.failed_calls += 1
                    if attempt == self.retry.max_retries:
                        raise
                    last_error = e

            raise RuntimeError(f"All retries exhausted") from last_error

    async def chat_stream(
        self,
        prompt: str,
        model: Optional[str] = None,
        system_prompt: Optional[str] = None,
        **kwargs,
    ) -> AsyncGenerator[str, None]:
        """统一流式 chat 接口。"""
        effective_model = model or (self.config.models[0] if self.config.models else "unknown")
        messages = self._build_messages(prompt, system_prompt)
        client = await self._get_client()

        async with self._semaphore:
            for attempt in range(self.retry.max_retries + 1):
                if self._cancel_event.is_set():
                    raise RuntimeError("Request cancelled by caller")
                try:
                    if attempt > 0:
                        await asyncio.sleep(self.retry.base_delay_s)
                    self.stats.total_calls += 1
                    start = time.monotonic()
                    async for chunk in self._do_chat_stream(client, messages, effective_model, **kwargs):
                        yield chunk
                    elapsed = time.monotonic() - start
                    self.stats.record_success(elapsed)
                    self.stats.successful_calls += 1
                    return
                except httpx.HTTPStatusError as e:
                    self.stats.failed_calls += 1
                    status = e.response.status_code
                    if status in self.retry.non_retryable_statuses or attempt == self.retry.max_retries:
                        raise
                except (httpx.ConnectError, httpx.RemoteProtocolError, httpx.TimeoutException):
                    self.stats.failed_calls += 1
                    if attempt == self.retry.max_retries:
                        raise

    def cancel(self) -> None:
        """中断当前正在执行的请求。"""
        self._cancel_event.set()

    def reset_cancel(self) -> None:
        """重置中断信号，允许新请求。"""
        self._cancel_event.clear()

    # ── 内部方法 ──────────────────────────────────────────

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
