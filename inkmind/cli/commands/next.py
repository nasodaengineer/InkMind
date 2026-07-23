"""inkmind next — 1 轮完整 Agent 协作（真实 LLM 生成）。

Planner → Writer → Editor（修订循环）→ MemoryKeeper → WindowShift。
默认调用真实 LLM Provider（DeepSeek，需 DEEPSEEK_API_KEY）；
设置 INKMIND_LLM_FAKE=1 可离线演示（确定性内容，不联网）。
"""

from __future__ import annotations

from inkmind.agents.collaboration import CollaborationPipeline
from inkmind.cli.base_command import BaseCommand
from inkmind.cli.db import get_uow
from inkmind.llm.client import build_llm_client
from inkmind.models.agent import Verdict


COMMAND = "next"
HELP = (
    "1 轮完整 Agent 协作（真实 LLM 生成）：Planner → Writer → Editor → MemoryKeeper → WindowShift"
)
USAGE = "inkmind next [--title TITLE] [--db PATH] [--novel-id UUID]"


def setup(subparsers) -> None:
    parser = subparsers.add_parser(COMMAND, help=HELP, usage=USAGE)
    parser.add_argument("--title", type=str, default="", help="章节标题（留空用 Planner 规划标题）")
    parser.add_argument("--novel-id", type=str, default="", help="小说 ID")
    parser.add_argument("--db", type=str, default="", help="数据库路径（覆盖配置）")


def _stats_block(llm) -> dict:
    """提取各 Provider 的公开统计字段（--json 输出用，含耗时）。"""
    get_stats = getattr(llm, "get_stats", None)
    if get_stats is None:
        return {}
    block = {}
    for name, s in get_stats().items():
        block[name] = {
            "total_calls": s.total_calls,
            "successful_calls": s.successful_calls,
            "failed_calls": s.failed_calls,
            "fallback_used": s.fallback_used,
            "total_tokens": s.total_tokens,
            "estimated_cost": s.estimated_cost,
            "min_latency": s.min_latency,
            "max_latency": s.max_latency,
            "avg_latency": s.avg_latency,
        }
    return block


class NextCommand(BaseCommand):
    """执行一轮真实 LLM 驱动的 Agent 协作流水线。"""

    @classmethod
    async def _run(cls, args, formatter, cfg, db_path, novel_id):
        if novel_id is None:
            formatter.error(
                "未指定 novel_id。请通过 --novel-id 参数、inkmind.toml 的 project.novel_id "
                "或 INKMIND_NOVEL_ID 环境变量设置。"
            )
            return

        llm = build_llm_client()
        try:
            async with get_uow(db_path) as uow:
                pipeline = CollaborationPipeline(llm)
                try:
                    result = await pipeline.run_one_round(uow, novel_id, title=args.title)
                except ValueError as e:
                    formatter.error(str(e))
                    return
                except RuntimeError as e:
                    formatter.error(
                        f"LLM 调用失败：{e}\n"
                        "提示：请确认 DEEPSEEK_API_KEY 环境变量已设置（见 docs/ds.md），"
                        "或设置 INKMIND_LLM_FAKE=1 进行离线演示。"
                    )
                    return
        finally:
            shutdown = getattr(llm, "shutdown", None)
            if shutdown is not None:
                await shutdown()

        # 输出结果
        verdict_text = "评审通过" if result.verdict == Verdict.APPROVE else "兜底放行"
        planner_text = f"新规划{result.planned_count}章" if result.planned_count else "复用大纲"
        summary = f"第 {result.chapter_index} 章「{result.chapter_title}」已完成一轮流水线"
        data = {
            "chapter_index": result.chapter_index,
            "chapter_title": result.chapter_title,
            "chapter_id": str(result.chapter_id),
            "content_length": result.content_length,
            "iterations": result.iterations,
            "verdict": result.verdict.value,
            "summary": result.summary,
            "key_events": result.key_events,
            "max_iterations_hit": result.max_iterations_hit,
            "planned_count": result.planned_count,
            "_stats": _stats_block(llm),  # ADR-0010-D：JSON 输出尾部附 _stats
        }

        if formatter.json_mode:
            formatter.success(summary, data=data)
        else:
            print(
                f"\n流水线执行: PlannerAgent({planner_text}) → "
                f"WriterAgent(第{result.chapter_index}章, "
                f"{result.content_length}字, 修订{result.iterations}次) → "
                f"EditorAgent({verdict_text}) → MemoryKeeper(记忆压缩) → "
                f"WindowShift(滑窗)"
            )
            formatter.success(summary)
            formatter.info(f"已完成章节数: {result.chapter_index}")
            formatter.info(f"章节摘要: {result.summary}")
            if result.key_events:
                formatter.info(f"关键事件: {', '.join(result.key_events)}")
            if result.max_iterations_hit:
                formatter.info("注意: 达到最大修订次数仍未通过评审，已放行定稿（可人工复查）")
            # ADR-0010-D：文本模式输出 ⏱⚡💰 统计摘要行
            for name, s in _stats_block(llm).items():
                if s["total_calls"]:
                    formatter.info(
                        f"统计[{name}]: ⏱ 平均耗时 {s['avg_latency']:.1f}s "
                        f"(最长 {s['max_latency']:.1f}s) | "
                        f"⚡ {s['total_tokens']} tokens ({s['total_calls']} 次调用) | "
                        f"💰 估算成本 ${s['estimated_cost']:.4f}"
                    )


run = NextCommand.execute
