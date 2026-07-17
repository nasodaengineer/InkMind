# 05 — Review 命令接入 UnitOfWork

**What to build:** 将 `inkmind cli review` 命令中的裸 SQLAlchemy 操作改为通过 `UnitOfWork.t3_editor_complete_review()` 事务方法完成评审结果的持久化和 PipelineState 更新。

**Blocked by:** 01 — CLI 基类提取 + 重复代码收敛, 02 — 修复 CLI UUID 碰撞

**Status:** ✅ done

## Acceptance criteria

- [x] `inkmind review --novel-id X` 执行评审后，评审结果由 `UnitOfWork.t3_editor_complete_review()` 写入
- [x] PipelineState 状态正确推进（如：评审完成→已定稿，或返回待修改）
- [x] 新增集成测试：CLI review → UoW → DB 验证评审结果与状态
- [x] 287 项测试全部通过
