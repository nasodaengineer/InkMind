"""速率限制器 — 滑动窗口计数器（ADR-0010 §10-E）。

每个 Provider 实例持有自己的 RateLimiter，防止同一 API Key
在短时间内被耗尽配额。配置来源：ProviderConfig.max_calls_per_minute。
"""

from __future__ import annotations

import asyncio
import time
from collections import deque


class RateLimiter:
    """滑动窗口速率限制器。

    窗口内调用数达到上限时，acquire() 挂起直到最旧的调用滑出窗口。

    用法:
        limiter = RateLimiter(max_calls=60, window_seconds=60.0)
        waited = await limiter.acquire()  # 返回实际等待秒数
    """

    def __init__(self, max_calls: int, window_seconds: float = 60.0):
        if max_calls < 1:
            raise ValueError(f"max_calls 必须 >= 1，收到 {max_calls}")
        self._max_calls = max_calls
        self._window = window_seconds
        self._calls: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def acquire(self) -> float:
        """等待直到可以发起调用，返回实际等待秒数。"""
        waited = 0.0
        while True:
            async with self._lock:
                now = time.monotonic()
                # 滑出窗口的旧调用出队
                while self._calls and self._calls[0] <= now - self._window:
                    self._calls.popleft()
                if len(self._calls) < self._max_calls:
                    self._calls.append(now)
                    return waited
                wait = self._calls[0] + self._window - now
            await asyncio.sleep(wait)
            waited += wait
