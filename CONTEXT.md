# InkMind

AI 小说协作写作系统。核心能力：基于多 Agent 协作的结构化长篇写作、事务式上下文持久化、多 Provider LLM 策略。

## Language

### 小说结构

**Novel**:
一部独立的小说作品。
_Avoid_: 书、故事、作品

**Volume**:
小说的逻辑分卷，仅超长篇（>30万字）使用，可选。
_Avoid_: 册、集

**Part**:
小说内部的情节大段落（如三幕结构中的"第一幕"），每个 Part 包含多个 Chapter。
_Avoid_: 部、段落

**Chapter**:
最小写作单元。每章 1000-3000 字，由 AI 一次性生成。支持状态机流转。
_Avoid_: 节、段、回

**ChapterStatus**:
Chapter 的状态：`大纲(Outline)` → `写作中(Writing)` → `待评审(Review)` → `已定稿(Final)`。

**ChapterVersion**:
Chapter 的历史快照，支持回溯和比较。

### 角色

**Character**:
小说中的角色。包含静态设定（性格标签、行为规则、背景）和动态状态（关系、处境、已知信息）。

**CharacterTimelineEntry**:
角色在特定章节的关键事件记录。独立于章节正文，用于跨章节一致性维护。

**PersonalityTag**:
角色的性格标签（如"勇敢、固执、温柔"）。叙事友好型，用于 AI 保持角色行为一致性。

**BehaviorRule**:
角色的行为准则。自由文本描述，如"面临危险时优先保护同伴"。

### 世界观

**World**:
小说的世界观设定。包含类型标签、基础设定、世界规则、势力阵营和时间轴标记。

**MagicSystem**:
世界的魔法或修炼体系。独立于普通规则的专门结构。

**Location**:
故事中的地理/空间位置。支持层级结构（大陆→区域→城市→建筑）。

### 组织

**Paragraph**:
章节正文的段落。追踪来源（AI 生成/用户撰写/AI 修订）和校验和，用于内容来源审计。

**Relation**:
角色之间的关系。纯文字描述，无量化的亲密度数值。

**Faction**:
世界观中的势力/阵营。

### Agent 流水线

**Agent**:
协作写作系统中的 AI 角色，负责特定写作职能（规划、写作、评审、记忆管理）。

**AgentPacket**:
Agent 间通信的标准数据包。包含 `source`、`target`、`novel_id`、`packet_type`、`payload`（强类型）和 `iteration` 次数。

**AgentType**:
Agent 角色枚举：`Planner`（规划）、`Writer`（写作）、`Editor`（评审）、`MemoryKeeper`（记忆管理）、`Designer`（架构设计）。

**PacketType**:
数据包类型枚举：`plan_request`、`batch_plan`、`write_request`、`draft`、`review_request`、`verdict`、`revision_request`、`memorize_request`、`memorized`。

**Verdict**:
Editor 的评审结论。简单二值：`approve`（通过）或 `needs_revision`（需修改）。附带问题描述列表。

**PipelineState**:
流水线全局状态。跟踪每章在流水线中的状态：`PLANNED` → `WRITING` → `DRAFT_READY` → `REVIEWING` → （`REVISING` 循环） → `APPROVED` → `FINALIZED`。

**Pipeline**:
写作流水线执行流程：Planner 批量规划 N 章大纲 → 对每章串行执行 Writer → Editor → (修订循环) → MemoryKeeper。最大修订次数默认 3 次。

### 记忆系统

**MemoryTier**:
四级记忆层级：`L0(全文索引)` → `L1(活跃上下文)` → `L2(压缩记忆)` → `L3(长期知识)`。越往上越浓缩，越往下越详细。

**L0 — FullTextIndex**:
全文索引层。每章定稿后按段落建立倒排索引，用于精确检索具体段落。支持按章节/关键词定位。

