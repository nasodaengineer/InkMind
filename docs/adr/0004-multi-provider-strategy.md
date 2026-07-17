# ADR-0004: 多 Provider 策略模式

## 状态

已采纳

## 上下文

InkMind 需要支持多种 LLM Provider（DeepSeek、Anthropic、Ollama 本地等）以实现生产级弹性和成本优化。不同 Agent 角色对模型能力的需求差异显著——Planner 需要强推理能力（如 deepseek-v4-pro），而 Writer、Editor、MemoryKeeper 等可使用经济型模型（如 deepseek-v4-flash）。生产环境中 API 不稳定是常态，需要自动降级能力。同时，多个 Provider 共享同一进程资源，需要并发控制防止过载。

核心约束：
- 多 Provider 共存，切换对上层透明
- Agent 与模型之间的绑定关系需可配置
- API 调用失败时自动降级，不中断写作流水线
- 并发请求受控，防止单 Provider 过载

## 决策

### 决策 1：架构模式 — 策略模式 + 工厂

采用策略模式（Strategy Pattern）配合工厂方法（Factory），实现 Provider 实现的完全解耦。

```
BaseProvider（抽象基类）
  ├── OpenAIProvider     （兼容 DeepSeek、OpenAI 等 OpenAI 兼容 API）
  ├── AnthropicProvider  （Anthropic Claude API）
  └── OllamaProvider     （本地 Ollama 服务）
```

- `BaseProvider` 定义统一接口：`chat()`、`chat_stream()`、`count_tokens()`、`validate_config()`
- 各 Provider 在模块加载时通过 `PROVIDER_REGISTRY` 字典注册自身
- `ProviderFactory` 根据 `ProviderConfig.provider_type` 实例化对应 Provider
- 新增 Provider 只需继承 `BaseProvider` + 注册，不改动现有代码

### 决策 2：模型分配 — 可配置 Agent→模型绑定

Agent 与模型的关联通过 `ModelRouterConfig` 集中管理，不硬编码在 Agent 逻辑中。

默认分配：

| Agent 角色 | 主模型 | 降级模型列表 |
|---|---|---|
| Planner | deepseek-v4-pro | deepseek-v4-flash, claude-3-opus |
| Writer | deepseek-v4-flash | claude-3-sonnet, deepseek-v4-flash-16k |
| Editor | deepseek-v4-flash | claude-3-haiku |
| MemoryKeeper | deepseek-v4-flash | claude-3-haiku |

- 每个 Agent 可配置降级模型列表（有序），主模型失败时按序尝试
- 降级链路在 `ModelRouterConfig.downgrade_chain` 中定义
- 支持运行时重绑定，无需重启进程

### 决策 3：Provider 注册 — 以 Provider 为中心

系统以 Provider 为第一公民注册，而非以模型为中心。

```python
class ProviderConfig:
    provider_type: str        # "openai" | "anthropic" | "ollama"
    base_url: str             # API 端点
    api_key_env: str          # 环境变量名（如 "DEEPSEEK_API_KEY"）
    models: list[str]         # 该 Provider 支持的模型列表
    max_concurrent: int = 3   # per-Provider 并发上限
```

- `ModelRouterConfig` 建立模型名 → Agent 的关联，但不关心模型由哪个 Provider 提供
- 路由时按 `PROVIDER_REGISTRY` 注册顺序匹配第一个含该模型的 Provider
- 支持多个 Provider 拥有相同模型名（如多个 Ollama 实例均提供 deepseek-v4-flash）

### 决策 4：并发控制 — per-Provider 信号量

每个 Provider 实例持有独立的 `asyncio.Semaphore`，默认 `max_concurrent=3`。

```
LLMClient
  ├── Provider A (Semaphore=3)
  │     ├── Request 1 (acquire)
  │     ├── Request 2 (acquire)
  │     └── Request 3 (acquire)  ← 第 4 个等待
  └── Provider B (Semaphore=3) ← 独立池
```

- 全局 `httpx.AsyncClient` 池按 `(base_url, protocol)` 索引复用
- `httpx.Limits(max_connections=10, max_keepalive_connections=5)` 控制协议层连接数
- 信号量等待队列不阻塞其他 Provider 的请求

### 决策 5：重试 — 固定间隔，无超时，可中断

```python
class RetryConfig:
    max_retries: int = 3
    retry_interval: float = 2.0   # 固定间隔，非指数退避
    timeout: None                 # 不设超时，依赖用户 cancel()
```

