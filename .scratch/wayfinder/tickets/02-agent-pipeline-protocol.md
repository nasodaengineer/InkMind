---
title: Agent 流水线通信协议
type: wayfinder:prototype
status: needs-triage
created: 2026-07-16
blocked_by: [01]
blocks: [03]
labels: [agent, wayfinder]
---

## Question

Planner→Writer→Editor 三个 Agent 之间如何传递结构化数据？

需要原型化的方案：

1. **输入/输出契约**：每个 Agent 接收什么、产出什么？用 Pydantic 模型定义严格接口。
   - Planner 输入：世界观设定 + 角色档案 + 用户意图 → 产出：大纲（Outline）
   - Writer 输入：大纲 + 前文记忆 + 风格指南 → 产出：章节正文
   - Editor 输入：章节正文 + 角色档案 + 情节地图 → 产出：评审报告 + 修改建议

2. **交互模式**：
   - 严格流水线（串行：Planner→Writer→Editor→反馈）
   - 带反馈回路（Editor 发现问题后回退给 Writer）
   - 并行多 Worker 写多章？

3. **数据载具（Canonical Packet）**：参考 Openwrite_skill 的 Packet 设计——所有 Agent 间通信通过一个标准化的数据包传递，包中包含来源、目标、数据载荷、元信息（版本、时间戳、格式）。

4. **批量 vs 单章**：Writer 是一次写一章还是可以一次写好几十章？

请用 Python 伪代码 + Pydantic 模型 + Mermaid 流程图表达设计方案。
