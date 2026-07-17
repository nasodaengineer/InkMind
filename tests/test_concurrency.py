"""并发安全集成测试。"""

from __future__ import annotations

import os
import tempfile

import pytest

from inkmind.storage.concurrency import FileLock
from inkmind.storage.unit_of_work import UnitOfWork


class TestFileLock:
    def test_acquire_release(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock = FileLock(tmp, "test")
            assert lock.acquire()
            lock.release()

    def test_mutex_behaviour(self):
        """两个锁不能同时持有。"""
        with tempfile.TemporaryDirectory() as tmp:
            lock1 = FileLock(tmp, "test", timeout=0.5)
            lock2 = FileLock(tmp, "test", timeout=0.5)

            acquired1 = lock1.acquire()
            assert acquired1

            # 锁1 持有时，锁2 无法获取
            acquired2 = lock2.acquire()
            assert not acquired2

            lock1.release()

            # 释放后锁2 可以获取
            acquired2 = lock2.acquire()
            assert acquired2
            lock2.release()


class TestUnitOfWorkLock:
    def test_uow_acquires_lock(self):
        """UnitOfWork 应能获取写锁。"""
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "test.db")
            # 创建空文件使 os.path.dirname 有效
            with open(db_path, "w") as f:
                f.write("")
            os.unlink(db_path)  # UnitOfWork 会创建

            with UnitOfWork(db_path) as uow:
                assert uow._lock is not None
                assert uow._lock._lock_path.endswith(".db.lock")


class TestFileLockContextManager:
    def test_context_manager_acquires_and_releases(self):
        """FileLock 的上下文管理器应正确获取和释放锁。"""
        with tempfile.TemporaryDirectory() as tmp:
            lock = FileLock(tmp, "ctx_test", timeout=1.0)
            with lock as acquired:
                assert acquired is True
            # 释放后应能重新获取
            assert lock.acquire()
            lock.release()

    def test_timeout_returns_false(self):
        """超时后 acquire 应返回 False。"""
        with tempfile.TemporaryDirectory() as tmp:
            lock1 = FileLock(tmp, "timeout_test", timeout=0.3)
            lock2 = FileLock(tmp, "timeout_test", timeout=0.3)

            assert lock1.acquire()
            # 锁1 持有时，锁2 超时应返回 False
            assert not lock2.acquire()
            lock1.release()
