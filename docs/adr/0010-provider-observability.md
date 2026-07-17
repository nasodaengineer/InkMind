# ADR-0010: Provider 可观测性层

## 状态

已采纳（2026-07-16）

## 背景

代码审查（2026-07-16）发现 Provider 层（ADR-0004）存在「发射后不管」问题：

1. **无 Token 用量追踪**：无法知道每次 LLM 调用消耗了多少 Token。
2. **无请求延迟记录**：无法判断哪个 Provider 响应慢。
3. **无失败率统计**：无法量化 Provider 稳定性，降级策略（ADR-0004 §4-C）缺乏数据支撑。
4. **无成本核算**：无法估算每次写作会话的 LLM 费用。
5. **无速率限制**：同一 API Key 可能在短时间内被耗尽配额。

在超长篇（百万字）场景下，LLM 调用可能达到数千次，缺乏可观测性意味着：
- 用户无法诊断「为什么 response 变慢了」
- 无法比较不同 Provider 的性价比
- 无法预警 API Key 配额耗尽

## 决策

### 10-A ProviderStats 数据类

**选择：每个 Provider 调用产生一个不可变 Stats 快照**

```python
@dataclass(frozen=True)
class ProviderStats:
    provider_name: str          # 实际使用的 Provider（降级后可能是备用）
    model_name: str             # 实际使用的模型
    latency_ms: float           # 端到端延迟（毫秒）
    prompt_tokens: int          # 输入 Token 数
    completion_tokens: int      # 输出 Token 数
    total_tokens: int           # 总 Token 数
    estimated_cost: float       # 估算费用（USD）
    success: bool               # 是否成功
    error_type: str | None      # 失败时的错误类别（timeout / rate_limit / auth / unknown）
    degraded: bool              # 是否经过降级链
    retry_count: int            # 重试次数
    timestamp: datetime         # 调用时间
```

### 10-B 埋点位置

在所有 Provider 的 `complete()` 和 `stream_complete()` 方法中插桩：

```
调用前：记录 start_time
调用后：
  成功 → ProviderStats(success=True, latency, tokens, cost)
  失败 → ProviderStats(success=False, error_type, latency)
```

埋点实现为 `Provider` 基类的 mixin 方法，不侵入各 Provider 的业务逻辑。

### 10-C Stats 聚合器

**选择：LLMClient 级聚合 + 可选外部导出**

```python
class LLMClient:
    def __init__(self):
        self._stats_history: list[ProviderStats] = []

    def record_stats(self, stats: ProviderStats) -> None:
        self._stats_history.append(stats)

    def aggregate_stats(self) -> dict:
        """返回当前会话的汇总统计"""
        return {
            "total_calls": len(self._stats_history),
            "total_tokens": sum(s.total_tokens for s in self._stats_history),
            "total_cost": sum(s.estimated_cost for s in self._stats_history),
            "avg_latency_ms": statistics.mean(s.latency_ms for s in self._stats_history),
            "success_rate": ...,
            "degradation_rate": ...,
            "provider_breakdown": {...},
        }

    def reset_stats(self) -> None:
        self._stats_history.clear()
```

### 10-D CLI 输出 Stats

- `inkmind write --json` 等命令在输出尾部附加 `_stats` 字段。
- `inkmind stats --llm` 显示 Provider 统计摘要。
- 默认文本模式下仅显示 `⏱ 2.3s · ⚡ 1,234 tokens · 💰 $0.012` 一行。

### 10-E 速率限制（Rate Limiter）

**选择：per-Provider 信号量 + 滑动窗口计数器**

```python
class RateLimiter:
    def __init__(self, max_calls: int, window_seconds: float = 60.0):
        self._max_calls = max_calls
        self._window = window_seconds
        self._calls: deque[float] = deque()

    async def acquire(self) -> float:
        """等待直到可以发起调用，返回等待秒数"""
        now = time.monotonic()
        while self._calls and self._calls[0] < now - self._window:
            self._calls.popleft()
        if len(self._calls) >= self._max_calls:
            wait = self._calls[0] + self._window - now
            await asyncio.sleep(wait)
        self._calls.append(time.monotonic())
        return max(0.0, self._calls[0] + self._window - time.monotonic())
```

- 每个 Provider 实例持有自己的 `RateLimiter`。
- 配置来源：`ProviderConfig.max_calls_per_minute`。

## 被否决的方案

- **外部可观测性平台**（Datadog/OpenTelemetry）：InkMind 是单用户命令行工具，引入外部平台过度设计。
- **进 SQLite 持久化 stats**：日志数据不应污染核心存储，用户需要时可以 `--json` 导出分析。
- **每个 Provider 自行实现埋点**：导致代码重复，基类 mixin 更简洁。

## 影响

- `Provider` 基类新增 `_record_call()` 方法，所有子类自动获得埋点能力。
- `LLMClient` 新增 `aggregate_stats()` 和 `reset_stats()` 方法。
- `RateLimiter` 新增为独立模块，不影响现有 Provider 调用链。
- CLI 输出尾部新增一行 stats 摘要（文本模式可读，JSON 模式结构化）。