重试规则：
- **不重试**：HTTP 400、401、403、422（客户端错误，重试无意义）
- **可重试**：网络错误（`ConnectError`、`RemoteProtocolError`）、HTTP 429（限流）、HTTP 500+（服务端错误）
- 固定间隔 2 秒而非指数退避：AI 写作场景下，可预测的等待时间比"可能更快"更重要，用户可通过 `cancel()` 随时中断
- 每次重试前检查 `cancel_flag`，支持优雅中断

### 决策 6：流式支持

所有 Provider 均需实现 `chat_stream()` 接口，与 `chat()` 走同一套 semaphore + 重试逻辑。

- 流式重试：当流中断且未收到完整响应时，触发重试
- 流式降级：主模型流式失败后，按 downgrade_chain 尝试降级模型的流式接口
- 流式降级全部失败则抛 `ProviderStreamError`，由调用方（Agent）决定如何处理

### 决策 7：生命周期

Provider 和客户端实例的生命周期由 `LLMClient` 统一管理。

```
初始化
  LLMClient.__init__() → 加载配置 → 初始化 Provider 实例池

运行期
  LLMClient.chat(model, messages, agent_type) → 路由 → Provider.chat()

关闭
  LLMClient.shutdown() → 关闭所有 httpx 客户端 → 清理信号量

中断
  LLMClient.cancel_all() → 设置全局 cancel_flag → 等待中的重试立即退出
  LLMClient.reset_cancel() → 清除 cancel_flag → 恢复请求
```

- `shutdown()` 是幂等的，多次调用安全
- `cancel_all()` / `reset_cancel()` 为 pair 调用，用于流水线暂停/恢复场景

## 被否决的方案

### 方案 A：按任务维度选择模型

让 Agent 在请求中声明任务特征（如 `"need_reasoning": true`、`"need_narrative": true`），系统根据特征自动匹配模型。

**否决理由**：增加了 Agent 的认知负担——Agent 实现者需要理解模型能力矩阵并正确声明特征。不如硬绑定（Agent→模型）清晰可控，且过度抽象导致调试困难。

### 方案 B：无降级策略

主模型调用失败直接向上层 Agent 抛异常，由 Agent 自行处理。

**否决理由**：生产环境 LLM API 的不可靠性是常态（限流、临时中断、超时）。如果每个 Agent 都要实现自己的重试/降级逻辑，将导致代码重复且降级行为不一致。统一在 LLMClient 层处理降级是更优的关注点分离。

### 方案 C：指数退避重试

```
delay = base_delay * (2 ^ attempt)  # 如 2s → 4s → 8s
```

**否决理由**：AI 写作场景下，等待时间的可预测性比"平均等待更短"更重要。用户（或流水线编排器）需要知道"最多等 6 秒"而不是"可能等 2-14 秒"。固定间隔配合用户 cancel() 提供了更可控的体验。

## 影响

### 正面
- 新增 Provider 只需继承 `BaseProvider` + 注册到 `PROVIDER_REGISTRY`，零修改现有代码
- Agent 代码完全无需感知底层 Provider 差异，始终调用 `LLMClient.chat(model, messages)`
- 降级对 Agent 透明，Agent 无需关心当前使用的是主模型还是降级模型
- per-Provider 信号量防止单个 Provider 过载拖垮整个进程

### 负面
- 需要配置至少一个 Provider 的环境变量（如 `DEEPSEEK_API_KEY`），首次使用存在配置门槛
- per-Provider 信号量 + 全局 httpx 池的双层限流增加了调试复杂度
- 固定间隔重试（而非指数退避）在网络抖动严重时可能连续 3 次均失败

### 门槛与依赖
- 需要安装 `httpx`（HTTP 客户端）、`pydantic`（配置模型）
- Provider 实现需额外安装对应 SDK（如 `anthropic`、`ollama`），通过 extras 可选依赖管理

## 相关代码

- `inkmind/llm/base.py` — `BaseProvider` 抽象基类定义
- `inkmind/llm/providers/` — 各 Provider 实现（OpenAI、Anthropic、Ollama 等）
- `inkmind/llm/factory.py` — `ProviderFactory` + `PROVIDER_REGISTRY`
- `inkmind/llm/router.py` — `ModelRouterConfig` + 路由逻辑
- `inkmind/llm/client.py` — `LLMClient` 生命周期管理
- `tests/test_llm_client.py` — 多 Provider 集成测试
- `tests/test_provider_fallback.py` — 降级链路测试
