# ADR-0006: 集成测试架构

## 状态

✅ 已采纳 · 2026-07-16

## 背景

工单 #06 要求在已完成的所有模块之上建立集成测试，确保以下核心场景端到端正确：

1. **T1-T5 事务** — Writer → Planner → Editor → MemoryKeeper → 滑窗更新
2. **幂等性** — 同一 packet 重复发送不产生重复数据
3. **回滚** — 事务内异常不影响数据库中已有数据
4. **快照** — JSON dump / restore 完整性
5. **故障恢复** — RecoveryManager 从存储重建运行时状态
6. **跨实体完整性** — Character / World / Timeline 等关联数据的 CURD
7. **章节版本管理** — 全量保留 + 基线标记

## 决策

### 1. 测试分层

```
Unit (263 项)              → 模块内纯逻辑测试
    ├── test_memory_models.py      (33)
    ├── test_llm_providers.py      (94)
    ├── test_storage.py            (44)
    └── test_compression_pipeline.py (62)
    
Integration (30 项)        → 数据库端到端流程
    └── test_integration.py
```

### 2. 测试夹具策略

- **`novel_id`** — 每个 Class 级 fixture 生成唯一 `novel_id`，测试间隔离
- **`db`** — `DatabaseManager` 绑定 SQLite `:memory:`，每次新建
- **`task_id`** — 需要 CompressionTask 的测试，在 Arrange 阶段直接插 ORM 行

### 3. 测试类职责

| 测试类 | 覆盖场景 |
|---|---|
| TestNovelAndChapterPipeline | 创建小说、T1 写入、版本历史、幂等 |
| TestPlannerPlanning | T2 批量规划、事务回滚 |
| TestEditorReview | T3 评审通过/驳回、基线标记 |
| TestMemoryCompression | T4 压缩完成 |
| TestWindowShift | T5 滑窗更新 |
| TestIdempotency | digest 计算、重复检测、processed 流程 |
| TestFullPipeline | 完整端到端流水线、回滚保护 |
| TestJSONSnapshot | 导出/恢复完整性、不存在的 novel |
| TestRecovery | 故障恢复完整流程、空 novel 恢复 |
| TestCrossEntityIntegrity | Character/World/Timeline 跨实体完整性 |
| TestChapterVersionManagement | 章节版本管理 |
| TestPipelineStateManagement | PipelineState CRUD |

### 4. 回滚验证模式

所有回滚测试采用统一的 Arrange-Act-Assert 模式：

```python
async with uow.transaction():
    await uow.t2_planner_complete_planning(chapters, wrong_state)
    # 应抛出异常，事务自动回滚

async with uow.transaction():
    stored = await uow.chapters.get_chapters_by_novel(novel_id)
    assert len(stored) == 0  # 无数据残留
```

## 结果

- **263 项测试全部通过**
- 测试覆盖率覆盖了所有 5 个事务边界（T1-T5）
- 所有回滚场景均验证了 "异常→无数据残留"
- 快照导出/恢复验证了 digest 一致性

## 关联

- [ADR-0001](./0001-domain-layer-architecture.md) — 领域模型层定义
- [ADR-0002](./0002-agent-pipeline-protocol.md) — Agent 流水线协议
- [ADR-0003](./0003-four-tier-compression-memory.md) — 四级压缩记忆
- [ADR-0004](./0004-multi-provider-strategy.md) — Provider 多模型策略
- [ADR-0005](./0005-transactional-persistence.md) — 事务式持久化
