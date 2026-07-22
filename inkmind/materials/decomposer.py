"""素材拆解引擎 — MaterialDecomposer。

接收 MaterialChunk → 调 LLM（flash 非流式）
四件套：低温 0.3 + 严格 schema/enum 白名单 + ≤3 重试 + 降级兜底
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from inkmind.llm.client import LLMClient
from inkmind.models.materials import (
    FRAGMENT_TYPES,
    MaterialChunk,
    MaterialFragment,
    MaterialSource,
)

logger = logging.getLogger(__name__)

# LLM 提示模板
DECOMPOSE_SYSTEM_PROMPT = """你是一个专业的文学素材拆解助手。你的任务是将一段小说素材文本拆解为结构化的素材碎片。

## 输出格式

你必须返回一个 JSON 对象，格式如下：
```json
{
  "schema_version": "1.0",
  "fragments": [
    {
      "title": "碎片标题（不超过20字）",
      "content": "碎片内容（不超过2000字）",
      "type": "excerpt",
      "tags": ["标签1", "标签2", "标签3"],
      "source_quote": "原文引用子串（8-50字，excerpt类型可空）",
      "reusability_note": "复用说明"
    }
  ]
}
```

## 碎片类型（type 字段，必须从以下枚举中选择）

- **excerpt**: 精彩原文摘录。可直接引用的原文片段。
- **scene_idea**: 场景构思。可用于写作的场景创意。
- **character_seed**: 角色种子。有潜力发展为完整角色的形象/特质。
- **setting_seed**: 设定种子。世界观/背景设定的灵感。
- **dialogue_sample**: 对话样本。逐字对话记录，以对话为主体。
- **style_sample**: 风格样本。语言风格/叙事技巧的范例。
- **technique**: 技法。可学习的写作技巧/结构手法。
- **misc**: 其他。无法归入以上类别的素材。

## 规则 Y：内容专类判断

- 先判断原文内容属于哪个专类。如果原文以对话（逐字对话）为主体，归属 dialogue_sample。
- dialogue_sample 必须设置 source_quote 来标示对话出处，excerpt 豁免 source_quote 要求。
- 普通素材中的个别对话不归为 dialogue_sample。

## 字段校验规则

- title: 不超过 20 字，必须精心提炼
- content: 不超过 2000 字
- type: 严格从上面 8 种枚举中选择，不确定时用 misc
- tags: 建议 3-6 个标签，从文本中提取关键词
- source_quote: 8-50 字的原文子串（excerpt 类型可以省略或为空）
- reusability_note: 必须填写，说明这个碎片在写作中如何复用
- 每条碎片只聚焦一个主题，不要混入多种类型

