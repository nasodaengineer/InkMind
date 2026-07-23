"""并发安全集成测试。"""

from __future__ import annotations

import asyncio
import os
import tempfile

import pytest

from inkmind.storage.concurrency import FileLock
from inkmind.storage.database import DatabaseManager
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


class TestFileLockFromPathCreation:
    def test_from_path_creates_lock_file(self):
        """FileLock.from_path 应基于 db_path 创建 .lock 文件。"""
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "test.db")
            lock = FileLock.from_path(db_path)
            assert lock._lock_path.endswith(".db.lock")
            assert lock.acquire()
            lock.release()


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


class TestFileLockFromPath:
    """ADR-0011 §11-B：锁文件路径为 {db_path}.lock。"""

    def test_from_path_lock_file_location(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "inkmind.db")
            lock = FileLock.from_path(db_path)
            assert lock._lock_path == db_path + ".lock"

            assert lock.acquire()
            assert os.path.exists(db_path + ".lock")
            lock.release()
            assert not os.path.exists(db_path + ".lock")

    def test_from_path_respects_timeout(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "x.db")
            lock1 = FileLock.from_path(db_path, timeout=0.3)
            lock2 = FileLock.from_path(db_path, timeout=0.3)
            assert lock1.acquire()
            assert not lock2.acquire()
            lock1.release()

    @pytest.mark.asyncio
    async def test_aacquire_async(self):
        """异步获取：语义与同步 acquire 一致，不阻塞事件循环。"""
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "async.db")
            lock = FileLock.from_path(db_path, timeout=1.0)
            assert await lock.aacquire()
            lock.release()


class TestUnitOfWorkSessionModeLock:
    """11-C：session 模式 UoW 接入 FileLock，commit 持锁。"""

    @pytest.fixture(autouse=True)
    def _fresh_db_manager(self, monkeypatch):
        """每个测试重置 DatabaseManager 单例，引擎绑定各自临时库。

        DatabaseManager 引擎是模块级单例（首个构造者获胜）；
        不重置时，前一个测试的临时目录删除后引擎指向失效路径。
        monkeypatch 在测试结束后恢复原值，不影响其他测试文件。
        """
        import inkmind.storage.database as db_mod

        monkeypatch.setattr(db_mod, "_engine", None)
        monkeypatch.setattr(db_mod, "_session_factory", None)
        monkeypatch.setattr(db_mod, "_default_manager", None)
        yield

    @pytest.mark.asyncio
    async def test_session_mode_with_db_path_has_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "x.db")
            mgr = DatabaseManager(db_path)
            async with mgr.session() as session:
                uow = UnitOfWork(session, db_path=db_path)
                assert uow._lock is not None
                assert uow._lock._lock_path == db_path + ".lock"
            await mgr.close()

    @pytest.mark.asyncio
    async def test_session_mode_without_db_path_no_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "y.db")
            mgr = DatabaseManager(db_path)
            async with mgr.session() as session:
                uow = UnitOfWork(session)
                assert uow._lock is None
            await mgr.close()

    @pytest.mark.asyncio
    async def test_get_uow_wires_lock_and_repos(self):
        """cli.db.get_uow：保持 session/repos 可用，同时接入 FileLock。"""
        from inkmind.cli.db import get_uow

        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "cli.db")
            async with get_uow(db_path) as uow:
                assert uow._lock is not None
                assert uow._lock._lock_path == db_path + ".lock"

    @pytest.mark.asyncio
    async def test_commit_acquires_and_releases_lock(self):
        from inkmind.cli.db import get_uow

        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "commit.db")
            async with get_uow(db_path) as uow:
                events = []
                orig_acquire = uow._lock.acquire
                orig_release = uow._lock.release

                def spy_acquire():
                    events.append("acquire")
                    return orig_acquire()

                def spy_release():
                    events.append("release")
                    orig_release()

                uow._lock.acquire = spy_acquire
                uow._lock.release = spy_release

                await uow.commit()

                assert events == ["acquire", "release"]
                # 提交后锁文件不残留
                assert not os.path.exists(db_path + ".lock")

    @pytest.mark.asyncio
    async def test_commit_lock_timeout_raises(self):
        """锁被外部持有时 commit 超时 → RuntimeError。"""
        from inkmind.cli.db import get_uow

        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "blocked.db")
            blocker = FileLock.from_path(db_path, timeout=1.0)
            assert blocker.acquire()
            try:
                async with get_uow(db_path, timeout=0.3) as uow:
                    with pytest.raises(RuntimeError, match="写锁超时"):
                        await uow.commit()
            finally:
                blocker.release()

    @pytest.mark.asyncio
    async def test_concurrent_commits_serialized(self):
        """两个 UoW 并发 commit：文件锁保证串行，互不嵌套。"""
        from inkmind.cli.db import get_uow

        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "race.db")
            # 预热：先串行完成建表，避免 worker 间 create_tables 竞态
            async with get_uow(db_path):
                pass

            order = []

            async def worker(tag: str, delay: float):
                async with get_uow(db_path) as uow:
                    orig_commit = uow._session.commit
                    recorded = {"done": False}

                    async def slow_commit():
                        if not recorded["done"]:
                            recorded["done"] = True
                            order.append(f"begin-{tag}")
                            await asyncio.sleep(delay)
                            await orig_commit()
                            order.append(f"end-{tag}")
                        else:
                            await orig_commit()

                    uow._session.commit = slow_commit
                    await uow.commit()

            await asyncio.gather(worker("a", 0.2), worker("b", 0.05))

            # 串行化：一对 begin/end 完整结束后另一对才开始
            assert order in (
                ["begin-a", "end-a", "begin-b", "end-b"],
                ["begin-b", "end-b", "begin-a", "end-a"],
            )

    @pytest.mark.asyncio
    async def test_sync_context_lock_not_double_acquired(self):
        """sync with 已持锁时，commit 不再重复获取（防自锁死）。"""
        from inkmind.cli.db import get_uow

        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "reentrant.db")
            async with get_uow(db_path) as uow:
                calls = {"acquire": 0}
                orig_acquire = uow._lock.acquire

                def counting_acquire():
                    calls["acquire"] += 1
                    return orig_acquire()

                uow._lock.acquire = counting_acquire

                with uow:
                    await uow.commit()

                assert calls["acquire"] == 1
                assert not os.path.exists(db_path + ".lock")
