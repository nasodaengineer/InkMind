# 01 — CLI 基类提取 + 重复代码收敛

**What to build:** 消除 `inkmind/inkmind/cli/commands/` 下 10 个命令文件中重复的 4 行样板代码（`OutputFormatter`、`CLIConfig.load`、`db_path`、`novel_id`），将 `_compute_content_digest` 从 `unit_of_work.py` 和 `idempotency.py` 中合并到后者，将所有运行时 `import` 移到模块顶层，为关键模块补上 `__all__`。

**Blocked by:** None — can start immediately.

**Status:** ✅ done

## Acceptance criteria

- [x] `BaseCommand` 基类提取完成，10 个命令文件各减少至少 4 行重复代码
- [x] `_compute_content_digest` 合并到 `inkmind/storage/digest.py` 中，`unit_of_work.py` 引用该实现
- [x] 所有 `import` 从函数体内移到文件顶层（PEP 8）
- [x] `inkmind/storage/models.py`、`inkmind/storage/__init__.py` 补上 `__all__`
- [x] 287 项测试全部通过，10 个子命令的 `--help` 和行为不变
