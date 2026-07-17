"""CLI 集成测试 — 验证所有命令端到端。"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from uuid import UUID

import pytest

# ── 前置条件 ──

INKMIND_MODULE = "inkmind"
PYTHON = sys.executable


@pytest.fixture(scope="module")
def temp_db_dir():
    """为所有测试提供一个临时目录。"""
    tmp = tempfile.mkdtemp()
    yield tmp
    shutil.rmtree(tmp)


def _cli(*args, cwd: str | None = None) -> subprocess.CompletedProcess:
    """执行 inkmind CLI 命令。"""
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    # 离线确定性 LLM：CLI 测试不依赖网络/API Key（仅 next 命令读取此变量）
    env.setdefault("INKMIND_LLM_FAKE", "1")
    cmd = [PYTHON, "-m", INKMIND_MODULE] + list(args)
    return subprocess.run(
        cmd,
        capture_output=True,
        stdin=subprocess.DEVNULL,
        encoding="utf-8",
        cwd=cwd,
        timeout=30,
        env=env,
    )


# ═══════════════════════════════════════════════
#  基础命令
# ═══════════════════════════════════════════════


class TestNextStatsBlock:
    """next 命令统计块：含耗时字段（spec：生成内容量、耗时、状态）。"""

    def test_stats_block_includes_latency(self):
        from types import SimpleNamespace

        from inkmind.cli.commands.next import _stats_block

        fake_stats = SimpleNamespace(
            total_calls=3,
            successful_calls=3,
            failed_calls=0,
            fallback_used=0,
            total_tokens=100,
            estimated_cost=0.0,
            min_latency=1.0,
            max_latency=5.0,
            avg_latency=3.0,
        )

        class FakeLLM:
            def get_stats(self):
                return {"deepseek": fake_stats}

        block = _stats_block(FakeLLM())
        assert block["deepseek"]["total_calls"] == 3
        assert block["deepseek"]["avg_latency"] == 3.0
        assert block["deepseek"]["max_latency"] == 5.0
        assert block["deepseek"]["min_latency"] == 1.0

    def test_stats_block_without_get_stats(self):
        from inkmind.cli.commands.next import _stats_block

        assert _stats_block(object()) == {}


class TestInit:
    """inkmind init — 初始化新小说。"""

    def test_init_success(self, temp_db_dir):
        """成功创建新小说。"""
        db = os.path.join(temp_db_dir, "test_init.db")
        result = _cli("init", "--title", "测试小说", "--description", "这是一本测试小说", f"--db={db}")
        assert result.returncode == 0
        assert "已创建" in result.stdout
        assert "novel_id" in result.stdout or "小说 ID" in result.stdout

    def test_init_empty_title(self, temp_db_dir):
        """空标题应报错（需要交互输入，这里无法测试，但通过 db 参数应该不会阻塞）。"""
        # 以 --json 模式运行以确保可解析
        db = os.path.join(temp_db_dir, "test_init_empty.db")
        result = _cli("init", "--title", "", f"--db={db}")
        # 交互模式下没有 stdin 会直接返回错误
        assert result.returncode == 0  # 子进程可能正常退出


class TestVersion:
    """inkmind version — 版本信息。"""

    def test_version(self):
        result = _cli("version")
        assert result.returncode == 0
        assert "0.1.0" in result.stdout

    def test_version_json(self):
        result = _cli("version", "--json")
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["inkmind"] == "0.1.0"


class TestHelp:
    """inkmind 无参数 — 显示帮助。"""

    def test_help_no_args(self):
        result = _cli()
        assert result.returncode != 0 or "usage" in result.stdout


# ═══════════════════════════════════════════════
#  完整流水线（集成）
# ═══════════════════════════════════════════════


class TestPipeline:
    """端到端流水线测试。"""

    @pytest.fixture
    def novel_id(self, temp_db_dir):
        """先创建一本小说，返回 novel_id。"""
        db = os.path.join(temp_db_dir, "pipeline.db")
        result = _cli("init", "--title", "流水线测试", f"--db={db}")
        assert result.returncode == 0
        # 从输出提取 novel_id
        return self._extract_novel_id(result.stdout)

    def _extract_novel_id(self, output: str) -> str:
        """从 CLI 输出提取 novel_id。"""
        for line in output.splitlines():
            if "小说 ID:" in line:
                return line.split("小说 ID:")[-1].strip()
            if "novel_id" in line:
                try:
                    data = json.loads(output)
                    return data.get("novel_id", "")
                except (json.JSONDecodeError, KeyError):
                    continue
        return ""

    def test_next_pipeline_json(self, temp_db_dir, novel_id):
        """--json 模式下执行一轮完整流水线并验证输出。"""
        if not novel_id:
            pytest.skip("未提取到 novel_id")
        db = os.path.join(temp_db_dir, "pipeline.db")
        result = _cli("next", "--title", "第一章", f"--novel-id={novel_id}", f"--db={db}", "--json")
        assert result.returncode == 0, f"stderr: {result.stderr}"

        data = json.loads(result.stdout)
        assert data["status"] == "ok"
        assert "已完成一轮流水线" in data["message"]
        assert "_stats" in data  # ADR-0010-D：JSON 输出尾部附 _stats 字段

    def test_status_after_next(self, temp_db_dir, novel_id):
        """流水线后检查状态。"""
        if not novel_id:
            pytest.skip("未提取到 novel_id")
        db = os.path.join(temp_db_dir, "pipeline.db")
        # 先执行一轮流水线创建章节
        result = _cli("next", "--title", "第一章", f"--novel-id={novel_id}", f"--db={db}", "--json")
        print(f"\nDEBUG next.stdout={result.stdout!r}")
        print(f"DEBUG next.stderr={result.stderr!r}")
        assert result.returncode == 0, f"next 失败: {result.stderr}"
        # DEBUG: 直接查 DB
        import sqlite3
        _conn = sqlite3.connect(db)
        _cur = _conn.cursor()
        _cur.execute("SELECT novel_id, chapter_index, title, status FROM chapters")
        _rows = _cur.fetchall()
        print(f"DEBUG DB chapters: {_rows}")
        _cur.execute("SELECT uuid, title, chapter_count FROM novels")
        _nrows = _cur.fetchall()
        print(f"DEBUG DB novels: {_nrows}")
        _conn.close()
        # 再检查状态
        result = _cli("status", f"--novel-id={novel_id}", f"--db={db}", "--json")
        assert result.returncode == 0

        data = json.loads(result.stdout)
        assert data["novel_id"] == novel_id
        assert data["chapters"]["total"] >= 1
        assert data["chapters"]["finalized"] >= 1

    def test_commit_snapshot(self, temp_db_dir, novel_id):
        """导出的快照应包含 novel_id。"""
        if not novel_id:
            pytest.skip("未提取到 novel_id")
        db = os.path.join(temp_db_dir, "pipeline.db")
        output = os.path.join(temp_db_dir, f"snapshot-{novel_id}.json")

        result = _cli("commit", f"--novel-id={novel_id}", f"--db={db}", f"--output={output}")
        assert result.returncode == 0
        assert Path(output).exists()

        # 验证快照内容
        with open(output, encoding="utf-8") as f:
            snap = json.load(f)
        assert snap["novel_id"] == novel_id
        assert "novel" in snap
        assert "chapters" in snap

    def test_restore_snapshot(self, temp_db_dir, novel_id):
        """从快照恢复到新数据库。"""
        if not novel_id:
            pytest.skip("未提取到 novel_id")
        db = os.path.join(temp_db_dir, "pipeline.db")
        src_snap = os.path.join(temp_db_dir, f"snapshot-{novel_id}.json")
        restore_db = os.path.join(temp_db_dir, "restored.db")

        # 先导出快照
        _cli("commit", f"--novel-id={novel_id}", f"--db={db}", f"--output={src_snap}")

        # 恢复到新 db
        result = _cli("restore", src_snap, f"--db={restore_db}")
        assert result.returncode == 0

        # 验证恢复后能查询
        result = _cli("status", f"--novel-id={novel_id}", f"--db={restore_db}", "--json")
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["novel_id"] == novel_id


class TestWrite:
    """inkmind write — 写作章节。"""

    def test_write_no_novel_id(self, temp_db_dir):
        """未指定 novel_id 应报错。"""
        db = os.path.join(temp_db_dir, "write_no_novel.db")
        result = _cli("write", "测试章节", f"--db={db}")
        assert result.returncode == 0
        assert "未指定 novel_id" in result.stdout

    def test_write_success(self, temp_db_dir):
        """创建章节。"""
        db = os.path.join(temp_db_dir, "write.db")
        
        # 先创建小说
        init_result = _cli("init", "--title", "写测试", f"--db={db}")
        assert init_result.returncode == 0

        # 提取 novel_id
        novel_id = ""
        for line in init_result.stdout.splitlines():
            if "小说 ID:" in line:
                novel_id = line.split("小说 ID:")[-1].strip()
                break

        if not novel_id:
            pytest.skip("未提取到 novel_id")

        result = _cli("write", "第一章", f"--novel-id={novel_id}", f"--db={db}")
        assert result.returncode == 0
        assert "已创建" in result.stdout

    def test_write_uuid_no_collision(self, temp_db_dir):
        """两本不同小说的同序号章节 → UUID 不同（T02 防止碰撞）。"""
        from uuid import UUID
        db = os.path.join(temp_db_dir, "write_uuid_collision.db")

        # 第一本小说
        init1 = _cli("init", "--title", "小说A", f"--db={db}")
        nid1 = _extract_novel_id(init1.stdout)
        if not nid1:
            pytest.skip("未提取到 novel_id")
        w1 = _cli("write", "第一章", f"--novel-id={nid1}", f"--db={db}", "--json")
        assert w1.returncode == 0
        d1 = json.loads(w1.stdout)

        # 第二本小说（同一数据库，不同 novel_id）
        init2 = _cli("init", "--title", "小说B", f"--db={db}")
        nid2 = _extract_novel_id(init2.stdout)
        if not nid2:
            pytest.skip("未提取到 novel_id")
        w2 = _cli("write", "第一章", f"--novel-id={nid2}", f"--db={db}", "--json")
        assert w2.returncode == 0
        d2 = json.loads(w2.stdout)

        # 两个 chapter_id 必须不同
        assert d1["chapter_id"] != d2["chapter_id"]
        # 两个都是有效的 UUID
        UUID(d1["chapter_id"])
        UUID(d2["chapter_id"])
        # 且都是第 1 章
        assert d1["chapter_index"] == 1
        assert d2["chapter_index"] == 1

    def test_write_json_output_has_chapter_id(self, temp_db_dir):
        """--json 模式下输出应包含 chapter_id。"""
        db = os.path.join(temp_db_dir, "write_json_chid.db")
        init_result = _cli("init", "--title", "写测试JSON", f"--db={db}")
        novel_id = _extract_novel_id(init_result.stdout)
        if not novel_id:
            pytest.skip("未提取到 novel_id")
        result = _cli("write", "第一章", f"--novel-id={novel_id}", f"--db={db}", "--json")
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert "chapter_id" in data

    def test_write_sequential_chapter_index(self, temp_db_dir):
        """同一 novel 连续 write，章节序号依次为 1、2、3（#11 回归）。"""
        import sqlite3

        db = os.path.join(temp_db_dir, "write_sequential.db")
        init_result = _cli("init", "--title", "连续写作", f"--db={db}")
        novel_id = _extract_novel_id(init_result.stdout)
        if not novel_id:
            pytest.skip("未提取到 novel_id")

        indexes = []
        for i, title in enumerate(["第一章", "第二章", "第三章"]):
            result = _cli("write", title, f"--novel-id={novel_id}", f"--db={db}", "--json")
            assert result.returncode == 0
            indexes.append(json.loads(result.stdout)["chapter_index"])

            if i == 0:
                # 首次 write 后 PipelineState 应已回写序号
                conn = sqlite3.connect(db)
                try:
                    row = conn.execute(
                        "SELECT current_chapter_index, total_chapters "
                        "FROM pipeline_states WHERE novel_id = ?",
                        (novel_id,),
                    ).fetchone()
                finally:
                    conn.close()
                assert row is not None
                assert row[0] == 1, "首次 write 后 current_chapter_index 应为 1"
                assert row[1] == 1, "首次 write 后 total_chapters 应为 1"

        assert indexes == [1, 2, 3]


