# ADR-0009: CLI 命令接入 UnitOfWork 事务边界

## 状态

已采纳（2026-07-16）

## 背景

代码审查（2026-07-16）发现 ADR-0005 定义的 5 个事务边界（T1–T5）在 CLI 层**被完全绕过**。

具体违反点：

| 命令 | 审查发现 | 违反 |
|------|----------|------|
| `write.py` L62-99 | 使用裸 SQLAlchemy 直接创建 `ChapterModel` + 直接 `session.update(PipelineStateModel)` | T1（Writer 完成章节） |
| `plan.py` L56-72 | 直接 `session.add(ChapterModel)` 批量插入 + 直接更新状态 | T2（Planner 完成规划） |
| `review.py` 类似 | 直接写入评审结论 + 更新章节状态 | T3（Editor 完成评审） |
| 全部 | 无 Digest 幂等校验 | ADR-0005 §5-3 |

后果：
- CLI 路径下 5 个事务边界**全部失效**
- Digest 幂等性（ADR-0005 §5-3）在 CLI 路径下**完全不生效**
- 没有章节版本管理（ADR-0005 §5-4 定义的 `ChapterVersionModel` 被忽略）

## 决策

### 9-A 所有 CLI 写入操作必须通过 UnitOfWork

**选择：CLI 命令禁止直接操作 ORM Session，全部委托给 UnitOfWork 事务方法**

```python
# ❌ 旧方式：裸 SQLAlchemy
ch_model = ChapterModel(uuid=ch_uuid, novel_id=novel_id, ...)
session.add(ch_model)
session.commit()

# ✅ 新方式：通过 UnitOfWork
result = await uow.transaction(
    repo_cls=ChapterRepo,
    novel_id=novel_id,
    action="write_chapter",
    data={"title": title, "content": content},
)
```

每个 CLI 命令对应的事务映射：

| 命令 | UnitOfWork 方法 | 事务边界 |
|------|----------------|----------|
| `inkmind write` | `uow.t1_writer_complete_chapter()` | T1 |
| `inkmind plan` | `uow.t2_planner_complete_planning()` | T2 |
| `inkmind review` | `uow.t3_editor_complete_review()` | T3 |

### 9-B 新增 UnitOfWork 事务方法签名

```python
class UnitOfWork:
    async def t1_writer_complete_chapter(
        self,
        novel_id: UUID,
        chapter_title: str,
        chapter_content: str,
        outline: ChapterOutline | None = None,
    ) -> Chapter: ...

    async def t2_planner_complete_planning(
        self,
        novel_id: UUID,
        chapters: list[ChapterOutline],
    ) -> list[Chapter]: ...

    async def t3_editor_complete_review(
        self,
        novel_id: UUID,
        chapter_id: UUID,
        verdict: str,
        issues: list[str],
    ) -> Chapter: ...
```

### 9-C CLI 命令内部事务流程

每个命令的 `_run()` 方法的统一模式：

```
1. 解析参数 → 构建领域模型对象
2. 创建 UnitOfWork 实例（通过 CLIUnitOfWorkFactory）
3. 调用对应事务方法
4. 通过 formatter 输出结果
5. 异常时事务自动回滚 + 输出错误
```

### 9-D CLI 路径下的 Digest 幂等

- UnitOfWork 事务方法内自动调用 `IdempotencyGuard.check_and_mark()`
- 重复请求（相同 digest）自动跳过，返回已有结果
- CLI 命令无需手动处理幂等逻辑

## 被否决的方案

- **在 CLI 命令中手动调用 Repository**：无法保证跨多次调用的原子性。
- **在 CLI 命令中手动调用 IdempotencyGuard**：每个命令都需要记住调用，容易遗漏。
- **在 CLI 命令中创建事务装饰器**：粒度太粗，无法精确控制每个事务边界内的操作集合。

## 影响

- `write.py` / `plan.py` / `review.py` 三文件**完全重写**内部实现，但保持 CLI 接口不变。
- 新增 `CLIUnitOfWorkFactory`（负责从 CLI 参数创建 UoW 实例）。
- Digest 幂等在 CLI 路径下**自动生效**，无需命令作者关心。
- 现有测试全部通过（接口兼容），新增 CLI→UoW 集成测试验证完整事务流。
