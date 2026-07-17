# 06 — `inkmind next` 命令真实化

**What to build:** 将 `inkmind next` 从硬编码占位符 + 裸 SQL 状态模拟改为真实的 Agent 协作循环（Planner → Writer → Editor → MemoryKeeper）。每轮执行输出真实 AI 生成内容，并通过 UnitOfWork 事务边界持久化。可以分阶段实现：优先构建 Agent Pipeline 编排框架和 1 轮全流程集成，后续再调优生成质量。

**Blocked by:** 02 — 修复 CLI UUID 碰撞（UUID 正确性前提）
*注意：不阻塞于 T03/T04/T05（UnitOfWork 事务方法可在相同接口上独立开发，最终集成时统一接入）*

**Status:** ✅ done（2026-07-17 真实 DeepSeek API E2E 验证通过 + 两轴 code-review 修复完成）

## Acceptance criteria

- [x] `inkmind next --novel-id X` 执行 1 轮完整协作 → 输出至少 1 个**真实 AI 生成**的新章节
   ✅ E2E 验证 4 轮（DeepSeek deepseek-v4-flash/pro）：第 1-4 章真实生成（2272/2128/1960/1642 字），
   章节摘要与关键事件由 MemoryKeeper 真实产出且情节跨章连贯
- [x] 协作序列：Planner 生成规划 → Writer 根据规划写作 → Editor 评审 → MemoryKeeper 更新上下文
   ✅ Planner 批量规划 5 章大纲（T2 持久化 PLANNED 占位章节），Writer prompt 注入本章大纲；
   已有大纲时跳过 Planner（E2E 第 4 轮 `planned_count=0`，3 次 LLM 调用）
- [x] 所有操作通过 UnitOfWork 事务边界（直接调用事务方法）
   ✅ T1/T2/T3/T4/T5 全部走 UoW；CompressionTask 创建与事务提交收拢为
   `uow.create_compression_task()` / `uow.commit()`（ADR-0009，不再触碰 `uow._session`）
- [x] PipelineState 自动推进到下一阶段
- [x] CLI 输出中包含各 Agent 的执行摘要（生成内容量、耗时、状态）
   ✅ JSON 输出 `_stats`（ADR-0010-D）：calls/tokens/cost + min/max/avg_latency；
   文本模式输出 ⏱⚡💰 统计摘要行
- [x] 新增集成测试：CLI next → 验证新章节与状态正确推进
   ✅ tests/test_cli.py（子进程 + INKMIND_LLM_FAKE）+ tests/test_collaboration.py（30+ 用例）

## 实现备忘（2026-07-17）

**E2E 暴露并修复的真实缺陷（TDD，各带回归测试）：**
1. `llm/providers/base.py` — httpx 客户端未配置 timeout（默认 5s），长文生成必触发
   `ReadTimeout`；改为 `timeout=None`（CLAUDE.md「无超时可中断」，取消走 `cancel()`）
2. `llm/providers/base.py` — 重试循环未捕获 `httpx.TimeoutException`（chat + chat_stream）
3. `llm/factory.py` — 降级错误信息用 `str(e)`，`str(ReadTimeout(''))` 为空串导致诊断全丢；
   改为 `str(e) or type(e).__name__`

**两轴 code-review 后修复：**
- Spec：Planner 接入协作循环（原骨架只有 Writer→Editor→MemoryKeeper）；CLI 补充耗时输出
- Standards：ADR-0009 UoW 封装（3 处 `uow._session` 直接访问移除）；
  滑窗 window_size 3→5（CONTEXT.md 默认窗口）；factory.py 候选模型/错误组装去重；
  `WriterAgent.last_model` 可变状态改为返回值携带；scripted.py role 级联改 dict 映射

**测试：** 333 项全部通过（本轮净增 13 项：超时处理 5 + Planner/流水线 6 + UoW 1 + CLI stats 2 -
原用例改造）。

**遗留（已转工单 10）：** ADR-0010 完整对齐（frozen Stats 快照 / record·aggregate·reset_stats /
RateLimiter）与 ADR-0011 CLI 写路径文件锁接入（`get_uow` session 模式无锁，全系 CLI 共性问题）。
