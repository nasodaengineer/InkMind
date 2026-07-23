"""素材领域模型。

素材是用户从外部导入的小说写作素材，经过 LLM 拆解后
变为结构化碎片，供写作时参考和使用。

三表结构：
  MaterialSource — 导入源（原始文本）
  MaterialChunk  — 按 8000 字段落吸附切块
  MaterialFragment — LLM 从块拆解出的结构化碎片

8 枚举类型前后端同契约。
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

# ═══════════════════════════════════════════════════════
#  常量：8 枚举类型（前后端同契约）
# ═══════════════════════════════════════════════════════

FRAGMENT_TYPES: list[str] = [
    "excerpt",
    "scene_idea",
    "character_seed",
    "setting_seed",
    "dialogue_sample",
    "style_sample",
    "technique",
    "misc",
]

# ═══════════════════════════════════════════════════════
#  状态枚举
# ═══════════════════════════════════════════════════════

MATERIAL_SOURCE_STATUSES: list[str] = [
    "pending",
    "processing",
    "done",
    "failed",
]

MATERIAL_CHUNK_STATUSES: list[str] = [
    "pending",
    "done",
    "failed",
    "low_quality",
]

# ═══════════════════════════════════════════════════════
#  MaterialSource — 导入源
# ═══════════════════════════════════════════════════════


class MaterialSource(BaseModel):
    """素材导入源。

    每次导入操作产生一条 source 记录。raw_text 为原始粘贴文本，
    content_digest 用于幂等校验。
    """

    id: UUID = Field(default_factory=uuid4)
    novel_id: UUID
    raw_text: str
    content_digest: str
    status: str = Field(default="pending")
    word_count: int = Field(default=0, ge=0)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    is_deleted: bool = Field(default=False)


# ═══════════════════════════════════════════════════════
#  MaterialChunk — 按段落吸附切块
# ═══════════════════════════════════════════════════════


class MaterialChunk(BaseModel):
    """素材拆解块。

    原文按段落分割、每块最多 8000 字（不截断段落）的切分结果。
    """

    id: UUID = Field(default_factory=uuid4)
    source_id: UUID
    chunk_index: int = Field(ge=0)
    content: str
    content_digest: str
    status: str = Field(default="pending")
    retry_count: int = Field(default=0, ge=0)
    error_message: str | None = None


# ═══════════════════════════════════════════════════════
#  MaterialFragment — 结构化碎片
# ═══════════════════════════════════════════════════════


class MaterialFragment(BaseModel):
    """LLM 拆解出的结构化碎片。

    每条碎片代表一个独立的素材单元，经人工编辑后 user_edited 标记为 True，
    后续重跑时自动跳过。
    """

    id: UUID = Field(default_factory=uuid4)
    source_id: UUID
    source_chunk_id: UUID
    title: str = Field(max_length=20)
    content: str = Field(max_length=2000)
    type: str  # 枚举约束见 FRAGMENT_TYPES
    tags: list[str] = Field(default_factory=list)
    source: str  # 来源描述（如 "用户导入：小说《XX》第三章"）
    source_quote: str | None = Field(default=None, max_length=50)
    reusability_note: str = Field(default="")
    user_note: str = Field(default="")
    user_edited: bool = Field(default=False)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
