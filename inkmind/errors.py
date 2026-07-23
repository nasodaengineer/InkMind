"""InkMind 业务错误码。"""

from __future__ import annotations


class StaleVersionError(Exception):
    """base_digest 乐观锁冲突（HTTP 409 语义）。

    当客户端持有的 base_digest 与服务端当前 content_digest 不一致时抛出，
    表示内容已被其他操作修改，本次保存被拒绝。
    """

    def __init__(self, expected: str, actual: str):
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"base_digest 冲突: 客户端持有 {expected[:12]}…, "
            f"服务端当前为 {actual[:12]}…"
        )
