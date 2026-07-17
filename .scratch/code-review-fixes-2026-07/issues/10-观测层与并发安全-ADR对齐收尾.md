# 10 — 观测层与并发安全 ADR 对齐收尾

**What to build:** 工单 06 两轴 code-review 发现的 ADR 残余缺口。07/08 已完成主体实现，但对照 ADR 原文仍有以下未对齐项：

**Blocked by:** None — can start immediately.

**Status:** ⏳ open

## Acceptance criteria

### ADR-0010（Provider 观测层，07 的残余）

- [ ] 10-A：每次调用产生一个不可变 Stats 快照（`@dataclass(frozen=True)`，含
  latency_ms / success / error_type / degraded / retry_count / timestamp）；
  现有 `ProviderStats` 是可变累计器，字段不齐
- [ ] 10-C：`LLMClient` 暴露 `record_stats` / `aggregate_stats` / `reset_stats`
  （现仅有 `get_stats()`）
- [ ] 10-E：`RateLimiter` / `max_calls_per_minute` 速率限制（完全缺失）

### ADR-0011（并发安全，08 的残余）

- [ ] 11-C：CLI 写路径 commit 须持文件锁。`cli/db.py get_uow` 返回 session 模式
  UoW（`_lock=None`），`init` / `next` / `plan` / `write` / `review` 等所有 CLI
  命令写事务均无文件锁互斥（`UnitOfWork` 的文件锁模式仅在以 db_path 字符串
  构造时启用，但该模式下 repos 为 None 不可用）——需要让 get_uow/get_session
  在保持 session 可用性的同时接入 FileLock

## 参考

- 审查原始记录见工单 06「实现备忘」与 2026-07-17 code-review 两轴报告
- `inkmind/storage/concurrency.py`（FileLock 实现）、`inkmind/storage/unit_of_work.py:51-87`（锁模式构造）
