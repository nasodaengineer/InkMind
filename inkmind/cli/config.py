"""配置加载器 — TOML + 环境变量 + 默认值三级 fallback。"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class CLIConfig:
    """CLI 运行时配置。"""

    db_path: str = field(default=".inkmind/data.db")
    novel_id: str | None = field(default=None)
    json_output: bool = field(default=False)

    @classmethod
    def load(
        cls,
        config_path: str | None = None,
        novel_id: str | None = None,
        json_output: bool = False,
    ) -> CLIConfig:
        """三级加载：TOML → 环境变量 → 默认值。"""
        cfg = cls()
        cfg.json_output = json_output

        # 1. TOML 文件（最低优先级）
        toml_path = config_path or "inkmind.toml"
        if Path(toml_path).exists():
            with open(toml_path, "rb") as f:
                data = tomllib.load(f)
            proj = data.get("project", {})
            if "novel_id" in proj:
                cfg.novel_id = str(proj["novel_id"])
            store = data.get("storage", {})
            if "path" in store:
                cfg.db_path = str(store["path"])

        # 2. 环境变量
        cfg.db_path = os.getenv("INKMIND_DB_PATH", cfg.db_path)
        env_novel = os.getenv("INKMIND_NOVEL_ID")
        if env_novel:
            cfg.novel_id = env_novel

        # 3. 命令行参数（最高优先级）
        if novel_id:
            cfg.novel_id = novel_id

        return cfg

    @property
    def novel_id_required(self) -> str:
        """获取 novel_id，若未设置则抛错。"""
        if self.novel_id is None:
            raise ValueError(
                "未指定 novel_id。请通过 --novel-id 参数、inkmind.toml 的 project.novel_id "
                "或 INKMIND_NOVEL_ID 环境变量设置。"
            )
        return self.novel_id
