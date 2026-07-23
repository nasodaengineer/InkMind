"""Agent 角色的 Prompt 构造。

每个 Agent 的系统提示词与任务提示词集中在此模块，
便于统一调优生成质量（工单 06 后续迭代的主要调整点）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from inkmind.models.agent import AnnotationRef

if TYPE_CHECKING:
    from inkmind.agents.collaboration import ChapterContext


# ──────────────────────────────────────────────
#  系统提示词
# ──────────────────────────────────────────────

WRITER_SYSTEM_PROMPT = (
    "你是一位专业的中文小说作家（Writer Agent）。根据给定的章节标题、"
    "小说背景、前文摘要与本章大纲，创作连贯、生动的章节正文。"
    "直接输出正文内容，不要输出章节标题、解释或任何元信息。"
)

PLANNER_SYSTEM_PROMPT = (
    "你是一位专业的中文小说策划（Planner Agent）。根据小说背景与前文摘要，"
    "批量规划后续章节大纲，保持宏观情节连贯、伏笔前后呼应。"
    '只输出 JSON：{"chapters": [{"index": 章节序号, "title": "章节标题", '
    '"outline": "100字以内的本章大纲"}]}。'
    "不要输出 JSON 以外的内容。"
)

EDITOR_SYSTEM_PROMPT = (
    "你是一位严格但务实的中文小说编辑（Editor Agent）。评审章节草稿后，"
    '只输出 JSON：{"verdict": "approve" 或 "needs_revision", "issues": ["问题1", ...]}。'
    "质量达标时 approve（issues 为空）；存在必须修改的问题时 needs_revision，"
    "并在 issues 中列出具体、可执行的修改意见。不要输出 JSON 以外的内容。"
)

MEMORY_KEEPER_SYSTEM_PROMPT = (
    "你是小说记忆管理 Agent（MemoryKeeper）。阅读定稿章节，产出结构化记忆，"
    '只输出 JSON：{"summary": "100字以内的章节摘要", "key_events": ["关键事件1", "关键事件2"]}。'
    "summary 用于后续章节写作的上下文注入，key_events 列出推动剧情的关键事件（1-5个）。"
    "不要输出 JSON 以外的内容。"
)


# ──────────────────────────────────────────────
#  任务提示词
# ──────────────────────────────────────────────


def build_writer_prompt(ctx: ChapterContext) -> str:
    """Writer 初稿 prompt：小说背景 + 前文摘要 + 本章大纲 + 本章任务。"""
    parts = [
        f"小说标题：《{ctx.novel_title}》",
    ]
    if ctx.novel_description:
        parts.append(f"小说简介：{ctx.novel_description}")

    if ctx.previous_summaries:
        parts.append("\n前文摘要：")
        for s in ctx.previous_summaries:
            parts.append(f"- {s}")
    else:
        parts.append("\n（这是小说的第一章，无前文。）")

    if ctx.outline:
        parts.append(f"\n本章大纲：{ctx.outline}")

    parts.append(
        f"\n请创作第 {ctx.chapter_index} 章「{ctx.chapter_title}」的正文，"
        "800-1500 字，与上文情节连贯。"
    )
    return "\n".join(parts)


def build_planner_prompt(
    novel_title: str,
    novel_description: str,
    previous_summaries: list[str],
    start_index: int,
    count: int,
) -> str:
    """Planner 批量规划 prompt：小说背景 + 前文摘要 + 规划区间。"""
    parts = [
        f"小说标题：《{novel_title}》",
    ]
    if novel_description:
        parts.append(f"小说简介：{novel_description}")

    if previous_summaries:
        parts.append("\n前文摘要：")
        for s in previous_summaries:
            parts.append(f"- {s}")
    else:
        parts.append("\n（小说尚未开始，无已定稿章节。）")

    parts.append(
        f"\n起始章节序号：{start_index}\n"
        f"请规划第 {start_index} 章到第 {start_index + count - 1} 章"
        f"（共 {count} 章）的大纲，chapters 数组必须包含 {count} 个元素，"
        "index 连续递增。"
    )
    return "\n".join(parts)


def build_revision_prompt(
    ctx: ChapterContext,
    previous_content: str,
    issues: list[str],
    iteration: int,
) -> str:
    """Writer 修订 prompt：上一版草稿 + Editor 的具体问题。"""
    issue_lines = "\n".join(f"{i}. {issue}" for i, issue in enumerate(issues, 1))
    return (
        f"小说《{ctx.novel_title}》第 {ctx.chapter_index} 章「{ctx.chapter_title}」"
        f"的第 {iteration} 次修订。\n\n"
        f"上一版草稿：\n{previous_content}\n\n"
        f"编辑提出的修改意见：\n{issue_lines}\n\n"
        "请针对上述问题重写本章正文（800-1500 字），保留可取之处，"
        "直接输出修订后的正文。"
    )


def build_editor_prompt(ctx: ChapterContext, content: str, iteration: int) -> str:
    """Editor 评审 prompt：章节草稿全文。"""
    return (
        f"请评审小说《{ctx.novel_title}》第 {ctx.chapter_index} 章"
        f"「{ctx.chapter_title}」的草稿（第 {iteration} 轮评审）。\n\n"
        f"草稿全文：\n{content}\n\n"
        "评审维度：情节连贯性、人物一致性、文字质量、与前文的衔接。"
        "除非存在必须修改的硬伤，否则倾向于 approve。"
    )


def build_memory_prompt(chapter_index: int, chapter_title: str, content: str) -> str:
    """MemoryKeeper 摘要 prompt：定稿章节全文。"""
    return f"请为第 {chapter_index} 章「{chapter_title}」生成结构化记忆。\n\n章节全文：\n{content}"


# ──────────────────────────────────────────────
#  五区批注序列化（Issue #41）
# ──────────────────────────────────────────────

_SERIALIZABLE_STATUSES = {"open", "relocated_fuzzy", "orphaned"}


def serialize_annotations(annotations: list[AnnotationRef], content: str = "") -> str:
    """将批注列表序列化为五区结构文本（服务端权威渲染）。

    五区顺序：章节总评（无锚）→ 修订指令 → 保留要求 → 读者疑问 → 已失效批注。
    序列化范围：status ∈ {open, relocated_fuzzy, orphaned} 且 intent ≠ note。
    锚定条目按文中位置排序；orphaned 注明原文已被改写。
    """
    eligible = [a for a in annotations if a.status in _SERIALIZABLE_STATUSES and a.intent != "note"]
    if not eligible:
        return ""

    chapter_general: list[AnnotationRef] = []
    instructions: list[AnnotationRef] = []
    references: list[AnnotationRef] = []
    questions: list[AnnotationRef] = []
    orphaned: list[AnnotationRef] = []

    for a in eligible:
        if a.status == "orphaned":
            orphaned.append(a)
        elif not a.anchored_quote:
            chapter_general.append(a)
        elif a.intent == "instruction":
            instructions.append(a)
        elif a.intent == "reference":
            references.append(a)
        elif a.intent == "question":
            questions.append(a)
        else:
            chapter_general.append(a)

    def _sort_key(a: AnnotationRef) -> int:
        if content and a.anchored_quote:
            pos = content.find(a.anchored_quote)
            return pos if pos >= 0 else len(content)
        return len(content)

    instructions.sort(key=_sort_key)
    references.sort(key=_sort_key)
    questions.sort(key=_sort_key)

    zones: list[str] = []

    if chapter_general:
        lines = ["【章节总评】"]
        for a in chapter_general:
            lines.append(_format_entry(a))
        zones.append("\n".join(lines))

    if instructions:
        lines = ["【修订指令】"]
        for a in instructions:
            lines.append(_format_entry(a))
        zones.append("\n".join(lines))

    if references:
        lines = ["【保留要求】"]
        for a in references:
            lines.append(_format_entry(a))
        zones.append("\n".join(lines))

    if questions:
        lines = ["【读者疑问】"]
        for a in questions:
            lines.append(_format_entry(a))
        zones.append("\n".join(lines))

    if orphaned:
        lines = ["【已失效批注】"]
        for a in orphaned:
            lines.append(_format_entry(a, orphaned=True))
        zones.append("\n".join(lines))

    return "\n\n".join(zones)


def _format_entry(a: AnnotationRef, orphaned: bool = False) -> str:
    parts: list[str] = []
    if a.anchored_quote:
        ctx = a.quote_context
        anchor_line = f"锚定引文：「{a.anchored_quote}」"
        if ctx.prefix or ctx.suffix:
            anchor_line += f"（前：…{ctx.prefix} / 后：{ctx.suffix}…）"
        parts.append(anchor_line)
    if orphaned:
        parts.append("（原文已被改写，请判断该意见是否仍需处理）")
    for c in a.comments:
        parts.append(c)
    return "\n".join(parts)


def build_annotation_revision_prompt(
    ctx: ChapterContext,
    previous_content: str,
    serialized_annotations: str,
    issues: list[str] | None = None,
    iteration: int = 1,
) -> str:
    """Writer 批示修订 prompt：上一版草稿 + 五区序列化批注 + 可选 issues。"""
    parts = [
        f"小说《{ctx.novel_title}》第 {ctx.chapter_index} 章「{ctx.chapter_title}」"
        f"的第 {iteration} 次修订。\n",
        f"上一版草稿：\n{previous_content}\n",
    ]
    if serialized_annotations:
        parts.append(f"作者批示：\n{serialized_annotations}\n")
    if issues:
        issue_lines = "\n".join(f"{i}. {issue}" for i, issue in enumerate(issues, 1))
        parts.append(f"编辑提出的修改意见：\n{issue_lines}\n")
    parts.append("请针对上述批示修订本章正文（800-1500 字），保留可取之处，直接输出修订后的正文。")
    return "\n".join(parts)