**L1 — ActiveContext**:
活跃上下文层。默认保持最近 5 章的全文作为滑窗，Writer 写第 N 章时自动注入。可通过元数据中未回收的伏笔动态扩展滑窗大小。

**SlidingWindowState**:
L1 滑窗状态。包含当前章节索引、最近章节列表、角色状态卡集合和待回收伏笔表。默认窗口 5 章，可扩展。

**CharacterStateCard**:
角色状态卡。L1 滑窗内每个角色的当前位置、最近行动和状态快照。用于 Writer 保持角色行为一致性。

**L2 — CompressedMemory**:
压缩记忆层。默认每 10 章触发一次异步 LLM 压缩，产出「一段总摘要 + 结构化事件清单」。粒度可因事件密度/伏笔数量动态调整。

**CompressionTask**:
L2 压缩任务。异步执行，生命周期：`PENDING` → `RUNNING` → `COMPLETED/FAILED`。完成时发送 `compression_notification`。

**L3 — LongTermArchive**:
长期知识层。存储角色档案、世界观手册、风格指南等不随章节变化的稳定知识。由用户或 Designer 维护。

**MemorySnapshot**:
记忆快照。Writer 写每一章前向 MemoryKeeper 请求，包含 L1 活跃上下文 + 最近 3 条 L2 压缩记忆 + L3 长期知识引用 + 伏笔提示。

**ForeshadowingMarker**:
伏笔标记。记录伏笔埋设章节、描述和预期回收章节。L1 滑窗利用伏笔表判断是否需要扩展窗口。

**CompressStrategy**:
压缩策略配置。默认粒度 10 章、最大待回收伏笔数 5 条。可开启动态粒度调整。

### LLM 与 Provider

- **LLMConfig** — 顶层 LLM 总配置，包含 Provider 注册表、模型路由、重试策略
- **ProviderConfig** — 单个 Provider 的注册配置：protocol (openai/anthropic/ollama)、base_url、api_key_env、models、max_concurrent
- **RetryConfig** — 重试策略：max_retries=3、base_delay_s=2.0、non_retryable_statuses=[400,401,403,422]
- **AgentModelBinding** — Agent→模型绑定：agent_role、primary_model、fallback_models
- **ModelRouter** — 模型路由器，负责将 Agent 角色解析为模型名，处理降级
- **ModelRouterConfig** — 完整模型路由配置，containing bindings 列表
- **LLMResponse** — 统一 LLM 响应模型：content、model、provider、finish_reason、usage
- **BaseProvider** — Provider 抽象基类，定义 chat/chat_stream/cancel 接口，含信号量并发控制
- **OpenAIProvider** — OpenAI 兼容格式 Provider（支持 DeepSeek、OpenAI、Groq 等）
- **AnthropicProvider** — Anthropic 格式 Provider（Claude 系列，system prompt 在顶层参数）
- **OllamaProvider** — Ollama 本地模型 Provider（OpenAI 兼容端点）
- **LLMClient** — 统一客户端入口，封装 ModelRouter + ProviderFactory
- **ProviderFactory** — Provider 实例工厂，按配置创建并管理 Provider 生命周期
- **ProviderStats** — 单次调用的不可变统计快照（frozen dataclass）：provider_name、model_name、latency_ms、prompt/completion/total_tokens、estimated_cost、success、error_type、degraded、retry_count、timestamp（ADR-0010 §10-A）
- **ProviderStatsAccumulator** — Provider 运行时累计器（可变）：total_calls、successful_calls、failed_calls、fallback_used；供 `LLMClient.get_stats()` 汇总视图
- **RateLimiter** — per-Provider 滑动窗口速率限制器；配置 `ProviderConfig.max_calls_per_minute`（默认 0 = 不限制）（ADR-0010 §10-E）
- **降级 (Fallback)** — 主模型失败时，自动按 fallback_models 列表依次尝试，兜底 default_model
- **可中断 (Cancellable)** — 请求不设超时，由 `cancel()` 主动中断所有进行中请求
