"""摘要工具函数。"""

from __future__ import annotations

import hashlib


def compute_content_digest(content: str) -> str:
    """计算内容的 SHA256 摘要。"""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()