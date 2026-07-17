"""并发安全 — SQLite 文件级写锁。"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Optional


class FileLock:
    """跨协程、跨线程的文件级互斥写锁（基于文件系统）。

    用于 SQLite 等不支持多 Writer 并发的场景。

    用法:
        lock = FileLock("/tmp", "mydb", timeout=5.0)
        with lock:
            # 执行写操作
            ...

    ADR-0011 §11-B：以数据库路径构造时，锁文件为 ``{db_path}.lock``：

        lock = FileLock.from_path("data/inkmind.db", timeout=30.0)
    """

    def __init__(self, lock_dir: str, lock_name: str, timeout: float = 5.0):
        self._lock_path = os.path.join(lock_dir, f".{lock_name}.lock")
        self._timeout = timeout

    @classmethod
    def from_path(cls, db_path: str, timeout: float = 5.0) -> "FileLock":
        """以数据库文件路径构造锁，锁文件路径为 ``{db_path}.lock``。

        与数据库文件同目录，确保同一数据库实例共用同一把锁。
        """
        lock = cls.__new__(cls)
        lock._lock_path = f"{db_path}.lock"
        lock._timeout = timeout
        return lock

    def acquire(self) -> bool:
        """尝试获取锁，超时返回 False。"""
        deadline = time.monotonic() + self._timeout
        while time.monotonic() < deadline:
            try:
                fd = os.open(self._lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(fd)
                return True
            except FileExistsError:
                time.sleep(0.05)
        return False

    async def aacquire(self) -> bool:
        """异步获取锁（阻塞轮询放入线程池，不阻塞事件循环）。"""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self.acquire)

    def release(self) -> None:
        """释放锁。"""
        try:
            if os.path.exists(self._lock_path):
                os.unlink(self._lock_path)
        except OSError:
            pass

    def __enter__(self) -> bool:
        return self.acquire()

    def __exit__(self, *args) -> None:
        self.release()
