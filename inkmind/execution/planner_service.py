"""PlannerService — AI 大纲规划服务。

提供四种 AI 规划操作：
1. draft_spine() — 六字段总纲 LLM 生成（零输入可自主命题）
2. draft_volume() — 单卷四字段 LLM 填补（保留已有字段）
3. split_volumes() — 全书拆 2-20 卷，批量生成卷纲
4. plan_chapters() — 卷内 5-50 章批量排章

使用已有的 LLMClient 和 ModelRouter，Planner 默认模型 deepseek-v4-pro。
"""

from __future__ import annotations

import json
import re
from typing import Any
from uuid import UUID

from inkmind.llm.client import LLMClient
from inkmind.models.novel import OutlineSpine, Volume


# ── Helper: 从 LLM 响应中提取 JSON ──


def _extract_json(text: str) -> str:
    """从 LLM 响应中提取 JSON 块（`````json ... ````` 或裸 ``{``..``}``）。"""
    # 优先匹配 ```json ... ```
    match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if match:
        return match.group(1).strip()
    # 退而求其次：找第一个 { 到最后一个 }
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        return text[start : end + 1]
    return text.strip()


# ── LLM 系统提示 ──


_SPINE_SYSTEM_PROMPT = """你是一位资深小说架构师。请根据用户描述生成完整的「书脊总纲」。

总纲包含以下六个字段，每个字段 30-200 字：
1. main_line — 主线：故事的核心推进线索
2. core_conflict — 核心矛盾：贯穿始终的冲突
3. ending — 结局：最终收束
4. selling_points — 卖点：本书最吸引读者的特色
5. world_background — 世界观背景：时代/世界设定
6. golden_finger — 金手指：主角的核心优势/外挂

请以 JSON 格式返回，字段名使用英文小写蛇形。如果用户未提供具体主题，请自主构思一个有市场潜力的网文选题。"""

_VOLUME_SYSTEM_PROMPT = """你是一位资深小说架构师。请根据已有总纲和卷上下文，填补指定卷的详细设定。

每卷包含以下四个字段，每个字段 50-300 字：
1. stage_goal — 阶段目标：本卷要达成的叙事目标
2. main_line — 主线：本卷的主线推进内容
3. side_line — 支线：本卷的支线/暗线
4. volume_cliffhanger — 卷末悬念：本卷结尾的钩子

请以 JSON 格式返回。保留已经存在的字段值不变，只填补空字段。如果用户提供了提示文本，请据此调整创作方向。"""

_SPLIT_VOLUMES_SYSTEM_PROMPT = """你是一位资深小说架构师。请根据已有的总纲，将全书拆分为多个卷。

每卷需包含以下字段：
1. title — 卷标题（简洁有力）
2. stage_goal — 阶段目标（30-150 字）
3. main_line — 本卷的主线（30-150 字）
4. side_line — 本卷的支线（可选）
5. volume_cliffhanger — 卷末悬念（30-150 字）
6. planned_size — 预计章数（10-30 章）

请以 JSON 数组格式返回，字段名使用英文小写蛇形。确保每卷有独立的叙事弧光，卷与卷之间有连续的悬念衔接。"""

_CHAPTER_SYSTEM_PROMPT = """你是一位资深小说大纲规划师。请根据已有总纲和卷纲，为指定卷批量规划章节大纲。

每章需包含以下字段：
1. title — 章节标题（5-20 字，有吸引力）
2. summary — 本章摘要（30-200 字）
3. key_events — 关键事件列表（2-5 条）
4. rhythm_marker — 节奏标记（"climax" 小高潮 / "big_climax" 大高潮 / null 普通章节）
5. pov — 视角角色
6. involved — 出场角色列表

请以 JSON 数组格式返回。注意节奏分布：每 5-8 章设置一个小高潮，全书四分之三处设置大高潮。
确保章与章之间有连贯的情节推进和悬念衔接。"""


