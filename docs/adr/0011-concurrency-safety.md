# ADR-0011: 并发安全策略 — 文件级互斥写锁

## 状态

已采纳（2026-07-16）

## 背景

代码审查（2026-07-16）发现 InkMind 的持久化层存在并发安全问题：

1. **SQLite 并发写入**（C5）：SQLite 在 WAL 模式下支持读写并发，但 Python 的 SQLAlchemy async session 不保证同一时间只有一个 writer。多个 `inkmind next` 并发执行时，事务 A 可能拿到事务 B 未提交的数据版本。
2. **无并发测试**：`test_uow_transaction_rollback` 仅验证了同一 session 内的回滚，没有并发场景覆盖。
3. **无 Provider 降级集成测试**：Provider 降级链（ADR-0004 §4-C）只在单元测试中验证，无集成级验证。

## 决策

### 11-A FileLock 文件级互斥写锁

**选择：基于 `portalocker` 的文件级互斥锁，保护 SQLite 数据库文件**

```python
import fcntl
import portalocker

class FileLock:
    """跨进程文件级互斥锁，确保同一时刻只有一个 writer。"""

    def __init__(self, lock_path: str, timeout: float = 30.0):
        self._lock_path = lock_path
        self._timeout = timeout
        self._fd: int | None = None

    async def __aenter__(self) -> "FileLock":
        loop = asyncio.get_running_loop()
        self._fd = await loop.run_in_executor(None, self._acquire)
        return self

    async def __aexit__(self, *args) -> None:
        if self._fd is not None:
            portalocker.unlock(self._fd)
            os.close(self._fd)
            self._fd = None

    def _acquire(self) -> int:
        fd = os.open(self._lock_path, os.O_CREAT | os.O_RDWR)
        portalocker.lock(fd, portalocker.LOCK_EX, timeout=self._timeout)
        return fd
```

锁定范围：
- **写入操作**（UnitOfWork commit）：获取文件锁
- **读取操作**（只读查询）：不加锁（SQLite WAL 模式允许多读单写）

### 11-B 锁文件位置

- 锁文件路径：`{db_path}.lock`（例如 `inkmind.db.lock`）
- 与数据库文件同目录，确保同一数据库实例共用同一把锁
- 进程退出时自动释放（文件描述符关闭 + portalocker 自动解锁）

### 11-C UnitOfWork 集成 FileLock

```python
class UnitOfWork:
    def __init__(self, session, db_path: str, ...):
        self._session = session
        self._lock = FileLock(f"{db_path}.lock")

    async def commit(self) -> None:
        async with self._lock:
            await self._session.commit()
```

### 11-D 并发事务不脏读保证

- 在 `commit()` 中获取锁，确保提交序列化。
- 在 `begin()` 中不获取锁（session 内部隔离级别为 SERIALIZABLE）。
- 对于读取后写入的复合操作（如「读取 PipelineState → 修改 → 写回」），在事务开始时获取读锁（`LOCK_SH`）。

### 11-E 集成测试套件

新增测试覆盖：

| 测试 | 覆盖场景 |
|------|---------|
| `test_concurrent_commits` | 两个协程同时 commit → 无 `database is locked`，最终数据一致 |
| `test_concurrent_read_write` | 一个在读、一个在写 → 读不阻塞，写排他 |
| `test_provider_fallback_integration` | Provider A 超时 → 自动降级到 B，metrics 正确记录降级链 |
| `test_uow_lock_timeout` | 锁超时 → 抛出 `LockTimeout` 异常，事务回滚 |
| `test_snapshot_while_writing` | 快照导出与写入并发 → 快照获得一致快照 |

## 被否决的方案

- **Python `threading.Lock`**：仅保护同一进程内线程，无法跨进程保护（用户可能开多个终端）。
- **SQLite `BEGIN IMMEDIATE`**：依赖数据库层的锁粒度太粗，无法控制锁超时行为。
- **Redis 分布式锁**：单用户桌面应用引入 Redis 过度设计。
- **完全串行化所有操作**：影响用户体验（读取也被阻塞），且 SQLite WAL 模式允许多读单写，应充分利用。

## 影响

- `portalocker` 新增为依赖（纯 Python，零 native 扩展）。
- `UnitOfWork.__init__` 新增 `db_path` 参数。
- 所有写事务自动获得文件级互斥保护。
- 新增测试文件 `tests/test_concurrency.py`（~100 行）。
- 快照导出（`SnapshotManager.export`）也受写锁保护，导出的始终是一致性快照。
