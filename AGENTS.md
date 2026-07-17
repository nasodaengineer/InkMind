# InkMind — AI 小说协作写作系统

## 项目概览

一个基于多 Agent 协作的 AI 小说写作系统，具备结构化上下文管理、多模型 Provider 策略和事务式持久化能力。借鉴了 ainovel-cli、MuMuAINovel 和 Openwrite_skill 三大项目的优秀架构设计。

## Triaging

- **bug** — 功能异常
- **feature** — 新功能请求
- **writing** — 写作质量/风格相关
- **memory** — 上下文/记忆管理问题
- **agent** — Agent 协作/调度问题
- **perf** — 性能优化

## Tech Stack

- Python 3.10+
- uv（包管理）
- ruff（lint + format）
- mypy（类型检查）
- pytest（测试）
- 单包结构：`inkmind/`

---

## Agents

### @planner

规划 Agent，负责批量规划多章大纲，保持宏观情节连贯性。

**接收 PacketType**：`plan_request`
**产出 PacketType**：`batch_plan`
**可调用 Agent**：@memory-keeper（获取 ContextSnapshot 作为输入）
**可调用技能**：无

### @writer

写作 Agent，负责根据单章大纲生成小说正文。接收修订请求后重写。

**接收 PacketType**：`write_request`、`revision_request`、`snapshot_response`
**产出 PacketType**：`draft`、`context_query`
**可调用 Agent**：@planner、@memory-keeper

### @editor

评审 Agent，负责检查写作质量、情节连贯性、角色一致性。输出简单二值结论。

**接收 PacketType**：`review_request`、`draft`
**产出 PacketType**：`verdict`（approve / needs_revision）、`revision_request`
**可调用 Agent**：@memory-keeper

### @memory-keeper

四级压缩记忆管理 Agent。负责滑窗管理、异步压缩、记忆快照组装和长期知识维护。

**核心能力（L0–L3）**：
- **L0 全文索引**：每章定稿后按段落索引，支持检索
- **L1 活跃上下文**：默认 5 章滑窗，伏笔驱动动态扩展；维护角色状态卡和待回收伏笔表
- **L2 压缩记忆**：每 10 章异步压缩为「一段总摘要 + 结构化事件清单」；可因事件/伏笔密度动态调整粒度
- **L3 长期知识**：角色档案 / 世界观手册 / 风格指南，不随章节变化

**接收 PacketType**：`memorize_request`、`snapshot_request`、`context_query`
**产出 PacketType**：`memorized`、`snapshot_response`、`compression_notification`、`context_result`
**可调用技能**：无（纯编排层，LLM 调用通过 provider 回调注入）
**可调用 Agent**：无

### @designer

架构设计 Agent，负责领域模型定义和系统架构演进。

**可调用技能**：无
**可调用 Agent**：无

---

### Pipeline 流程

```
┌─ 初始化 ──────────────────────────────────────┐
│  Writer → SnapshotRequest → MemoryKeeper       │
│  MemoryKeeper → SnapshotResponse → Writer      │
└─────────────────────────────────────────────────┘

┌─ 规划 ────────────────────────────────────────┐
│  Planner 批量规划 5-50 章大纲                   │
└─────────────────────────────────────────────────┘

┌─ 执行 (严格串行, 每章) ───────────────────────┐
│                                                │
│  Step 1: Writer 从 MemoryKeeper 拉取快照       │
│          (SnapshotRequest → SnapshotResponse)   │
│                                                │
│  Step 2: Writer → Draft                        │
│                                                │
│  Step 3: Editor → Verdict                      │
│            ├── approve → Step 4                │
│            └── needs_revision (≤3次)            │
│                  → Writer 修订 → Step 3         │
│                                                │
│  Step 4: Editor → MemorizeRequest              │
│          → MemoryKeeper 更新 L0/L1/L2          │
│          → Memorized (完成)                    │
│                                                │
│  ═══ 异步: MemoryKeeper 后台压缩 L2 ═══       │
│          → CompressionNotification (完成通知)   │
│                                                │
└─────────────────────────────────────────────────┘

┌─ 修订保护 ────────────────────────────────────┐
│  每章最多 3 次修订迭代                          │
│  超限自动降级为 APPROVED                        │
└─────────────────────────────────────────────────┘
```

---

## Skills

### @domain-model

领域模型层定义——纯数据模型，无框架耦合，无 IO，无 LLM 调用。

涉及文件：
- `inkmind/models/novel.py`
- `inkmind/models/character.py`
- `inkmind/models/world.py`
- `inkmind/models/chapter.py`

### @agent-pipeline

Agent 流水线通信协议——强类型数据包、Pipeline 状态机、严格串行执行流程。

涉及文件：
- `inkmind/models/agent.py`

### @memory-architecture

四级压缩记忆架构——滑窗管理、异步压缩、记忆快照、长期知识管理。

涉及文件：
- `inkmind/models/memory.py`
- `inkmind/memory/compressor.py`
- `inkmind/memory/`

### @provider

多 Provider 策略模式——统一 LLM 客户端接口，支持 OpenAI / Anthropic / 本地模型切换 + 自动降级。