class PlannerService:
    """AI 大纲规划服务。

    所有方法通过 LLMClient 调用 Planner 角色模型（deepseek-v4-pro）。
    """

    def __init__(self, llm_client: LLMClient) -> None:
        self._llm = llm_client

    # ═══════════════════════════════════════════════════════════
    #  1. draft_spine — 六字段总纲 LLM 生成
    # ═══════════════════════════════════════════════════════════

    async def draft_spine(
        self,
        novel_id: UUID,
        prompt: str | None = None,
    ) -> OutlineSpine:
        """LLM 生成六字段总纲。

        Args:
            novel_id: 小说 ID
            prompt: 可选的用户提示文本（空则自主命题）

        Returns:
            包含 LLM 生成结果的 OutlineSpine
        """
        user_message = prompt or "请为我构思一部有市场潜力的网文，生成完整的书脊总纲。"

        response = await self._llm.chat(
            agent_role="planner",
            prompt=user_message,
            system_prompt=_SPINE_SYSTEM_PROMPT,
        )

        raw = _extract_json(response.content)
        data = json.loads(raw)

        # 只取总纲的六个字段，忽略其他
        return OutlineSpine(
            novel_id=novel_id,
            main_line=data.get("main_line", ""),
            core_conflict=data.get("core_conflict", ""),
            ending=data.get("ending", ""),
            selling_points=data.get("selling_points", ""),
            world_background=data.get("world_background", ""),
            golden_finger=data.get("golden_finger", ""),
        )

    # ═══════════════════════════════════════════════════════════
    #  2. draft_volume — 单卷四字段 LLM 填补
    # ═══════════════════════════════════════════════════════════

    async def draft_volume(
        self,
        spine: OutlineSpine,
        volume: Volume,
        prompt: str | None = None,
    ) -> Volume:
        """LLM 填补单卷四字段（保留已有字段）。

        Args:
            spine: 当前总纲（作为上下文）
            volume: 当前卷（已有字段保留，空字段由 LLM 填补）
            prompt: 可选的用户提示文本

        Returns:
            填补后的 Volume（仅修改空字段）
        """
        # 构建已有字段描述
        existing_fields = []
        if volume.stage_goal:
            existing_fields.append(f'stage_goal: "{volume.stage_goal}"')
        if volume.main_line:
            existing_fields.append(f'main_line: "{volume.main_line}"')
        if volume.side_line:
            existing_fields.append(f'side_line: "{volume.side_line}"')
        if volume.volume_cliffhanger:
            existing_fields.append(f'volume_cliffhanger: "{volume.volume_cliffhanger}"')

        context = (
            f"总纲信息：\n"
            f"  主线：{spine.main_line}\n"
            f"  核心矛盾：{spine.core_conflict}\n"
            f"  结局：{spine.ending}\n"
            f"  世界观背景：{spine.world_background}\n\n"
            f"当前卷：第 {volume.volume_index} 卷「{volume.title}」\n"
            f"预计章数：{volume.planned_size}\n"
        )

        if existing_fields:
            context += "已有字段：\n  " + "\n  ".join(existing_fields) + "\n\n"
            context += "请保留以上已有字段值不变，仅填补以下空字段：\n"
            if not volume.stage_goal:
                context += "  - stage_goal（阶段目标）\n"
            if not volume.main_line:
                context += "  - main_line（主线）\n"
            if not volume.side_line:
                context += "  - side_line（支线）\n"
            if not volume.volume_cliffhanger:
                context += "  - volume_cliffhanger（卷末悬念）\n"
        else:
            context += "请填补该卷的所有四个字段。\n"

        if prompt:
            context += f"\n用户提示：{prompt}\n"

        user_message = f"{context}\n请以 JSON 格式返回完整的四字段（含已有的）。"

        response = await self._llm.chat(
            agent_role="planner",
            prompt=user_message,
            system_prompt=_VOLUME_SYSTEM_PROMPT,
        )

        raw = _extract_json(response.content)
        data = json.loads(raw)

        # 合并：保留已有字段，只填补空字段
        return Volume(
            id=volume.id,
            novel_id=volume.novel_id,
            volume_index=volume.volume_index,
            title=volume.title,
            stage_goal=volume.stage_goal or data.get("stage_goal", ""),
            main_line=volume.main_line or data.get("main_line", ""),
            side_line=volume.side_line or data.get("side_line", ""),
            volume_cliffhanger=volume.volume_cliffhanger or data.get("volume_cliffhanger", ""),
            planned_size=data.get("planned_size", volume.planned_size),
        )

    # ═══════════════════════════════════════════════════════════
    #  3. split_volumes — 全书拆 2-20 卷
    # ═══════════════════════════════════════════════════════════

    async def split_volumes(
        self,
        spine: OutlineSpine,
        volume_count: int,
        prompt: str | None = None,
    ) -> list[dict[str, Any]]:
        """LLM 拆卷 — 批量生成卷纲。

        Args:
            spine: 当前总纲（作为上下文）
            volume_count: 拆卷数量（2-20）
            prompt: 可选的用户提示文本

        Returns:
            list[dict] — 每卷的字段 dict（title/stage_goal/main_line/side_line/volume_cliffhanger/planned_size）
        """
        context = (
            f"总纲信息：\n"
            f"  主线：{spine.main_line}\n"
            f"  核心矛盾：{spine.core_conflict}\n"
            f"  结局：{spine.ending}\n"
            f"  卖点：{spine.selling_points}\n"
            f"  世界观背景：{spine.world_background}\n"
            f"  金手指：{spine.golden_finger}\n"
        )

        if prompt:
            context += f"\n用户提示：{prompt}\n"

        user_message = (
            f"{context}\n"
            f"请将这部小说拆分为 {volume_count} 卷。"
            f"以 JSON 数组格式返回，每个元素包含 title/stage_goal/main_line/side_line/volume_cliffhanger/planned_size 字段。"
        )

        response = await self._llm.chat(
            agent_role="planner",
            prompt=user_message,
            system_prompt=_SPLIT_VOLUMES_SYSTEM_PROMPT,
        )

        raw = _extract_json(response.content)
        data = json.loads(raw)

        # 如果返回的是 dict 包含 volumes 键，取该键；否则假设是数组
        if isinstance(data, dict):
            data = data.get("volumes", data.get("data", []))

        if not isinstance(data, list):
            # 尝试将 dict 值中的 volumes 提取出来
            for val in data.values():
                if isinstance(val, list):
                    data = val
                    break

        return data

    # ═══════════════════════════════════════════════════════════
    #  4. plan_chapters — 卷内 5-50 章批量排章
    # ═══════════════════════════════════════════════════════════

    async def plan_chapters(
        self,
        spine: OutlineSpine,
        volume: Volume,
        chapter_count: int,
        start_index: int = 1,
        prompt: str | None = None,
        existing_chapters: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """LLM 批量排章 — 生成卷内章节大纲。

        Args:
            spine: 当前总纲（作为上下文）
            volume: 当前卷（作为上下文）
            chapter_count: 待规划章数（5-50）
            start_index: 起始章节序号
            prompt: 可选的用户提示文本
            existing_chapters: 卷内已有章节列表（用于上下文连贯）

        Returns:
            list[dict] — 每章的字段 dict（title/summary/key_events/rhythm_marker/pov/involved）
        """
        context = (
            f"总纲信息：\n"
            f"  主线：{spine.main_line}\n"
            f"  核心矛盾：{spine.core_conflict}\n"
            f"  世界观背景：{spine.world_background}\n\n"
            f"当前卷：第 {volume.volume_index} 卷「{volume.title}」\n"
            f"  阶段目标：{volume.stage_goal}\n"
            f"  主线：{volume.main_line}\n"
            f"  支线：{volume.side_line}\n"
            f"  卷末悬念：{volume.volume_cliffhanger}\n"
            f"  章节起始序号：{start_index}\n"
        )

        if existing_chapters:
            ctx_lines = []
            for ch in existing_chapters:
                ctx_lines.append(f"  #{ch.get('chapter_index', '?')} {ch.get('title', '?')}")
            context += "已有章节：\n" + "\n".join(ctx_lines) + "\n"

        if prompt:
            context += f"\n用户提示：{prompt}\n"

        user_message = (
            f"{context}\n"
            f"请为当前卷规划 {chapter_count} 章大纲（从章节 {start_index} 开始）。"
            f"以 JSON 数组格式返回，每个元素包含 title/summary/key_events/rhythm_marker/pov/involved 字段。"
            f"注意：key_events 为字符串数组，rhythm_marker 为 'climax'/'big_climax'/null。"
        )

        response = await self._llm.chat(
            agent_role="planner",
            prompt=user_message,
            system_prompt=_CHAPTER_SYSTEM_PROMPT,
        )

        raw = _extract_json(response.content)
        data = json.loads(raw)

        # 如果返回的是 dict 包含 chapters 键，取该键；否则假设是数组
        if isinstance(data, dict):
            data = data.get("chapters", data.get("data", data.get("outlines", [])))

        if not isinstance(data, list):
            for val in data.values():
                if isinstance(val, list):
                    data = val
                    break

        # 分配章节序号
        for i, ch in enumerate(data):
            ch["chapter_index"] = start_index + i

        return data
