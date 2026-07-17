# ADR-0002: Agent 流水线通信协议

**Status**: Accepted

## 语境

InkMind 由 4 个写作 Agent（Planner、Writer、Editor、MemoryKeeper）协作完成小说写作。需要定义 Agent 之间的数据交换格式、协作流程和状态跟踪方式。

核心约束：
- 超长篇（百万字）需要结构化的批量规划
- 每章 1000-3000 字，单次生成
- AI 生成的输出需要评审反馈回路（有最大迭代上限）
- Agent 间异步通信，数据包必须自描述

## 决策

### 交互模式：严格串行（方案 1）

每章走完整的 Plan → Write → Review → (revise loop) → Finalize 串行流水线：

```
Planner 批量规划 N 章大纲
  │
  ▼ (存储大纲)
  │
  ┌──── 对每章 [ch1 → ch2 → ... → chN] 依次执行 ────┐
  │                                                    │
  │   Writer(ch) → DraftReady                         │
  │      ↓                                            │
  │   Editor(ch) → approves?                          │
  │      ├── Yes → MemoryKeeper(ch) → 下一章          │
  │      └── No  → Writer(ch) 修订 → 重审            │
  │                 （最多 max_iterations 次）         │
  └────────────────────────────────────────────────────┘
```

选择原因：质量一致性压倒吞吐量。在 AI 生成场景下，章与章之间的连贯性依赖前一章的定稿状态，串行能保证 MemoryKeeper 的上下文始终基于最新定稿。

### 数据包：强类型 Pydantic 模型（方案 2-B）

每种 PacketType 对应一个明确的 Payload 模型：

| PacketType | Payload Model | 方向 |
|---|---|---|
| `plan_request` | `PlanRequestPayload` | → Planner |
| `batch_plan` | `BatchPlanPayload` | Planner → |
| `write_request` | `WriteRequestPayload` | → Writer |
| `draft` | `DraftPayload` | Writer → |
| `review_request` | `ReviewRequestPayload` | → Editor |
| `verdict` | `VerdictPayload` | Editor → |
| `revision_request` | `RevisionRequestPayload` | → Writer |
| `memorize_request` | `MemorizeRequestPayload` | → MemoryKeeper |
| `memorized` | `MemorizedPayload` | MemoryKeeper → |

运行时通过 `packet.packet_type` + `isinstance(packet.payload, DraftPayload)` 做类型窄化，不需要 discriminated union。

### 批量策略：Planner 批量 + Writer 单章（方案 2-C）

- **Planner**：一次调用规划 5-50 章大纲，保持宏观连贯性
- **Writer**：一次只写一章，聚焦微观质量
- **Editor**：一次评审一章

### 评审输出：简单二值（方案 2-A）

```python
class VerdictPayload:
    verdict: "approve" | "needs_revision"
    issues: list[str]  # 仅在 needs_revision 时有值
```

无量化评分、无逐段标注。原因：
- 超长篇场景下，细化评审的 ROI 低于快速迭代
- LLM 评审的精度不足以支撑结构化报告的经济性
- 简单二值 + 问题列表已足够 Writer 修正方向

### 修订保护：最大迭代次数

```python
class PipelineState:
    max_iterations: int = 3  # 默认最大修订次数
```

超过最大迭代次数的章节降级为 `APPROVED`（附已知问题列表），防止无限循环。这是容错设计——AI 可能在某次评审时持续认为 "不够好"，需要有硬性退出机制。

### Packet 的通用结构

```python
class AgentPacket:
    packet_id: UUID      # 唯一标识
    source: AgentType    # 来源 Agent
    target: AgentType    # 目标 Agent
    novel_id: UUID       # 所属小说
    packet_type: PacketType
    payload: PacketPayload  # 强类型载荷
    created_at: datetime
    version: int         # 版本号
    iteration: int       # 反馈回路迭代次数（0 = 首次）
```

### Pipeline 状态机

每章在流水线中的状态流转：

```
PLANNED → WRITING → DRAFT_READY → REVIEWING
                                      ↓
                                  ┌────┴────┐
                              APPROVED   NEEDS_REVISION → REVISING → WRITING
                                  │
                                  ↓
                              FINALIZED
```

## 被否决的方案

- **流水线并行（方案 3）**：多章同时 Writer/Editor 并行虽提升吞吐量，但章间连贯性难以保证，且 MemoryKeeper 上下文更新会乱序。百万字场景下，质量 > 速度。
- **结构化评审报告（方案 2-B）**：段落级标注 + 量化评分虽然详尽，但对 LLM 的稳定性和 Token 成本都是挑战。简单二值更轻量、更鲁棒。
- **纯 dict payload**：失去类型安全，Agent 集成时需要运行时猜测字段结构，更容易出 Bug。

## 影响

- Agent 实现者必须实现对应 PacketType 的处理函数，Payload 的字段契约是编译期可验证的。
- Writer 需要能够解析 `WriteRequestPayload` 和 `RevisionRequestPayload`（两者都包含 `ChapterOutline`，接口一致）。
- MemoryKeeper 只需处理 `MemorizeRequestPayload`，输出 `MemorizedPayload` 确认。
- Pipeline 编排器需要维护 `PipelineState`，跟踪每章的状态并控制修订迭代次数。

## 相关代码

- `inkmind/models/agent.py` — 所有 Packet 和 Payload 类型定义
- `tests/test_agent_pipeline.py` — 覆盖全部 PacketType 的单元测试（10 项）