请分析以下素材文本并返回拆解结果。"""


class MaterialDecomposer:
    """素材拆解引擎。

    负责将 MaterialChunk 通过 LLM 拆解为 MaterialFragment 列表。
    支持重试、进度通知、降级兜底。
    """

    def __init__(
        self,
        llm_client: LLMClient,
        max_retries: int = 3,
        temperature: float = 0.3,
    ):
        self._llm = llm_client
        self._max_retries = max_retries
        self._temperature = temperature
        # 进度事件通道（asyncio.Event：不通过 Run，独立通知 UI）
        self._progress_events: asyncio.Event = asyncio.Event()
        self._progress_data: dict[str, Any] = {}

    async def decompose_chunk(
        self,
        chunk: MaterialChunk,
        source: MaterialSource | None = None,
    ) -> list[MaterialFragment]:
        """拆解单个 MaterialChunk。

        内部重试逻辑：
        1. 调用 LLM → 解析 JSON
        2. 校验字段（title≤20, content≤2000, type 白名单, source_quote 8-50）
        3. 失败则清洗回喂重试，最多 3 次
        4. 全部失败 → 使用原文 excerpt 逃生门
        5. 低质（type 降级到 misc）→ 标记 low_quality 但不自动重试
        """
        source_text = chunk.content
        last_error: Exception | None = None

        for attempt in range(self._max_retries + 1):
            try:
                prompt = f"请拆解以下素材文本：\n\n{source_text}"
                if attempt > 0:
                    # 回喂重试：加入上次失败信息
                    prompt = (
                        f"上次拆解因校验失败，请修正后重新输出。\n"
                        f"错误信息：{last_error}\n\n"
                        f"原文：\n\n{source_text}"
                    )

                response = await self._llm.chat(
                    agent_role="editor",  # 用 flash 模型
                    prompt=prompt,
                    system_prompt=DECOMPOSE_SYSTEM_PROMPT,
                    temperature=self._temperature,
                    response_format={"type": "json_object"},
                )

                fragments_data = self._parse_response(response.content)
                fragments = self._validate_and_build(
                    fragments_data, chunk, source
                )

                # 成功
                self._emit_progress(
                    chunk_id=str(chunk.id),
                    chunk_index=chunk.chunk_index,
                    status="done",
                    fragment_count=len(fragments),
                )
                return fragments

            except (json.JSONDecodeError, ValueError) as e:
                last_error = e
                logger.warning(
                    "Chunk %s attempt %d/%d failed: %s",
                    chunk.id,
                    attempt + 1,
                    self._max_retries,
                    e,
                )
                continue
            except Exception as e:
                # LLM 调用异常，直接尝试降级
                logger.error(
                    "Chunk %s LLM call failed: %s", chunk.id, e
                )
                last_error = e
                continue

        # 全部重试失败 → excerpt 逃生门
        logger.warning(
            "Chunk %s all retries exhausted, using excerpt fallback",
            chunk.id,
        )
        fallback = self._build_excerpt_fallback(chunk, source)
        self._emit_progress(
            chunk_id=str(chunk.id),
            chunk_index=chunk.chunk_index,
            status="failed",
            fragment_count=1,
            error="所有重试失败，使用原文摘录逃生门",
        )
        return [fallback]

    async def decompose_source(
        self,
        source: MaterialSource,
        chunks: list[MaterialChunk],
    ) -> dict[str, list[MaterialFragment]]:
        """拆解一个来源的所有 pending chunks。"""
        result: dict[str, list[MaterialFragment]] = {}
        for chunk in chunks:
            fragments = await self.decompose_chunk(chunk, source)
            result[str(chunk.id)] = fragments
        return result

    # ── 内部方法 ──────────────────────────────────────────

    def _parse_response(self, content: str) -> list[dict]:
        """解析 LLM 返回的 JSON。"""
        # 尝试直接解析
        data = json.loads(content)
        if isinstance(data, dict):
            fragments = data.get("fragments", data.get("data", []))
            if isinstance(fragments, dict):
                fragments = list(fragments.values())
        elif isinstance(data, list):
            fragments = data
        else:
            raise ValueError(f"Unexpected JSON structure: {type(data)}")

        if not isinstance(fragments, list):
            raise ValueError(f"fragments is not a list: {type(fragments)}")

        return fragments

    def _validate_and_build(
        self,
        fragments_data: list[dict],
        chunk: MaterialChunk,
        source: MaterialSource | None,
    ) -> list[MaterialFragment]:
        """校验并构建 MaterialFragment 列表。"""
        fragments: list[MaterialFragment] = []
        source_desc = (
            f"用户导入素材"
            if source is None
            else f"用户导入素材（{source.word_count}字）"
        )

        for i, item in enumerate(fragments_data):
            if not isinstance(item, dict):
                logger.warning("Skipping non-dict fragment at index %d", i)
                continue

            title = str(item.get("title", ""))[:20]
            content = str(item.get("content", ""))[:2000]
            raw_type = str(item.get("type", "misc")).lower()

            # type 白名单校验
            if raw_type not in FRAGMENT_TYPES:
                logger.warning(
                    "Unknown type '%s', downgrading to misc", raw_type
                )
                raw_type = "misc"

            # tags
            raw_tags = item.get("tags", [])
            tags = (
                [str(t)[:30] for t in raw_tags if isinstance(t, str)][:10]
                if isinstance(raw_tags, list)
                else []
            )

            # source_quote: 8-50 字校验 (excerpt 豁免)
            raw_quote = item.get("source_quote")
            source_quote: str | None = None
            if raw_quote and raw_type != "excerpt":
                quote_str = str(raw_quote).strip()
                if 8 <= len(quote_str) <= 50:
                    source_quote = quote_str
                else:
                    logger.warning(
                        "source_quote length %d out of range [8,50], dropping",
                        len(quote_str),
                    )
            elif raw_quote and raw_type == "excerpt":
                source_quote = str(raw_quote)[:50] or None

            reusability_note = str(item.get("reusability_note", ""))[:500]

            fragment = MaterialFragment(
                source_id=chunk.source_id,
                source_chunk_id=chunk.id,
                title=title or f"素材碎片 #{i + 1}",
                content=content or "（内容为空）",
                type=raw_type,
                tags=tags,
                source=source_desc,
                source_quote=source_quote,
                reusability_note=reusability_note,
            )
            fragments.append(fragment)

        if not fragments:
            raise ValueError("No valid fragments after validation")

        return fragments

    def _build_excerpt_fallback(
        self,
        chunk: MaterialChunk,
        source: MaterialSource | None,
    ) -> MaterialFragment:
        """构建 excerpt 逃生门片段。"""
        content = chunk.content[:2000]
        return MaterialFragment(
            source_id=chunk.source_id,
            source_chunk_id=chunk.id,
            title="原文摘录",
            content=content,
            type="excerpt",
            tags=["原文"],
            source=(
                f"用户导入素材（{source.word_count}字）"
                if source
                else "用户导入素材"
            ),
            reusability_note="原始文本摘录，可从中提取创作灵感",
            source_quote=None,
        )

    # ── 进度事件 ─────────────────────────────────────────

    def _emit_progress(self, **data: Any) -> None:
        """发出进度事件。"""
        self._progress_data = data
        self._progress_events.set()

    def get_progress(self) -> dict[str, Any] | None:
        """获取最新进度数据。"""
        if not self._progress_data:
            return None
        return dict(self._progress_data)

    async def wait_progress(self) -> dict[str, Any]:
        """等待下一次进度更新（协程）。"""
        await self._progress_events.wait()
        self._progress_events.clear()
        return dict(self._progress_data)

    def reset_progress(self) -> None:
        """重置进度状态。"""
        self._progress_data = {}
        self._progress_events.clear()
