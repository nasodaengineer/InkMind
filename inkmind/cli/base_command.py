"""CLI 命令基类 — 消除 10+ 命令中重复的 4 行样板代码。"""

from __future__ import annotations

from uuid import UUID

from inkmind.cli.config import CLIConfig
from inkmind.cli.formatter import OutputFormatter


class BaseCommand:
    """所有 CLI 命令的基类。
    
    子类只需实现 `_run()` 方法。
    基类自动处理 OutputFormatter 初始化、CLIConfig 加载、
    db_path 解析、novel_id 提取。
    """

    @classmethod
    def execute(cls, args) -> None:
        """入口点：初始化上下文并调用子类的 _run。"""
        formatter = OutputFormatter(json_mode=getattr(args, "json", False))
        cfg = CLIConfig.load(json_output=formatter.json_mode)
        db_path = getattr(args, "db", None) or cfg.db_path
        
        novel_id: UUID | None = None
        raw_nid = getattr(args, "novel_id", None) or cfg.novel_id
        if raw_nid:
            if isinstance(raw_nid, UUID):
                novel_id = raw_nid
            else:
                novel_id = UUID(raw_nid)
        
        result = cls._run(args, formatter, cfg, db_path, novel_id)
        if hasattr(result, "__await__"):
            import asyncio
            return asyncio.run(result)
        return result
    
    @classmethod
    def _run(cls, args, formatter, cfg, db_path, novel_id):
        """子类实现具体逻辑。"""
        raise NotImplementedError
