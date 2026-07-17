"""输出格式化 — 默认可读文本，--json 切换。"""

from __future__ import annotations

import json
from typing import Any, Callable


class OutputFormatter:
    """格式化 CLI 输出。

    两种模式：
    - 文本模式（默认）: 人类可读的输出
    - JSON 模式（--json）: 机器可读的 JSON 输出
    """

    def __init__(self, json_mode: bool = False):
        self.json_mode = json_mode

    def success(self, message: str, data: dict | None = None) -> None:
        """成功消息。"""
        payload = {"status": "ok", "message": message}
        if data:
            payload.update(data)
        if self.json_mode:
            print(json.dumps(payload, indent=2, default=str, ensure_ascii=False))
        else:
            print(f"✅ {message}")

    def error(self, message: str) -> None:
        """错误消息。"""
        if self.json_mode:
            print(
                json.dumps(
                    {"status": "error", "message": message},
                    indent=2,
                    ensure_ascii=False,
                )
            )
        else:
            print(f"❌ {message}")

    def info(self, message: str) -> None:
        """信息消息（JSON 模式不输出）。"""
        if not self.json_mode:
            print(f"ℹ️  {message}")

    def print_dict(self, data: dict, text_fn: Callable[[dict], str] | None = None) -> None:
        """打印一个结构化 dict——JSON 模式直接输出，文本模式调用 text_fn。"""
        if self.json_mode:
            print(json.dumps(data, indent=2, default=str, ensure_ascii=False))
        elif text_fn:
            print(text_fn(data))
        else:
            print(json.dumps(data, indent=2, default=str, ensure_ascii=False))

    def print_table(self, headers: list[str], rows: list[list[str]]) -> None:
        """打印表格（文本模式）。"""
        if self.json_mode:
            print(
                json.dumps(
                    {"headers": headers, "rows": rows},
                    indent=2,
                    ensure_ascii=False,
                )
            )
            return

        col_widths = [len(h) for h in headers]
        for row in rows:
            for i, cell in enumerate(row):
                col_widths[i] = max(col_widths[i], len(cell))

        def fmt_row(cells: list[str]) -> str:
            parts = []
            for i, c in enumerate(cells):
                parts.append(f" {c:<{col_widths[i]}} ")
            return "│" + "│".join(parts) + "│"

        sep = "├" + "┼".join("─" * (w + 2) for w in col_widths) + "┤"
        top = "╭" + "┬".join("─" * (w + 2) for w in col_widths) + "╮"
        bot = "╰" + "┴".join("─" * (w + 2) for w in col_widths) + "╯"

        print(top)
        print(fmt_row(headers))
        print(sep)
        for row in rows:
            print(fmt_row(row))
        print(bot)