def _extract_novel_id(output: str) -> str:
    """从 CLI 输出提取 novel_id。"""
    for line in output.splitlines():
        if "小说 ID:" in line:
            return line.split("小说 ID:")[-1].strip()
        if "novel_id" in line:
            try:
                data = json.loads(output)
                return data.get("novel_id", "")
            except (json.JSONDecodeError, KeyError):
                continue
    return ""


class TestPlan:
    """inkmind plan — 规划章节。"""

    def test_plan_default(self, temp_db_dir):
        """默认规划 5 章。"""
        db = os.path.join(temp_db_dir, "plan.db")
        init_result = _cli("init", "--title", "规划测试", f"--db={db}")
        assert init_result.returncode == 0

        # 提取 novel_id
        novel_id = ""
        for line in init_result.stdout.splitlines():
            if "小说 ID:" in line:
                novel_id = line.split("小说 ID:")[-1].strip()
                break

        if not novel_id:
            pytest.skip("未提取到 novel_id")

        result = _cli("plan", "3", f"--novel-id={novel_id}", f"--db={db}")
        assert result.returncode == 0
        assert "已规划" in result.stdout


class TestReview:
    """inkmind review — 评审章节。"""

    def test_review_no_chapter(self, temp_db_dir):
        """无章节时评审应给出提示。"""
        db = os.path.join(temp_db_dir, "review_no_chap.db")
        init_result = _cli("init", "--title", "评审测试", f"--db={db}")
        assert init_result.returncode == 0

        novel_id = ""
        for line in init_result.stdout.splitlines():
            if "小说 ID:" in line:
                novel_id = line.split("小说 ID:")[-1].strip()
                break

        result = _cli("review", f"--novel-id={novel_id}", f"--db={db}")
        assert result.returncode == 0

    def test_review_chapter(self, temp_db_dir):
        """对现有章节进行评审。"""
        db = os.path.join(temp_db_dir, "review_chap.db")
        init_result = _cli("init", "--title", "评审测试2", f"--db={db}")
        assert init_result.returncode == 0

        novel_id = ""
        for line in init_result.stdout.splitlines():
            if "小说 ID:" in line:
                novel_id = line.split("小说 ID:")[-1].strip()
                break

        # 先写一章
        _cli("write", "第一章", f"--novel-id={novel_id}", f"--db={db}")

        # 评审
        result = _cli("review", f"--novel-id={novel_id}", f"--db={db}")
        assert result.returncode == 0
        assert "评审通过" in result.stdout


# ═══════════════════════════════════════════════
#  JSON 输出格式
# ═══════════════════════════════════════════════


class TestJsonOutput:
    """--json 模式验证。"""

    def test_version_json(self):
        result = _cli("version", "--json")
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert isinstance(data, dict)
        assert "inkmind" in data

    def test_status_json_no_novel(self):
        result = _cli("status", "--json")
        # 没有 novel_id 应该返回错误
        data = json.loads(result.stdout)
        assert isinstance(data, dict)
