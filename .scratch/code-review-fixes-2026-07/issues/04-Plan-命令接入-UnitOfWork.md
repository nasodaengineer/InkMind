# 04 — Plan 命令接入 UnitOfWork

**What to build:** 将 `inkmind cli plan` 命令中的裸 SQLAlchemy 操作改为通过 `UnitOfWork.t2_planner_complete_planning()` 事务方法完成规划/大纲的持久化和 PipelineState 更新。

**Blocked by:** 01 — CLI 基类提取 + 重复代码收敛, 02 — 修复 CLI UUID 碰撞

**Status:** ✅ done

## Acceptance criteria

- [x] `inkmind plan --novel-id X` 执行规划后，大纲数据由 `UnitOfWork.t2_planner_complete_planning()` 写入
- [x] PipelineState 状态正确推进（如：规划完成→写作中）
- [x] 新增集成测试：CLI plan → UoW → DB 验证大纲与状态
- [x] 287 项测试全部通过
