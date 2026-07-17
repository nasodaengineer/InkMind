# 事务式持久化策略 — 技术调研报告

> **日期**: 2026-07-16  
> **范围**: 数据库选型、原子写入、内容去重、快照版本历史、跨进程并发、业界参考

## 1. 数据库选型

| 方案 | 类型 | 部署复杂度 | 并发能力 | 适合阶段 |
|------|------|-----------|----------|----------|
| **SQLite (WAL模式)** | 嵌入式 | 零部署 | 支持多读单写 | **MVP+Phase2** ⭐ |
| PostgreSQL + asyncpg | 独立服务 | 高 | 优秀 | Phase 3 |
| 文件系统+索引 | 自管理 | 中 | 差 | **不推荐** |

**推荐: SQLite (WAL) + SQLAlchemy 2.0 Async** — 单 Worker MVP 阶段完全够用，Phase 3 可平滑迁移至 PostgreSQL

## 2. 原子写入

SQLite WAL 模式提供 ACID 事务保证:
- `BEGIN TRANSACTION` → 写入 → `COMMIT`
- WAL 模式下，写入不阻塞读取
- 崩溃自动恢复 (WAL checkpoint)
- FastAPI 单 Worker 下无需跨进程锁

## 3. Content Hash 去重

**推荐: SHA256** — 对小说内容的段落级去重

```python
import hashlib
content_hash = hashlib.sha256(content.encode('utf-8')).hexdigest()
```

- 复写概率 < 2^(-256)，可以认为唯一
- 用于幂等性: 相同 content_hash 的章节不重复写入
- 参考 Novel Engine: 50 版本上限 + SHA256 dedup

## 4. 快照与版本历史

### 推荐: 全量快照 + gzip 压缩

| 方案 | 存储开销 (100万字/100章) | 恢复速度 | 实现复杂度 |
|------|--------------------------|----------|-----------|
| 全量快照 | ~2MB/版本 × 50版 = 100MB | O(1) | 低 ⭐ |
| 增量快照 (WAL-based) | ~5MB/版本 × 50版 = 250MB | O(n) | 高 |
| Git-like 快照 | ~3MB/版本 × 50版 = 150MB | O(n) | 高 |

**架构**:
```
documents 表          document_versions 表
┌─────────────┐      ┌─────────────────────────┐
│ id           │──┐   │ id (PK)                 │
│ title        │  └──→│ doc_id (FK)             │
│ content      │      │ version (int)           │
│ content_hash │      │ content_compressed      │ ← gzip
│ word_count   │      │ content_hash            │ ← SHA256
│ updated_at   │      │ content_size            │
└─────────────┘      │ compressed_size         │
                      │ source ('write'/'revert')│
                      │ created_at              │
                      └─────────────────────────┘
```

回滚时自动保存当前状态为新快照 (标记为 `source='revert'`)，确保可逆。

## 5. 跨进程并发

FastAPI 多 Worker + SQLite:
- SQLite 默认写时锁定整个数据库
- 解决方案: **单 Worker + asyncio** (Phase 1/2)；或使用写队列 (如 River Queue with SQLite)
- Phase 3: 迁移至 PostgreSQL + asyncpg + 连接池

## 6. 参考项目

### Novel Engine
- SHA256 去重 + 50 版本上限
- 原子写入: 临时文件 + 重命名
- 段落级版本追踪

### novelWriter
- 原子写入: 先写临时 `.tmp` 文件，验证完整性后重命名覆盖
- 双重校验: 先写内容再写索引，确保一致
- 崩溃恢复: 启动时校验所有文件，发现移动的 `.tmp` 文件自动尝试恢复

## 原始引用

1. [SQLite WAL 模式](https://www.sqlite.org/wal.html)
2. [FastAPI + SQLAlchemy 2.0 Production (DEV)](https://dev.to/ayush_kaushik_b450595c233/fastapi-sqlalchemy-20-in-production-building-high-performance-async-apis-11ni)
3. [Novel Engine TECHINCAL.md](https://github.com/john-paul-ruf/novel-engine/blob/main/TECHNICAL.md)
4. [novelWriter Storage Technical Docs](https://novelwriter.io/docs/technical/storage.html)
5. [sqlite-history (Simon Willison)](https://simonwillison.net/2023/Apr/15/sqlite-history/)
6. [Abusing SQLite to Handle Concurrency](https://blog.skypilot.co/abusing-sqlite-to-handle-concurrency/)
7. [FastAPI-TaskFlow Multi-Instance](https://attakay78.github.io/fastapi-taskflow/guide/multi-instance/)