**架构：**
- `LLMClient` (client.py) — Agent 代码的统一入口，封装 ModelRouter + ProviderFactory
- `ModelRouter` (factory.py) — Agent→模型路由，处理降级
- `ProviderFactory` (factory.py) — Provider 实例工厂
- `BaseProvider` (providers/base.py) — 抽象基类：统一 chat/chat_stream/cancel 接口
- `OpenAIProvider` / `AnthropicProvider` / `OllamaProvider` — 各 Provider 实现

**默认模型分配：**
| Agent | 主模型 | 降级 |
|-------|--------|------|
| Planner | deepseek-v4-pro | deepseek-v4-flash |
| Writer | deepseek-v4-flash | — |
| Editor | deepseek-v4-flash | — |
| MemoryKeeper | deepseek-v4-flash | — |

**配置方式：** `inkmind/models/llm.py` 中的 LLMConfig
- Provider 注册：ProviderConfig（base_url, api_key_env, max_concurrent）
- 模型路由：ModelRouterConfig（bindings: Agent→主模型 + 降级列表）
- 重试策略：RetryConfig（默认 3 次，固定 2s 间隔，无超时可中断）

**并发控制：** per-Provider asyncio.Semaphore（默认 max_concurrent=3）

**涉及文件：**
- `inkmind/models/llm.py` — 配置模型
- `inkmind/llm/client.py` — 统一客户端接口
- `inkmind/llm/factory.py` — 工厂 + 路由 + 降级
- `inkmind/llm/providers/base.py` — 抽象基类
- `inkmind/llm/providers/openai_provider.py` — OpenAI/DeepSeek
- `inkmind/llm/providers/anthropic_provider.py` — Anthropic
- `inkmind/llm/providers/ollama_provider.py` — Ollama

### @persistence

事务式持久化——原子写入、digest 幂等、快照回滚、跨进程锁。

**架构：**
- `ORM 表层` (models.py) — SQLAlchemy 声明式表定义：NovelModel / ChapterModel / ChapterVersionModel / PipelineStateModel / CompressionTaskModel / MemoryArchiveModel / SnapshotModel / PacketTrackingModel
- `序列化层` (serializers.py) — Pydantic ↔ ORM 双向转换，统一 serialize()/deserialize() 接口
- `Repository 层` (repositories.py) — NovelRepo / ChapterRepo / PipelineRepo / TaskRepo / ArchiveRepo
- `事务管理` (uow.py) — UnitOfWork 模式，5 个原子事务边界（T1-Writer / T2-Planner / T3-Editor / T4-MemoryKeeper / T5-SlidingWindow）
- `快照管理` (snapshot.py) — JSON 导出 + 一次性恢复，`exported_at` / `digest` 元数据
- `故障恢复` (recovery.py) — RecoveryManager.recover() 按序恢复 8 步状态
- `幂等校验` (idempotency.py) — per-packet SHA-256 digest 去重

**digest 幂等：**
- `PayloadBase.digest` — SHA-256 of serialized payload
- `PacketTrackingModel.digest` — UNIQUE 约束
- 写入前校验：packet_type + packet_id + digest 重复则跳过

**事务边界（5 个原子事务）：**
| 事务 | 包含操作 |
|------|----------|
| T1 Writer 完成章节 | 写入 Chapter content + 更新 ChapterStatus → DRAFT_READY |
| T2 Planner 完成规划 | 批量插入 ChapterOutline + 更新 PipelineState.total_chapters |
| T3 Editor 完成评审 | 写入 Verdict + 更新 ChapterStatus → APPROVED/REVISING |
| T4 MemoryKeeper 压缩 | 写入 CompressedMemory + 更新 L2Archive + 标记 Task COMPLETED |
| T5 滑窗更新 | 更新 SlidingWindowState + L1 snapshot |

**故障恢复（8 步）：**
1. 加载 L0Index → 2. 加载 L2Archive → 3. 加载 L3Archive → 4. 加载 SlidingWindowState
5. 加载 CompressionTask（PENDING/RUNNING）→ 6. 重置 RUNNING 为 PENDING
7. 加载 PipelineState → 8. 返回 RecoveredMemoryState

**涉及文件：**
- `inkmind/storage/models.py` — ORM 表定义
- `inkmind/storage/serializers.py` — 序列化
- `inkmind/storage/repositories.py` — Repository 模式
- `inkmind/storage/uow.py` — UnitOfWork 事务管理
- `inkmind/storage/snapshot.py` — 快照导出/恢复
- `inkmind/storage/recovery.py` — RecoveryManager 故障恢复
- `inkmind/storage/idempotency.py` — Digest 幂等校验器

### @testing

测试覆盖——从单元测试到集成的完整测试体系。

涉及文件：
- `tests/`

---

## Scratchpad

- `.scratch/` — 开发笔记与历史工单归档（问题跟踪已迁移至 GitHub Issues）

## Specs

项目规范文档入口：`docs/`

## Agent skills

### Issue tracker

Issues live in GitHub Issues (`nasodaengineer/InkMind`), managed via the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

Uses the default five canonical roles. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context — `CONTEXT.md` + `docs/adr/` at repo root. See `docs/agents/domain.md`.

### llm

测试用的大模型：`docs\ds.md`
