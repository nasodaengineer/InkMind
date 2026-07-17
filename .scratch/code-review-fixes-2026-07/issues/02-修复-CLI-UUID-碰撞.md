# 02 — 修复 CLI UUID 跨小说碰撞

**What to build:** 修复 `inkmind/cli/commands/write.py` 中使用 `UUID(int=next_index)` 生成章节 ID 的问题。该实现导致不同小说创建相同序号章节时 UUID 完全一致，快照恢复场景下章节互相覆盖。改为 `uuid4()` 生成全局唯一 ID。

**Blocked by:** None — can start immediately.（与 T01 并行；若 T01 已合并基类，则在该基类之上改）

**Status:** ✅ done

## Acceptance criteria

- [x] `write.py` 中 `ch_uuid = str(UUID(int=next_index))` 替换为 `ch_uuid = str(uuid4())`
- [x] 新增测试：两个不同 `novel_id` 各自创建第 1 章 → 生成的 UUID 不相同
- [x] 新增测试：JSON 快照 dump → restore → 两个小说章节不互相覆盖
- [x] 287 项测试全部通过
