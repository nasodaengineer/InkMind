# 03 — Write 命令接入 UnitOfWork

**What to build:** 将 `inkmind cli write` 命令中正在使用裸 SQLAlchemy 操作（`session.add(ch_model)` + `session.commit()` ）改为通过 `UnitOfWork.t1_writer_complete_chapter()` 事务方法完成章节创建和 PipelineState 更新。Digest 幂等性在此路径下生效。

**Blocked by:** 01 — CLI 基类提取 + 重复代码收敛, 02 — 修复 CLI UUID 碰撞

**Status:** ✅ done

## Acceptance criteria

- [x] `inkmind write --novel-id X` 写完一章后，章节记录由 `UnitOfWork.t1_writer_complete_chapter()` 写入
- [x] Digest 幂等生效：重复调用相同内容 → 第二次跳过写入并返回已存在的章节 ID
- [x] PipelineState（状态机：大纲→写作中→待评审→已定稿）通过 UoW 事务边界更新
- [x] 新增集成测试：CLI write → UoW → DB 验证章节与状态正确写入
- [x] 287 项测试全部通过
