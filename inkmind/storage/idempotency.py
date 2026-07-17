"""per-packet digest 幂等去重。

IdempotencyGuard 提供两个核心能力：
1. is_duplicate — 检查 digest 是否已被处理
2. mark_processed — 标记 digest 为已处理（在事务边界内）

去重粒度：per-packet。每个 AgentPacket 的 (packet_id, digest)
联合唯一。同一内容重新发送（如网络重试）会被跳过。
"""

from __future__ import annotations

import hashlib
import json
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from inkmind.models.agent import AgentPacket
from inkmind.storage.models import ProcessedDigestModel


def compute_packet_digest(packet: AgentPacket) -> str:
    """计算 AgentPacket 的 SHA256 digest。

    基于 (novel_id, packet_type, payload.json(), iteration)
    生成唯一指纹。
    """
    payload_json = json.dumps(
        packet.payload.model_dump(mode="json"),
        sort_keys=True,
        default=str,
    )
    raw = f"{packet.novel_id}:{packet.packet_type.value}:{payload_json}:{packet.iteration}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class IdempotencyGuard:
    """幂等守卫。

    在事务中检查并标记 digest，确保同一 packet 不会被重复处理。
    """

    def __init__(self, session: AsyncSession):
        self._session = session

    async def is_duplicate(self, digest: str) -> bool:
        """检查 digest 是否已被处理过。"""
        result = await self._session.execute(
            select(ProcessedDigestModel).where(
                ProcessedDigestModel.digest == digest
            )
        )
        return result.scalar_one_or_none() is not None

    async def mark_processed(
        self, digest: str, packet_id: UUID
    ) -> None:
        """标记 digest 为已处理（事务提交时持久化）。

        使用 merge() 实现 INSERT OR REPLACE 语义，
        保证同一 digest 重复标记不报错。
        """
        entry = ProcessedDigestModel(
            digest=digest,
            packet_id=str(packet_id),
        )
        await self._session.merge(entry)

    async def is_already_processed(
        self, packet: AgentPacket
    ) -> tuple[bool, str]:
        """一站式：计算 digest 并检查是否已处理。

        Returns:
            (is_duplicate, digest)
        """
        digest = compute_packet_digest(packet)
        dup = await self.is_duplicate(digest)
        return dup, digest
