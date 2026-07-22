"""serve 子命令 — 启动可视化工作台 Web 服务器。"""

from __future__ import annotations

import argparse
import os
import sys
import webbrowser
from pathlib import Path

from inkmind.cli.config import CLIConfig


def setup(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("serve", help="启动可视化工作台 Web 服务器")
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="监听地址（默认: 127.0.0.1）",
    )
    parser.add_argument(
        "-p", "--port",
        type=int,
        default=8000,
        help="监听端口（默认: 8000）",
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        help="开发模式：文件变更自动重启",
    )
    parser.add_argument(
        "--open",
        action="store_true",
        help="启动后自动打开浏览器",
    )
    parser.add_argument(
        "--db",
        default=None,
        help="SQLite 数据库路径（默认: .inkmind/data.db）",
    )


def run(args: argparse.Namespace) -> None:
    """启动 uvicorn 服务器。"""
    # ── 生产模式检查 dist ──
    dist = Path(__file__).resolve().parent.parent.parent.parent / "web" / "dist"
    if not args.reload and not dist.is_dir():
        print(
            "错误：未找到前端构建产物 web/dist/。\n"
            "请先执行以下命令构建前端：\n\n"
            "  cd web && pnpm install && pnpm build\n\n"
            "或在开发模式使用 --reload 并另行启动 pnpm dev。",
            file=sys.stderr,
        )
        sys.exit(1)

    # ── 数据库路径 ──
    if args.db:
        db_path = args.db
    else:
        cfg = CLIConfig.load()
        db_path = cfg.db_path

    # ── 确保 db 目录存在 ──
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    # ── 设置环境变量供 create_app 读取 ──
    os.environ["INKMIND_DB_PATH"] = db_path

    # ── 启动 uvicorn ──
    if args.open:
        webbrowser.open(f"http://{args.host}:{args.port}")

    import uvicorn

    uvicorn.run(
        "inkmind.api.app:create_app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        factory=True,
    )
