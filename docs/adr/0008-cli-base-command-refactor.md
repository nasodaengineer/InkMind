# ADR-0008: CLI 命令架构重构 — 基类 + 代码质量收敛

## 状态

已采纳（2026-07-16）

## 背景

代码审查（2026-07-16）发现 CLI 层存在系统性代码质量问题：

1. **10 个命令重复 4 行样板代码**（M1）：每个 `run()` 函数前 4 行完全一致（`OutputFormatter` 初始化、`CLIConfig.load`、`db_path` 解析、`novel_id` 类型转换），共 40+ 行冗余。
2. **`_compute_content_digest` 两处重复定义**（M2）：`unit_of_work.py` 和 `idempotency.py` 各定义一次 SHA-256 digest 函数。
3. **CLI imports 放在 async 函数体内**（M3）：多个命令在运行时才 `import` SQLAlchemy 模块，违反 PEP 8 顶层 import 惯例，增加每次调用的开销。
4. **关键模块缺少 `__all__`**（M5）：`storage/models.py` 的 10+ ORM 类、`storage/__init__.py` 的公开 API 无显式导出边界，IDE 无法精确补全。
5. **`--json` 标志预处理脆弱**（M4）：`main.py` 使用 `"--json" in sys.argv[1:]` + 过滤移除，无法处理 `--title "--json"` 或 `--json=true` 变体。
6. **无软删除机制**（M6）：所有实体（Novel/Chapter/Character/World）的删除是物理 `session.delete()`，误删后无法恢复。
7. **工程基础设施缺失**（N3–N5）：无 `.editorconfig`、无 `pre-commit` hooks、`version` 命令输出占位符。

## 决策

### 8-A BaseCommand 基类

**选择：模板方法模式消除命令重复**

```python
class BaseCommand:
    """所有 CLI 命令的基类。"""

    name: ClassVar[str]
    help: ClassVar[str]

    @classmethod
    def setup(cls, subparsers) -> None:
        parser = subparsers.add_parser(cls.name, help=cls.help)
        cls._add_arguments(parser)
        parser.set_defaults(func=cls.execute)

    @classmethod
    def _add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        pass  # 子类覆写

    @classmethod
    async def _run(cls, args: argparse.Namespace) -> None:
        raise NotImplementedError

    @classmethod
    def execute(cls, args: argparse.Namespace) -> None:
        """命令入口：统一前置处理 → 执行 → 输出"""
        formatter = OutputFormatter(json_mode=_is_json_mode(args))
        cfg = CLIConfig.load(novel_id=getattr(args, "novel_id", None), json_output=formatter.json_mode)
        db_path = args.db or cfg.db_path
        args._novel_id = UUID(cfg.novel_id_required)
        args._db_path = db_path
        args._formatter = formatter
        args._cfg = cfg
        asyncio.run(cls._run(args))
```

效果：
- 每个子命令只需实现 `_add_arguments()` 和 `_run()`。
- `execute()` 中的前置逻辑（formatter/config/db_path/novel_id）统一处理，**消除 40 行样板代码**。
- 通过 `_is_json_mode()` 函数精确解析 `--json`（仅当它是独立参数时解析，支持 `--json` 和 `--no-json`）。

### 8-B Digest 统一来源

**选择：`inkmind/storage/digest.py` 作为 digest 运算的唯一来源**

- 将 `_compute_content_digest` 从 `unit_of_work.py` 和 `idempotency.py` 中提取到独立模块。
- 提供两个公开函数：
  ```python
  def compute_digest(data: bytes) -> str: ...
  def compute_str_digest(data: str) -> str: ...
  ```
- 所有模块通过 `from inkmind.storage.digest import compute_digest` 引用。

### 8-C 软删除机制

**选择：`is_deleted` 标记列代替物理删除**

```python
class BaseModel(Base):
    __abstract__ = True
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
```

- 所有查询自动加 `WHERE is_deleted = 0` 过滤器（通过 SQLAlchemy `@event.listens_for` 或 Repository 层默认过滤）。
- 提供 `NovelRepo.hard_delete()` 用于真正的清理（仅管理员使用）。
- Repository 层新增 `soft_delete()` 和 `restore()` 方法。

### 8-D 工程基础设施

| 项目 | 选择 | 原因 |
|------|------|------|
| `.editorconfig` | UTF-8, LF, 4 空格 indent | 跨编辑器一致性 |
| `.pre-commit-config.yaml` | ruff lint + ruff format + mypy | 提交前质量门禁 |
| `pyproject.toml` 行长度 | line-length = 127 | 与 Ruff 默认一致 |
| ORM `default` | `created_at=func.now(), updated_at=func.now()` | 自动时间戳 |
| `version` 命令 | 从 `importlib.metadata` 读取 | 与 pyproject.toml 版本保持同步 |

## 被否决的方案

- **Decorator 替代基类**：装饰器无法共享 `execute()` 中的完整前置逻辑，需要额外参数传递，不如基类直接。
- **完全物理删除**：用户误删后无法恢复，且不利于审计。软删除 + 定时清理更合适。
- **`__all__` 用 wildcard 导出**：失去 IDE 精确补全价值，显式列表更可控。

## 影响

- 所有 10 个 CLI 命令从「独立函数」迁移到「继承 `BaseCommand`」，需逐个调整。
- `__init__.py` 的命令注册方式从 `dict` 映射改为 `class.__subclasses__()` 自动发现。
- Repository 层新增 `BaseRepository` 基类，统一处理 `is_deleted` 过滤。
- `pre-commit` 配置需要团队安装（`pre-commit install`）。
