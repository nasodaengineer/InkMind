---
title: 事务式持久化存储策略
type: wayfinder:research
status: research-complete
resolved_at: 2026-07-16
research_findings: '.scratch/research/persistence-strategy.md'
created: 2026-07-16
blocked_by: [01, 03]
labels: [persistence, wayfinder]
---

## Question

如何实现事务式持久化——原子写入、digest 幂等、快照回滚、跨进程锁？

需要研究的核心问题：

1. **存储引擎选型**：
   - SQLite（轻量，单文件，开发期友好）
   - PostgreSQL（生产级，JSONB 支持，适合复杂查询）
   - 文件系统 + 索引（类似 Openwrite_skill 的 src/data 隔离）
   - 第一阶段 MVP 用 SQLite，后续可迁移？

2. **原子写入**：
   - 每次章节保存如何保证不丢数据？
   - 写入时程序崩溃如何恢复？
   - SQLite 的 WAL 模式 vs 显式事务

3. **Digest 幂等**：
   - 每个数据块用 content hash 做唯一标识
   - 相同内容重复写入自动去重
   - hash 碰撞处理？SHA256 足矣？

4. **快照与回滚**：
   - 用户如何回到某个历史版本？
   - 快照是全量快照还是增量快照？
   - 存储空间预算——100万字的快照存储开销

5. **跨进程锁**：
   - FastAPI 多 worker 场景下的写冲突
   - 文件锁 vs 数据库行锁
   - SQLite 并发写限制如何解决？

6. **参考实现**：
   - Openwrite_skill 的 `write_chapter` 事务机制
   - ainovel-cli 的持久化设计

请输出存储层接口设计 + 关键操作的伪代码实现。
